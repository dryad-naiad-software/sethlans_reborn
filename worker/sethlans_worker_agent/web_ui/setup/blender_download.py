# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Background Blender download with progress tracking.

Wraps the existing ``tool_manager`` download/install pipeline,
reporting progress to the download-progress registry so the setup
wizard frontend can poll for updates.

Runs on a background thread spawned by ``handlers_blender``.
"""

import logging
import os
import platform
import stat
from pathlib import Path

from sethlans_worker_agent.web_ui.setup.download_progress import (
    get_task, update_task,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)

logger = logging.getLogger(__name__)


def download_blender_with_progress(task_id: str, version: str) -> None:
    """Download Blender using tool_manager with progress tracking.

    Called from a daemon thread. Updates the download-progress
    registry as it proceeds. On success, appends the
    ``blender_installed`` wizard checkpoint.
    """
    from sethlans_worker_agent.tool_manager import tool_manager_instance

    progress = get_task(task_id)
    if progress is None:
        return
    cancel = progress.cancel_event
    tm = tool_manager_instance

    download_spec = _resolve_download_spec(task_id, tm, version, cancel)
    if download_spec is None:
        return  # Already updated task status

    full_version, url, expected_hash = download_spec

    try:
        update_task(task_id, status="downloading", percent=15)
        with tm._download_lock:
            tm._downloading_versions.add(full_version)
        tm._scan_cache = None
        try:
            _run_download_pipeline(
                task_id, tm, full_version, url,
                expected_hash, cancel,
            )
        finally:
            with tm._download_lock:
                tm._downloading_versions.discard(full_version)
    except Exception as e:
        logger.error(
            "Blender download failed for task %s: %s",
            task_id, e, exc_info=True,
        )
        update_task(task_id, status="failed", error=str(e))


def _resolve_download_spec(task_id, tm, version, cancel):
    """Resolve version and gather download info.

    Returns ``(full_version, url, expected_hash)`` on success, or
    ``None`` if the task should stop (already installed, failed, or
    cancelled). Updates the task status on failure.
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

    exe = tm.get_blender_executable_path(full_version)
    if exe:
        append_wizard_checkpoint("blender_installed")
        update_task(task_id, status="complete", percent=100)
        return None

    update_task(task_id, status="downloading", percent=10)
    blender_releases = tm._get_blender_download_info()
    platform_id = tm._get_platform_identifier()
    release_info = blender_releases.get(
        full_version, {},
    ).get(platform_id)

    if not release_info:
        update_task(
            task_id, status="failed",
            error=f"No release for {full_version} on {platform_id}.",
        )
        return None

    url = release_info.get("url")
    if not url:
        update_task(
            task_id, status="failed",
            error=f"No download URL for {full_version}.",
        )
        return None

    if cancel.is_set():
        update_task(task_id, status="failed", error="Cancelled")
        return None

    return full_version, url, release_info.get("sha256")


def _run_download_pipeline(
    task_id, tm, full_version, url, expected_hash, cancel,
):
    """Execute the download -> verify -> extract pipeline."""
    from sethlans_worker_agent.utils import file_operations

    tm._create_tools_directory_if_not_exists()
    download_path = file_operations.download_file(url, tm.blender_dir)

    if cancel.is_set():
        _cleanup_file(download_path)
        update_task(task_id, status="failed", error="Cancelled")
        return

    update_task(task_id, status="verifying", percent=60)
    if not expected_hash:
        _cleanup_file(download_path)
        update_task(
            task_id, status="failed",
            error="No SHA256 hash available for verification.",
        )
        return

    if not file_operations.verify_hash(download_path, expected_hash):
        _cleanup_file(download_path)
        update_task(
            task_id, status="failed",
            error="SHA256 hash verification failed.",
        )
        return

    if cancel.is_set():
        _cleanup_file(download_path)
        update_task(task_id, status="failed", error="Cancelled")
        return

    update_task(task_id, status="extracting", percent=75)
    file_operations.extract_archive(download_path, tm.blender_dir)
    file_operations.cleanup_archive(download_path)
    tm._scan_cache = None

    exe_path = tm.get_blender_executable_path(full_version)
    if exe_path and platform.system() != "Windows":
        st = os.stat(exe_path)
        os.chmod(exe_path, st.st_mode | stat.S_IEXEC)

    append_wizard_checkpoint("blender_installed")
    update_task(task_id, status="complete", percent=100)
    logger.info(
        "Blender %s downloaded and installed (task %s).",
        full_version, task_id,
    )


def _cleanup_file(path) -> None:
    """Best-effort delete of a download artifact."""
    try:
        if path and Path(path).exists():
            os.remove(path)
    except OSError:
        pass
