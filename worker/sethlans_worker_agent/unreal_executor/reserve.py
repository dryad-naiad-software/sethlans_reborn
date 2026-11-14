# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``UnrealExecutor.try_reserve`` (Unreal foundation FR-UE-1, FR-UE-21,
FR-UE-26 check 6a, FR-UE-27 check 6b; Conc R2-M2, chunk 3 L2 contract).

Checks, in order; the first failure returns ``None`` (the job is skipped,
not claimed):

0. the machine is fully idle (``capacity.has_no_reservations()``);
1. Unreal is enabled and the startup recovery succeeded;
2. ``job_params.validate_job_data`` (before any share access);
3. the per-worker Unreal token (``BoundedSemaphore(1)``, non-blocking);
4. a supported install of the job's ``engine_build``;
5. ``source_type == 'shared_project'`` (part of 2; logged explicitly);
6. the **static** VRAM gate on the sampler's latest reading (no wait):
   the relaxed pilot gate (``pilot_need_mb``, ``nvidia-smi`` only) or the
   measured-peak gate (FR-UE-21);
6a. the farm Zen port is free or held by the farm zenserver;
6b. the latest known-folder check passed within ``check_6b_max_age_s()``;
7. one read of the worker's share state: a configured, reachable root,
   copied into ``JobParams.share_root``; the archive/overlay paths resolve
   inside it (string-only);
8. the FR-PA-3 probes (archive unless a ``ready`` entry exists; overlay),
   on the ``poll`` pool within the per-call probe budget;
9. the gate again on a ``fresh()`` reading (``used_mb`` read at reserve
   time) with its own short deadline.

**No resource is held when this returns None or raises** (chunk 3 L2): the
token is released on every such path. ``ProbePending`` is raised only when
the sole reason to decline is a probe still pending.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from sethlans_worker_agent.executors.base import ProbePending, RendererReservation
from sethlans_worker_agent.unreal_executor import job_params as jp
from sethlans_worker_agent.unreal_executor import vram_gate_check

logger = logging.getLogger(__name__)

WARN_INTERVAL_SECONDS = 600.0
VRAM_FRESH_MAX_AGE_S = 5.0
VRAM_FRESH_DEADLINE_S = 2.0


class UnrealReservation(RendererReservation):
    """The token plus what ``execute`` needs from the checks."""

    def __init__(self, params, install, gate, on_release: Callable[[], None]):
        super().__init__(on_release=on_release)
        self.params = params
        self.install = install
        self.gate = gate


@dataclass
class ReserveDeps:
    """The checks' inputs (production values in ``executor.default_deps``)."""
    is_idle: Callable[[], bool]
    status: Callable[[], tuple]           # (enabled, reason) incl. the recovery gate
    installs: Callable[[], tuple]
    sampler: Callable[[], object]
    port_ok: Callable[[], object]         # zen_port.PortCheck
    isolation_ok: Callable[[], bool]
    share_state: Callable[[], object]
    entry_ready: Callable[[str], bool]
    reachability: Callable[[], object]
    resolve: Callable[[object, str, str], object]
    budget: Callable[[], object]
    clock: Callable[[], float] = time.monotonic


class Reserver:

    def __init__(self, deps: ReserveDeps):
        self.deps = deps
        self.token = threading.BoundedSemaphore(1)
        self._warned: Dict[str, float] = {}
        self._warn_lock = threading.Lock()  # leaf lock

    # -- logging --------------------------------------------------------
    def _warn_rate_limited(self, key: str, message: str, *args) -> None:
        now = self.deps.clock()
        with self._warn_lock:
            last = self._warned.get(key)
            due = last is None or now - last >= WARN_INTERVAL_SECONDS
            if due:
                self._warned[key] = now
                for stale in [k for k, t in self._warned.items() if now - t >= WARN_INTERVAL_SECONDS * 2]:
                    del self._warned[stale]
        if due:
            logger.warning(message, *args)

    def _skip(self, job_id, message: str, *args) -> None:
        self._warn_rate_limited(f"skip:{job_id}:{message}", "Skipping Unreal job %s: " + message, job_id, *args)

    # -- checks ---------------------------------------------------------
    def _prechecks(self, job_data: dict):
        if not self.deps.is_idle():
            return None
        enabled, reason = self.deps.status()
        if not enabled:
            self._skip(job_data.get('id'), "Unreal is disabled on this worker (%s).", reason)
            return None
        try:
            return jp.validate_job_data(job_data)
        except jp.JobDataError as exc:
            self._skip(job_data.get('id'), "invalid job data (%s).", exc)
            return None

    def _install(self, params):
        for install in self.deps.installs():
            if install.supported and install.build == params.engine_build:
                return install
        self._skip(params.job_id, "no supported install of build %s.", params.engine_build)
        return None

    def _gate(self, params, reading):
        result = vram_gate_check.evaluate(params, reading)
        if not result.ok:
            self._skip(params.job_id, "%s", result.message)
        return result

    def _local_checks(self, params) -> bool:
        port = self.deps.port_ok()
        if not port.ok:
            self._warn_rate_limited('zen-port', "Unreal jobs skipped: %s", port.message)
            return False
        if not self.deps.isolation_ok():
            self._warn_rate_limited('isolation', "Unreal jobs skipped: the known-folder check has not passed "
                                                 "recently (FR-UE-27).")
            return False
        return True

    def _share(self, params):
        state = self.deps.share_state()
        if state.root is None or not state.reachable:
            self._skip(params.job_id, "the Sethlans share is not reachable.")
            return None
        params = params.with_share_root(state.root)
        try:
            self.deps.resolve(state.root, params.archive_path, 'file')
            if params.overlay_path:
                self.deps.resolve(state.root, params.overlay_path, 'dir')
        except ValueError as exc:
            self._skip(params.job_id, "share path rejected (%s).", exc)
            return None
        return params

    def _probes(self, params) -> Optional[bool]:
        """True, False, or None (pending)."""
        reach, budget = self.deps.reachability(), self.deps.budget()
        checks = []
        if not self.deps.entry_ready(params.archive_sha256):
            checks.append((params.archive_path, 'file'))
        if params.overlay_path:
            checks.append((params.overlay_path, 'dir'))
        pending = False
        for relpath, kind in checks:
            result = reach.check(params.share_root, relpath, kind, job_id=params.job_id, pool='poll', budget=budget)
            if result is None:
                pending = True
            elif not result:
                self._skip(params.job_id, "%s is not reachable on the share.", relpath)
                return False
        return None if pending else True

    def _fresh_gate(self, params):
        sampler = self.deps.sampler()
        if sampler is None:
            self._skip(params.job_id, "no GPU memory reading.")
            return None
        reading = sampler.fresh(max_age_s=VRAM_FRESH_MAX_AGE_S, deadline=time.monotonic() + VRAM_FRESH_DEADLINE_S)
        gate = self._gate(params, reading)
        return gate if gate.ok else None

    def _after_token(self, params):
        install = self._install(params)
        if install is None:
            return None
        sampler = self.deps.sampler()
        if not self._gate(params, sampler.latest() if sampler is not None else None).ok:
            return None
        if not self._local_checks(params):
            return None
        params = self._share(params)
        if params is None:
            return None
        probes = self._probes(params)
        if probes is None:
            raise ProbePending()
        if not probes:
            return None
        gate = self._fresh_gate(params)
        if gate is None:
            return None
        return UnrealReservation(params, install, gate, on_release=self.token.release)

    def try_reserve(self, job_data: dict) -> Optional[UnrealReservation]:
        params = self._prechecks(job_data)
        if params is None:
            return None
        if not self.token.acquire(blocking=False):
            return None
        reservation = None
        try:
            reservation = self._after_token(params)
            return reservation
        finally:
            if reservation is None:
                self.token.release()
