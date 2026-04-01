# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for version_sync module: heartbeat parsing, download actions,
patch upgrades, version cleanup, pending queues, and busy deferral."""

import pytest

from sethlans_worker_agent import version_sync
from sethlans_worker_agent.tool_manager import tool_manager_instance


@pytest.fixture(autouse=True)
def reset_pending_state():
    """Reset module-level pending queues between tests."""
    with version_sync._pending_lock:
        version_sync._pending_downloads.clear()
    with version_sync._pending_removals_lock:
        version_sync._pending_removals.clear()
    yield
    with version_sync._pending_lock:
        version_sync._pending_downloads.clear()
    with version_sync._pending_removals_lock:
        version_sync._pending_removals.clear()


class TestParseRequiredVersions:

    def test_parses_valid_response(self):
        response = {
            'id': 1,
            'required_blender_versions': [
                {'series': '4.2', 'version': '4.2.19'},
                {'series': '4.5', 'version': '4.5.8'},
            ]
        }
        result = version_sync.parse_required_versions(response)
        assert len(result) == 2
        assert result[0]['series'] == '4.2'

    def test_returns_empty_for_none(self):
        assert version_sync.parse_required_versions(None) == []

    def test_returns_empty_for_missing_key(self):
        assert version_sync.parse_required_versions({'id': 1}) == []

    def test_returns_empty_for_non_list(self):
        response = {'required_blender_versions': 'not-a-list'}
        assert version_sync.parse_required_versions(response) == []


class TestGetInstalledVersions:

    def test_maps_series_to_version(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'scan_for_local_blenders',
            return_value=[
                {'version': '4.2.19', 'platform': 'linux-x64'},
                {'version': '4.5.8', 'platform': 'linux-x64'},
            ]
        )
        installed = version_sync.get_installed_versions()
        assert installed == {'4.2': '4.2.19', '4.5': '4.5.8'}

    def test_keeps_highest_patch_per_series(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'scan_for_local_blenders',
            return_value=[
                {'version': '4.5.6', 'platform': 'linux-x64'},
                {'version': '4.5.8', 'platform': 'linux-x64'},
            ]
        )
        installed = version_sync.get_installed_versions()
        assert installed == {'4.5': '4.5.8'}


class TestComputeDownloadActions:

    def test_missing_version_needs_download(self):
        required = [{'series': '4.2', 'version': '4.2.19'}]
        installed = {}
        actions = version_sync.compute_download_actions(required, installed)
        assert len(actions) == 1
        assert actions[0]['version'] == '4.2.19'

    def test_up_to_date_version_no_action(self):
        required = [{'series': '4.5', 'version': '4.5.8'}]
        installed = {'4.5': '4.5.8'}
        actions = version_sync.compute_download_actions(required, installed)
        assert actions == []

    def test_newer_patch_triggers_upgrade(self):
        required = [{'series': '4.2', 'version': '4.2.20'}]
        installed = {'4.2': '4.2.19'}
        actions = version_sync.compute_download_actions(required, installed)
        assert len(actions) == 1
        assert actions[0]['version'] == '4.2.20'

    def test_older_patch_no_action(self):
        required = [{'series': '4.2', 'version': '4.2.18'}]
        installed = {'4.2': '4.2.19'}
        actions = version_sync.compute_download_actions(required, installed)
        assert actions == []

    def test_skips_empty_fields(self):
        required = [{'series': '', 'version': ''}]
        actions = version_sync.compute_download_actions(required, {})
        assert actions == []


class TestComputeRemovableVersions:

    def test_unrequired_version_is_removable(self):
        required = [{'series': '4.5', 'version': '4.5.8'}]
        installed = {'4.2': '4.2.19', '4.5': '4.5.8'}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, {}
        )
        assert '4.2.19' in removable
        assert deferred == []

    def test_in_use_version_is_deferred(self):
        required = [{'series': '4.5', 'version': '4.5.8'}]
        installed = {'4.2': '4.2.19', '4.5': '4.5.8'}
        active_jobs = {1: {'blender_version': '4.2.19'}}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, active_jobs
        )
        assert removable == []
        assert '4.2.19' in deferred

    def test_required_version_not_removed(self):
        required = [
            {'series': '4.2', 'version': '4.2.19'},
            {'series': '4.5', 'version': '4.5.8'},
        ]
        installed = {'4.2': '4.2.19', '4.5': '4.5.8'}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, {}
        )
        assert removable == []
        assert deferred == []


class TestDownloadVersions:

    def test_downloads_and_counts_success(self, mocker):
        mock_ensure = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value='/path/to/blender'
        )
        actions = [{'series': '4.2', 'version': '4.2.19'}]
        count = version_sync.download_versions(actions)
        assert count == 1
        mock_ensure.assert_called_once_with('4.2.19')

    def test_counts_failures(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value=None
        )
        actions = [{'series': '4.2', 'version': '4.2.19'}]
        count = version_sync.download_versions(actions)
        assert count == 0


class TestUpgradePatchVersions:

    def test_downloads_new_and_removes_old(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value='/path/blender'
        )
        mocker.patch.object(
            tool_manager_instance, 'get_blender_executable_path',
            return_value='/path/blender'
        )
        mock_remove = mocker.patch.object(
            tool_manager_instance, 'remove_blender_version',
            return_value=True
        )
        actions = [{'series': '4.2', 'version': '4.2.20'}]
        installed = {'4.2': '4.2.19'}

        count = version_sync.upgrade_patch_versions(actions, installed)
        assert count == 1
        mock_remove.assert_called_once_with('4.2.19')

    def test_keeps_old_on_failed_download(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value=None
        )
        mock_remove = mocker.patch.object(
            tool_manager_instance, 'remove_blender_version'
        )
        actions = [{'series': '4.2', 'version': '4.2.20'}]
        installed = {'4.2': '4.2.19'}

        count = version_sync.upgrade_patch_versions(actions, installed)
        assert count == 0
        mock_remove.assert_not_called()


class TestPendingDownloads:

    def test_queue_and_process(self, mocker):
        actions = [{'series': '4.2', 'version': '4.2.19'}]
        version_sync.queue_pending_downloads(actions)

        mock_ensure = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value='/path/blender'
        )
        version_sync.process_pending_downloads()
        mock_ensure.assert_called_once_with('4.2.19')

    def test_process_clears_queue(self, mocker):
        actions = [{'series': '4.2', 'version': '4.2.19'}]
        version_sync.queue_pending_downloads(actions)

        mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value='/path/blender'
        )
        version_sync.process_pending_downloads()

        # Second call should be a no-op.
        mock_ensure2 = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available'
        )
        version_sync.process_pending_downloads()
        mock_ensure2.assert_not_called()


class TestPendingRemovals:

    def test_deferred_removal_processed_when_idle(self, mocker):
        mock_remove = mocker.patch.object(
            tool_manager_instance, 'remove_blender_version',
            return_value=True
        )
        version_sync.queue_pending_removals(['4.2.19'])
        version_sync.process_pending_removals({})
        mock_remove.assert_called_once_with('4.2.19')

    def test_still_in_use_stays_deferred(self, mocker):
        mock_remove = mocker.patch.object(
            tool_manager_instance, 'remove_blender_version'
        )
        version_sync.queue_pending_removals(['4.2.19'])
        active_jobs = {1: {'blender_version': '4.2.19'}}
        version_sync.process_pending_removals(active_jobs)
        mock_remove.assert_not_called()

        # Should still be pending.
        with version_sync._pending_removals_lock:
            assert '4.2.19' in version_sync._pending_removals


class TestSyncVersions:

    def test_idle_downloads_missing(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'scan_for_local_blenders',
            side_effect=[
                [],  # first call in compute
                [],  # after downloads
                [{'version': '4.5.8', 'platform': 'linux-x64'}],  # final check
            ]
        )
        mock_ensure = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available',
            return_value='/path/blender'
        )

        response = {
            'required_blender_versions': [
                {'series': '4.5', 'version': '4.5.8'}
            ]
        }
        ready = version_sync.sync_versions(response, is_busy=False, active_jobs={})
        assert ready is True
        mock_ensure.assert_called_with('4.5.8')

    def test_busy_queues_downloads(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'scan_for_local_blenders',
            return_value=[]
        )
        mock_ensure = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available'
        )

        response = {
            'required_blender_versions': [
                {'series': '4.5', 'version': '4.5.8'}
            ]
        }
        ready = version_sync.sync_versions(response, is_busy=True, active_jobs={})
        assert ready is False
        mock_ensure.assert_not_called()

        # Check that downloads were queued.
        with version_sync._pending_lock:
            assert len(version_sync._pending_downloads) == 1

    def test_no_required_versions_returns_true(self, mocker):
        response = {'id': 1}
        ready = version_sync.sync_versions(response, is_busy=False, active_jobs={})
        assert ready is True

    def test_all_versions_installed_no_downloads(self, mocker):
        mocker.patch.object(
            tool_manager_instance, 'scan_for_local_blenders',
            return_value=[
                {'version': '4.2.19', 'platform': 'linux-x64'},
                {'version': '4.5.8', 'platform': 'linux-x64'},
            ]
        )
        mock_ensure = mocker.patch.object(
            tool_manager_instance, 'ensure_blender_version_available'
        )

        response = {
            'required_blender_versions': [
                {'series': '4.2', 'version': '4.2.19'},
                {'series': '4.5', 'version': '4.5.8'},
            ]
        }
        ready = version_sync.sync_versions(response, is_busy=False, active_jobs={})
        assert ready is True
        mock_ensure.assert_not_called()
