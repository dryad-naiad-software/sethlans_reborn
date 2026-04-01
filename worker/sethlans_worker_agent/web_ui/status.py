# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Status snapshot assembly for the worker web UI.

Reads from job_processor, system_monitor, blender_executor, and config
to build a JSON-serializable status dict. Each lock is acquired and
released independently (never nested) following the lock ordering
convention documented in job_processor.py:
    _gpu_lock < _active_jobs_lock < _cpu_lock < _output_lock
"""

import logging

from sethlans_worker_agent import config, job_processor, blender_executor
from sethlans_worker_agent.hardware_detection import (
    HOSTNAME, IP_ADDRESS, OS_INFO,
    get_gpu_device_details, get_cpu_thread_count,
)
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)


def get_status_snapshot():
    """
    Build a JSON-serializable snapshot of the worker's current state.

    Acquires each lock independently to avoid deadlocks. No nested locking.

    Returns:
        dict: Status data suitable for JSON serialization.
    """
    # --- Read WORKER_ID ---
    # Access WORKER_ID via module attribute at call time, not import time,
    # so we see the current value after register_with_manager() updates it.
    # Safe on CPython: the GIL makes simple attribute reads of immutable
    # types atomic.
    worker_id = system_monitor.WORKER_ID

    # --- GPU allocation (acquires _gpu_lock, then releases) ---
    gpu_allocation_raw = job_processor.get_gpu_assignment_snapshot()
    # Convert int keys to strings for JSON serialization
    gpu_allocation = {str(k): v for k, v in gpu_allocation_raw.items()}

    # --- Active jobs (acquires _active_jobs_lock, then releases) ---
    active_jobs_raw = job_processor.get_active_jobs_snapshot()

    # --- Recent jobs (acquires _recent_jobs_lock, then releases) ---
    recent_jobs = job_processor.get_recent_jobs_snapshot()

    # --- Enrich active jobs with last Blender output line ---
    active_jobs = []
    for job_id, info in active_jobs_raw.items():
        entry = dict(info)
        entry['last_output_line'] = blender_executor.get_last_output_line(
            job_id
        )
        active_jobs.append(entry)

    # --- Hardware info (cached, no lock needed) ---
    gpu_details = get_gpu_device_details()
    gpu_list = []
    for gpu in gpu_details:
        gpu_list.append({
            'name': gpu.get('name', 'Unknown'),
            'type': gpu.get('type', 'Unknown'),
            # VRAM is best-effort; return null when unavailable
            'vram': gpu.get('vram', None),
        })

    cpu_threads = get_cpu_thread_count()

    # --- Installed Blender versions (no sensitive paths) ---
    installed_blenders = tool_manager_instance.scan_for_local_blenders()
    tool_versions = [v['version'] for v in installed_blenders]

    # --- Registration and pause status ---
    registration_status = 'registered' if worker_id else 'pending'
    paused = job_processor.is_paused()

    return {
        'worker': {
            'hostname': HOSTNAME,
            'ip_address': IP_ADDRESS,
            'os': OS_INFO,
            'worker_id': worker_id,
            'registration_status': registration_status,
            'manager_url': config.MANAGER_API_URL,
            'paused': paused,
        },
        'hardware': {
            'gpus': gpu_list,
            'cpu_threads': cpu_threads,
        },
        'config': {
            'polling_interval': config.JOB_POLLING_INTERVAL_SECONDS,
            'heartbeat_interval': config.HEARTBEAT_INTERVAL_SECONDS,
            'force_cpu': config.FORCE_CPU_ONLY,
            'force_gpu': config.FORCE_GPU_ONLY,
            'gpu_split_mode': config.GPU_SPLIT_MODE,
            'blender_versions': tool_versions,
        },
        'active_jobs': active_jobs,
        'gpu_allocation': gpu_allocation,
        'recent_jobs': recent_jobs,
        'tools': tool_versions,
    }
