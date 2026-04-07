# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job lifecycle helpers invoked after a claim succeeds.

Split out of job_processor to keep that module under the 300-line
ceiling. Contains the render-upload-report flow and the small
_finalize_and_upload utility it depends on.
"""
import datetime
import logging
import os
from typing import Any, Dict

from sethlans_worker_agent import api_handler, blender_executor
from sethlans_worker_agent.utils.render_time_parser import parse_render_time

logger = logging.getLogger(__name__)


def _finalize_and_upload(success, was_canceled, job_id, path,
                         thumbnail_path=None):
    """Decide final status, upload output, clean up temp files."""
    if not success:
        return "CANCELED" if was_canceled else "ERROR"
    if path and api_handler.upload_render_output(
        job_id, path, thumbnail_path=thumbnail_path
    ):
        for file_path in (path, thumbnail_path):
            if not file_path:
                continue
            try:
                os.remove(file_path)
                parent = os.path.dirname(file_path)
                if not os.listdir(parent):
                    os.rmdir(parent)
            except OSError as e:
                logger.warning(f"Could not clean up output file: {e}")
    return "DONE"


def process_claimed_job(job_data: Dict[str, Any]):
    """Render, upload, report status, and release the capacity slot.

    Imports job_processor lazily to avoid a circular import; we need
    access to its module-level state (_capacity, _active_jobs_lock,
    _active_jobs, _recent_jobs, _recent_jobs_lock) which is owned
    there for backward compatibility with the web UI snapshots.
    """
    from sethlans_worker_agent import job_processor

    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    reservation = job_data.get('_reservation')
    assigned_gpu_index = (
        reservation.primary_gpu_index if reservation is not None
        else job_data.get('assigned_gpu_index')
    )

    capacity = job_processor.get_worker_capacity()
    if assigned_gpu_index is not None and capacity is not None:
        logger.info(
            f"Job {job_id} reserved GPU {assigned_gpu_index}. "
            f"Current assignments: {capacity.gpu_assignments_snapshot()}"
        )

    try:
        (success, was_canceled, stdout, stderr, blender_error_msg,
         final_output_path, thumbnail_path) = blender_executor.execute_blender_job(
            job_data, assigned_gpu_index=assigned_gpu_index)
    finally:
        if capacity is not None:
            capacity.release_slot(job_id)
        with job_processor._active_jobs_lock:
            job_processor._active_jobs.pop(job_id, None)

    completed_at = datetime.datetime.now(
        datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    payload = {
        "completed_at": completed_at, "last_output": stdout,
        "error_message": blender_error_msg,
    }
    render_time = parse_render_time(stdout)
    if render_time is not None:
        payload["render_time_seconds"] = render_time
    payload["status"] = _finalize_and_upload(
        success, was_canceled, job_id, final_output_path,
        thumbnail_path=thumbnail_path,
    )

    api_handler.update_job_status(job_id, payload)
    logger.info(
        f"Successfully reported final status '{payload['status']}' "
        f"for job {job_id}."
    )

    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.append({
            'job_id': job_id, 'name': job_name,
            'status': payload["status"],
            'render_time_seconds': render_time,
            'completed_at': completed_at,
        })
        if len(job_processor._recent_jobs) > 20:
            job_processor._recent_jobs[:] = job_processor._recent_jobs[-20:]
