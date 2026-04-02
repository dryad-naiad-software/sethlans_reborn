# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker agent version_sync module.

Tests version parsing, download action computation, removable version
detection, and backoff tracking.
"""
import time

from sethlans_worker_agent import version_sync


# --- parse_required_versions ---

class TestParseRequiredVersions:

    def test_extracts_versions_list(self):
        resp = {
            'required_blender_versions': [
                {'series': '4.1', 'version': '4.1.1'},
                {'series': '4.2', 'version': '4.2.0'},
            ]
        }
        result = version_sync.parse_required_versions(resp)
        assert len(result) == 2
        assert result[0]['version'] == '4.1.1'

    def test_empty_response_returns_empty(self):
        assert version_sync.parse_required_versions(None) == []
        assert version_sync.parse_required_versions({}) == []

    def test_missing_key_returns_empty(self):
        assert version_sync.parse_required_versions({'other': 'data'}) == []

    def test_non_list_value_returns_empty(self):
        resp = {'required_blender_versions': 'not-a-list'}
        assert version_sync.parse_required_versions(resp) == []


# --- compute_download_actions ---

class TestComputeDownloadActions:

    def test_missing_version_needs_download(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        installed = {}
        actions = version_sync.compute_download_actions(required, installed)
        assert len(actions) == 1
        assert actions[0]['version'] == '4.1.1'

    def test_installed_version_needs_no_action(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        installed = {'4.1': '4.1.1'}
        actions = version_sync.compute_download_actions(required, installed)
        assert actions == []

    def test_older_installed_needs_upgrade(self):
        required = [{'series': '4.1', 'version': '4.1.2'}]
        installed = {'4.1': '4.1.1'}
        actions = version_sync.compute_download_actions(required, installed)
        assert len(actions) == 1
        assert actions[0]['version'] == '4.1.2'

    def test_newer_installed_needs_no_action(self):
        required = [{'series': '4.1', 'version': '4.1.0'}]
        installed = {'4.1': '4.1.2'}
        actions = version_sync.compute_download_actions(required, installed)
        assert actions == []

    def test_skips_entries_with_empty_series(self):
        required = [{'series': '', 'version': '4.1.0'}]
        installed = {}
        assert version_sync.compute_download_actions(required, installed) == []

    def test_skips_entries_with_empty_version(self):
        required = [{'series': '4.1', 'version': ''}]
        installed = {}
        assert version_sync.compute_download_actions(required, installed) == []

    def test_multiple_series_mixed(self):
        required = [
            {'series': '4.1', 'version': '4.1.1'},
            {'series': '4.2', 'version': '4.2.0'},
            {'series': '3.6', 'version': '3.6.5'},
        ]
        installed = {'4.1': '4.1.1', '3.6': '3.6.3'}
        actions = version_sync.compute_download_actions(required, installed)
        versions = [a['version'] for a in actions]
        assert '4.2.0' in versions  # missing
        assert '3.6.5' in versions  # upgrade
        assert '4.1.1' not in versions  # up to date


# --- compute_removable_versions ---

class TestComputeRemovableVersions:

    def test_unrequired_version_is_removable(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        installed = {'4.1': '4.1.1', '3.6': '3.6.5'}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, {}
        )
        assert '3.6.5' in removable
        assert deferred == []

    def test_required_version_is_not_removable(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        installed = {'4.1': '4.1.1'}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, {}
        )
        assert removable == []

    def test_in_use_version_is_deferred(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        installed = {'4.1': '4.1.1', '3.6': '3.6.5'}
        active_jobs = {42: {'blender_version': '3.6.5'}}
        removable, deferred = version_sync.compute_removable_versions(
            required, installed, active_jobs
        )
        assert removable == []
        assert '3.6.5' in deferred

    def test_empty_installed_returns_empty(self):
        required = [{'series': '4.1', 'version': '4.1.1'}]
        removable, deferred = version_sync.compute_removable_versions(
            required, {}, {}
        )
        assert removable == []
        assert deferred == []


# --- Backoff tracking ---

class TestBackoffTracking:

    def test_not_in_backoff_initially(self):
        assert version_sync._is_in_backoff('4.1.1') is False

    def test_record_failure_puts_version_in_backoff(self):
        version_sync._record_failure('4.1.1')
        assert version_sync._is_in_backoff('4.1.1') is True

    def test_clear_failure_removes_backoff(self):
        version_sync._record_failure('4.1.1')
        version_sync._clear_failure('4.1.1')
        assert version_sync._is_in_backoff('4.1.1') is False

    def test_backoff_delay_increases_with_attempts(self):
        version_sync._record_failure('4.1.1')
        with version_sync._failed_lock:
            info1 = version_sync._failed_versions['4.1.1'].copy()

        version_sync._record_failure('4.1.1')
        with version_sync._failed_lock:
            info2 = version_sync._failed_versions['4.1.1'].copy()

        # Second retry should be further in the future
        assert info2['next_retry'] > info1['next_retry']
        assert info2['attempts'] == 2

    def test_backoff_capped_at_max(self):
        # Record many failures to exceed the cap
        for _ in range(20):
            version_sync._record_failure('4.1.1')

        with version_sync._failed_lock:
            info = version_sync._failed_versions['4.1.1']
        # The delay should not exceed _BACKOFF_MAX
        max_delay = version_sync._BACKOFF_MAX
        # next_retry should be at most time.time() + _BACKOFF_MAX + margin
        assert info['next_retry'] <= time.time() + max_delay + 1

    def test_clear_nonexistent_version_is_noop(self):
        version_sync._clear_failure('nonexistent')
        assert version_sync._is_in_backoff('nonexistent') is False


# --- _version_tuple ---

class TestVersionTuple:

    def test_parses_three_part_version(self):
        assert version_sync._version_tuple('4.1.2') == (4, 1, 2)

    def test_parses_two_part_version(self):
        assert version_sync._version_tuple('4.1') == (4, 1)

    def test_ordering(self):
        assert version_sync._version_tuple('4.1.2') > version_sync._version_tuple('4.1.1')
        assert version_sync._version_tuple('4.2.0') > version_sync._version_tuple('4.1.9')


# --- queue / process pending ---

class TestPendingDownloads:

    def test_queue_and_process(self, mocker):
        mock_download = mocker.patch(
            'sethlans_worker_agent.version_sync.download_versions'
        )
        actions = [{'series': '4.1', 'version': '4.1.1'}]
        version_sync.queue_pending_downloads(actions)

        with version_sync._pending_lock:
            assert len(version_sync._pending_downloads) == 1

        version_sync.process_pending_downloads()
        mock_download.assert_called_once()

        with version_sync._pending_lock:
            assert len(version_sync._pending_downloads) == 0

    def test_queue_replaces_previous(self):
        version_sync.queue_pending_downloads(
            [{'version': '4.1.1'}]
        )
        version_sync.queue_pending_downloads(
            [{'version': '4.2.0'}]
        )
        with version_sync._pending_lock:
            assert len(version_sync._pending_downloads) == 1
            assert version_sync._pending_downloads[0]['version'] == '4.2.0'


class TestPendingRemovals:

    def test_queue_deduplicates(self):
        version_sync.queue_pending_removals(['4.1.1', '4.1.1'])
        with version_sync._pending_removals_lock:
            assert version_sync._pending_removals == ['4.1.1']

    def test_process_removes_when_not_in_use(self, mocker):
        mock_remove = mocker.patch(
            'sethlans_worker_agent.version_sync'
            '.tool_manager_instance.remove_blender_version'
        )
        version_sync.queue_pending_removals(['3.6.5'])
        version_sync.process_pending_removals({})
        mock_remove.assert_called_once_with('3.6.5')

    def test_process_defers_when_in_use(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.version_sync'
            '.tool_manager_instance.remove_blender_version'
        )
        version_sync.queue_pending_removals(['3.6.5'])
        active = {1: {'blender_version': '3.6.5'}}
        version_sync.process_pending_removals(active)
        # Should be re-queued, not removed
        with version_sync._pending_removals_lock:
            assert '3.6.5' in version_sync._pending_removals
