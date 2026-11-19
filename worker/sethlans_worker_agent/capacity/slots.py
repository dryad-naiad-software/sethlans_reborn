# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Stateful slot tracker.

WorkerCapacity owns the GPU assignment map, keyed by card key (FR-CL-3),
and the CPU slot flag. All slot mutations take a single internal lock;
the lock is acquired and released entirely within each public method and
is never held across an API call or subprocess invocation (lock ordering
rule). A GPU reservation copies its cards from the profile under the lock.
"""
import logging
import threading
from typing import Dict, Optional, Tuple

from sethlans_worker_agent.capacity import exclusive
from sethlans_worker_agent.capacity.drift import (
    DETECTION_FAILED,
    adopt_detection_executable,
    detect_physical_gpus_now,
    set_drift_exit_code,
)
from sethlans_worker_agent.capacity.gpu_list import (
    describe_gpu_list_change,
    gpu_identity,
)
from sethlans_worker_agent.capacity.profile import (
    CapacityProfile,
    SlotReservation,
)

logger = logging.getLogger(__name__)


class WorkerCapacity:
    """Stateful slot tracker.

    Constructed once at worker startup from a CapacityProfile. The
    profile is immutable; only the slot assignment maps change at
    runtime.
    """

    def __init__(self, profile: CapacityProfile):
        self._profile = profile
        self._lock = threading.Lock()
        self._gpu_assignments: Dict[str, int] = {}  # card key -> job_id
        self._cpu_slot_job_id: Optional[int] = None
        # Job holding a whole-machine reservation (capacity/exclusive.py).
        self._exclusive_job_id: Optional[int] = None

    @property
    def profile(self) -> CapacityProfile:
        return self._profile

    @property
    def total(self) -> int:
        return self._profile.total

    def _gpu_free_locked(self) -> bool:
        """True when a GPU slot is free; counts only held keys of cards in the profile (FR-CL-3)."""
        profile = self._profile
        if profile.gpu_slot_count == 0:
            return False
        held = [key for key in profile.card_keys if key in self._gpu_assignments]
        if profile.gpu_mode == "combined":
            return not held
        return len(held) < profile.gpu_slot_count

    def is_full(self) -> bool:
        """True if no slot is available for any device preference.

        Source of truth for the polling gate (FR-6). Inspects
        _gpu_assignments and _cpu_slot_job_id under _lock. _active_jobs
        lags slot reservation and MUST NOT be consulted here.
        """
        with self._lock:
            cpu_full = self._cpu_slot_job_id is not None or self._profile.cpu_slot_count == 0
            return not self._gpu_free_locked() and cpu_full

    def free_device_prefs(self) -> list:
        """Return device preferences this worker can currently accept (FR-9)."""
        with self._lock:
            gpu_free = self._gpu_free_locked()
            cpu_free = self._profile.cpu_slot_count > 0 and self._cpu_slot_job_id is None
        prefs = []
        if gpu_free:
            prefs.append('GPU')
        if cpu_free:
            prefs.append('CPU')
        if gpu_free or cpu_free:
            prefs.append('ANY')
        return prefs

    def _reserve_gpu_locked(self, job_id: int) -> Optional[Tuple[str, ...]]:
        """Mark card keys for ``job_id``. Caller must already hold self._lock.

        Split mode marks the first free card in use (address order); combined
        mode marks every card in use at once. Returns the marked keys, or
        None when no GPU slot is free.
        """
        if not self._gpu_free_locked():
            return None
        keys = self._profile.card_keys
        if self._profile.gpu_mode != "combined":
            keys = next(((key,) for key in keys if key not in self._gpu_assignments), ())
        for key in keys:
            self._gpu_assignments[key] = job_id
        return keys or None

    def reserve_for_job(
        self, job_id: int, device_pref: str
    ) -> Optional[SlotReservation]:
        """Atomically reserve a slot.

        Returns a reservation handle the caller MUST release via
        release_slot(job_id). Returns None if no compatible slot is free.
        Prefers a GPU slot when device_pref is GPU or ANY and a GPU is
        available; falls back to CPU for CPU or ANY otherwise.
        """
        with self._lock:
            profile = self._profile
            keys = self._reserve_gpu_locked(job_id) if device_pref in ('GPU', 'ANY') else None
            if keys is not None:
                # The copy is taken under the lock, from the profile the keys came from (C2).
                return SlotReservation(
                    job_id=job_id, device_used='GPU', card_keys=keys, cards=profile.cards,
                    gpu_mode=profile.gpu_mode, ignored=profile.ignored, problem=profile.problem,
                )
            if (device_pref in ('CPU', 'ANY') and profile.cpu_slot_count > 0
                    and self._cpu_slot_job_id is None):
                self._cpu_slot_job_id = job_id
                return SlotReservation(job_id=job_id, device_used='CPU', problem=profile.problem)
        return None

    def reserve_exclusive(self, job_id: int) -> Optional[SlotReservation]:
        """Reserve every slot for one job (Unreal, FR-WX-10); see exclusive.py."""
        return exclusive.reserve_exclusive(self, job_id)

    def has_no_reservations(self) -> bool:
        """True when no slot holds any reservation (FR-WX-13)."""
        return exclusive.has_no_reservations(self)

    def retain_slots(self, job_id: int) -> None:
        """Keep job_id's slots until restart, safe from a re-claim; see exclusive.py."""
        exclusive.retain_slots(self, job_id)

    def has_exclusive_reservation(self) -> bool:
        """True while a whole-machine reservation is held (FR-WX-14)."""
        return exclusive.has_exclusive_reservation(self)

    def release_slot(self, job_id: int) -> None:
        """Release whatever slot job_id holds. Idempotent.

        Contract (FR-13): acquires _lock, iterates _gpu_assignments.items()
        to find and pop any entry whose value equals job_id, and clears
        _cpu_slot_job_id if it equals job_id. Must NEVER raise. Silent
        no-op (DEBUG log only) if job_id holds no slot.
        """
        try:
            with self._lock:
                released_keys = [
                    key for key, owner in self._gpu_assignments.items()
                    if owner == job_id
                ]
                for key in released_keys:
                    self._gpu_assignments.pop(key, None)
                released_cpu = False
                if self._cpu_slot_job_id == job_id:
                    self._cpu_slot_job_id = None
                    released_cpu = True
            if released_keys or released_cpu:
                logger.info(
                    "Released slot for job %s (gpu cards=%s cpu=%s).",
                    job_id, released_keys, released_cpu,
                )
            else:
                logger.debug(
                    "release_slot(%s): no slot held; no-op.", job_id,
                )
        except BaseException as exc:  # noqa: BLE001 - contract: never raise
            logger.error(
                "release_slot(%s) unexpected error suppressed: %s",
                job_id, exc,
            )

    def gpu_assignments_snapshot(self) -> Dict[str, int]:
        """Return a copy of the GPU assignment map under _lock.

        Used by the worker web UI via the job_processor.get_gpu_assignment_snapshot
        thin pass-through.
        """
        with self._lock:
            return dict(self._gpu_assignments)

    def assert_gpu_list_unchanged(self) -> Optional[str]:
        """Compare a fresh, uncached GPU detection with the list in use (#239).

        The list in use is the cached system_monitor.get_gpu_device_details()
        that slots and render scripts work from; the detection runs with the
        Blender that produced it, or a fallback when that one was removed,
        adopted when its list matches (drift.py, #253). Any difference in the
        ordered (device id, backend) pairs is a change: a card gone or new,
        a backend or id changed, or the same cards in another order. On a
        change, triggers the drift handler flow per FR-22a: terminate
        active Blender subprocesses via the existing cancellation path in
        blender_executor, set the exit-code flag, and set
        agent._shutdown_event. sys.exit is NEVER called from this module.

        Skip conditions (no-op, do not trigger drift):
          * the profile has no Blender card in use (method CPU, no card
            ticked or none usable); the ~5-15s detection subprocess is not run.
          * ``detect_physical_gpus_now()`` returned ``None`` -- transient
            failure: returns ``DETECTION_FAILED`` ("try again later").
        """
        if not self._profile.cards or self._profile.gpu_slot_count == 0:
            logger.debug("assert_gpu_list_unchanged: no Blender card in use, skipping drift check.")
            return None

        current = detect_physical_gpus_now()
        if current is None:
            # Transient failure. Do NOT treat as drift; a request stays pending.
            return DETECTION_FAILED
        from sethlans_worker_agent import system_monitor
        change = describe_gpu_list_change(
            gpu_identity(system_monitor.get_gpu_device_details()), current,
        )
        if change is None:
            adopt_detection_executable()  # a replacement detection Blender agreed (#253)
            return None

        logger.critical(
            "GPU change detected: %s. Initiating graceful shutdown.", change,
        )
        from sethlans_worker_agent import blender_stop
        blender_stop.mark_hardware_change()  # first: the stop reason is right even if a step below raises
        try:
            # Imported lazily to avoid a circular import.
            from sethlans_worker_agent import job_processor
            job_processor.terminate_all_active_jobs_for_drift()
        except BaseException as exc:  # noqa: BLE001 - shutdown must proceed
            logger.error(
                "Error during drift-triggered termination: %s", exc,
            )
        finally:
            set_drift_exit_code(1)
            try:
                from sethlans_worker_agent import agent as agent_module
                agent_module._shutdown_event.set()
            except BaseException as exc:  # noqa: BLE001
                logger.error(
                    "Could not set shutdown event during drift handler: %s",
                    exc,
                )
