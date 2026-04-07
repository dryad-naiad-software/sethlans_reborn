# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Stateful slot tracker.

WorkerCapacity owns the GPU index assignment map and the CPU slot flag.
All slot mutations take a single internal lock; the lock is acquired
and released entirely within each public method and is never held
across an API call or subprocess invocation (lock ordering rule).

Replaces the legacy _cpu_lock, _gpu_lock, _gpu_assignment_map, and
_reserve_next_available_gpu module-level primitives in job_processor.
"""
import logging
import threading
from typing import Dict, List, Optional

from sethlans_worker_agent.capacity.drift import (
    count_physical_gpus_now,
    set_drift_exit_code,
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
        self._gpu_assignments: Dict[int, int] = {}  # gpu_index -> job_id
        self._cpu_slot_job_id: Optional[int] = None

    @property
    def profile(self) -> CapacityProfile:
        return self._profile

    @property
    def total(self) -> int:
        return self._profile.total

    def is_full(self) -> bool:
        """True if no slot is available for any device preference.

        Source of truth for the polling gate (FR-6). Inspects
        _gpu_assignments and _cpu_slot_job_id under _lock. _active_jobs
        lags slot reservation and MUST NOT be consulted here.
        """
        with self._lock:
            gpus_used = len(self._gpu_assignments)
            cpu_used = self._cpu_slot_job_id is not None
            gpu_full = gpus_used >= self._profile.gpu_slot_count
            cpu_full = cpu_used or self._profile.cpu_slot_count == 0
            return gpu_full and cpu_full

    def free_device_prefs(self) -> List[str]:
        """Return device preferences this worker can currently accept (FR-9)."""
        with self._lock:
            gpus_used = len(self._gpu_assignments)
            cpu_used = self._cpu_slot_job_id is not None
            gpu_free = gpus_used < self._profile.gpu_slot_count
            cpu_free = (
                self._profile.cpu_slot_count > 0 and not cpu_used
            )

        prefs: List[str] = []
        if gpu_free:
            prefs.append('GPU')
        if cpu_free:
            prefs.append('CPU')
        if gpu_free or cpu_free:
            prefs.append('ANY')
        return prefs

    def _reserve_gpu_locked(self, job_id: int) -> Optional[List[int]]:
        """Allocate GPU indices. Caller must already hold self._lock.

        Returns a list of Blender-ordering GPU indices on success, or
        None if no GPU slot is free. Combined mode returns an empty
        list (signals "use all non-CPU devices" to render_script).
        """
        profile = self._profile
        if profile.gpu_slot_count == 0:
            return None
        if len(self._gpu_assignments) >= profile.gpu_slot_count:
            return None

        # FORCE_GPU_INDEX: pin to one physical index. slot count == 1.
        if profile.force_gpu_index is not None:
            idx = profile.force_gpu_index
            if idx in self._gpu_assignments:
                return None
            self._gpu_assignments[idx] = job_id
            return [idx]

        # Combined mode: single slot, all GPUs used by one Blender invocation.
        if profile.gpu_mode == "combined":
            # Sentinel key 0 marks the combined slot as occupied. The
            # reservation's gpu_indices is an empty list, which signals
            # render_script to enable all non-CPU devices via the default
            # branch in _append_cycles_gpu_config.
            self._gpu_assignments[0] = job_id
            return []

        # Split mode: pick the first free physical index.
        for i in range(profile.startup_gpu_count):
            if i not in self._gpu_assignments:
                self._gpu_assignments[i] = job_id
                return [i]
        return None

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
            gpu_indices: Optional[List[int]] = None
            cpu_acquired = False

            if device_pref in ('GPU', 'ANY'):
                gpu_indices = self._reserve_gpu_locked(job_id)

            if gpu_indices is None and device_pref in ('CPU', 'ANY'):
                if (
                    self._profile.cpu_slot_count > 0
                    and self._cpu_slot_job_id is None
                ):
                    self._cpu_slot_job_id = job_id
                    cpu_acquired = True

        if gpu_indices is not None:
            return SlotReservation(
                job_id=job_id,
                device_used='GPU',
                gpu_indices=list(gpu_indices),
            )
        if cpu_acquired:
            return SlotReservation(
                job_id=job_id,
                device_used='CPU',
                gpu_indices=[],
            )
        return None

    def release_slot(self, job_id: int) -> None:
        """Release whatever slot job_id holds. Idempotent.

        Contract (FR-13): acquires _lock, iterates _gpu_assignments.items()
        to find and pop any entry whose value equals job_id, and clears
        _cpu_slot_job_id if it equals job_id. Must NEVER raise. Silent
        no-op (DEBUG log only) if job_id holds no slot.
        """
        try:
            with self._lock:
                released_gpu_indices = [
                    idx for idx, owner in self._gpu_assignments.items()
                    if owner == job_id
                ]
                for idx in released_gpu_indices:
                    self._gpu_assignments.pop(idx, None)
                released_cpu = False
                if self._cpu_slot_job_id == job_id:
                    self._cpu_slot_job_id = None
                    released_cpu = True
            if released_gpu_indices or released_cpu:
                logger.info(
                    "Released slot for job %s (gpu_indices=%s cpu=%s).",
                    job_id, released_gpu_indices, released_cpu,
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

    def gpu_assignments_snapshot(self) -> Dict[int, int]:
        """Return a copy of the GPU assignment map under _lock.

        Used by the worker web UI via the job_processor.get_gpu_assignment_snapshot
        thin pass-through.
        """
        with self._lock:
            return dict(self._gpu_assignments)

    def assert_gpu_count_unchanged(self) -> None:
        """Compare current uncached physical count against startup count.

        On mismatch, triggers the drift handler flow per FR-22a:
        terminate active Blender subprocesses via the existing
        cancellation path in blender_executor, set the exit-code flag,
        and set agent._shutdown_event. sys.exit is NEVER called from
        inside this module.
        """
        current = count_physical_gpus_now()
        if current == self._profile.startup_gpu_count:
            return

        logger.critical(
            "GPU drift detected! Startup count: %d, current: %d. "
            "Initiating graceful shutdown.",
            self._profile.startup_gpu_count, current,
        )
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
