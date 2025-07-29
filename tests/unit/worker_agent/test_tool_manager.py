# sethlans_reborn/tests/unit/worker_agent/test_tool_manager.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

import pytest
import re
from unittest.mock import MagicMock

# Import the ToolManager instance and its dependencies for mocking
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent import config
from sethlans_worker_agent.utils import file_operations, blender_release_parser


# --- Tests for _resolve_version ---

def test_resolve_version_handles_full_version_string():
    """If a full X.Y.Z version is provided, it should be returned as-is."""
    result = tool_manager_instance._resolve_version("4.1.1")
    assert result == "4.1.1"


def test_resolve_version_finds_latest_local_patch(mocker):
    """If a partial X.Y version is given, it finds the latest among local installs."""
    mock_local_blenders = [
        {'version': '4.5.0'},
        {'version': '4.5.2'},
        {'version': '4.1.1'},
        {'version': '4.5.1'},
    ]
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=mock_local_blenders)
    result = tool_manager_instance._resolve_version("4.5")
    assert result == "4.5.2"


def test_resolve_version_finds_latest_web_patch_if_not_local(mocker):
    """If no local patches are found, it checks the web for the latest patch."""
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    mock_web_releases = {"4.5.0": {}, "4.5.1": {}, "4.2.9": {}}
    mocker.patch.object(tool_manager_instance, '_get_blender_download_info', return_value=mock_web_releases)
    result = tool_manager_instance._resolve_version("4.5")
    assert result == "4.5.1"


def test_resolve_version_returns_none_for_invalid_format():
    """If the version string format is invalid, it returns None."""
    assert tool_manager_instance._resolve_version("4") is None
    assert tool_manager_instance._resolve_version("4.5.1.2") is None
    assert tool_manager_instance._resolve_version("latest") is None


# --- Tests for scan_for_local_blenders ---

def test_scan_for_local_blenders(mocker):
    """
    Tests that the scanner correctly identifies directories that represent
    valid, executable Blender installations.
    """
    mock_dir_valid = MagicMock()
    mock_dir_valid.name = "blender-4.1.1-windows-x64"
    mock_dir_valid.is_dir.return_value = True

    mock_dir_invalid_name = MagicMock()
    mock_dir_invalid_name.name = "not-blender-folder"
    mock_dir_invalid_name.is_dir.return_value = True

    mocker.patch('pathlib.Path.exists', return_value=True)
    mocker.patch('pathlib.Path.iterdir', return_value=[mock_dir_valid, mock_dir_invalid_name])
    mocker.patch('pathlib.Path.is_file', return_value=True)
    mocker.patch.object(tool_manager_instance, '_get_executable_path_for_install')

    found = tool_manager_instance.scan_for_local_blenders()

    assert len(found) == 1
    assert found[0]['version'] == "4.1.1"


# --- Tests for get_blender_executable_path ---

def test_get_blender_executable_path_success(mocker):
    """Tests that the correct executable path is returned when it exists."""
    mocker.patch.object(tool_manager_instance, '_get_platform_identifier', return_value="windows-x64")
    mocker.patch.object(tool_manager_instance, '_get_executable_path_for_install',
                        return_value="C:/expected/path/blender.exe")
    mocker.patch('pathlib.Path.is_file', return_value=True)

    result = tool_manager_instance.get_blender_executable_path("4.1.1")

    assert result == "C:/expected/path/blender.exe"


def test_get_blender_executable_path_not_found(mocker):
    """Tests that None is returned when the executable file does not exist."""
    mocker.patch.object(tool_manager_instance, '_get_platform_identifier', return_value="windows-x64")
    mocker.patch.object(tool_manager_instance, '_get_executable_path_for_install',
                        return_value="C:/expected/path/blender.exe")
    mocker.patch('pathlib.Path.is_file', return_value=False)

    result = tool_manager_instance.get_blender_executable_path("4.1.1")

    assert result is None


# --- Tests for _get_blender_download_info ---

def test_get_blender_download_info_from_cache(mocker):
    """Tests that download info is loaded from cache if the cache file exists."""
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='{"data": "from cache"}'))  # Mock open
    mock_load = mocker.patch.object(file_operations, 'load_json', return_value={"data": "from cache"})
    mock_parser = mocker.patch.object(blender_release_parser, 'get_blender_releases')

    result = tool_manager_instance._get_blender_download_info()

    assert result == {"data": "from cache"}
    mock_load.assert_called_once()
    mock_parser.assert_not_called()


def test_get_blender_download_info_from_parser(mocker):
    """Tests that download info is generated and saved when cache is absent."""
    mocker.patch('os.path.exists', return_value=False)
    mocker.patch('builtins.open', mocker.mock_open())  # Mock open for writing
    mock_dump = mocker.patch.object(file_operations, 'dump_json')
    mock_parser = mocker.patch.object(blender_release_parser, 'get_blender_releases',
                                      return_value={"data": "from parser"})

    result = tool_manager_instance._get_blender_download_info()

    assert result == {"data": "from parser"}
    mock_parser.assert_called_once()
    mock_dump.assert_called_once_with({"data": "from parser"}, mocker.ANY)


# --- Tests for ensure_blender_version_available ---

@pytest.fixture
def mock_ensure_deps(mocker):
    """Fixture to mock all dependencies for ensure_blender_version_available."""
    mocker.patch.object(tool_manager_instance, '_create_tools_directory_if_not_exists')
    mocker.patch.object(tool_manager_instance, '_resolve_version', side_effect=lambda v: v) # Pass through by default
    mock_get_exe = mocker.patch.object(tool_manager_instance, 'get_blender_executable_path')
    mock_get_info = mocker.patch.object(tool_manager_instance, '_get_blender_download_info')

    mock_download = mocker.patch.object(file_operations, 'download_file')
    mock_verify = mocker.patch.object(file_operations, 'verify_hash')
    mock_extract = mocker.patch.object(file_operations, 'extract_archive')
    mock_cleanup = mocker.patch.object(file_operations, 'cleanup_archive')

    mocker.patch.object(tool_manager_instance, '_get_platform_identifier', return_value="windows-x64")

    return {
        "get_exe": mock_get_exe, "get_info": mock_get_info, "download": mock_download,
        "verify": mock_verify, "extract": mock_extract, "cleanup": mock_cleanup
    }


def test_ensure_blender_already_exists(mock_ensure_deps):
    """Tests the case where the requested version is already installed."""
    mock_ensure_deps["get_exe"].return_value = "/path/to/blender.exe"

    result = tool_manager_instance.ensure_blender_version_available("4.1.1")

    assert result == "/path/to/blender.exe"
    mock_ensure_deps["get_info"].assert_not_called()


def test_ensure_blender_download_success(mock_ensure_deps, mocker):
    """Tests the full successful download, verify, and extract workflow."""
    mocker.patch('platform.system', return_value="Linux")
    mocker.patch('os.stat', return_value=MagicMock(st_mode=0o644))
    mock_chmod = mocker.patch('os.chmod')

    mocker.patch.object(tool_manager_instance, '_get_platform_identifier', return_value="linux-x64")
    mocker.patch.object(tool_manager_instance, '_resolve_version', return_value="4.1.1")

    mock_ensure_deps["get_exe"].side_effect = [None, "/path/to/blender.exe"]
    mock_ensure_deps["get_info"].return_value = {
        "4.1.1": {"linux-x64": {"url": "http://a.tar.xz", "sha256": "hash123"}}
    }
    mock_ensure_deps["download"].return_value = "/tmp/a.tar.xz"
    mock_ensure_deps["verify"].return_value = True

    result = tool_manager_instance.ensure_blender_version_available("4.1.1")

    assert result == "/path/to/blender.exe"
    mock_ensure_deps["download"].assert_called_once_with("http://a.tar.xz", mocker.ANY)
    mock_chmod.assert_called_once()
    mock_ensure_deps["cleanup"].assert_called_once()


def test_ensure_blender_version_not_in_releases(mock_ensure_deps, mocker):
    """Tests when the requested version doesn't exist in the release info."""
    mocker.patch.object(tool_manager_instance, '_resolve_version', return_value="9.9.9")
    mock_ensure_deps["get_exe"].return_value = None
    mock_ensure_deps["get_info"].return_value = {}

    result = tool_manager_instance.ensure_blender_version_available("9.9.9")

    assert result is None
    mock_ensure_deps["download"].assert_not_called()


def test_ensure_blender_hash_verification_fails(mock_ensure_deps, mocker):
    """Tests that a failed hash check aborts the process and cleans up."""
    mocker.patch.object(tool_manager_instance, '_resolve_version', return_value="4.1.1")
    mock_ensure_deps["get_exe"].return_value = None
    mock_ensure_deps["get_info"].return_value = {
        "4.1.1": {"windows-x64": {"url": "http://a.zip", "sha256": "hash123"}}
    }
    mock_ensure_deps["download"].return_value = "/tmp/a.zip"
    mock_ensure_deps["verify"].return_value = False
    mock_remove = mocker.patch('os.remove')

    result = tool_manager_instance.ensure_blender_version_available("4.1.1")

    assert result is None
    mock_remove.assert_called_once_with("/tmp/a.zip")
    mock_ensure_deps["extract"].assert_not_called()


@pytest.mark.parametrize("system, machine, expected_id", [
    ("Linux", "x86_64", "linux-x64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Windows", "AMD64", "windows-x64"),
    ("Darwin", "x86_64", "macos-x64"),
    ("Darwin", "arm64", "macos-arm64"),
    ("SunOS", "sparc", None),
])
def test_get_platform_identifier_parameterized(mocker, system, machine, expected_id):
    """
    Tests the _get_platform_identifier method for various OS and architecture combinations.
    """
    mocker.patch('platform.system', return_value=system)
    mocker.patch('platform.machine', return_value=machine)

    result = tool_manager_instance._get_platform_identifier()

    assert result == expected_id