# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the platform_utils module.

Tests executable path construction. Platform ids are tested with
``shared.blender_sources.platforms`` (BS-SRC-6).
"""
from pathlib import Path

from sethlans_worker_agent.platform_utils import get_executable_path_for_blender


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
