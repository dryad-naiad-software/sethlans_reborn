# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reporting one render result to the manager (split out of job_lifecycle).

Upload, status report, yield outcome and the local recent-jobs list. A
result the worker stopped (#246, #247) is not reported: it is handed back
with yield-requeue on a worker shutdown, or left alone when the job is no
longer ours. A 409 on the upload or on the status report means the job is
no longer assigned to this worker (#217): nothing more is sent and the
local output is removed; a user-cancelled job whose CANCELED report gets
that 409 (it is already terminal) is still recorded as cancelled.

A job is reported DONE only when its output was uploaded (#249). A missing
output file, or an upload that failed after its retries, is reported ERROR
with a message saying so in the final status report; after a failed upload
the rendered file is kept on the worker. Multi-frame jobs have no single
output path and keep the earlier behaviour (DONE after a successful exit).
"""
import datetime
import logging
import os

from sethlans_worker_agent import api_handler, blender_hand_back
from sethlans_worker_agent.api_conflict import NO_LONGER_ASSIGNED, JobNoLongerAssigned
from sethlans_worker_agent.utils.render_time_parser import parse_render_time

logger = logging.getLogger(__name__)


def _remove_local_output(*file_paths):
    """Delete rendered files (and their emptied directories)."""
    for file_path in file_paths:
        if not file_path or not os.path.exists(file_path):
            continue
        try:
            os.remove(file_path)
            parent = os.path.dirname(file_path)
            if not os.listdir(parent):
                os.rmdir(parent)
        except OSError as e:
            logger.warning(f"Could not clean up output file: {e}")


def _finalize(success, was_canceled, job_id, path, thumbnail_path=None):
    """Decide the final status, upload the output and clean up.

    Returns:
        ``(status, error_message)``: ``error_message`` replaces the render's
        own when the output is missing or its upload failed (#249), else
        None. ``status`` is None when the manager rejected the upload with
        409 (job canceled, requeued or reassigned meanwhile, #217); then the
        local output is removed and no status must be reported.
    """
    if not success:
        return ("CANCELED" if was_canceled else "ERROR"), None
    if not path:
        return "DONE", None  # multi-frame: no single output path
    if not os.path.isfile(path):
        return "ERROR", f"Blender finished but produced no output file ({os.path.basename(path)})."
    try:
        uploaded = api_handler.upload_render_output(job_id, path, thumbnail_path=thumbnail_path)
    except JobNoLongerAssigned:
        _remove_local_output(path, thumbnail_path)
        return None, None
    if not uploaded:
        return "ERROR", ("The render finished but uploading its output failed; the file is kept in the "
                         f"worker's output folder as {os.path.basename(path)}.")
    _remove_local_output(path, thumbnail_path)
    return "DONE", None


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


def _render_time(result):
    """Executor-measured time, else parsed from stdout (Blender)."""
    if result.render_time_seconds is not None:
        return result.render_time_seconds
    return parse_render_time(result.stdout)


def report_result(job_data, result, job_processor):
    """Upload and report one result."""
    job_id = job_data.get('id')
    job_name = job_data.get('name', 'Unnamed Job')
    if result.stopped:
        blender_hand_back.hand_back(job_id, result.stop_reason)
        return
    if result.released:
        logger.info(f"Job {job_id} was handed back to the manager; not reporting a result.")
        return

    # Handle yield outcome (FR-7, FR-9)
    if result.was_yielded and result.yield_monitor is not None:
        _handle_yield_outcome(job_data, result.yield_monitor, result.grace_outcome)
        if result.grace_outcome == "aborted":
            return
        # grace_outcome == "finished": fall through to upload

    completed_at = datetime.datetime.now(
        datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    payload = {
        "completed_at": completed_at, "last_output": result.stdout,
        "error_message": result.error_message,
    }
    render_time = _render_time(result)
    if render_time is not None:
        payload["render_time_seconds"] = render_time
    final_status, output_error = _finalize(
        result.success, result.was_canceled, job_id, result.output_path,
        thumbnail_path=result.thumbnail_path,
    )
    if final_status is None:
        logger.info(
            f"Job {job_id} is no longer assigned to this worker; "
            f"not reporting a result."
        )
        return
    payload["status"] = final_status
    if output_error:
        payload["error_message"] = output_error

    no_longer_ours = api_handler.update_job_status(job_id, payload) == NO_LONGER_ASSIGNED
    if no_longer_ours and final_status != "CANCELED":  # a cancelled job is still recorded below
        _remove_local_output(result.output_path, result.thumbnail_path)
        return
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
