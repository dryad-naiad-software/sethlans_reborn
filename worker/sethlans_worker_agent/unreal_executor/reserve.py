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
2. ``job_params.validate_job_data`` (before any share access); a project
   refused after an adapter mismatch (``adapter_log.refused``) is declined;
3. a free Unreal slot (``slot.SlotPool``, the lowest free slot, non-blocking;
   render per card FR-PC-SLOT-1), which replaced the per-worker token;
4. a supported install of the job's ``engine_build``; then the Unreal
   cards in use (card selection C-27 b), which the exclusive reservation
   holds;
5. ``source_type == 'shared_project'`` (part of 2; logged explicitly);
6. the **static** VRAM gate on the sampler's latest reading (no wait):
   the relaxed pilot gate (``pilot_need_mb``, a reading that measures card
   memory) or the measured-peak gate (FR-UE-21), on the job's card (D22);
6a. the slot's farm Zen port is free or held by a farm zenserver;
6b. the latest known-folder check passed within ``check_6b_max_age_s()``;
7. one read of the worker's share state: a configured, reachable root,
   copied into ``JobParams.share_root``; the archive/overlay paths resolve
   inside it (string-only);
8. the FR-PA-3 probes (archive unless a ``ready`` entry exists; overlay),
   on the ``poll`` pool within the per-call probe budget;
9. the gate again on a ``fresh()`` reading (``used_mb`` read at reserve
   time) with its own short deadline; its card must be one of the Unreal
   cards in use read in step 4, and a pilot's card must have a card key
   (FR-GU-10). The reservation keeps that gate: the job's first launch is
   asked for its card (``gate.card_key``).

A ``shots`` job (sequence rendering D3, FR-SD-3) skips only the memory gate
(steps 6 and 9: no reading is needed, no card is chosen); every other check
runs, and its reservation carries the Unreal cards in use (step 4), so the
exclusive reservation can mark them.

**No resource is held when this returns None or raises** (chunk 3 L2): the
slot is given back on every such path. The reservation's release gives it
back; a retained reservation keeps it until restart. ``ProbePending`` is
raised only when the sole reason to decline is a probe still pending.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.executors.base import ProbePending, RendererReservation
from sethlans_worker_agent.unreal_executor import job_params as jp
from sethlans_worker_agent.unreal_executor import adapter_log, vram_gate_check
from sethlans_worker_agent.unreal_executor.slot import SlotPool

logger = logging.getLogger(__name__)

WARN_INTERVAL_SECONDS = 600.0
VRAM_FRESH_MAX_AGE_S = 5.0
VRAM_FRESH_DEADLINE_S = 2.0


class UnrealReservation(RendererReservation):
    """The slot plus what ``execute`` needs from the checks."""

    def __init__(self, params, install, gate, on_release: Callable[[], None], card_keys: tuple = (),
                 slot: int = 0):
        super().__init__(on_release=on_release)
        self.params = params
        self.install = install
        self.gate = gate
        self.card_keys = tuple(card_keys)  # held by the exclusive reservation (job_claim, C-27 b)
        self.slot = slot  # the job's Unreal slot (FR-PC-SLOT-1); its release gives it back


@dataclass
class ReserveDeps:
    """The checks' inputs (production values in ``executor.default_deps``)."""
    is_idle: Callable[[], bool]
    status: Callable[[], tuple]           # (enabled, reason) incl. the recovery gate
    installs: Callable[[], tuple]
    sampler: Callable[[], object]
    port_ok: Callable[[int], object]      # (slot) -> zen_port.PortCheck of that slot's port
    isolation_ok: Callable[[], bool]
    share_state: Callable[[], object]
    entry_ready: Callable[[str], bool]
    reachability: Callable[[], object]
    resolve: Callable[[object, str, str], object]
    budget: Callable[[], object]
    clock: Callable[[], float] = time.monotonic
    card_keys: Optional[Callable[[], tuple]] = None  # the Unreal cards in use (C-27 b); None: not checked


class Reserver:

    def __init__(self, deps: ReserveDeps):
        self.deps = deps
        self.slots = SlotPool()
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
            params = jp.validate_job_data(job_data)
        except jp.JobDataError as exc:
            self._skip(job_data.get('id'), "invalid job data (%s).", exc)
            return None
        if adapter_log.refused(adapter_log.project_key(params)):
            self._skip(params.job_id, "its project reported another graphics card on this worker (refused until "
                                      "restart).")
            return None
        return params

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

    def _local_checks(self, params, slot: int) -> bool:
        port = self.deps.port_ok(slot)
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
        if params.is_shots:  # D3: no memory gate, no reading, no card choice; the card keys are still held
            return vram_gate_check.evaluate(params, None)
        sampler = self.deps.sampler()
        if sampler is None:
            self._skip(params.job_id, "no GPU memory reading.")
            return None
        reading = sampler.fresh(max_age_s=VRAM_FRESH_MAX_AGE_S, deadline=time.monotonic() + VRAM_FRESH_DEADLINE_S)
        gate = self._gate(params, reading)
        return gate if gate.ok else None

    def _gated_card_ok(self, params, gate, card_keys: tuple) -> bool:
        """FR-GU-10: the gated card is still an Unreal card in use; a pilot needs a card the manager can match."""
        if params.is_pilot and gate.card_key is None:
            self._skip(params.job_id, "the card the memory gate checked has no PCI address, so a pilot could not "
                                      "be reported.")
            return False
        in_use = {key_address(key) for key in card_keys} - {None}  # unreadable keys match nothing (fail closed)
        gated = key_address(gate.card_key)
        if gate.card_key is not None and card_keys and (gated is None or gated not in in_use):
            self._skip(params.job_id, "the card the memory gate checked is no longer an Unreal card in use.")
            return False
        return True

    def _card_keys(self, params) -> Optional[tuple]:
        """The Unreal cards in use (FR-CL-1), whatever Blender's slots and the compute method (C-27 b)."""
        if self.deps.card_keys is None:
            return ()
        keys = tuple(self.deps.card_keys())
        if not keys:
            self._skip(params.job_id, "no ticked graphics card can be used.")
            return None
        return keys

    def _after_slot(self, params, slot: int):
        install = self._install(params)
        if install is None:
            return None
        card_keys = self._card_keys(params)
        if card_keys is None:
            return None
        sampler = self.deps.sampler()
        if not self._gate(params, sampler.latest() if sampler is not None else None).ok:
            return None
        if not self._local_checks(params, slot):
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
        if gate is None or not self._gated_card_ok(params, gate, card_keys):
            return None
        return UnrealReservation(params, install, gate, on_release=lambda: self.slots.give(slot),
                                 card_keys=card_keys, slot=slot)

    def try_reserve(self, job_data: dict) -> Optional[UnrealReservation]:
        params = self._prechecks(job_data)
        if params is None:
            return None
        slot = self.slots.take()
        if slot is None:
            return None
        reservation = None
        try:
            reservation = self._after_slot(params, slot)
            return reservation
        finally:
            if reservation is None:
                self.slots.give(slot)
