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
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.web_ui import auth, server
from sethlans_worker_agent.web_ui.setup.gate import mark_setup_complete

from ._health_fixtures import _bring_up_wsgi, _wait_for_bind


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


@pytest.fixture(autouse=True)
def _reset_setup_gate():
    """Reset the setup gate module-level state between tests."""
    from sethlans_worker_agent.web_ui.setup import gate
    gate._setup_complete = False
    yield
    gate._setup_complete = False


# --- /api/health/ integration test fixtures ------------------------
# Bring-up plumbing lives in ``_health_fixtures._bring_up_wsgi``;
# fixtures live here so pytest auto-discovers them in the worker
# integration test package without forcing test modules to import
# (and therefore F811-shadow) the fixture names.

@pytest.fixture()
def web_ui_setup_complete(mocker, tmp_path):
    """Bring up the WSGI app with the setup gate **open**.

    Fixes ``system_monitor.WORKER_ID`` to a known sentinel string so
    the test asserts the post-enrollment branch.  Restores the
    original value on teardown.
    """
    original_worker_id = system_monitor.WORKER_ID
    system_monitor.WORKER_ID = 'integration-worker-id-42'

    port, password = _bring_up_wsgi(mocker, tmp_path)

    # Setup-complete variant -- gate is open.
    mark_setup_complete()

    _wait_for_bind(port, '/api/health/', expect_status_in={200})

    yield {
        'base_url': f'http://127.0.0.1:{port}',
        'token': password,
    }

    server.stop_server()
    auth.reset_cache()
    system_monitor.WORKER_ID = original_worker_id


@pytest.fixture()
def web_ui_setup_pending(mocker, tmp_path):
    """Bring up the WSGI app with the setup gate **closed**.

    Forces ``system_monitor.WORKER_ID`` to ``None`` so the handler
    exercises the pre-enrollment branch (FR-H-7).  ``mark_setup_complete``
    is intentionally NOT called -- this is the DevOps-LOW-5 inversion
    of the standard harness and is the entire point of this fixture.
    """
    original_worker_id = system_monitor.WORKER_ID
    system_monitor.WORKER_ID = None

    port, password = _bring_up_wsgi(mocker, tmp_path)

    # Sanity: gate is closed -- ``/api/health/`` is the only path that
    # should reply 200; everything else replies 503.
    _wait_for_bind(port, '/api/health/', expect_status_in={200})

    yield {
        'base_url': f'http://127.0.0.1:{port}',
        'token': password,
    }

    server.stop_server()
    auth.reset_cache()
    system_monitor.WORKER_ID = original_worker_id
