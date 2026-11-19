# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Live slot rebuild (FR-CL-4, C1, C2).

``request_rebuild()`` (any thread, also before the capacity exists) counts a
generation and marks a rebuild pending. While one is pending no GPU slot is
free (``WorkerCapacity._gpu_free_locked``), so no GPU or Unreal job is
claimed; the CPU slot is unaffected.

``step(capacity)`` runs on the agent-loop thread on every
``job_processor.maybe_assert_gpu_list_unchanged`` call, before its rate
limit, and only while a rebuild is pending; while a whole-machine (Unreal)
reservation is held it does nothing, so no OS probe runs during an Unreal
render (FR-CK-1). It reads the generation, builds
the new profile (``card_check.rebuild_profile``: ``store.snapshot()``, a
fresh OS probe and the Blender list of the last check, with the
level-triggered alerts) and calls ``try_rebuild``. Under
``WorkerCapacity._lock``, ``try_rebuild`` leaves the rebuild pending when the
generation moved meanwhile, and waits while a held key that is not retained
belongs to a card absent from the new profile, while the GPU mode changes
and any such key is held, or while a whole-machine (Unreal) reservation is
held. Otherwise it swaps the profile and clears the mark: jobs on cards that
stay in use keep running, and keys retained until restart do not block.
The CPU slot holder is kept.

**Lock order:** ``_lock`` is the innermost lock of the worker. Code holding
``WorkerCapacity._lock`` may take it; code holding it takes no other lock.
``_last_wait`` is read and written by the agent-loop thread only.
"""
import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SWAPPED, MOVED, WAITING = 'swapped', 'moved', 'waiting'

_lock = threading.Lock()
_generation = 0
_pending = False
_waiting = False   # the last attempt was blocked by a running job
_last_wait: Optional[Tuple[int, dict]] = None  # (generation, assignments) of the last WAITING attempt


def request_rebuild() -> None:
    """Ask for slots built afresh from the saved choice and the cards (any thread; takes only ``_lock``)."""
    global _generation, _pending
    with _lock:
        _generation += 1
        _pending = True


def state() -> Tuple[int, bool]:
    """``(generation, pending)``."""
    with _lock:
        return _generation, _pending


def generation() -> int:
    with _lock:
        return _generation


def pending() -> bool:
    with _lock:
        return _pending


def rebuild_waiting() -> bool:
    """True while a rebuild is pending and waits for running GPU jobs (FR-CW-3, ``rebuild_waiting``)."""
    with _lock:
        return _pending and _waiting


def mark_built(built_from: int) -> None:
    """The capacity was built from generation ``built_from`` (``init_capacity``): clear the mark unless it moved."""
    global _pending, _waiting
    with _lock:
        if _generation == built_from:
            _pending = _waiting = False


def _blocked_locked(capacity, profile) -> bool:
    """Caller holds ``capacity._lock``: a running job keeps the current profile (FR-CL-4)."""
    held = {key for key, owner in capacity._gpu_assignments.items() if owner >= 0}
    if not held:
        return False
    if profile.gpu_mode != capacity._profile.gpu_mode or held - set(profile.card_keys):
        return True
    exclusive = capacity._exclusive_job_id  # may be stale; held only while it still owns a key
    return exclusive is not None and exclusive >= 0 and exclusive in capacity._gpu_assignments.values()


def try_rebuild(capacity, profile, built_from: int) -> str:
    """Swap in ``profile`` if nothing blocks it; ``SWAPPED``, ``MOVED`` (stays pending) or ``WAITING``."""
    global _pending, _waiting
    with capacity._lock:
        with _lock:
            if _generation != built_from:
                return MOVED
        blocked = _blocked_locked(capacity, profile)
        with _lock:
            if _generation != built_from:
                return MOVED
            if blocked:
                _waiting = True
                return WAITING
            capacity._profile = profile
            _pending = _waiting = False
    return SWAPPED


def step(capacity) -> Optional[str]:
    """One rebuild attempt while a rebuild is pending (agent-loop thread); the outcome or None."""
    global _last_wait
    global _waiting
    built_from, is_pending = state()
    if not is_pending:
        return None
    if capacity.has_exclusive_reservation():  # no OS probe during an Unreal render (FR-CK-1, FR-WX-14)
        with _lock:
            _waiting = True
        return WAITING
    held = capacity.gpu_assignments_snapshot()
    if _last_wait == (built_from, held):
        return WAITING  # nothing changed since the last blocked attempt: no new probe
    from sethlans_worker_agent.capacity import card_check
    try:
        profile = card_check.rebuild_profile(capacity)
    except Exception:  # noqa: BLE001 - the loop goes on; the rebuild stays pending
        logger.exception("The slots could not be rebuilt yet; trying again.")
        return None
    if profile is None:
        return None  # the saved choice or the OS list could not be read: stays pending
    outcome = capacity.try_rebuild(profile, built_from)
    _last_wait = (built_from, held) if outcome == WAITING else None
    if outcome == SWAPPED:
        from sethlans_worker_agent.capacity import card_profile
        card_profile.log_profile(profile, rebuilt=True)
    elif outcome == WAITING:
        logger.info("Waiting for running GPU jobs to finish before using the new card list.")
    return outcome


def reset_for_tests() -> None:
    """Forget the generation, the mark and the last wait. Test-only hook."""
    global _generation, _pending, _waiting, _last_wait
    with _lock:
        _generation, _pending, _waiting = 0, False, False
    _last_wait = None
