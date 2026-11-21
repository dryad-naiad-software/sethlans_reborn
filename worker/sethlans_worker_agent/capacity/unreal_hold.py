# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-card Unreal hold (render per card FR-PC-CAP-1, -2, -4; D1, D2, D4, D10).

Functions taking a ``WorkerCapacity`` and using its ``_lock``; they live
here so ``slots.py`` stays under the 300-line limit, and ``WorkerCapacity``
delegates to them in one line each.

- An Unreal job holds exactly one card key in ``_gpu_assignments`` and is
  recorded in ``_unreal_jobs`` (owner -> held key). It takes nothing else
  from Blender's slots: Blender split slots take the keys that are free,
  and a combined-mode Blender job, which needs every Blender key, never
  runs beside an Unreal job holding one of them.
- Held and free are decided by PCI address (``cards.identity.key_address``,
  integer tuples, never text). A key without a readable address is never
  free and is never held. The key the caller holds is the inventory key
  (the Unreal reserver matches the gate's key to it), so Blender's own
  text match of its profile keys sees the hold.
- D2: while any Unreal job holds a card (a retained one included) the CPU
  slot counts as taken; an Unreal card is not held while the CPU slot is
  held (a retained Blender CPU job included: its process may still run).
- D10: every successful reservation of any kind, when another holder
  exists, adds every current holder and the new one to ``_shared``. A job
  that starts and ends inside another's lifetime therefore still marks it.
- ``release_slot`` drops the owner from ``_unreal_jobs`` and ``_shared``;
  ``retain_slots`` re-keys every map to the retained owner key.

Lock order: ``WorkerCapacity._lock`` -> ``rebuild._lock`` (unchanged). The
callers run on the agent-loop thread (the claim) or any thread (queries).
"""
import logging
from typing import TYPE_CHECKING, Dict, Iterable, Optional, Set, Tuple

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.identity import key_address

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sethlans_worker_agent.capacity.slots import WorkerCapacity

logger = logging.getLogger(__name__)

BLENDER, UNREAL = 'BLENDER', 'UNREAL'


def retained_owner(job_id: int) -> int:
    """The owner key a retained slot is moved to: no job id can equal it."""
    return -abs(int(job_id)) - 1


def _holders_locked(capacity: 'WorkerCapacity') -> Set[int]:
    holders = set(capacity._gpu_assignments.values())
    if capacity._cpu_slot_job_id is not None:
        holders.add(capacity._cpu_slot_job_id)
    return holders


def mark_shared_locked(capacity: 'WorkerCapacity', job_id: int) -> None:
    """D10, caller holds ``_lock`` and has just recorded ``job_id``'s reservation."""
    others = _holders_locked(capacity) - {job_id}
    if others:
        capacity._shared.update(others)
        capacity._shared.add(job_id)


def cpu_taken_by_unreal_locked(capacity: 'WorkerCapacity') -> bool:
    """D2, caller holds ``_lock``: the CPU slot counts as taken while any Unreal job holds a card."""
    return bool(capacity._unreal_jobs)


def _held_addresses_locked(capacity: 'WorkerCapacity') -> Set[tuple]:
    return {address for address in map(key_address, capacity._gpu_assignments) if address is not None}


def _free_keys_locked(capacity: 'WorkerCapacity', keys: Iterable[str]) -> Tuple[str, ...]:
    held = _held_addresses_locked(capacity)
    return tuple(key for key in keys if (address := key_address(key)) is not None and address not in held)


def reserve_unreal_card(capacity: 'WorkerCapacity', job_id: int, key) -> Optional[SlotReservation]:
    """Hold ``key`` for Unreal job ``job_id``, or return None (FR-PC-CAP-1).

    None when a rebuild is pending, ``key`` has no readable address, a held
    key has the same address (whatever its spelling), the CPU slot is held
    (D2), or ``job_id`` already holds something.
    """
    address = key_address(key)
    with capacity._lock:
        if (rebuild.pending() or address is None or address in _held_addresses_locked(capacity)
                or capacity._cpu_slot_job_id is not None or job_id in _holders_locked(capacity)):
            return None
        capacity._gpu_assignments[key] = job_id
        capacity._unreal_jobs[job_id] = key
        mark_shared_locked(capacity, job_id)
        shared = job_id in capacity._shared
    logger.info("Unreal job %s holds graphics card %s (machine shared: %s).", job_id, key, shared)
    return SlotReservation(job_id=job_id, device_used='GPU', card_keys=(key,))


def free_keys(capacity: 'WorkerCapacity', keys: Iterable[str]) -> Tuple[str, ...]:
    """The given keys whose address is readable and not held, in the given order."""
    with capacity._lock:
        return _free_keys_locked(capacity, tuple(keys))


def can_take_unreal(capacity: 'WorkerCapacity', keys: Iterable[str]) -> bool:
    """No rebuild pending, a free key among ``keys`` and the CPU slot not held (FR-PC-CAP-1, -5)."""
    keys = tuple(keys)
    with capacity._lock:
        if rebuild.pending() or capacity._cpu_slot_job_id is not None:
            return False
        return bool(_free_keys_locked(capacity, keys))


def has_unreal_job(capacity: 'WorkerCapacity') -> bool:
    """True while any Unreal job (a retained one included) holds a card."""
    with capacity._lock:
        return bool(capacity._unreal_jobs)


def was_shared(capacity: 'WorkerCapacity', job_id: int) -> bool:
    """D10: another reservation was held on the machine at some moment while ``job_id``'s was."""
    with capacity._lock:
        return job_id in capacity._shared


def holds_snapshot(capacity: 'WorkerCapacity') -> Dict[str, Tuple[int, str]]:
    """Held card key -> ``(owner, 'BLENDER' | 'UNREAL')``, from one ``_lock`` snapshot.

    A retained owner is reported as its (negative) owner key.
    """
    with capacity._lock:
        return {key: (owner, UNREAL if owner in capacity._unreal_jobs else BLENDER)
                for key, owner in capacity._gpu_assignments.items()}


def has_no_reservations(capacity: 'WorkerCapacity') -> bool:
    """True when no slot and no card holds any reservation."""
    with capacity._lock:
        return not capacity._gpu_assignments and capacity._cpu_slot_job_id is None


def forget_locked(capacity: 'WorkerCapacity', job_id: int) -> None:
    """``release_slot``, caller holds ``_lock``: the owner leaves ``_unreal_jobs`` and ``_shared``."""
    capacity._unreal_jobs.pop(job_id, None)
    capacity._shared.discard(job_id)


def retain_slots(capacity: 'WorkerCapacity', job_id: int) -> None:
    """Move everything ``job_id`` holds to its retained owner key.

    A retained reservation (a process that would not die, FR-UE-11) keeps
    its card, its CPU slot and its Unreal hold until the worker restarts.
    Re-keyed, they can no longer be freed by ``release_slot(job_id)`` when
    the manager requeues the job and this worker claims it again: job ids
    are positive, the retained key is negative.
    """
    owner = retained_owner(job_id)
    with capacity._lock:
        for key, holder in list(capacity._gpu_assignments.items()):
            if holder == job_id:
                capacity._gpu_assignments[key] = owner
        if capacity._cpu_slot_job_id == job_id:
            capacity._cpu_slot_job_id = owner
        if job_id in capacity._unreal_jobs:
            capacity._unreal_jobs[owner] = capacity._unreal_jobs.pop(job_id)
        if job_id in capacity._shared:
            capacity._shared.discard(job_id)
            capacity._shared.add(owner)
    logger.warning("Job %s: its slots stay held until the worker restarts (owner %s).", job_id, owner)
