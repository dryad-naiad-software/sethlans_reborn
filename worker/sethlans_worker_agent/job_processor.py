# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job processing: polling, claiming, execution, and resource management.

Lock ordering convention (alphabetical):
    _gpu_lock < _active_jobs_lock < _cpu_lock
No code path may acquire a later lock while holding an earlier one.
"""
import datetime
import logging
import os
import threading
from typing import Optional, Dict, Any

from sethlans_worker_agent import config, system_monitor, blender_executor, api_handler
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils.render_time_parser import parse_render_time

logger = logging.getLogger(__name__)

# A simple map of {gpu_device_index: job_id}
_gpu_assignment_map = {}
# A thread-safe lock to protect all access to _gpu_assignment_map.
_gpu_lock = threading.Lock()

# Active jobs dict: {job_id: {metadata}} for status endpoint visibility.
_active_jobs = {}
_active_jobs_lock = threading.Lock()

# A thread-safe lock to ensure only one CPU-bound job runs at a time.
_cpu_lock = threading.Lock()

# Pause event: when set, the main loop skips job polling.
# Heartbeats continue; in-flight jobs complete normally.
_pause_event = threading.Event()

# Recent completed jobs ring buffer (last 20).
_recent_jobs = []
_recent_jobs_lock = threading.Lock()


def _reserve_next_available_gpu(job_id: int) -> Optional[int]:
    """Atomically find and reserve the first available GPU for a job."""
    gpu_info = system_monitor.get_gpu_device_details()
    num_gpus = len(gpu_info)

    if num_gpus == 0:
        return None

    with _gpu_lock:
        busy_indices = set(_gpu_assignment_map.keys())
        for i in range(num_gpus):
            if i not in busy_indices:
                _gpu_assignment_map[i] = job_id
                return i

    return None


def poll_and_claim_job(worker_id: int) -> Optional[Dict[str, Any]]:
    """
    Polls the manager for an available job and attempts to claim it.

    Filters by hardware capabilities, prioritizes GPUs in split mode,
    and falls back to CPU when GPUs are busy. Returns claimed job data
    dict with resource assignments, or None.
    """
    detected_gpus = system_monitor.detect_gpu_devices()
    gpus_are_available = len(detected_gpus) > 0

    if config.FORCE_GPU_ONLY and not gpus_are_available:
        logger.info("FORCE_GPU_ONLY is enabled, but no GPUs were detected. Skipping job poll.")
        return None

    params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}
    if config.FORCE_GPU_ONLY:
        params['gpu_available'] = 'true'
    elif config.FORCE_CPU_ONLY:
        params['gpu_available'] = 'false'

    # Include installed Blender versions for version-aware filtering (P3-F1).
    installed = tool_manager_instance.scan_for_local_blenders()
    if installed:
        params['available_versions'] = ','.join(
            b['version'] for b in installed
        )

    available_jobs = api_handler.poll_for_available_jobs(params)
    if not available_jobs:
        return None

    job_to_claim = available_jobs[0]
    job_id = job_to_claim.get('id')
    job_name = job_to_claim.get('name', 'Unnamed Job')
    device_pref = job_to_claim.get('render_device')

    acquired_cpu_lock = False
    assigned_gpu_index = None

    if config.GPU_SPLIT_MODE and gpus_are_available:
        if device_pref in ('GPU', 'ANY'):
            assigned_gpu_index = _reserve_next_available_gpu(job_id)

        if assigned_gpu_index is not None:
            logger.info(f"Found available GPU slot {assigned_gpu_index} for job '{job_name}'.")
        else:
            if device_pref in ('CPU', 'ANY'):
                acquired_cpu_lock = _cpu_lock.acquire(blocking=False)
                if acquired_cpu_lock:
                    logger.info(f"All GPUs busy; claiming job '{job_name}' for CPU as fallback.")
                else:
                    logger.info("All GPUs and the CPU are busy. Skipping claim.")
                    return None
            else:
                logger.info("GPU split mode active, all GPUs busy. Skipping GPU-only job.")
                return None
    else:
        is_cpu_job = (device_pref == 'CPU') or (device_pref == 'ANY' and not gpus_are_available)
        if is_cpu_job:
            acquired_cpu_lock = _cpu_lock.acquire(blocking=False)
            if not acquired_cpu_lock:
                logger.info("CPU is busy. Skipping claim for CPU-bound job.")
                return None

    logger.info(f"Attempting to claim job '{job_name}' (ID: {job_id})...")
    if api_handler.claim_job(job_id, worker_id):
        logger.info(f"Successfully claimed job '{job_name}'!")
        config_snapshot = {
            'force_cpu': config.FORCE_CPU_ONLY,
            'force_gpu': config.FORCE_GPU_ONLY,
            'gpu_split_mode': config.GPU_SPLIT_MODE,
        }
        job_to_claim['assigned_gpu_index'] = assigned_gpu_index
        job_to_claim['_acquired_cpu_lock'] = acquired_cpu_lock
        job_to_claim['_config_snapshot'] = config_snapshot

        device_used = 'CPU'
        if assigned_gpu_index is not None:
            device_used = 'GPU'
        elif not acquired_cpu_lock and device_pref == 'GPU':
            device_used = 'GPU'

        with _active_jobs_lock:
            _active_jobs[job_id] = {
                'job_id': job_id,
                'name': job_name,
                'render_engine': job_to_claim.get('render_engine', ''),
                'render_device': device_pref,
                'gpu_index': assigned_gpu_index,
                'device_used': device_used,
                'start_time': datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
            }
        return job_to_claim
    else:
        # Claim failed (e.g., race condition). Release any acquired resources.
        if acquired_cpu_lock:
            _cpu_lock.release()
        if assigned_gpu_index is not None:
            with _gpu_lock:
                _gpu_assignment_map.pop(assigned_gpu_index, None)

    return None


def _finalize_and_upload(success, was_canceled, job_id, path):
    """Determine final status, upload output if successful, clean up."""
    if not success:
        return "CANCELED" if was_canceled else "ERROR"
    if path and api_handler.upload_render_output(job_id, path):
        try:
            os.remove(path)
            parent = os.path.dirname(path)
            if not os.listdir(parent):
                os.rmdir(parent)
        except OSError as e:
            logger.warning(f"Could not clean up render output: {e}")
    return "DONE"


def process_claimed_job(job_data: Dict[str, Any]):
    """
    Processes a claimed job: renders via Blender, uploads output, reports status.

    Handles the full lifecycle including resource cleanup in the finally block.
    GPU release is unconditional based on actual allocation, not current config.
    """
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    assigned_gpu_index = job_data.get('assigned_gpu_index')
    acquired_cpu_lock = job_data.get('_acquired_cpu_lock', False)

    if assigned_gpu_index is not None:
        with _gpu_lock:
            current = dict(_gpu_assignment_map)
        logger.info(f"Job {job_id} reserved GPU {assigned_gpu_index}. Current assignments: {current}")

    try:
        success, was_canceled, stdout, stderr, blender_error_msg, final_output_path = blender_executor.execute_blender_job(
            job_data, assigned_gpu_index=assigned_gpu_index)
    finally:
        if acquired_cpu_lock:
            _cpu_lock.release()

        # Release GPU unconditionally based on what was ACTUALLY ALLOCATED,
        # not current config state. This prevents GPU slot leaks when
        # GPU_SPLIT_MODE is toggled mid-job via control endpoints.
        if assigned_gpu_index is not None:
            with _gpu_lock:
                removed = _gpu_assignment_map.pop(assigned_gpu_index, None)
                if removed is not None:
                    logger.info(
                        f"Released GPU {assigned_gpu_index} from job {job_id}. "
                        f"Current assignments: {dict(_gpu_assignment_map)}"
                    )

        # Remove from active jobs tracking
        with _active_jobs_lock:
            _active_jobs.pop(job_id, None)

    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    job_update_payload = {"completed_at": completed_at, "last_output": stdout, "error_message": blender_error_msg}
    render_time = parse_render_time(stdout)
    if render_time is not None:
        job_update_payload["render_time_seconds"] = render_time

    job_update_payload["status"] = _finalize_and_upload(
        success, was_canceled, job_id, final_output_path
    )

    api_handler.update_job_status(job_id, job_update_payload)
    logger.info(f"Successfully reported final status '{job_update_payload['status']}' for job {job_id}.")

    with _recent_jobs_lock:
        _recent_jobs.append({
            'job_id': job_id,
            'name': job_name,
            'status': job_update_payload["status"],
            'render_time_seconds': render_time,
            'completed_at': completed_at,
        })
        # Keep only the last 20 entries
        if len(_recent_jobs) > 20:
            _recent_jobs[:] = _recent_jobs[-20:]


def get_and_claim_job(worker_id):
    """Poll, claim, and dispatch a job to a new thread. Returns the Thread or None."""
    job_data = poll_and_claim_job(worker_id)
    if job_data:
        job_id = job_data.get('id')
        logger.info(f"Dispatching job {job_id} to a new processing thread.")
        job_thread = threading.Thread(
            target=process_claimed_job, args=(job_data,),
            name=f"job-{job_id}"
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
    """Return a copy of the GPU assignment map under the lock."""
    with _gpu_lock:
        return dict(_gpu_assignment_map)


def get_recent_jobs_snapshot():
    """Return a copy of the recent completed jobs list under the lock."""
    with _recent_jobs_lock:
        return list(_recent_jobs)
