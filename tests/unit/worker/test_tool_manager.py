# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the ToolManager class.

Tests version resolution, scan caching/TTL, reference counting,
and download exclusion filtering.
"""
import time

import pytest

from sethlans_worker_agent.tool_manager import ToolManager


@pytest.fixture()
def tm(mocker, tmp_path):
    """Create a ToolManager with a temporary tools directory."""
    mocker.patch(
        'sethlans_worker_agent.config.MANAGED_TOOLS_DIR', tmp_path
    )
    manager = ToolManager()
    manager.tools_dir = tmp_path
    manager.blender_dir = tmp_path / 'blender'
    manager.blender_dir.mkdir(parents=True, exist_ok=True)
    return manager


# --- Reference Counting ---

class TestReferenceCount:

    def test_acquire_and_release(self, tm):
        tm.acquire_version('4.1.1')
        assert tm.is_version_in_use('4.1.1') is True
        tm.release_version('4.1.1')
        assert tm.is_version_in_use('4.1.1') is False

    def test_multiple_acquires_require_matching_releases(self, tm):
        tm.acquire_version('4.1.1')
        tm.acquire_version('4.1.1')
        tm.release_version('4.1.1')
        assert tm.is_version_in_use('4.1.1') is True
        tm.release_version('4.1.1')
        assert tm.is_version_in_use('4.1.1') is False

    def test_release_without_acquire_is_safe(self, tm):
        tm.release_version('4.1.1')
        assert tm.is_version_in_use('4.1.1') is False

    def test_version_not_acquired_is_not_in_use(self, tm):
        assert tm.is_version_in_use('4.1.1') is False


# --- Scan Cache ---

class TestScanCache:

    def test_scan_uses_cache_within_ttl(self, tm, mocker):
        mock_exe = mocker.patch.object(
            tm, '_get_executable_path_for_install',
            return_value=str(tm.blender_dir / 'fake.exe')
        )
        # Pre-populate cache
        tm._scan_cache = [
            {'version': '4.1.1', 'platform': 'windows-x64'}
        ]
        tm._scan_cache_time = time.time()

        result = tm.scan_for_local_blenders()
        assert len(result) == 1
        assert result[0]['version'] == '4.1.1'
        # Should not scan filesystem since cache is fresh
        mock_exe.assert_not_called()

    def test_scan_refreshes_after_ttl(self, tm, mocker):
        tm._scan_cache = [
            {'version': '4.1.1', 'platform': 'windows-x64'}
        ]
        tm._scan_cache_time = time.time() - 60  # expired

        # No actual dirs in blender_dir, so scan returns empty
        result = tm.scan_for_local_blenders()
        assert result == []

    def test_scan_excludes_downloading_versions(self, tm):
        tm._scan_cache = [
            {'version': '4.1.1', 'platform': 'windows-x64'},
            {'version': '4.2.0', 'platform': 'windows-x64'},
        ]
        tm._scan_cache_time = time.time()
        tm._downloading_versions.add('4.2.0')

        result = tm.scan_for_local_blenders()
        versions = [b['version'] for b in result]
        assert '4.1.1' in versions
        assert '4.2.0' not in versions


# --- Version Resolution ---

class TestResolveVersion:

    def test_full_version_passes_through(self, tm):
        assert tm._resolve_version('4.1.1') == '4.1.1'

    def test_invalid_format_returns_none(self, tm):
        assert tm._resolve_version('abc') is None
        assert tm._resolve_version('4') is None
        assert tm._resolve_version('4.1.1.1') is None

    def test_partial_version_resolves_from_local(self, tm, mocker):
        mocker.patch.object(
            tm, 'scan_for_local_blenders',
            return_value=[
                {'version': '4.1.0', 'platform': 'win'},
                {'version': '4.1.2', 'platform': 'win'},
                {'version': '4.1.1', 'platform': 'win'},
            ]
        )
        result = tm._resolve_version('4.1')
        assert result == '4.1.2'  # highest patch

    def test_partial_version_falls_back_to_web(self, tm, mocker):
        mocker.patch.object(
            tm, 'scan_for_local_blenders', return_value=[]
        )
        mocker.patch.object(
            tm, '_get_blender_download_info',
            return_value={
                '4.1.0': {}, '4.1.3': {}, '4.1.1': {},
                '4.2.0': {},
            }
        )
        result = tm._resolve_version('4.1')
        assert result == '4.1.3'

    def test_partial_version_no_match_returns_none(self, tm, mocker):
        mocker.patch.object(
            tm, 'scan_for_local_blenders', return_value=[]
        )
        mocker.patch.object(
            tm, '_get_blender_download_info', return_value={}
        )
        assert tm._resolve_version('9.9') is None


# --- get_blender_executable_path ---

class TestGetBlenderExecutablePath:

    def test_returns_path_when_file_exists(self, tm, mocker, tmp_path):
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        install_dir = (
            tm.blender_dir / 'blender-4.1.1-windows-x64'
        )
        install_dir.mkdir(parents=True)
        exe = install_dir / 'blender.exe'
        exe.write_text('fake')

        mocker.patch.object(
            tm, '_get_executable_path_for_install',
            return_value=str(exe)
        )
        result = tm.get_blender_executable_path('4.1.1')
        assert result == str(exe)

    def test_returns_none_when_not_installed(self, tm, mocker):
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        mocker.patch.object(
            tm, '_get_executable_path_for_install',
            return_value='/nonexistent/blender.exe'
        )
        assert tm.get_blender_executable_path('4.1.1') is None


# --- remove_blender_version ---

class TestRemoveBlenderVersion:

    def test_refuses_when_in_use(self, tm, mocker):
        tm.acquire_version('4.1.1')
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        assert tm.remove_blender_version('4.1.1') is False

    def test_removes_directory(self, tm, mocker):
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        install_dir = tm.blender_dir / 'blender-4.1.1-windows-x64'
        install_dir.mkdir(parents=True)
        (install_dir / 'blender.exe').write_text('fake')

        assert tm.remove_blender_version('4.1.1') is True
        assert not install_dir.exists()

    def test_returns_false_when_dir_missing(self, tm, mocker):
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        assert tm.remove_blender_version('4.1.1') is False

    def test_returns_false_when_no_platform(self, tm, mocker):
        mocker.patch.object(
            tm, '_get_platform_identifier', return_value=None
        )
        assert tm.remove_blender_version('4.1.1') is False

    def test_invalidates_scan_cache_after_removal(self, tm, mocker):
        mocker.patch.object(
            tm, '_get_platform_identifier',
            return_value='windows-x64'
        )
        install_dir = tm.blender_dir / 'blender-4.1.1-windows-x64'
        install_dir.mkdir(parents=True)
        tm._scan_cache = [{'version': '4.1.1'}]

        tm.remove_blender_version('4.1.1')
        assert tm._scan_cache is None
