# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the version_sync module.

Exercises sync_versions end-to-end flow including download triggering,
busy queuing, pending download processing, backoff tracking, and
patch version upgrades.
"""

import time

from sethlans_worker_agent import version_sync
from sethlans_worker_agent.version_sync import (
    sync_versions,
    process_pending_downloads,
    queue_pending_downloads,
    _record_failure,
    _clear_failure,
    _is_in_backoff,
    _failed_versions,
    _pending_downloads,
    _pending_lock,
)


# -- sync_versions triggers downloads for missing versions --

def test_sync_downloads_missing_versions(mocker):
    """sync_versions triggers download for versions not installed."""
    heartbeat = {
        'required_blender_versions': [
            {'series': '4.1', 'version': '4.1.1'},
            {'series': '4.2', 'version': '4.2.0'},
        ],
    }

    # Nothing installed
    mocker.patch.object(
        version_sync.tool_manager_instance,
        'scan_for_local_blenders',
        return_value=[],
    )

    download_mock = mocker.patch(
        'sethlans_worker_agent.version_sync.download_versions',
        return_value=2,
    )
    mocker.patch(
        'sethlans_worker_agent.version_sync.upgrade_patch_versions',
        return_value=0,
    )

    sync_versions(heartbeat, is_busy=False, active_jobs={})

    download_mock.assert_called_once()
    actions = download_mock.call_args[0][0]
    versions = [a['version'] for a in actions]
    assert '4.1.1' in versions
    assert '4.2.0' in versions


# -- sync_versions when busy queues downloads --

def test_sync_queues_downloads_when_busy(mocker):
    """When worker is busy, downloads are queued instead of executed."""
    heartbeat = {
        'required_blender_versions': [
            {'series': '4.3', 'version': '4.3.0'},
        ],
    }

    mocker.patch.object(
        version_sync.tool_manager_instance,
        'scan_for_local_blenders',
        return_value=[],
    )

    download_mock = mocker.patch(
        'sethlans_worker_agent.version_sync.download_versions',
    )
    queue_mock = mocker.patch(
        'sethlans_worker_agent.version_sync.queue_pending_downloads',
    )

    sync_versions(heartbeat, is_busy=True, active_jobs={})

    # download_versions should NOT be called
    download_mock.assert_not_called()
    # queue_pending_downloads should be called with the actions
    queue_mock.assert_called_once()


# -- process_pending_downloads executes queued downloads --

def test_process_pending_downloads_executes_queued(mocker):
    """Queued downloads are processed when worker becomes idle."""
    actions = [
        {'series': '4.3', 'version': '4.3.0'},
        {'series': '4.4', 'version': '4.4.0'},
    ]

    queue_pending_downloads(actions)

    # Verify they are queued
    with _pending_lock:
        assert len(_pending_downloads) == 2

    download_mock = mocker.patch(
        'sethlans_worker_agent.version_sync.download_versions',
    )

    process_pending_downloads()

    download_mock.assert_called_once()
    called_actions = download_mock.call_args[0][0]
    assert len(called_actions) == 2

    # Queue should be empty now
    with _pending_lock:
        assert len(_pending_downloads) == 0


def test_process_pending_no_op_when_empty(mocker):
    """process_pending_downloads is a no-op when queue is empty."""
    download_mock = mocker.patch(
        'sethlans_worker_agent.version_sync.download_versions',
    )

    process_pending_downloads()
    download_mock.assert_not_called()


# -- Backoff tracking --

def test_backoff_recorded_on_failure():
    """Failed download records backoff with exponential delay."""
    version = '4.5.0'

    assert _is_in_backoff(version) is False

    _record_failure(version)
    assert _is_in_backoff(version) is True

    # Check backoff info
    info = _failed_versions[version]
    assert info['attempts'] == 1
    assert info['next_retry'] > time.time()


def test_backoff_increases_exponentially():
    """Successive failures increase backoff delay."""
    version = '4.5.0'

    _record_failure(version)
    first_retry = _failed_versions[version]['next_retry']

    _record_failure(version)
    second_retry = _failed_versions[version]['next_retry']

    # Second retry should be later than first
    assert second_retry > first_retry
    assert _failed_versions[version]['attempts'] == 2


def test_backoff_cleared_on_success():
    """Successful download clears backoff tracking."""
    version = '4.5.0'

    _record_failure(version)
    assert _is_in_backoff(version) is True

    _clear_failure(version)
    assert _is_in_backoff(version) is False
    assert version not in _failed_versions


def test_backoff_skips_download(mocker):
    """Version in backoff is skipped during download_versions."""
    version = '4.5.0'
    _record_failure(version)

    ensure_mock = mocker.patch.object(
        version_sync.tool_manager_instance,
        'ensure_blender_version_available',
    )

    actions = [{'series': '4.5', 'version': version}]
    version_sync.download_versions(actions)

    ensure_mock.assert_not_called()


# -- Upgrade patch version --

def test_upgrade_removes_old_version(mocker):
    """Upgrade installs new patch and removes old version."""
    installed = {'4.1': '4.1.0'}
    actions = [{'series': '4.1', 'version': '4.1.2'}]

    mocker.patch.object(
        version_sync.tool_manager_instance,
        'ensure_blender_version_available',
        return_value='/fake/blender-4.1.2',
    )
    mocker.patch.object(
        version_sync.tool_manager_instance,
        'get_blender_executable_path',
        return_value='/fake/blender-4.1.2/blender',
    )
    remove_mock = mocker.patch.object(
        version_sync.tool_manager_instance,
        'remove_blender_version',
    )

    count = version_sync.upgrade_patch_versions(actions, installed)

    assert count == 1
    remove_mock.assert_called_once_with('4.1.0')


def test_upgrade_no_op_when_same_version(mocker):
    """Upgrade is skipped when installed version matches required."""
    installed = {'4.1': '4.1.1'}
    actions = [{'series': '4.1', 'version': '4.1.1'}]

    ensure_mock = mocker.patch.object(
        version_sync.tool_manager_instance,
        'ensure_blender_version_available',
    )

    count = version_sync.upgrade_patch_versions(actions, installed)

    assert count == 0
    ensure_mock.assert_not_called()
