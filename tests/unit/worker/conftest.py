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
    WorkerCapacity instance, the drift check cadence timestamp, and the
    drift exit-code flag from the capacity package.
    """
    from sethlans_worker_agent import job_processor
    from sethlans_worker_agent.capacity import drift as drift_module
    with job_processor._active_jobs_lock:
        job_processor._active_jobs.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    job_processor._pause_event.clear()
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    drift_module._drift_detected_exit_code = None
    yield
    job_processor._capacity = None
    job_processor._last_drift_check_ts = 0.0
    drift_module._drift_detected_exit_code = None


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
        'sethlans_worker_agent.config.FORCE_CPU_ONLY', False
    )
    mocker.patch(
        'sethlans_worker_agent.config.FORCE_GPU_ONLY', False
    )
    mocker.patch(
        'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
    )
    mocker.patch(
        'sethlans_worker_agent.config.GPU_MODE', 'split'
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
