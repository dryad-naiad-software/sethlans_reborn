# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Whole-machine exclusive reservation (Unreal foundation FR-WX-10, D1).

Functions taking a ``WorkerCapacity`` and using its ``_lock``; they live
here so ``slots.py`` stays under the 300-line limit. ``WorkerCapacity``
exposes them as ``reserve_exclusive``, ``has_no_reservations`` and
``has_exclusive_reservation``.

An exclusive reservation marks, under ``_lock``, exactly the GPU keys
``_reserve_gpu_locked`` would assign in the current mode until none is
free (split: ``0..startup_gpu_count-1``; combined: the sentinel key 0),
plus the CPU slot when the profile has one. ``is_full()`` and
``free_device_prefs()`` therefore report full in every mode, and the
existing ``release_slot(job_id)`` frees everything.
"""
import logging
from typing import TYPE_CHECKING, List, Optional

from sethlans_worker_agent.capacity.profile import SlotReservation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sethlans_worker_agent.capacity.slots import WorkerCapacity

logger = logging.getLogger(__name__)


def _holds_any_locked(capacity: 'WorkerCapacity') -> bool:
    return bool(capacity._gpu_assignments) or capacity._cpu_slot_job_id is not None


def reserve_exclusive(
    capacity: 'WorkerCapacity', job_id: int,
) -> Optional[SlotReservation]:
    """Reserve every slot for ``job_id`` atomically, or return None.

    Succeeds only when no slot holds any reservation. Returns None when
    the profile has no GPU slot (Unreal needs a GPU), and in forced-index
    mode (``SETHLANS_FORCE_GPU_INDEX``): the host's other GPUs belong to
    sibling worker processes, so "whole worker" is not "whole machine"
    (Dom N-4, D10f; defense in depth for FR-WD-7). The reservation has
    ``device_used='GPU'`` and ``gpu_indices=[]``, so ``primary_gpu_index``
    is None: the marked keys live only in ``_gpu_assignments``.
    """
    profile = capacity._profile
    if profile.gpu_slot_count == 0 or profile.force_gpu_index is not None:
        return None
    with capacity._lock:
        if _holds_any_locked(capacity):
            return None
        marked: List[int] = []
        while capacity._reserve_gpu_locked(job_id) is not None:
            marked = [k for k, v in capacity._gpu_assignments.items() if v == job_id]
        if not marked:
            return None
        if profile.cpu_slot_count > 0:
            capacity._cpu_slot_job_id = job_id
        capacity._exclusive_job_id = job_id
    logger.info(
        "Reserved the whole machine for job %s (gpu keys=%s, cpu=%s).",
        job_id, sorted(marked), profile.cpu_slot_count > 0,
    )
    return SlotReservation(job_id=job_id, device_used='GPU', gpu_indices=[])


def has_no_reservations(capacity: 'WorkerCapacity') -> bool:
    """True when no slot holds any reservation (FR-WX-13 "fully idle")."""
    with capacity._lock:
        return not _holds_any_locked(capacity)


def has_exclusive_reservation(capacity: 'WorkerCapacity') -> bool:
    """True while an exclusive reservation still holds its slots.

    ``release_slot`` does not know about exclusivity; the recorded job id
    is treated as stale (and cleared) once that job holds no slot.
    """
    with capacity._lock:
        job_id = capacity._exclusive_job_id
        if job_id is None:
            return False
        held = (
            job_id in capacity._gpu_assignments.values()
            or capacity._cpu_slot_job_id == job_id
        )
        if not held:
            capacity._exclusive_job_id = None
        return held
