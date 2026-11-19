# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared fixtures for worker agent unit tests.
"""
import pytest


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

    Clears active/recent job collections, the pause event, the module-global
    WorkerCapacity instance, the card check cadence timestamp, the per-job
    stop registry, the rebuild generation and pending mark, and the card
    check's remembered Blender list.
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
    _reset_unreal_card_state()
    yield
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    capacity_module.reset_gpu_list_recheck()
    capacity_module.reset_missing_executable_warning()
    blender_stop.reset_for_tests()
    rebuild.reset_for_tests()
    card_check.reset_for_tests()
    _reset_unreal_card_state()


def _reset_unreal_card_state():
    """The Unreal job's card choice and the last FR-CU-6 reason (card selection chunk 7).

    The choice module is looked up, not imported: some tests make importing
    ``unreal_executor`` fail on purpose.
    """
    import sys
    from sethlans_worker_agent.cards import unreal_cards
    for name in ('adapter_choice', 'adapter_log'):  # the card choice; the projects refused after a mismatch
        module = sys.modules.get(f'sethlans_worker_agent.unreal_executor.{name}')
        if module is not None:
            module.reset_for_tests()
    unreal_cards.reset_for_tests()


@pytest.fixture(autouse=True)
def _quiet_card_startup_log(monkeypatch):
    """Mark the card startup log as done, so no test probes the real cards.

    ``job_processor.init_capacity`` logs the card list once per process,
    which runs the real OS probe (DXGI on Windows, sysfs on Linux,
    ``system_profiler`` on macOS). The card tests re-arm it with
    ``startup_log.reset_for_tests()``.
    """
    from sethlans_worker_agent.cards import startup_log
    monkeypatch.setattr(startup_log, '_done', True)
    yield


@pytest.fixture(autouse=True)
def _isolated_compute_section(monkeypatch):
    """No saved compute section and no ``SETHLANS_COMPUTE_*`` variables.

    ``config_compute.load`` would otherwise read this machine's real
    ``config.json``. With no section the method is ``cpu`` and no card is
    probed. Tests that need GPU slots patch ``config_compute._read_raw``.
    Also forgets the alerts the store kept in memory, and marks the
    Eevee/Workbench card condition as known (not holding), so no job start
    probes the real cards; ``test_warnings.py`` sets it as it needs.
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
    """The wizard's Blender step starts no real Blender device check in tests.

    ``blender_devices.start_device_check`` would run the first installed
    Blender on a background thread. Tests of the check call
    ``blender_devices._start()`` with the detection patched. Also forgets the
    settings page's cached card probe.
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
    """Registration and heartbeats build their ``cards`` report from an empty probe.

    ``cards.payload`` would otherwise run the real OS probe. The payload
    tests put back the real seam, taken at their import. Also
    forgets that a malformed manager dismissal was warned about.
    """
    from sethlans_worker_agent.cards import choice, payload
    from sethlans_worker_agent.cards.inventory import Inventory
    from sethlans_worker_agent.web_ui import cards_view
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(Inventory(), None))
    choice.reset_for_tests()
    yield
    choice.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_setup_gate_state():
    """Reset the setup gate module-level state between tests."""
    from sethlans_worker_agent.web_ui.setup import gate
    gate._setup_complete = False
    yield
    gate._setup_complete = False


@pytest.fixture(autouse=True)
def _reset_setup_status_state():
    """Reset handlers_status module-level state between tests."""
    from sethlans_worker_agent.web_ui.setup import handlers_status
    handlers_status._current_topology = None
    handlers_status._current_checkpoints = []
    yield
    handlers_status._current_topology = None
    handlers_status._current_checkpoints = []


@pytest.fixture(autouse=True)
def _reset_setup_discovery_state():
    """Reset handlers_discovery module-level state between tests."""
    from sethlans_worker_agent.web_ui.setup import handlers_discovery
    handlers_discovery._selected_manager_url = None
    handlers_discovery._selected_manager_id = None
    handlers_discovery._selected_manager_meta = None
    yield
    handlers_discovery._selected_manager_url = None
    handlers_discovery._selected_manager_id = None
    handlers_discovery._selected_manager_meta = None


@pytest.fixture(autouse=True)
def _reset_download_progress_state():
    """Reset download_progress module-level task registry."""
    from sethlans_worker_agent.web_ui.setup import download_progress
    with download_progress._tasks_lock:
        download_progress._download_tasks.clear()
    yield
    with download_progress._tasks_lock:
        download_progress._download_tasks.clear()


@pytest.fixture()
def setup_complete():
    """Flip the worker setup gate open so dashboard routes are reachable.

    Used by the sync WSGI ``wsgi_app`` test suite (``test_wsgi_app*``).
    Not autouse -- tests opt in by listing it as a parameter.
    """
    from sethlans_worker_agent.web_ui.setup import gate
    gate.mark_setup_complete()
    yield
    gate._setup_complete = False


@pytest.fixture()
def fresh_shutdown_event():
    """Bind a fresh ``threading.Event`` as the agent's shutdown event.

    The ``wsgi_app`` resolves the agent's real ``_shutdown_event`` on
    first call; tests replace it with a clean event so ``is_set()`` is
    deterministic and cannot leak between tests.  Used by the sync
    WSGI control-plane tests.
    """
    import threading
    from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module
    ev = threading.Event()
    wsgi_app_module._shutdown_event_ref = ev
    yield ev
    wsgi_app_module._shutdown_event_ref = None


@pytest.fixture()
def bearer_header(mocker):
    """Patch password validation to accept the literal ``test-token``.

    Returns a ready-to-use ``Authorization: Bearer test-token`` header
    dict for the sync WSGI control-plane tests.
    """
    mocker.patch(
        'sethlans_worker_agent.web_ui.wsgi_app.validate_password',
        side_effect=lambda pw: pw == 'test-token',
    )
    return {'Authorization': 'Bearer test-token'}


@pytest.fixture()
def mock_config(mocker):
    """Provide a mocker-patched config module with sensible defaults."""
    mocker.patch(
        'sethlans_worker_agent.config.MANAGER_API_URL',
        'https://127.0.0.1:8080/api/'
    )
    mocker.patch(
        'sethlans_worker_agent.config.API_TOKEN', 'test-token-abc'
    )
    mocker.patch(
        'sethlans_worker_agent.config.CPU_THREADS', 0
    )
    mocker.patch(
        'sethlans_worker_agent.config.UI_ENABLED', True
    )
    mocker.patch(
        'sethlans_worker_agent.config.UI_PORT', 8081
    )
    mocker.patch(
        'sethlans_worker_agent.config.UI_BIND_ADDRESS', '127.0.0.1'
    )
    mocker.patch(
        'sethlans_worker_agent.config.CERT_FINGERPRINT',
        'ab' * 32
    )


@pytest.fixture()
def sample_job_data():
    """Return a minimal job data dict for testing."""
    return {
        'id': 42,
        'name': 'Test Render',
        'render_device': 'CPU',
        'render_engine': 'CYCLES',
        'blender_version': '4.1.1',
        'start_frame': 1,
        'end_frame': 1,
        'output_file_pattern': 'output/test_####',
        'asset': {
            'blend_file': 'http://127.0.0.1:8080/media/assets/scene.blend'
        },
        'render_settings': {},
    }
