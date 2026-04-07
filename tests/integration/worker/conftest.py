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
    """Reset job_processor module-level state between tests.

    The legacy _cpu_lock, _gpu_lock, and _gpu_assignment_map primitives
    were removed in the worker capacity gate feature (issue #48). All
    slot state now lives on the WorkerCapacity instance referenced by
    job_processor._capacity. Tests that construct a WorkerCapacity
    should reset _capacity to None in their own teardown; this autouse
    fixture handles the process-global dicts that survive across tests.
    """
    from sethlans_worker_agent import job_processor
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    yield
    # Post-test cleanup: the capacity instance may have been replaced
    # during the test, but we always tear it down so the next test
    # starts from a clean slate.
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()


@pytest.fixture(autouse=True)
def _reset_web_ui_auth():
    """Reset the web_ui auth module's cached state between tests."""
    from sethlans_worker_agent.web_ui import auth
    auth.reset_cache()
    yield
    auth.reset_cache()
