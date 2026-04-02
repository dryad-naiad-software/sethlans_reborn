# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the platform_utils module.

Tests platform identifier detection and executable path construction.
"""
from pathlib import Path

from sethlans_worker_agent.platform_utils import (
    get_platform_identifier,
    get_executable_path_for_blender,
)


class TestGetPlatformIdentifier:

    def test_windows_amd64(self, mocker):
        mocker.patch('platform.system', return_value='Windows')
        mocker.patch('platform.machine', return_value='AMD64')
        assert get_platform_identifier() == 'windows-x64'

    def test_windows_x86_64(self, mocker):
        mocker.patch('platform.system', return_value='Windows')
        mocker.patch('platform.machine', return_value='x86_64')
        assert get_platform_identifier() == 'windows-x64'

    def test_linux_x86_64(self, mocker):
        mocker.patch('platform.system', return_value='Linux')
        mocker.patch('platform.machine', return_value='x86_64')
        assert get_platform_identifier() == 'linux-x64'

    def test_linux_aarch64(self, mocker):
        mocker.patch('platform.system', return_value='Linux')
        mocker.patch('platform.machine', return_value='aarch64')
        assert get_platform_identifier() == 'linux-arm64'

    def test_linux_unsupported_arch(self, mocker):
        mocker.patch('platform.system', return_value='Linux')
        mocker.patch('platform.machine', return_value='i686')
        assert get_platform_identifier() is None

    def test_macos_x86_64(self, mocker):
        mocker.patch('platform.system', return_value='Darwin')
        mocker.patch('platform.machine', return_value='x86_64')
        assert get_platform_identifier() == 'macos-x64'

    def test_macos_arm64(self, mocker):
        mocker.patch('platform.system', return_value='Darwin')
        mocker.patch('platform.machine', return_value='arm64')
        assert get_platform_identifier() == 'macos-arm64'

    def test_unsupported_os(self, mocker):
        mocker.patch('platform.system', return_value='FreeBSD')
        mocker.patch('platform.machine', return_value='x86_64')
        assert get_platform_identifier() is None


class TestGetExecutablePathForBlender:

    def test_windows_path(self, mocker, tmp_path):
        mocker.patch('platform.system', return_value='Windows')
        result = get_executable_path_for_blender(
            tmp_path, 'blender-4.1.1-windows-x64'
        )
        expected = tmp_path / 'blender-4.1.1-windows-x64' / 'blender.exe'
        assert result == expected

    def test_linux_path(self, mocker, tmp_path):
        mocker.patch('platform.system', return_value='Linux')
        result = get_executable_path_for_blender(
            tmp_path, 'blender-4.1.1-linux-x64'
        )
        expected = tmp_path / 'blender-4.1.1-linux-x64' / 'blender'
        assert result == expected

    def test_macos_path(self, mocker, tmp_path):
        mocker.patch('platform.system', return_value='Darwin')
        result = get_executable_path_for_blender(
            tmp_path, 'blender-4.1.1-macos-arm64'
        )
        expected = (
            tmp_path / 'blender-4.1.1-macos-arm64'
            / 'Blender.app' / 'Contents' / 'MacOS' / 'Blender'
        )
        assert result == expected

    def test_accepts_string_base_dir(self, mocker, tmp_path):
        mocker.patch('platform.system', return_value='Linux')
        result = get_executable_path_for_blender(
            str(tmp_path), 'blender-4.1.1-linux-x64'
        )
        assert isinstance(result, Path)
