# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender adapter over ``blender_executor.execute_blender_job`` (FR-WX-2/3).

``execute_blender_job`` is looked up on the module at call time, so tests
that patch ``sethlans_worker_agent.blender_executor.execute_blender_job``
keep working. ``blender_executor.py`` itself is not modified.
"""
from typing import Optional

from sethlans_worker_agent import blender_executor
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
            job_data, assigned_gpu_index=ctx.assigned_gpu_index,
            yield_monitor_factory=ctx.yield_monitor_factory,
            manual_stop_event=ctx.manual_stop_event,
            shutdown_event=ctx.shutdown_event,
        )
        return RenderResult(
            success=success, was_canceled=was_canceled,
            was_yielded=was_yielded, stdout=stdout, stderr=stderr,
            error_message=error_message, output_path=output_path,
            thumbnail_path=thumbnail_path, yield_monitor=yield_monitor,
            grace_outcome=grace_outcome, render_time_seconds=None,
            released=False,
        )
