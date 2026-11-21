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
The profile is replaced only by a rebuild (rebuild.py, FR-CL-4), under the
lock; while one is pending no GPU slot is free. An Unreal job holds one
card key and nothing else (unreal_hold.py, render per card FR-PC-CAP).
"""
import logging
import threading
from typing import Dict, Iterable, Optional, Set, Tuple

from sethlans_worker_agent.capacity import rebuild, unreal_hold
from sethlans_worker_agent.capacity.profile import (
    CapacityProfile,
    SlotReservation,
)

logger = logging.getLogger(__name__)


class WorkerCapacity:
    """Stateful slot tracker.

    Constructed at worker startup from a CapacityProfile. The profile
    itself is immutable; a rebuild swaps in another one (rebuild.py).
    """

    def __init__(self, profile: CapacityProfile):
        self._profile = profile
        self._lock = threading.Lock()
        self._gpu_assignments: Dict[str, int] = {}  # card key -> job_id
        self._cpu_slot_job_id: Optional[int] = None
        self._unreal_jobs: Dict[int, str] = {}  # Unreal owner -> the one card key it holds (unreal_hold.py)
        self._shared: Set[int] = set()  # owners that saw another reservation held (D10)

    @property
    def profile(self) -> CapacityProfile:
        return self._profile

    @property
    def total(self) -> int:
        return self._profile.total

    def _gpu_free_locked(self) -> bool:
        """True when a GPU slot is free; counts only held keys of cards in the profile (FR-CL-3).

        Never while a rebuild is pending (FR-CL-4); ``rebuild._lock`` is
        taken inside this lock, which the lock order allows.
        """
        profile = self._profile
        if profile.gpu_slot_count == 0 or rebuild.pending():
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
            return not self._gpu_free_locked() and not self._cpu_free_locked()

    def _cpu_free_locked(self) -> bool:
        """The CPU slot is free: it exists, nobody holds it and no Unreal job runs (D2)."""
        return (self._profile.cpu_slot_count > 0 and self._cpu_slot_job_id is None
                and not unreal_hold.cpu_taken_by_unreal_locked(self))

    def free_device_prefs(self) -> list:
        """Return device preferences this worker can currently accept (FR-9)."""
        with self._lock:
            gpu_free = self._gpu_free_locked()
            cpu_free = self._cpu_free_locked()
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
                unreal_hold.mark_shared_locked(self, job_id)
                # The copy is taken under the lock, from the profile the keys came from (C2).
                return SlotReservation(
                    job_id=job_id, device_used='GPU', card_keys=keys, cards=profile.cards,
                    gpu_mode=profile.gpu_mode, ignored=profile.ignored, problem=profile.problem,
                )
            if device_pref in ('CPU', 'ANY') and self._cpu_free_locked():
                self._cpu_slot_job_id = job_id
                unreal_hold.mark_shared_locked(self, job_id)
                return SlotReservation(job_id=job_id, device_used='CPU', problem=profile.problem)
        return None

    def reserve_unreal_card(self, job_id: int, key) -> Optional[SlotReservation]:
        """Hold one card for an Unreal job, by PCI address (FR-PC-CAP-1); see unreal_hold.py."""
        return unreal_hold.reserve_unreal_card(self, job_id, key)

    def free_keys(self, keys: Iterable[str]) -> Tuple[str, ...]:
        """The given keys whose address is readable and not held, in order; see unreal_hold.py."""
        return unreal_hold.free_keys(self, keys)

    def can_take_unreal(self, keys: Iterable[str]) -> bool:
        """A free key among ``keys``, no Blender CPU job, no pending rebuild (FR-PC-CAP-5); see unreal_hold.py."""
        return unreal_hold.can_take_unreal(self, keys)

    def has_unreal_job(self) -> bool:
        """True while any Unreal job holds a card (a retained one included); see unreal_hold.py."""
        return unreal_hold.has_unreal_job(self)

    def was_shared(self, job_id: int) -> bool:
        """D10: another reservation was held while job_id's was; see unreal_hold.py."""
        return unreal_hold.was_shared(self, job_id)

    def holds_snapshot(self) -> Dict[str, Tuple[int, str]]:
        """Held key -> (owner, 'BLENDER' | 'UNREAL') from one ``_lock`` snapshot; see unreal_hold.py."""
        return unreal_hold.holds_snapshot(self)

    def has_no_reservations(self) -> bool:
        """True when no slot and no card holds any reservation."""
        return unreal_hold.has_no_reservations(self)

    def retain_slots(self, job_id: int) -> None:
        """Keep job_id's slots and Unreal hold until restart, safe from a re-claim; see unreal_hold.py."""
        unreal_hold.retain_slots(self, job_id)

    def release_slot(self, job_id: int) -> None:
        """Release whatever slot job_id holds. Idempotent.

        Contract (FR-13): acquires _lock, iterates _gpu_assignments.items()
        to find and pop any entry whose value equals job_id, clears
        _cpu_slot_job_id if it equals job_id, and drops job_id from
        _unreal_jobs and _shared. Must NEVER raise. Silent
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
                unreal_hold.forget_locked(self, job_id)
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
        """The periodic card check (FR-CK-1, FR-CK-3); ``DETECTION_FAILED`` = try again later. See card_check.py."""
        from sethlans_worker_agent.capacity import card_check  # imports the cards package
        return card_check.check(self)

    def jobs_on_unusable_cards(self, states) -> list:
        """Job ids on cards not usable for their renderer (FR-CK-3); see card_stops.py."""
        from sethlans_worker_agent.capacity import card_stops
        return card_stops.jobs_on_unusable_cards(self, states)

    def try_rebuild(self, profile: CapacityProfile, generation: int) -> str:
        """Swap in ``profile`` unless a running job blocks it (FR-CL-4); see rebuild.py."""
        return rebuild.try_rebuild(self, profile, generation)
