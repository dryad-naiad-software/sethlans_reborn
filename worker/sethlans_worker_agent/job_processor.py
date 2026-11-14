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
from sethlans_worker_agent import executors, job_claim, job_resources, job_selection
from sethlans_worker_agent import system_monitor, unreal_bridge
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
# Drift check runs once per heartbeat interval (FR-23), not once per poll.
_last_drift_check_ts: float = 0.0


def init_capacity() -> capacity_module.WorkerCapacity:
    """Build the module-global WorkerCapacity from current hardware."""
    global _capacity
    gpus = system_monitor.get_gpu_device_details()
    cpu_cores = system_monitor.get_cpu_thread_count()
    force_gpu_index: Optional[int] = None
    if config.FORCE_GPU_INDEX is not None:
        try:
            force_gpu_index = int(config.FORCE_GPU_INDEX)
        except (ValueError, TypeError):
            logger.error(
                "Invalid SETHLANS_FORCE_GPU_INDEX '%s'; ignoring.",
                config.FORCE_GPU_INDEX,
            )

    profile = capacity_module.compute_capacity_profile(
        detected_gpu_count=len(gpus),
        cpu_cores=cpu_cores,
        force_cpu_only=config.FORCE_CPU_ONLY,
        force_gpu_only=config.FORCE_GPU_ONLY,
        force_gpu_index=force_gpu_index,
        gpu_mode=config.GPU_MODE,
        cpu_threads_config=config.CPU_THREADS,
    )
    capacity_module.log_capacity_profile(
        profile, len(gpus), config.FORCE_CPU_ONLY, config.FORCE_GPU_ONLY,
    )
    _capacity = capacity_module.WorkerCapacity(profile)
    return _capacity


def get_worker_capacity() -> Optional[capacity_module.WorkerCapacity]:
    """Return the module-global WorkerCapacity instance, or None."""
    return _capacity


def capacity_is_full() -> bool:
    """Polling gate source of truth (FR-6). Delegates to WorkerCapacity."""
    if _capacity is None:
        return False
    return _capacity.is_full()


def maybe_assert_gpu_count_unchanged() -> None:
    """Run the GPU drift check at most once per heartbeat interval (FR-23).

    The timestamp is updated AFTER a successful assert return. If the
    assert raises, the timestamp stays at its previous value so the
    next loop iteration retries immediately rather than waiting a full
    interval.
    """
    global _last_drift_check_ts
    if _capacity is None:
        return
    # FR-WX-14: never enumerate GPUs while an Unreal render owns the machine.
    if not unreal_bridge.drift_check_allowed():
        return
    now = time.monotonic()
    interval = max(1, config.HEARTBEAT_INTERVAL_SECONDS)
    if (now - _last_drift_check_ts) < interval:
        return
    _capacity.assert_gpu_count_unchanged()
    _last_drift_check_ts = now


# Drift-cancel retry budget: 3 attempts with 1s, 2s, 4s backoff.
_DRIFT_CANCEL_RETRIES = 3
_DRIFT_CANCEL_BACKOFF_BASE = 1.0


def _cancel_job_with_retry(job_id: int) -> bool:
    """Retry update_job_status(CANCELED) with bounded backoff.

    Returns True on success, False if all attempts failed.
    """
    for attempt in range(1, _DRIFT_CANCEL_RETRIES + 1):
        try:
            api_handler.update_job_status(job_id, {"status": "CANCELED"})
            return True
        except BaseException as exc:  # noqa: BLE001 - drift path must not raise
            logger.warning(
                "Drift handler: cancel attempt %d/%d for job %s failed: %s",
                attempt, _DRIFT_CANCEL_RETRIES, job_id, exc,
            )
            if attempt < _DRIFT_CANCEL_RETRIES:
                time.sleep(_DRIFT_CANCEL_BACKOFF_BASE * (2 ** (attempt - 1)))
    logger.error(
        "Drift handler: giving up on cancel for job %s after %d attempts.",
        job_id, _DRIFT_CANCEL_RETRIES,
    )
    return False


def terminate_all_active_jobs_for_drift() -> None:
    """Mark every in-flight Blender subprocess for cancellation.

    Called by the drift handler. Flips each active job's status to
    CANCELED on the manager; the job's own polling loop inside
    blender_executor.execute_blender_job observes and terminates.
    Uses a bounded retry with exponential backoff to tolerate transient
    manager unavailability during shutdown.

    Jobs whose renderer supports release (Unreal) are not PATCHed; the
    shutdown event releases them to the manager instead (FR-WX-14).
    """
    with _active_jobs_lock:
        active = [(job_id, entry.get('renderer')) for job_id, entry in _active_jobs.items()]
    releasable = [job_id for job_id, renderer in active if executors.supports_release(renderer)]
    for job_id, _renderer in active:
        if job_id not in releasable and _cancel_job_with_retry(job_id):
            logger.warning(
                "Drift handler: marked job %s for cancellation.", job_id,
            )
    if releasable:
        job_resources.request_shutdown_for_release(releasable)


def _build_poll_params(free_prefs):
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
    # Unreal only on a fully idle machine (FR-WX-13); {} when disabled.
    node_idle = _capacity is not None and _capacity.has_no_reservations()
    params.update(unreal_bridge.poll_params(node_idle=node_idle))
    return params


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

    free_prefs = _capacity.free_device_prefs()
    if not free_prefs:
        return None

    available_jobs = api_handler.poll_for_available_jobs(
        _build_poll_params(free_prefs)
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
