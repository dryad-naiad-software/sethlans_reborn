# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender adapter over ``blender_executor.execute_blender_job`` (FR-WX-2/3).

``execute_blender_job`` is looked up on the module at call time, so tests
that patch ``sethlans_worker_agent.blender_executor.execute_blender_job``
keep working. A render the executor stopped for a worker shutdown becomes
``stopped`` (blender_stop.py, #246/#247), as does a job whose Blender
version is not installed (``blender_version_missing``, BS-WK-11).
"""
from typing import Optional

from sethlans_worker_agent import blender_executor, blender_stop
from sethlans_worker_agent.executors.base import (
    ExecutionContext,
    RendererReservation,
    RenderResult,
)


class BlenderExecutor:
    """Blender jobs: always eligible, one shared capacity slot each."""

    renderer = 'BLENDER'
    capacity_mode = 'shared'

    def try_reserve(self, job_data: dict) -> Optional[RendererReservation]:
        """Blender has no renderer-side resource: a no-op reservation."""
        return RendererReservation()

    def execute(self, job_data: dict, ctx: ExecutionContext) -> RenderResult:
        """Run Blender and map its 10-tuple 1:1 onto ``RenderResult``."""
        (success, was_canceled, was_yielded, stdout, stderr,
         error_message, output_path, thumbnail_path, yield_monitor,
         grace_outcome) = blender_executor.execute_blender_job(
            job_data, reservation=ctx.reservation,
            yield_monitor_factory=ctx.yield_monitor_factory,
            manual_stop_event=ctx.manual_stop_event,
            shutdown_event=ctx.shutdown_event,
        )
        # The executor marks a stop only while the render still runs.
        stop_reason = blender_stop.take_stop_reason(job_data)
        stopped = stop_reason is not None
        if stopped:
            blender_stop.remove_partial_output(job_data)
        return RenderResult(
            success=success, was_canceled=was_canceled,
            was_yielded=was_yielded, stdout=stdout, stderr=stderr,
            error_message=error_message, output_path=output_path,
            thumbnail_path=thumbnail_path, yield_monitor=yield_monitor,
            grace_outcome=grace_outcome, render_time_seconds=None,
            released=False, stopped=stopped, stop_reason=stop_reason,
        )
