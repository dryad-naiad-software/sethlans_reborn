# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job lifecycle helpers invoked after a claim succeeds.

Split out of job_processor to keep that module under the 300-line
ceiling. Contains the render flow and YieldMonitor integration; the
upload and status report of a result live in job_report.

The render runs through the job's executor (Unreal foundation FR-WX-4):
``executors.get_executor(job_data.get('renderer') or 'BLENDER')``.
Resource release follows ``job_resources``: renderer reservation before
capacity slot, nothing while retained, plus an outermost safety net that
repeats the releases only if the inner ``finally`` never ran, so it can
never free slots of a later claim of the same job id.
"""
import logging
import threading
from typing import Any, Dict

from sethlans_worker_agent import api_handler, config, executors, job_resources
from sethlans_worker_agent.job_report import report_result as _report_result
from sethlans_worker_agent.executors import ExecutionContext, UnsupportedRendererError

logger = logging.getLogger(__name__)


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


def process_claimed_job(job_data: Dict[str, Any]):
    """Render, upload, report status, and release the capacity slot.

    Integrates YieldMonitor for idle detection yield handling. The
    outermost ``finally`` is the resource safety net (FR-WX-4): it
    releases only what the inner ``finally`` did not, and always removes
    the id from ``_unfinished_job_ids`` last, after report and upload.
    """
    job_id = job_data.get('id')
    logger.info("Render thread started for job %s", job_id)
    try:
        _process_claimed_job_inner(job_data)
    except Exception as exc:
        logger.exception(
            "Unhandled exception in render thread for job %s", job_id,
        )
        message = (
            str(exc) if isinstance(exc, UnsupportedRendererError)
            else "Worker render thread crashed unexpectedly."
        )
        try:
            api_handler.update_job_status(job_id, {
                "status": "ERROR",
                "error_message": message,
            })
        except Exception:
            logger.exception("Failed to report ERROR status for job %s",
                             job_id)
    finally:
        job_resources.release_after_job(job_data)


def _execute_and_release(job_data, capacity, ctx: ExecutionContext):
    """Run the job's executor; then release its resources (FR-WX-4).

    The executor lookup is inside the ``try``, so an unsupported renderer
    still releases the slot, the reservation and the ``_active_jobs``
    entry. The Blender slot is released here, before the upload, as before.
    ``_resources_released`` then tells the outermost safety net not to
    release by this job id again: by then the id may belong to a new claim.
    """
    job_id = job_data.get('id')
    try:
        executor = executors.get_executor(
            job_data.get('renderer') or executors.DEFAULT_RENDERER)
        return executor.execute(job_data, ctx)
    finally:
        # Renderer reservation first, then the slot; neither if retained.
        job_resources.release_reservations(
            job_id, job_data.get('_renderer_reservation'), capacity)
        job_data[job_resources.RESOURCES_RELEASED] = True
        job_resources.forget_job(job_data, unfinished=False)


def _process_claimed_job_inner(job_data: Dict[str, Any]):
    """Inner implementation — exceptions propagate to the wrapper."""
    from sethlans_worker_agent import job_processor
    # Lazy import to avoid argparse trigger during tests.
    try:
        from sethlans_worker_agent.agent import _shutdown_event
    except SystemExit:
        _shutdown_event = threading.Event()

    job_id = job_data.get('id')
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

    result = _execute_and_release(job_data, capacity, ExecutionContext(
        assigned_gpu_index=assigned_gpu_index,
        yield_monitor_factory=ym_factory,
        manual_stop_event=manual_stop,
        shutdown_event=_shutdown_event,
    ))
    try:
        _report_result(job_data, result, job_processor)
    finally:
        _run_after_report(job_id, result)


def _run_after_report(job_id, result):
    """The executor's post-report hook (Unreal cleanup); never raises."""
    hook = getattr(result, 'after_report', None)
    if hook is None:
        return
    try:
        hook()
    except Exception:  # noqa: BLE001 - cleanup must not fail the job thread
        logger.exception("Post-report cleanup failed for job %s", job_id)
