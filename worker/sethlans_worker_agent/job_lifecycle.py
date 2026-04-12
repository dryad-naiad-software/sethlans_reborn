# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job lifecycle helpers invoked after a claim succeeds.

Split out of job_processor to keep that module under the 300-line
ceiling. Contains the render-upload-report flow, YieldMonitor
integration, and the _finalize_and_upload utility.
"""
import datetime
import logging
import os
import threading
from typing import Any, Dict

from sethlans_worker_agent import api_handler, blender_executor, config
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


def _make_yield_monitor_factory(job_data, manual_stop, shutdown_event):
    """Return a factory callable(blender_pid) -> YieldMonitor or None."""
    if not config.IDLE_DETECTION_ENABLED:
        return None

    render_engine = job_data.get('render_engine', 'CYCLES')
    job_id = job_data.get('id')

    def factory(blender_pid):
        from sethlans_worker_agent.idle_detection import YieldMonitor
        monitor = YieldMonitor(
            blender_pid=blender_pid,
            job_id=job_id,
            render_engine=render_engine,
            manual_stop_event=manual_stop,
            shutdown_event=shutdown_event,
        )
        monitor.start()
        return monitor

    return factory


def _handle_yield_outcome(job_data, yield_monitor, grace_outcome):
    """Post yield event to manager and conditionally requeue.

    If ``grace_outcome`` is ``"finished"``, the frame completed
    during the grace period -- the yield event is recorded but the
    job is NOT requeued (it will be uploaded by the normal path).
    If ``"aborted"``, the process was killed and the job is requeued.
    """
    from sethlans_worker_agent.idle_detection import set_yield_cooldown
    from sethlans_worker_agent import system_monitor

    set_yield_cooldown()

    reason = yield_monitor.get_reason()
    if reason is None:
        return

    worker_id = system_monitor.WORKER_ID
    job_id = job_data.get('id')
    progress = reason.progress

    if worker_id:
        api_handler.post_yield_event(
            worker_id=worker_id,
            reason=reason.reason,
            grace_outcome=grace_outcome,
            progress_at_yield=progress,
            job_id=job_id,
        )

    if grace_outcome == "aborted":
        api_handler.post_yield_requeue(job_id, reason.reason)


def process_claimed_job(job_data: Dict[str, Any]):
    """Render, upload, report status, and release the capacity slot.

    Integrates YieldMonitor for idle detection yield handling.
    """
    job_id = job_data.get('id')
    logger.info("Render thread started for job %s", job_id)
    try:
        _process_claimed_job_inner(job_data)
    except Exception:
        logger.exception(
            "Unhandled exception in render thread for job %s", job_id,
        )
        try:
            api_handler.update_job_status(job_id, {
                "status": "ERROR",
                "error_message": "Worker render thread crashed unexpectedly.",
            })
        except Exception:
            logger.exception("Failed to report ERROR status for job %s",
                             job_id)


def _process_claimed_job_inner(job_data: Dict[str, Any]):
    """Inner implementation — exceptions propagate to the wrapper."""
    from sethlans_worker_agent import job_processor
    # Lazy import to avoid argparse trigger during tests.
    try:
        from sethlans_worker_agent.agent import _shutdown_event
    except SystemExit:
        _shutdown_event = threading.Event()

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
            f"Assignments: {capacity.gpu_assignments_snapshot()}"
        )

    manual_stop = threading.Event()
    ym_factory = _make_yield_monitor_factory(
        job_data, manual_stop, _shutdown_event,
    )

    try:
        (success, was_canceled, was_yielded, stdout, stderr,
         blender_error_msg, final_output_path,
         thumbnail_path, yield_monitor,
         grace_outcome) = blender_executor.execute_blender_job(
            job_data, assigned_gpu_index=assigned_gpu_index,
            yield_monitor_factory=ym_factory,
            manual_stop_event=manual_stop,
            shutdown_event=_shutdown_event,
        )
    finally:
        if capacity is not None:
            capacity.release_slot(job_id)
        with job_processor._active_jobs_lock:
            job_processor._active_jobs.pop(job_id, None)

    # Handle yield outcome (FR-7, FR-9)
    if was_yielded and yield_monitor is not None:
        _handle_yield_outcome(job_data, yield_monitor, grace_outcome)
        if grace_outcome == "aborted":
            return
        # grace_outcome == "finished": fall through to upload

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
        f"Reported status '{payload['status']}' for job {job_id}."
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
