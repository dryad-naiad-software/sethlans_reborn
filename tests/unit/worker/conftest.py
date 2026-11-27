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
    from sethlans_worker_agent import blender_wait, version_sync
    blender_wait.reset()
    with version_sync._pending_lock:
        version_sync._pending_downloads.clear()
    with version_sync._pending_removals_lock:
        version_sync._pending_removals.clear()
    yield
    blender_wait.reset()
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
    for name in ('adapter_choice', 'adapter_log', 'reserve_card', 'ram_ledger', 'disk_ledger', 'startup_watch'):
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
def _reset_cards_cache():
    """Forget the settings page's cached card probe."""
    from sethlans_worker_agent.web_ui import cards_cache
    cards_cache.reset_for_tests()
    yield
    cards_cache.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_page_password_env(monkeypatch):
    """No ``SETHLANS_WORKER_UI_PASSWORD[_FILE]`` from the developer's shell (FR-DK-6).

    ``agent.main`` applies them to the real ``config.json``; tests that need them set them.
    """
    for name in ('SETHLANS_WORKER_UI_PASSWORD_FILE', 'SETHLANS_WORKER_UI_PASSWORD'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_page_auth():
    """Forget the page password's counted failures, result cache and warnings (FR-SEC-8)."""
    from sethlans_worker_agent.web_ui import auth
    auth.reset_for_tests()
    yield
    auth.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_card_probe_in_heartbeats(monkeypatch):
    """Registration and heartbeats build their ``cards`` report from an empty probe.

    ``cards.payload`` would otherwise run the real OS probe; the payload tests put back the real seam, taken
    at their import. Also forgets the manager-answer warnings and the request outcome kept in memory.
    """
    from sethlans_worker_agent.cards import choice, payload, remote
    from sethlans_worker_agent.cards.inventory import Inventory
    from sethlans_worker_agent.web_ui import cards_view
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(Inventory(), None))
    choice.reset_for_tests()
    remote.reset_for_tests()
    yield
    choice.reset_for_tests()
    remote.reset_for_tests()


@pytest.fixture()
def worker_home(tmp_path, monkeypatch):
    """A scratch worker folder: ``SETHLANS_WORKER_DATA_DIR`` and the per-user folders point under ``tmp_path``.

    ``config_store`` then reads and writes ``<worker_home>/config.json``
    only. The folder exists up front, so no write applies a folder ACL.
    """
    home = tmp_path / 'home'
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME'):
        folder = home / name.lower()
        folder.mkdir(parents=True)
        monkeypatch.setenv(name, str(folder))
    worker = tmp_path / 'worker'
    worker.mkdir()
    for name in ('SETHLANS_SETTINGS_DIR', 'SETHLANS_SETTINGS_FILE', 'SETHLANS_DATA_ROOT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(worker))
    return worker


@pytest.fixture()
def no_page_password(worker_home):
    """An isolated worker folder with no page password stored (FR-WK-4)."""
    return worker_home


@pytest.fixture()
def page_password(worker_home):
    """``test-token`` stored as the page password hash (real format, few rounds); returns the password."""
    from tests.unit.worker._page_password import fast_hash, write_hash
    write_hash(worker_home, fast_hash('test-token'))
    return 'test-token'


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
def bearer_header(page_password):
    """``Authorization: Bearer test-token`` for the WSGI control-plane tests.

    The hash of ``test-token`` is stored in an isolated ``config.json``, so
    ``auth.check_bearer`` runs its real path (FR-SEC-8).
    """
    return {'Authorization': f'Bearer {page_password}'}


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
