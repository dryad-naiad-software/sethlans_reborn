# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``UnrealExecutor.try_reserve`` (Unreal foundation FR-UE-1, FR-UE-21,
FR-UE-26 check 6a, FR-UE-27 check 6b; Conc R2-M2, chunk 3 L2 contract).

Checks, in order; the first failure returns ``None`` (the job is skipped,
not claimed):

0. an Unreal job may be claimed now (``unreal_bridge.can_take_unreal``: a
   free Unreal card, no Blender CPU job, no pending rebuild; render per
   card FR-PC-CAP-5);
0b. the start-up rule (``ReserveDeps.startup_rule``, FR-PC-SU-2): under
   ``one_at_a_time`` the job is declined while another claim is starting
   (``startup_watch``); under ``together`` nothing is checked;
1. Unreal is enabled and the startup recovery succeeded;
2. ``job_params.validate_job_data`` (before any share access); a project
   refused after an adapter mismatch (``adapter_log.refused``) is declined;
3. a free Unreal slot (``slot.SlotPool``, the lowest free slot, non-blocking;
   render per card FR-PC-SLOT-1), which replaced the per-worker token;
4. a supported install of the job's ``engine_build``; then the Unreal
   cards in use (card selection C-27 b);
5. ``source_type == 'shared_project'`` (part of 2; logged explicitly);
6. the **static** VRAM gate on the sampler's latest reading (no wait):
   the relaxed pilot gate (``pilot_need_mb``, a reading that measures card
   memory) or the measured-peak gate (FR-UE-21), on the first free card it
   fits (render per card D4, FR-PC-SEL-1; ``reserve_card.free_now``);
6a. the slot's farm Zen port is free or held by a farm zenserver;
6b. the latest known-folder check passed within ``check_6b_max_age_s()``;
7. the share state and 8. the FR-PA-3 probes (``reserve_share``);
9. the gate again on a ``fresh()`` reading (``used_mb`` read at reserve
   time) with its own short deadline, over the free cards read again; its
   card must be one of the Unreal cards in use read in step 4, and a
   pilot's card must have a card key (FR-GU-10). The reservation keeps that
   gate: the job's first launch is asked for its card (``gate.card_key``).
9a. the RAM gate (``reserve_local.ram_gate``, render per card D9,
   FR-PC-RAM-4, -6): the job's physical and commit need, checked only while
   another job runs on the machine.

A ``shots`` job (sequence rendering D3, FR-SD-3) skips only the memory gate
(steps 6 and 9: no reading is needed); every other check runs.

The reservation's ``card_key`` is the card ``job_claim`` holds and the
first launch is asked for (``reserve_card.choose``, render per card D4,
D14, FR-PC-CAP-3); a job with no free card it fits is declined, so the
claim walk moves on. The hold, taken under ``WorkerCapacity._lock`` after this
returns, is the final check.

**No resource is held when this returns None or raises** (chunk 3 L2): the
slot is given back on every such path. The reservation's release gives it
back; a retained reservation keeps it until restart.

Each reservation has a ``claim_id`` (uuid4 hex, D16) and is registered, as the
last step, in the RAM ledger (``ReserveDeps.ram``) with the need check 9a
worked out (FR-PC-RAM-5) and in the disk ledger (``ReserveDeps.disk``) with a
need of 0 (FR-PC-DSK-1), then marked *starting* in the start-up watch
(``ReserveDeps.startup``, FR-PC-SU-1; ``reserve_claim``). Every entry is dropped
by the release (before the slot is given back) and by ``retain_until_restart``,
which keeps only the slot (FR-PC-SU-1). ``ProbePending`` is
raised only when the sole reason to decline is a probe still pending.
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.executors.base import ProbePending
from sethlans_worker_agent.unreal_executor import job_params as jp
from sethlans_worker_agent.unreal_executor import (
    adapter_log, disk_ledger, ram_ledger, reserve_card, reserve_claim, reserve_local, reserve_share, startup_watch,
    vram_gate_check)
from sethlans_worker_agent.unreal_executor.reservation import UnrealReservation
from sethlans_worker_agent.unreal_executor.slot import SlotPool

logger = logging.getLogger(__name__)
__all__ = ['ReserveDeps', 'Reserver', 'UnrealReservation']

WARN_INTERVAL_SECONDS = 600.0
VRAM_FRESH_MAX_AGE_S = 5.0
VRAM_FRESH_DEADLINE_S = 2.0


@dataclass
class ReserveDeps:
    """The checks' inputs (production values in ``executor.default_deps``)."""
    can_take: Callable[[], bool]          # check 0 (FR-PC-CAP-5)
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
    ram: Optional[ram_ledger.RamLedger] = None  # production: ram_ledger.ledger; None: a ledger of this Reserver
    capacity: Optional[Callable[[], object]] = None  # the WorkerCapacity (free keys, D14); None: not checked
    d8_key: Optional[Callable[[], Optional[str]]] = None  # D8's Unreal card now (override, D14)
    disk: Optional[disk_ledger.DiskLedger] = None  # production: disk_ledger.ledger; None: one of this Reserver
    entry_dir: Optional[Callable[[str], Optional[Path]]] = None  # the archive's cache entry (warm-up marker)
    ram_readers: Optional[reserve_local.RamReaders] = None  # None: psutil and GetPerformanceInfo
    startup: Optional[startup_watch.StartupWatch] = None  # production: startup_watch.watch; None: one of this Reserver
    startup_rule: Optional[Callable[[], str]] = None  # check 0b (FR-PC-SU-2): config_unreal.STARTUP_RULE; None: together


class Reserver:

    def __init__(self, deps: ReserveDeps):
        self.deps = deps
        self.slots = SlotPool()
        self.ram = deps.ram if deps.ram is not None else ram_ledger.RamLedger()
        self.disk = deps.disk if deps.disk is not None else disk_ledger.DiskLedger()
        self.startup = deps.startup if deps.startup is not None else startup_watch.StartupWatch()
        self._books = reserve_claim.Books(self.slots, self.ram, self.disk, self.startup)
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
        if not self.deps.can_take():
            return None
        if self.deps.startup_rule is not None and not self.startup.admits(self.deps.startup_rule()):
            self._skip(job_data.get('id'), "%s", startup_watch.STILL_STARTING)  # check 0b (FR-PC-SU-2)
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

    def _gate(self, params, reading, card_keys: tuple):
        result = vram_gate_check.evaluate(params, reading, reserve_card.free_now(card_keys, self.deps))
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

    def _fresh_gate(self, params, card_keys: tuple):
        if params.is_shots:  # D3: no memory gate, no reading, no card choice; the card keys are still held
            return vram_gate_check.evaluate(params, None)
        sampler = self.deps.sampler()
        if sampler is None:
            self._skip(params.job_id, "no GPU memory reading.")
            return None
        reading = sampler.fresh(max_age_s=VRAM_FRESH_MAX_AGE_S, deadline=time.monotonic() + VRAM_FRESH_DEADLINE_S)
        gate = self._gate(params, reading, card_keys)
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
        if not self._gate(params, sampler.latest() if sampler is not None else None, card_keys).ok:
            return None
        if not self._local_checks(params, slot):
            return None
        params = reserve_share.share(params, self.deps, self._skip)
        if params is None:
            return None
        probes = reserve_share.probes(params, self.deps, self._skip)
        if probes is None:
            raise ProbePending()
        if not probes:
            return None
        gate = self._fresh_gate(params, card_keys)
        if gate is None or not self._gated_card_ok(params, gate, card_keys):
            return None
        card_key, declined = reserve_card.choose(params, gate, card_keys, self.deps)
        ram = reserve_local.ram_gate(params, gate, slot, self.deps, self.ram) if not declined else None
        if declined or not ram.ok:
            self._skip(params.job_id, "%s", declined or ram.message)
            return None
        claim_id = uuid.uuid4().hex
        return UnrealReservation(params, install, gate,
                                 on_release=lambda: reserve_claim.release(claim_id, slot, self._books),
                                 card_keys=card_keys, slot=slot, claim_id=claim_id, ram=self.ram,
                                 card_key=card_key, disk=self.disk, ram_need=ram.need, startup=self.startup)

    def try_reserve(self, job_data: dict) -> Optional[UnrealReservation]:
        params = self._prechecks(job_data)
        if params is None:
            return None
        slot = self.slots.take()
        if slot is None:
            return None
        reservation = None
        try:
            claimed = self._after_slot(params, slot)
            if claimed is not None:
                reserve_claim.register(claimed, self._books)
            reservation = claimed
            return reservation
        finally:
            if reservation is None:
                self.slots.give(slot)
