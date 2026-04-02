# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared fixtures for worker agent integration tests.

Provides temporary directories, fresh ToolManager instances,
and state reset fixtures for threading-related modules.
"""

import pytest

from sethlans_worker_agent.tool_manager import ToolManager


@pytest.fixture()
def tmp_tools_dir(tmp_path):
    """Temporary directory for tool_manager operations."""
    tools = tmp_path / "managed_tools"
    tools.mkdir()
    blender = tools / "blender"
    blender.mkdir()
    return tools


@pytest.fixture()
def tool_manager(tmp_tools_dir, mocker):
    """Fresh ToolManager instance pointing at tmp_tools_dir."""
    mocker.patch(
        'sethlans_worker_agent.config.MANAGED_TOOLS_DIR',
        tmp_tools_dir,
    )
    tm = ToolManager()
    tm.tools_dir = tmp_tools_dir
    tm.blender_dir = tmp_tools_dir / "blender"
    return tm


@pytest.fixture(autouse=True)
def _reset_api_auth_state():
    """Reset the api_auth module's threading.Event between tests."""
    from sethlans_worker_agent import api_auth
    api_auth._auth_failed.clear()
    yield
    api_auth._auth_failed.clear()


@pytest.fixture(autouse=True)
def _reset_version_sync_state():
    """Reset version_sync module-level state between tests."""
    from sethlans_worker_agent import version_sync
    with version_sync._failed_lock:
        version_sync._failed_versions.clear()
    with version_sync._pending_lock:
        version_sync._pending_downloads.clear()
    with version_sync._pending_removals_lock:
        version_sync._pending_removals.clear()
    yield
    with version_sync._failed_lock:
        version_sync._failed_versions.clear()
    with version_sync._pending_lock:
        version_sync._pending_downloads.clear()
    with version_sync._pending_removals_lock:
        version_sync._pending_removals.clear()


@pytest.fixture(autouse=True)
def _reset_job_processor_state():
    """Reset job_processor module-level state between tests."""
    from sethlans_worker_agent import job_processor
    with job_processor._gpu_lock:
        job_processor._gpu_assignment_map.clear()
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()
    if job_processor._cpu_lock.locked():
        try:
            job_processor._cpu_lock.release()
        except RuntimeError:
            pass
    yield


@pytest.fixture(autouse=True)
def _reset_web_ui_auth():
    """Reset the web_ui auth module's cached token between tests."""
    from sethlans_worker_agent.web_ui import auth
    auth._token = None
    yield
    auth._token = None
