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
    from sethlans_worker_agent import blender_stop
    from sethlans_worker_agent import capacity as capacity_module
    from sethlans_worker_agent.capacity import card_check, rebuild
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    capacity_module.reset_gpu_list_recheck()
    capacity_module.reset_missing_executable_warning()
    blender_stop.reset_for_tests()
    rebuild.reset_for_tests()
    card_check.reset_for_tests()
    yield
    # Post-test cleanup: the capacity instance may have been replaced
    # during the test, but we always tear it down so the next test
    # starts from a clean slate.
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    capacity_module.reset_gpu_list_recheck()
    capacity_module.reset_missing_executable_warning()
    blender_stop.reset_for_tests()
    rebuild.reset_for_tests()
    card_check.reset_for_tests()
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()


@pytest.fixture(autouse=True)
def _quiet_card_startup_log(monkeypatch):
    """Mark the card startup log as done, so no test probes the real cards.

    ``job_processor.init_capacity`` logs the card list once per process,
    which runs the real OS probe (DXGI on Windows, sysfs on Linux,
    ``system_profiler`` on macOS). Same mechanism as the unit-suite
    fixture; ``monkeypatch`` restores the flag after each test.
    """
    from sethlans_worker_agent.cards import startup_log
    monkeypatch.setattr(startup_log, '_done', True)
    yield


@pytest.fixture(autouse=True)
def _isolated_compute_section(monkeypatch):
    """No saved compute section and no ``SETHLANS_COMPUTE_*`` variables.

    Same as the unit-suite fixture: ``config_compute.load`` would otherwise
    read this machine's real ``config.json``. Tests that need GPU slots
    use ``_capacity_harness.install_fake_capacity``. Also forgets the
    alerts the store kept in memory, and marks the Eevee/Workbench card
    condition as known (not holding), so no job start probes the real cards.
    """
    from sethlans_worker_agent import config_compute
    from sethlans_worker_agent.cards import in_use, store
    from sethlans_worker_agent.cards import warnings as card_warnings
    for name in (config_compute.ENV_METHOD, config_compute.ENV_GPU_MODE,
                 config_compute.ENV_CARDS, config_compute.ENV_ALLOW):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_compute, '_read_raw', lambda: None)
    in_use.reset_env_records_for_tests()
    store.reset_memory_for_tests()
    card_warnings.reset_for_tests(False)
    yield
    in_use.reset_env_records_for_tests()
    store.reset_memory_for_tests()
    card_warnings.reset_for_tests(False)


@pytest.fixture(autouse=True)
def _no_setup_blender_device_check(monkeypatch):
    """No real Blender device check from the wizard's Blender step; no cached card probe.

    Same as the unit-suite fixture.
    """
    from sethlans_worker_agent.web_ui import cards_cache
    from sethlans_worker_agent.web_ui.setup import blender_devices
    monkeypatch.setattr(blender_devices, 'start_device_check', lambda: None)
    monkeypatch.setattr(blender_devices, '_failed', False)
    cards_cache.reset_for_tests()
    yield
    cards_cache.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_card_probe_in_heartbeats(monkeypatch):
    """Registration and heartbeats build their ``cards`` report from an empty probe (no real OS probe).

    Same as the unit-suite fixture.
    """
    from sethlans_worker_agent.cards import choice, payload
    from sethlans_worker_agent.cards.inventory import Inventory
    from sethlans_worker_agent.web_ui import cards_view
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(Inventory(), None))
    choice.reset_for_tests()
    yield
    choice.reset_for_tests()


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
