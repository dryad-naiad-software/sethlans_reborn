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
"""
import datetime
import logging
import threading
import time
from typing import Any, Dict, Optional

from sethlans_worker_agent import api_handler, config
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.job_lifecycle import process_claimed_job
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

_active_jobs: Dict[int, Dict[str, Any]] = {}
_active_jobs_lock = threading.Lock()
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
    """
    with _active_jobs_lock:
        active_ids = list(_active_jobs.keys())
    for job_id in active_ids:
        if _cancel_job_with_retry(job_id):
            logger.warning(
                "Drift handler: marked job %s for cancellation.", job_id,
            )


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
    return params


def poll_and_claim_job(worker_id: int) -> Optional[Dict[str, Any]]:
    """Poll the manager and attempt to claim an available job.

    FR-10 slot reservation order: reserve BEFORE api_handler.claim_job,
    release in a try/finally with an explicit claimed flag so Exception
    and BaseException both release the slot uniformly.
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

    job_to_claim = available_jobs[0]
    job_id = job_to_claim.get('id')
    job_name = job_to_claim.get('name', 'Unnamed Job')
    device_pref = job_to_claim.get('render_device')

    reservation = _capacity.reserve_for_job(job_id, device_pref)
    if reservation is None:
        logger.info(
            "No compatible slot for job '%s' (%s). Skipping claim.",
            job_name, device_pref,
        )
        return None

    logger.info(
        f"Attempting to claim job '{job_name}' (ID: {job_id}) "
        f"for device {reservation.device_used}."
    )
    claimed = False
    try:
        if api_handler.claim_job(job_id, worker_id):
            claimed = True
            logger.info(f"Successfully claimed job '{job_name}'!")
            job_to_claim['_reservation'] = reservation
            job_to_claim['assigned_gpu_index'] = reservation.primary_gpu_index
            with _active_jobs_lock:
                _active_jobs[job_id] = {
                    'job_id': job_id, 'name': job_name,
                    'render_engine': job_to_claim.get('render_engine', ''),
                    'render_device': device_pref,
                    'gpu_index': reservation.primary_gpu_index,
                    'device_used': reservation.device_used,
                    'start_time': datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                }
            return job_to_claim
        return None
    finally:
        if not claimed:
            _capacity.release_slot(job_id)


def get_and_claim_job(worker_id):
    """Poll, claim, and dispatch a job to a new thread. Returns Thread or None."""
    job_data = poll_and_claim_job(worker_id)
    if job_data:
        job_id = job_data.get('id')
        logger.info(f"Dispatching job {job_id} to a new processing thread.")
        job_thread = threading.Thread(
            target=process_claimed_job, args=(job_data,),
            name=f"job-{job_id}",
        )
        job_thread.start()
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
