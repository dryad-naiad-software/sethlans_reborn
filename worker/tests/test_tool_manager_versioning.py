# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ToolManager thread-safe download tracking and version removal."""

import pytest

from sethlans_worker_agent.tool_manager import ToolManager


@pytest.fixture
def tm(tmp_path):
    """Create a ToolManager with a temporary directory."""
    manager = ToolManager()
    manager.tools_dir = tmp_path / "tools"
    manager.blender_dir = manager.tools_dir / "blender"
    manager.blender_dir.mkdir(parents=True)
    return manager


class TestDownloadingVersionsExclusion:
    """Tests for P2-F5: scan excludes mid-download versions."""

    def test_scan_excludes_downloading_version(self, tm, mocker):
        """A version in _downloading_versions is excluded from scan results."""
        # Create a valid-looking blender install directory.
        install_dir = tm.blender_dir / "blender-4.5.8-linux-x64"
        install_dir.mkdir()

        mocker.patch.object(
            tm, '_get_executable_path_for_install',
            return_value=str(install_dir / "blender")
        )
        mocker.patch('pathlib.Path.is_file', return_value=True)

        # Without downloading flag, version appears.
        found = tm.scan_for_local_blenders()
        assert any(b['version'] == '4.5.8' for b in found)

        # With downloading flag, version is excluded.
        with tm._download_lock:
            tm._downloading_versions.add('4.5.8')
        found = tm.scan_for_local_blenders()
        assert not any(b['version'] == '4.5.8' for b in found)

    def test_downloading_set_cleared_after_install(self, tm, mocker):
        """_downloading_versions is cleared even if download fails."""
        mocker.patch.object(tm, '_create_tools_directory_if_not_exists')
        mocker.patch.object(tm, '_resolve_version', return_value='4.5.8')
        mocker.patch.object(tm, 'get_blender_executable_path', return_value=None)
        mocker.patch.object(tm, '_download_and_install', side_effect=Exception("fail"))

        with pytest.raises(Exception):
            tm.ensure_blender_version_available('4.5.8')

        with tm._download_lock:
            assert '4.5.8' not in tm._downloading_versions

    def test_downloading_set_cleared_on_success(self, tm, mocker):
        """_downloading_versions is cleared after successful install."""
        mocker.patch.object(tm, '_create_tools_directory_if_not_exists')
        mocker.patch.object(tm, '_resolve_version', return_value='4.5.8')
        mocker.patch.object(
            tm, 'get_blender_executable_path',
            side_effect=[None, '/path/blender']
        )
        mocker.patch.object(tm, '_download_and_install', return_value='/path/blender')

        result = tm.ensure_blender_version_available('4.5.8')
        assert result == '/path/blender'

        with tm._download_lock:
            assert '4.5.8' not in tm._downloading_versions

    def test_idempotent_already_installed(self, tm, mocker):
        """ensure_blender_version_available is a no-op for installed versions (P2-NF2)."""
        mocker.patch.object(tm, '_create_tools_directory_if_not_exists')
        mocker.patch.object(tm, '_resolve_version', return_value='4.5.8')
        mocker.patch.object(
            tm, 'get_blender_executable_path',
            return_value='/path/blender'
        )
        mock_download = mocker.patch.object(tm, '_download_and_install')

        result = tm.ensure_blender_version_available('4.5.8')
        assert result == '/path/blender'
        mock_download.assert_not_called()


class TestRemoveBlenderVersion:
    """Tests for P4-F4: remove_blender_version."""

    def test_removes_existing_directory(self, tm, mocker):
        mocker.patch.object(tm, '_get_platform_identifier', return_value='linux-x64')
        install_dir = tm.blender_dir / "blender-4.2.19-linux-x64"
        install_dir.mkdir()
        (install_dir / "blender").touch()

        result = tm.remove_blender_version('4.2.19')
        assert result is True
        assert not install_dir.exists()

    def test_returns_false_for_missing_directory(self, tm, mocker):
        mocker.patch.object(tm, '_get_platform_identifier', return_value='linux-x64')
        result = tm.remove_blender_version('9.9.9')
        assert result is False

    def test_returns_false_when_no_platform(self, tm, mocker):
        mocker.patch.object(tm, '_get_platform_identifier', return_value=None)
        result = tm.remove_blender_version('4.2.19')
        assert result is False

    def test_rejects_path_traversal(self, tm, mocker):
        mocker.patch.object(tm, '_get_platform_identifier', return_value='linux-x64')
        # Version string that would resolve outside blender_dir
        result = tm.remove_blender_version('../../etc')
        assert result is False


class TestThreadSafetyContract:
    """Verify the _download_lock protects concurrent access."""

    def test_concurrent_scan_and_download_flag(self, tm, mocker):
        """scan_for_local_blenders does not report a version being downloaded."""
        mocker.patch.object(
            tm, '_get_executable_path_for_install',
            return_value=str(tm.blender_dir / "fake" / "blender")
        )
        mocker.patch('pathlib.Path.is_file', return_value=True)

        # Create two version directories.
        (tm.blender_dir / "blender-4.2.19-linux-x64").mkdir()
        (tm.blender_dir / "blender-4.5.8-linux-x64").mkdir()

        # Mark 4.5.8 as downloading.
        with tm._download_lock:
            tm._downloading_versions.add('4.5.8')

        found = tm.scan_for_local_blenders()
        versions = [b['version'] for b in found]
        assert '4.2.19' in versions
        assert '4.5.8' not in versions
