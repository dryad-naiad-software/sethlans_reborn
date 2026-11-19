# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Stopping a Blender render the worker should no longer run (#246, #247).

**Worker shutdown.** When the worker shuts down, every running Blender
subprocess is stopped at once, and no new one is started. There is no grace
period: the existing yield grace already aborts on shutdown, and a repeated
frame is better than a result uploaded half-way through the exit. The job
thread then hands the job back (``blender_hand_back``); if that fails, the
manager's restart reconciliation (or the stuck-job reaper) requeues it.

**No longer ours.** The executor loop's manager poll also stops the render
when the job is no longer RENDERING, or is RENDERING for another worker.
Nothing is sent for it. A failed poll never stops a render.

**Order.** A render that already finished when the stop is noticed is left
alone and reported normally. Otherwise the stop mark is written into the
job's dict BEFORE the process tree is terminated: on POSIX psutil reaps our
own child, so ``process.wait()`` later returns 0 for a killed render, and
the executor must decide by the mark, never by the exit code. A psutil
error while stopping is logged and the job is still treated as stopped.
After a stop the wait for the process is bounded (``STOP_WAIT_SECONDS``);
a process that will not die (stuck in a driver call after a GPU loss) is
logged CRITICAL and the job's renderer reservation is retained, so its slot
stays held until the worker restarts, and the job thread goes on.

**Own process group.** Blender (and the thumbnail pass) is started in its
own process group or session (``popen_group_kwargs``), so a terminal
Ctrl+C, a signal to the worker's process group or a service manager stop
reaches only the worker, which then stops Blender itself and records why.
If Blender still ends during a shutdown without a usable result, the job is
marked stopped afterwards (``mark_if_cut_short``) and handed back.

**Per-job stop (FR-CL-5).** A job whose card can no longer be used is
stopped through its own stop event (``blender_job_stop``, re-exported
here), joined with the shutdown event, so every check below sees it.

Signals: the shutdown event and the per-job events are ``threading.Event``
objects whose locks are leaves; job threads read them. The stop reason
travels in the job's own dict from the executor to the Blender adapter.
"""
import logging
import os
import platform
import subprocess
from typing import Optional

import psutil

from sethlans_worker_agent import config
from sethlans_worker_agent.blender_job_stop import (  # noqa: F401 - re-exported (FR-CL-5)
    CARD_STOP_KEY, WORKER_HARDWARE_CHANGED, WORKER_SHUTDOWN, JobStop, forget_card_stop, job_stop,
    register_card_stop, request_card_stop, reset_for_tests, stop_reason,
)
from sethlans_worker_agent.blender_yield import terminate_process_tree

logger = logging.getLogger(__name__)

STOPPED_KEY = '_stopped_by_worker'
STARTED_AT_KEY = '_blender_started_at'
NOT_ASSIGNED = 'no_longer_assigned'
CANCELED = 'canceled'
STOP_WAIT_SECONDS = 10
STOPPED_MESSAGE = "Stopped by the worker."
_reason = stop_reason


def resolve_output_pattern(output_file_pattern) -> str:
    """Return the job's local output pattern under WORKER_OUTPUT_DIR.

    Blender's ``//`` prefix is stripped to avoid UNC path interpretation on
    Windows.
    """
    clean_pattern = output_file_pattern.lstrip('/')
    return os.path.normpath(os.path.join(config.WORKER_OUTPUT_DIR, clean_pattern))


def popen_group_kwargs() -> dict:
    """``Popen`` arguments that start Blender in its own process group/session."""
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def mark_if_cut_short(shutdown_event, job_data, return_code, resolved_output_pattern) -> None:
    """Mark a render that ended during a shutdown without a usable result.

    Covers a Blender that exited on its own during the shutdown (for example
    a signal that reached it directly) before the loop stopped it: unless it
    exited 0 and its single output file exists (multi-frame jobs: exit 0 is
    enough), the job is handed back rather than reported.
    """
    if job_data.get(STOPPED_KEY) or shutdown_event is None or not shutdown_event.is_set():
        return
    start, end = job_data.get('start_frame', 1), job_data.get('end_frame', 1)
    if return_code == 0 and (start != end or os.path.isfile(
            resolved_output_pattern.replace('####', f"{start:04d}"))):
        return
    reason = _reason(shutdown_event)
    logger.warning("Job %s ended during a stop (%s) without a usable result; handing it back.",
                   job_data.get('id'), reason)
    job_data[STOPPED_KEY] = reason


def _stop(process, job_id, job_data, reason) -> None:
    """Mark the job stopped, note when its process started, then kill the tree."""
    job_data[STOPPED_KEY] = reason
    try:
        job_data[STARTED_AT_KEY] = psutil.Process(process.pid).create_time()
    except psutil.Error:
        job_data.pop(STARTED_AT_KEY, None)
    try:
        terminate_process_tree(process.pid, job_id)
    except psutil.Error as exc:  # AccessDenied etc.: still a stopped job
        logger.error("[Job %s] Could not stop the Blender process tree (pid %s): %s", job_id, process.pid, exc)


def stopped_before_start(shutdown_event, job_id, job_data) -> bool:
    """True (and the job marked) if the worker shuts down, or the job was stopped, before Blender starts."""
    if shutdown_event is None or not shutdown_event.is_set():
        return False
    reason = _reason(shutdown_event)
    logger.warning("[Job %s] Stopping (%s): not starting Blender.", job_id, reason)
    job_data[STOPPED_KEY] = reason
    return True


def stop_on_shutdown(shutdown_event, process, job_id, job_data) -> bool:
    """Stop a running render if the worker shuts down or the job was stopped; return whether it did."""
    if shutdown_event is None or not shutdown_event.is_set() or process.poll() is not None:
        return False
    reason = _reason(shutdown_event)
    logger.warning("[Job %s] Stopping (%s): stopping the Blender render.", job_id, reason)
    _stop(process, job_id, job_data, reason)
    return True


def check_manager(job_id, process, job_data) -> Optional[str]:
    """Poll the manager once; stop the render if it was canceled or is not ours.

    Returns ``CANCELED`` (user cancel, reported as before), ``NOT_ASSIGNED``
    (requeued or claimed by another worker; nothing is reported), or None to
    keep rendering, including when the poll fails or the render already
    finished.
    """
    from sethlans_worker_agent import api_extras, system_monitor
    state = api_extras.get_job_assignment(job_id)
    if state is None or process.poll() is not None:
        return None
    status, worker = state
    if status == 'CANCELED':
        logger.warning("[Job %s] Cancellation received.", job_id)
        terminate_process_tree(process.pid, job_id)
        return CANCELED
    own = system_monitor.WORKER_ID
    if status == 'RENDERING' and (worker is None or own is None or worker == own):
        return None
    logger.warning(
        "[Job %s] The manager no longer assigns this job to this worker (status %s, worker %s): "
        "stopping the Blender render without reporting it.", job_id, status, worker,
    )
    _stop(process, job_id, job_data, NOT_ASSIGNED)
    return NOT_ASSIGNED


def wait_for_exit(process, job_id, job_data, killed=False) -> int:
    """``process.wait()``, bounded after any worker-side kill.

    ``killed`` covers a user cancel and an aborted yield; a stop mark counts
    too. A killed process that is still alive after ``STOP_WAIT_SECONDS`` is
    logged CRITICAL and the job's renderer reservation retained (its slot
    stays held until the worker restarts); -1 is returned.
    """
    if not (killed or job_data.get(STOPPED_KEY)):
        return process.wait()
    try:
        return process.wait(timeout=STOP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.critical(
            "[Job %s] Blender (pid %s) did not exit %d s after it was stopped; keeping its slot "
            "until the worker restarts.", job_id, process.pid, STOP_WAIT_SECONDS,
        )
        reservation = job_data.get('_renderer_reservation')
        if reservation is not None:
            reservation.retain_until_restart()
        return -1


def take_stop_reason(job_data) -> Optional[str]:
    """Return (and clear) why the executor stopped this job's render, or None."""
    return job_data.pop(STOPPED_KEY, None)


def remove_partial_output(job_data) -> None:
    """Delete output the stopped render may have written for this job's frames.

    Only files modified at or after this job's Blender process started are
    removed: an older file with the same name belongs to someone else (the
    output directory is shared). Without a known start time nothing is
    removed.
    """
    started_at = job_data.pop(STARTED_AT_KEY, None)
    pattern = job_data.get('output_file_pattern')
    if started_at is None or not pattern:
        return
    resolved = resolve_output_pattern(pattern)
    start, end = job_data.get('start_frame', 1), job_data.get('end_frame', 1)
    for frame in range(start, end + 1):
        path = resolved.replace('####', f"{frame:04d}")
        try:
            if os.path.isfile(path) and os.path.getmtime(path) >= started_at:
                os.remove(path)
                logger.info("Removed partial output %s of a stopped render.", path)
        except OSError as exc:
            logger.warning("Could not remove partial output %s: %s", path, exc)


def wait_or_stop(process, stop_event, job_id, poll_seconds: float = 1.0) -> Optional[int]:
    """Wait for ``process``; stop its tree if ``stop_event`` is set first.

    Returns the exit code, or None when the process was stopped.
    """
    while True:
        try:
            return process.wait(timeout=poll_seconds)
        except subprocess.TimeoutExpired:
            if stop_event is not None and stop_event.is_set():
                logger.warning("[Job %s] Stopping (%s): stopping the thumbnail pass.", job_id, _reason(stop_event))
                try:
                    terminate_process_tree(process.pid, job_id)
                except psutil.Error as exc:
                    logger.error("[Job %s] Could not stop the thumbnail pass (pid %s): %s",
                                 job_id, process.pid, exc)
                return None
