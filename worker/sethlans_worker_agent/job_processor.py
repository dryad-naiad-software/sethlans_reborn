# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job processing: polling, claiming, execution, resource release.

All slot reservation state lives in the WorkerCapacity instance owned
by this module (constructed via init_capacity() at startup). The
legacy _cpu_lock, _gpu_lock, _gpu_assignment_map, and
_reserve_next_available_gpu primitives have been removed.

Lock ordering (enforced by convention + unit tests):
    WorkerCapacity._lock < _active_jobs_lock < _recent_jobs_lock
No code path may acquire a later lock while holding an earlier one.
hardware_detection._gpu_details_lock is taken with no worker lock held
and nests only tool_manager._download_lock (a leaf).
The GPU re-check request (capacity.recheck, a threading.Event) is a leaf:
its internal lock is never held while any of the above is taken.

_active_jobs is for UI/status reporting ONLY. It MUST NOT back the
polling gate (FR-6); use capacity_is_full() -> WorkerCapacity.is_full().

Job choice and claiming live in job_selection / job_claim (Unreal
foundation FR-WX-6/7); Unreal code is reached only via unreal_bridge.
"""
import logging
import threading
import time
from typing import Any, Dict, Optional, Set

from sethlans_worker_agent import api_handler, config
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent import job_claim, job_resources, job_selection
from sethlans_worker_agent import system_monitor, unreal_bridge
from sethlans_worker_agent.capacity import card_check, card_profile, rebuild
from sethlans_worker_agent.cards import startup_log as cards_startup_log
from sethlans_worker_agent.cards import store as cards_store
from sethlans_worker_agent.job_lifecycle import process_claimed_job
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

_active_jobs: Dict[int, Dict[str, Any]] = {}
_active_jobs_lock = threading.Lock()
# Claimed jobs not yet fully finished (report/upload/release done), for the
# heartbeat's active_job_ids (FR-RT-17). Guarded by _active_jobs_lock; the
# poll thread is the only adder. Read via get_unfinished_job_ids().
_unfinished_job_ids: Set[int] = set()
_pause_event = threading.Event()
_recent_jobs = []
_recent_jobs_lock = threading.Lock()

_capacity: Optional[capacity_module.WorkerCapacity] = None
# Drift check runs once per heartbeat interval (FR-23), not once per poll;
# a failed GPU device check brings it forward (#239, capacity/recheck.py).
_last_drift_check_ts: float = 0.0


def init_capacity() -> capacity_module.WorkerCapacity:
    """Build the module-global WorkerCapacity from the saved card choice (FR-CL-2, FR-CK-1).

    The Blender detection fills its cache first; the start check then
    finds the ticked cards in a fresh OS probe joined with that list and
    raises the alerts for those it cannot use (capacity/card_check.py).
    With method ``cpu`` no card is probed or used. The generation read
    before the section is recorded, so a rebuild requested meanwhile stays
    pending (FR-CL-4).
    """
    global _capacity
    system_monitor.get_gpu_device_details()
    cards_startup_log.log_once_at_start()  # log the OS/Blender card list once
    generation = rebuild.generation()
    section = cards_store.snapshot()
    profile = card_profile.profile_for(section, card_check.start_check(section))
    card_profile.log_profile(profile)
    _capacity = capacity_module.WorkerCapacity(profile)
    rebuild.mark_built(generation)
    return _capacity


def get_worker_capacity() -> Optional[capacity_module.WorkerCapacity]:
    """Return the module-global WorkerCapacity instance, or None."""
    return _capacity


def capacity_is_full() -> bool:
    """Polling gate source of truth (FR-6). Delegates to WorkerCapacity.

    A fully idle machine is never full: with no Blender slot at all it may
    still take an Unreal job (C-27 b); ``poll_and_claim_job`` decides.
    """
    if _capacity is None:
        return False
    return _capacity.is_full() and not _capacity.has_no_reservations()


def maybe_assert_gpu_list_unchanged() -> None:
    """A pending slot rebuild on every call; the card check once per heartbeat interval (FR-23, FR-CK-1).

    The rebuild step (capacity/rebuild.py, FR-CL-4) runs before the rate
    limit and does nothing unless a rebuild is pending; its OS probe never
    starts Blender. A failed GPU device check brings the card check forward,
    with a floor between early runs (capacity/recheck.py). A pending request
    is looked at only when the check may run, so the Unreal gate defers it.

    The timestamp is updated AFTER a successful check. If it raises, the
    timestamp stays at its previous value so the next loop iteration
    retries immediately rather than waiting a full interval, and the early
    request is put back.
    """
    global _last_drift_check_ts
    if _capacity is None:
        return
    rebuild.step(_capacity)
    # FR-WX-14: Blender never enumerates GPUs while an Unreal render owns the machine; the OS list is
    # still read once per interval, so a lost card stops that render and raises its alert (C-24 m).
    if not unreal_bridge.drift_check_allowed():
        card_check.check_during_unreal(_capacity, time.monotonic())
        return
    now = time.monotonic()
    due = (now - _last_drift_check_ts) >= max(1, config.HEARTBEAT_INTERVAL_SECONDS)
    if not capacity_module.take_gpu_list_check_turn(now, due):
        return
    capacity_module.run_gpu_list_check(_capacity)
    _last_drift_check_ts = now


def _build_poll_params(free_prefs, unreal_params=None):
    params: Dict[str, str] = {
        'status': 'QUEUED', 'assigned_worker__isnull': 'true',
    }
    if free_prefs:
        params['device_prefs'] = ','.join(free_prefs)
    installed = tool_manager_instance.scan_for_local_blenders()
    if installed:
        params['available_versions'] = ','.join(
            b['version'] for b in installed
        )
    if unreal_params is None:  # Unreal only on a fully idle machine (FR-WX-13); {} when disabled.
        unreal_params = _unreal_poll_params()
    params.update(unreal_params)
    return params


def _unreal_poll_params() -> Dict[str, str]:
    # A pending rebuild refuses the exclusive reservation: asking for Unreal jobs then would only run try_reserve.
    node_idle = _capacity is not None and _capacity.has_no_reservations() and not rebuild.pending()
    return unreal_bridge.poll_params(node_idle=node_idle)


def _device_prefs(free_prefs, unreal_params) -> list:
    """C-27 b: GPU jobs are asked for when Unreal may take one, even without a free Blender GPU slot.

    Unreal jobs are GPU jobs; a Blender GPU job polled this way is skipped
    unless a Blender GPU slot is free (``job_selection``).
    """
    if unreal_params and 'GPU' not in free_prefs:
        return list(free_prefs) + ['GPU']
    return list(free_prefs)


def poll_and_claim_job(worker_id: int) -> Optional[Dict[str, Any]]:
    """Poll the manager and attempt to claim an available job.

    FR-10 slot reservation order: reserve BEFORE api_handler.claim_job,
    release in a try/finally with an explicit claimed flag so Exception
    and BaseException both release the slot uniformly (job_claim).
    The first eligible job in response order is claimed (job_selection).
    """
    if _capacity is None:
        logger.error("poll_and_claim_job called before init_capacity().")
        return None

    unreal_params = _unreal_poll_params()
    prefs = _device_prefs(_capacity.free_device_prefs(), unreal_params)
    if not prefs:
        return None

    available_jobs = api_handler.poll_for_available_jobs(
        _build_poll_params(prefs, unreal_params)
    )
    if not available_jobs:
        return None

    selection = job_selection.select_claimable_job(available_jobs, _capacity)
    if selection is None:
        return None
    return job_claim.claim_selected(selection, _capacity, worker_id)


def get_and_claim_job(worker_id):
    """Poll, claim, and dispatch a job to a new thread. Returns Thread or None."""
    job_data = poll_and_claim_job(worker_id)
    if job_data:
        job_id = job_data.get('id')
        logger.info(f"Dispatching job {job_id} to a new processing thread.")
        try:
            job_thread = threading.Thread(
                target=process_claimed_job, args=(job_data,),
                name=f"job-{job_id}",
            )
            job_thread.start()
        except Exception:
            logger.exception("Could not start the thread for job %s.", job_id)
            job_resources.release_after_dispatch_failure(job_data, _capacity)
            return None
        return job_thread
    return None


def pause():
    """Pause job polling. In-flight jobs continue; heartbeats continue."""
    _pause_event.set()
    logger.info("Worker paused. Job polling suspended.")


def resume():
    """Resume job polling after a pause."""
    _pause_event.clear()
    logger.info("Worker resumed. Job polling active.")


def is_paused():
    """Return True if the worker is currently paused."""
    return _pause_event.is_set()


def get_unfinished_job_ids():
    """Sorted ids of claimed jobs not yet fully finished (FR-RT-17).

    Read under ``_active_jobs_lock``. Feeds the heartbeat's
    ``active_job_ids`` and ``job_selection``'s same-id guard.
    """
    with _active_jobs_lock:
        return sorted(_unfinished_job_ids)


def get_active_jobs_snapshot():
    """Return a copy of the active jobs dict under the lock."""
    with _active_jobs_lock:
        return dict(_active_jobs)


def get_gpu_assignment_snapshot():
    """Thin pass-through to WorkerCapacity.gpu_assignments_snapshot().

    The worker web UI status endpoint imports this accessor and expects
    the same shape as the legacy module-level map.
    """
    if _capacity is None:
        return {}
    return _capacity.gpu_assignments_snapshot()


def get_recent_jobs_snapshot():
    """Return a copy of the recent completed jobs list under the lock."""
    with _recent_jobs_lock:
        return list(_recent_jobs)
