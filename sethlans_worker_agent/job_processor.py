#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/job_processor.py
"""
The core module for the worker agent responsible for handling all render job tasks.

This module orchestrates the job processing workflow by coordinating between the
API handler (for manager communication) and the Blender executor (for running
render processes). It supports concurrent job execution by dispatching each
claimed job to a separate thread.
"""

import datetime
import logging
import math
import os
import re
import threading
from typing import Optional, Dict, Any

from sethlans_worker_agent import config, system_monitor, blender_executor, api_handler

logger = logging.getLogger(__name__)

# A simple map of {gpu_device_index: job_id}
_gpu_assignment_map = {}


def _get_next_available_gpu() -> Optional[int]:
    """
    Finds the index of the first available GPU that is not currently assigned a job.

    This function is used when GPU split mode is active to determine which GPU
    a new job should be assigned to. It checks the number of detected GPUs against
    the internal assignment map.

    Returns:
        An integer representing the device index of a free GPU, or None if all
        GPUs are currently busy.
    """
    # Use the detailed GPU info to get an accurate count of physical devices.
    gpu_info = system_monitor.get_gpu_device_details()
    num_gpus = len(gpu_info)

    if num_gpus == 0:
        return None

    busy_indices = set(_gpu_assignment_map.keys())
    for i in range(num_gpus):
        if i not in busy_indices:
            return i

    return None


def _parse_render_time(stdout_text):
    """
    Parses Blender's stdout log content to find the total render time by
    finding the unique final summary line containing "(Saving:)".

    Args:
        stdout_text (str): The full stdout log from the Blender subprocess.

    Returns:
        int or None: The total render time in seconds, rounded up to the nearest
                     whole number, or None if the time could not be parsed.
    """
    time_line_regex = re.compile(r"Time: (?:(\d{2}):)?(\d{2}):(\d{2}\.\d{2})")

    for line in stdout_text.splitlines():
        if "(Saving:" in line:
            match = time_line_regex.search(line)
            if match:
                try:
                    hours_str, minutes_str, seconds_str = match.groups()
                    hours = int(hours_str) if hours_str else 0
                    minutes = int(minutes_str)
                    seconds = float(seconds_str)
                    total_seconds = int(math.ceil((hours * 3600) + (minutes * 60) + seconds))
                    logger.info(f"Parsed render time: {total_seconds} seconds from line: '{line.strip()}'")
                    return total_seconds
                except (IndexError, ValueError) as e:
                    logger.warning(f"Found summary line but failed to parse time: '{line.strip()}' - {e}")
                    return None
    logger.warning("Could not find the final 'Time: ... (Saving: ...)' summary line in the render output.")
    return None


def poll_and_claim_job(worker_id: int) -> Optional[Dict[str, Any]]:
    """
    Polls the manager for an available job and attempts to claim it.

    This function sends a request to the manager's job list endpoint, applying
    filters based on the worker's configured hardware capabilities. If a job is
    available, it attempts to claim it by updating the `assigned_worker` field.

    In GPU split mode, it will first check for an available GPU slot before
    attempting to claim a job.

    Args:
        worker_id (int): The unique ID of the worker, as assigned by the manager.

    Returns:
        A dictionary containing the job data if a job was successfully claimed,
        otherwise None.
    """
    detected_gpus = system_monitor.detect_gpu_devices()
    gpu_available = len(detected_gpus) > 0

    if config.FORCE_GPU_ONLY and not gpu_available:
        logger.info("FORCE_GPU_ONLY is enabled, but no GPUs were detected. Skipping job poll.")
        return None

    params = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

    # Correctly set polling parameters based on forced hardware modes.
    if config.FORCE_GPU_ONLY:
        params['gpu_available'] = 'true'
    elif config.FORCE_CPU_ONLY:
        params['gpu_available'] = 'false'

    available_jobs = api_handler.poll_for_available_jobs(params)
    if not available_jobs:
        return None

    job_to_claim = available_jobs[0]
    assigned_gpu_index = None
    job_id = job_to_claim.get('id')
    job_name = job_to_claim.get('name', 'Unnamed Job')

    is_splittable_gpu_job = job_to_claim.get('render_device') in ('GPU', 'ANY')
    if config.GPU_SPLIT_MODE and is_splittable_gpu_job:
        assigned_gpu_index = _get_next_available_gpu()
        if assigned_gpu_index is None:
            logger.info("GPU split mode is active, but all GPUs are busy. Skipping claim.")
            return None

    logger.info(f"Found {len(available_jobs)} available job(s). Attempting to claim job '{job_name}' (ID: {job_id})...")
    if api_handler.claim_job(job_id, worker_id):
        logger.info(f"Successfully claimed job '{job_name}'!")
        job_to_claim['assigned_gpu_index'] = assigned_gpu_index
        return job_to_claim

    return None


def process_claimed_job(job_data: Dict[str, Any]):
    """
    Processes a job that has already been claimed by this worker.

    This function handles the entire execution lifecycle for a claimed job:
    1. Updates the job status to 'RENDERING'.
    2. Executes the Blender render subprocess.
    3. Parses the result and determines the final status ('DONE', 'ERROR', etc.).
    4. Uploads the render output if successful.
    5. Reports the final status and metadata back to the manager.

    Args:
        job_data (dict): The dictionary of job data returned from a successful claim.
    """
    job_id = job_data.get('id')
    assigned_gpu_index = job_data.get('assigned_gpu_index')

    api_handler.update_job_status(job_id, {"status": "RENDERING"})

    if config.GPU_SPLIT_MODE and assigned_gpu_index is not None:
        _gpu_assignment_map[assigned_gpu_index] = job_id
        logger.info(f"Assigned job {job_id} to GPU {assigned_gpu_index}. Current assignments: {_gpu_assignment_map}")

    try:
        success, was_canceled, stdout, stderr, blender_error_msg, final_output_path = blender_executor.execute_blender_job(
            job_data, assigned_gpu_index=assigned_gpu_index)
    finally:
        if config.GPU_SPLIT_MODE and assigned_gpu_index is not None:
            if _gpu_assignment_map.pop(assigned_gpu_index, None) is not None:
                logger.info(f"Released GPU {assigned_gpu_index} from job {job_id}. Current assignments: {_gpu_assignment_map}")

    job_update_payload = {
        "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
        "last_output": stdout, "error_message": blender_error_msg}
    render_time = _parse_render_time(stdout)
    if render_time is not None:
        job_update_payload["render_time_seconds"] = render_time

    if success:
        job_update_payload["status"] = "DONE"
        if final_output_path and api_handler.upload_render_output(job_id, final_output_path):
            try:
                logger.info(f"Cleaning up local render output: {final_output_path}")
                os.remove(final_output_path)
                output_dir = os.path.dirname(final_output_path)
                if not os.listdir(output_dir):
                    os.rmdir(output_dir)
            except OSError as e:
                logger.warning(f"Could not clean up temporary render file or directory: {e}")
    elif was_canceled:
        job_update_payload["status"] = "CANCELED"
    else:
        job_update_payload["status"] = "ERROR"

    api_handler.update_job_status(job_id, job_update_payload)
    logger.info(f"Successfully reported final status '{job_update_payload['status']}' for job {job_id}.")


def get_and_claim_job(worker_id):
    """
    Polls for, claims, and processes one available job by dispatching it to a new thread.

    This function orchestrates the main workflow for a worker's
    operational loop. It first calls `poll_and_claim_job` to acquire a job.
    If a job is successfully claimed, it is handed off to the `process_claimed_job`
    function, which is executed in a separate, non-blocking thread. This allows
    the main worker loop to remain responsive and poll for additional jobs.

    Args:
        worker_id (int): The unique ID of the worker, as assigned by the manager.
    """
    job_data = poll_and_claim_job(worker_id)
    if job_data:
        job_thread = threading.Thread(target=process_claimed_job, args=(job_data,))
        job_thread.start()