# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Background Blender download with progress tracking.

Wraps the worker's single install entry point,
``blender_install.install(..., blocking=True)`` (BS-WK-3), reporting
progress to the download-progress registry so the setup wizard frontend
can poll for updates. The cancel event stops a running download.

Runs on a background thread spawned by ``handlers_blender``.
"""

import logging

from sethlans_worker_agent.web_ui.setup import blender_devices
from sethlans_worker_agent.web_ui.setup.download_progress import (
    get_task, update_task,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)

logger = logging.getLogger(__name__)

# Install step -> (task status, percent), the steps the page has always shown.
_STEP_PROGRESS = {
    "downloading": ("downloading", 15),
    "verifying": ("verifying", 60),
    "extracting": ("extracting", 75),
}


def download_blender_with_progress(task_id: str, version: str) -> None:
    """Install Blender with progress tracking.

    Called from a daemon thread. Updates the download-progress
    registry as it proceeds. On success, appends the
    ``blender_installed`` wizard checkpoint.
    """
    from sethlans_worker_agent import blender_install
    from sethlans_worker_agent.tool_manager import tool_manager_instance

    progress = get_task(task_id)
    if progress is None:
        return
    cancel = progress.cancel_event

    full_version = _resolve_version(task_id, tool_manager_instance, version, cancel)
    if full_version is None:
        return  # Already updated task status

    def on_step(step: str) -> None:
        status, percent = _STEP_PROGRESS[step]
        update_task(task_id, status=status, percent=percent)

    try:
        result = blender_install.install(full_version, blocking=True, stop=cancel, on_step=on_step)
    except Exception as e:
        logger.error(
            "Blender download failed for task %s: %s",
            task_id, e, exc_info=True,
        )
        update_task(task_id, status="failed", error=str(e))
        return
    if not result.ok:
        update_task(task_id, status="failed", error=result.error or "Blender install failed.")
        return
    _finish(task_id)
    logger.info(
        "Blender %s downloaded and installed (task %s).",
        full_version, task_id,
    )


def _finish(task_id) -> None:
    append_wizard_checkpoint("blender_installed")
    blender_devices.start_device_check()
    update_task(task_id, status="complete", percent=100)


def _resolve_version(task_id, tm, version, cancel):
    """Resolve the version; return ``X.Y.Z``, or None when the task should stop.

    None when the version is unknown, the task was cancelled or the version
    is installed already; the task status is updated in each case.
    """
    update_task(task_id, status="downloading", percent=5)
    full_version = tm._resolve_version(version)
    if not full_version:
        update_task(
            task_id, status="failed",
            error=f"Unknown version: {version}",
        )
        return None

    if cancel.is_set():
        update_task(task_id, status="failed", error="Cancelled")
        return None

    if tm.get_blender_executable_path(full_version):
        _finish(task_id)
        return None

    update_task(task_id, status="downloading", percent=10)
    return full_version
