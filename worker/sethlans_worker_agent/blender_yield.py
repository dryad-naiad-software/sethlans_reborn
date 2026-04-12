# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender subprocess yield and termination helpers (FR-6, FR-7).

Extracted from blender_executor.py to keep both modules under 300 lines.
Provides the SIGTERM->SIGKILL process tree kill, the abort-condition
checker, and the grace-period yield handler.
"""
import logging
import time

import psutil

from sethlans_worker_agent import api_handler

logger = logging.getLogger(__name__)


def _safe_terminate(proc):
    """Terminate a single process, ignoring NoSuchProcess."""
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        pass


def _safe_kill(proc):
    """Kill a single process, ignoring NoSuchProcess."""
    try:
        proc.kill()
    except psutil.NoSuchProcess:
        pass


def terminate_process_tree(pid, job_id=None):
    """SIGTERM -> 5s -> SIGKILL on the Blender process tree."""
    tag = f"[Job {job_id}] " if job_id else ""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            _safe_terminate(child)
        _safe_terminate(parent)
        logger.info(f"{tag}Sent SIGTERM to process tree. Waiting up to 5s.")
        _, alive = psutil.wait_procs(children + [parent], timeout=5)
        if alive:
            logger.warning(
                f"{tag}{len(alive)} process(es) survived SIGTERM. "
                f"Escalating to SIGKILL."
            )
            for p in alive:
                _safe_kill(p)
        else:
            logger.info(f"{tag}All processes exited after SIGTERM.")
    except psutil.NoSuchProcess:
        logger.info(f"{tag}Process already exited.")


def should_abort(manual_stop_event, job_id, shutdown_event):
    """Return True if any abort condition is met (FR-6g).

    Checks:
    1. manual_stop_event.is_set() -- user triggered stop
    2. get_job_status(job_id) == 'CANCELED' -- manager canceled the job
    3. shutdown_event.is_set() -- worker is shutting down
    """
    if manual_stop_event is not None and manual_stop_event.is_set():
        return True
    if shutdown_event is not None and shutdown_event.is_set():
        return True
    if api_handler.get_job_status(job_id) == 'CANCELED':
        return True
    return False


def handle_yield(process, job_id, render_engine, yield_monitor,
                 manual_stop_event, shutdown_event):
    """Handle a yield event during rendering (FR-6).

    Determines whether to allow the current frame to finish (grace
    period) or abort immediately based on render engine and progress.

    Returns a tuple ``(was_yielded, grace_outcome)`` where:
    - ``was_yielded`` is always True (a yield was triggered).
    - ``grace_outcome`` is ``"finished"`` if the Blender process
      exited cleanly during the grace period, ``"aborted"`` if it
      was killed or no grace was allowed.
    """
    from sethlans_worker_agent.idle_detection.progress_parser import (
        parse_blender_progress,
    )
    from sethlans_worker_agent import config
    from sethlans_worker_agent.blender_executor import get_last_output_line

    reason = yield_monitor.get_reason()
    line = get_last_output_line(job_id)
    progress = parse_blender_progress(line or "", render_engine)

    # Determine grace-period behavior (FR-6b, FR-6c)
    allow_finish = False
    if render_engine != 'CYCLES':
        # Eevee/Workbench: always allow to finish (fast renders)
        allow_finish = True
    elif progress is not None and progress >= 0.75:
        allow_finish = True

    reason_str = reason.reason if reason else "unknown"
    grace_outcome = "aborted"

    if allow_finish and process.poll() is None:
        grace_cap = config.IDLE_GRACE_PERIOD_CAP_SECONDS
        deadline = time.monotonic() + grace_cap
        logger.info(
            "[Job %s] Yield (%s). Allowing finish (progress=%s, cap=%ds).",
            job_id, reason_str,
            f"{progress:.0%}" if progress else "indeterminate", grace_cap,
        )
        while process.poll() is None and time.monotonic() < deadline:
            if should_abort(manual_stop_event, job_id, shutdown_event):
                break
            if manual_stop_event is not None:
                manual_stop_event.wait(2)
            else:
                time.sleep(2)
        if process.poll() is None:
            p = progress or 0.0
            if p >= 0.95:
                logger.warning(
                    "Aborting job %s at %.0f%% after grace cap. "
                    "Frame may be in compositing/denoising.",
                    job_id, p * 100,
                )
            terminate_process_tree(process.pid, job_id)
        else:
            # Process exited cleanly during the grace period
            grace_outcome = "finished"
    elif process.poll() is None:
        logger.info(
            "[Job %s] Yield (%s). Immediate abort (progress=%s).",
            job_id, reason_str,
            f"{progress:.0%}" if progress else "indeterminate",
        )
        terminate_process_tree(process.pid, job_id)
    else:
        # Process already exited before we could act
        grace_outcome = "finished"

    return True, grace_outcome
