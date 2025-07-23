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
import logging
import os
import platform
from unittest.mock import patch, MagicMock

# Import the ToolManager instance and config for testing
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent import config

logger = logging.getLogger(__name__)


# --- Test Case 1: scan_for_blender_versions_success_windows_x64 ---

def test_scan_for_blender_versions_success_windows_x64(mocker):
    """
    Test Case: scan_for_blender_versions_success_windows_x64
    Purpose: Verify that scan_for_blender_versions correctly identifies and filters
             locally installed Blender versions for a Windows x64 system.
    Asserts:
        - The returned dictionary contains the correct Blender versions for Windows x64.
        - Correct os and config calls are made.
    """
    # Mock config.MANAGED_TOOLS_DIR to point to a predictable test path
    mocker.patch.object(config, 'MANAGED_TOOLS_DIR', "/tmp/managed_tools_test")

    # Mock os.path.exists and os.path.isdir to simulate directory structure
    # and os.listdir to simulate folder contents
    mock_os_path_exists = mocker.patch('os.path.exists')
    mock_os_path_isdir = mocker.patch('os.path.isdir')
    mock_os_listdir = mocker.patch('os.listdir')

    # Mock config.CURRENT_PLATFORM_BLENDER_DETAILS for a Windows x64 worker
    mock_config_platform_details = {
        'download_suffix': 'windows-x64',
        'executable_path_in_folder': 'blender.exe'
    }
    mocker.patch.object(config, 'CURRENT_PLATFORM_BLENDER_DETAILS', mock_config_platform_details)

    # Mock _get_managed_blender_executable_full_path as it's an internal helper call
    # It should return a path if the executable is "found" in the simulated folder
    mock_get_executable_path_helper = mocker.patch.object(
        tool_manager_instance, '_get_managed_blender_executable_full_path', return_value="/mock/path/blender.exe"
    )

    # --- Simulate directory and file existence ---
    # managed_tools_test/blender/
    # ├── blender-3.6.0-windows-x64/ (should be skipped due to < 4.x in final output, but helper is called)
    # ├── blender-4.0.0-windows-x64/
    # ├── blender-4.1.0-windows-x64/
    # ├── blender-4.1.1-windows-x64/
    # ├── blender-4.1.1-linux-x64/ (should be skipped due to platform mismatch, helper not called)
    # └── other_folder/ (not blender- prefix, helper not called)

    mock_blender_path = os.path.join(config.MANAGED_TOOLS_DIR, 'blender')
    mock_os_path_exists.side_effect = lambda path: True if path == mock_blender_path else False
    mock_os_path_isdir.side_effect = lambda path: True if path.startswith(mock_blender_path) else False
    mock_os_listdir.return_value = [
        "blender-3.6.0-windows-x64",
        "blender-4.0.0-windows-x64",
        "blender-4.1.0-windows-x64",
        "blender-4.1.1-windows-x64",
        "blender-4.1.1-linux-x64",
        "other_folder"
    ]

    # Run the method under test
    available_tools = tool_manager_instance.scan_for_blender_versions()

    # Assertions
    # Only 4.x+ Windows x64 versions should be returned in final output
    assert available_tools == {'blender': ['4.0.0', '4.1.0', '4.1.1']}
    mock_os_listdir.assert_called_once_with(mock_blender_path)

    # Verify calls to the internal helper _get_managed_blender_executable_full_path
    # It is called for all folders matching 'blender-X.Y.Z-platform' AND the current OS suffix
    # So, 4.0.0, 4.1.0, 4.1.1, 3.6.0 (all windows-x64 matches)
    assert mock_get_executable_path_helper.call_count == 4

    mock_get_executable_path_helper.assert_any_call("blender-3.6.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.0.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.1.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.1.1-windows-x64")

    print(f"\n[UNIT TEST] scan_for_blender_versions_success_windows_x64 passed.")


# --- Test Case 2: get_blender_executable_path_windows_x64_success ---

def test_get_blender_executable_path_windows_x64_success(mocker):
    """
    Test Case: get_blender_executable_path_windows_x64_success
    Purpose: Verify that get_blender_executable_path returns the correct absolute path
             to the Blender executable for a Windows x64 system.
    Asserts:
        - The returned path is correct.
        - os.path.exists is called to check for the executable.
    """
    # Define dummy values
    test_version = "4.2.12"
    mock_managed_tools_dir = "/mock/managed_tools_root"
    mock_expected_exe_subpath = "blender.exe"
    mock_full_exe_path = os.path.join(mock_managed_tools_dir, "blender", f"blender-{test_version}-windows-x64",
                                      mock_expected_exe_subpath)

    # Mock config.MANAGED_TOOLS_DIR
    mocker.patch.object(config, 'MANAGED_TOOLS_DIR', mock_managed_tools_dir)

    # Mock config.CURRENT_PLATFORM_BLENDER_DETAILS for a Windows x64 worker
    mock_config_platform_details = {
        'download_suffix': 'windows-x64',
        'executable_path_in_folder': mock_expected_exe_subpath
    }
    mocker.patch.object(config, 'CURRENT_PLATFORM_BLENDER_DETAILS', mock_config_platform_details)

    # Mock os.path.exists to simulate the executable existing
    mock_os_path_exists = mocker.patch('os.path.exists', return_value=True)

    # Run the method under test
    returned_path = tool_manager_instance.get_blender_executable_path(test_version)

    # Assertions
    assert returned_path == mock_full_exe_path, \
        f"Expected path {mock_full_exe_path}, but got {returned_path}."

    # Assert os.path.exists was called once with the correct full path
    mock_os_path_exists.assert_called_once_with(mock_full_exe_path)

    print(f"\n[UNIT TEST] get_blender_executable_path_windows_x64_success passed.")


# --- Test Case 3: test_get_blender_executable_path_not_found ---

def test_get_blender_executable_path_not_found(mocker):
    """
    Test Case: test_get_blender_executable_path_not_found
    Purpose: Verify that get_blender_executable_path returns None
             if the Blender executable file does not exist.
    Asserts:
        - The returned path is None.
        - os.path.exists is called exactly once with the expected path for the current OS.
    """
    # Define dummy values
    test_version = "4.2.12"
    mock_managed_tools_dir = "/mock/managed_tools_root"
    # Note: mock_expected_exe_subpath is 'blender.exe' from current_platform_details mock
    mock_full_exe_path_win = os.path.join(mock_managed_tools_dir, "blender", f"blender-{test_version}-windows-x64",
                                          "blender.exe")
    # mock_full_exe_path_linux = os.path.join(mock_managed_tools_dir, "blender", f"blender-{test_version}-windows-x64", "blender")
    # mock_full_exe_path_mac = os.path.join(mock_managed_tools_dir, "blender", f"blender-{test_version}-windows-x64", "blender.app", "Contents", "MacOS", "blender")

    # Mock config.MANAGED_TOOLS_DIR
    mocker.patch.object(config, 'MANAGED_TOOLS_DIR', mock_managed_tools_dir)

    # Mock config.CURRENT_PLATFORM_BLENDER_DETAILS for a Windows x64 worker
    mock_config_platform_details = {
        'download_suffix': 'windows-x64',
        'executable_path_in_folder': 'blender.exe'  # It will try this first
    }
    mocker.patch.object(config, 'CURRENT_PLATFORM_BLENDER_DETAILS', mock_config_platform_details)

    # Mock os.path.exists to simulate the executable NOT existing
    mock_os_path_exists = mocker.patch('os.path.exists', return_value=False)  # Will always return False

    # Run the method under test
    returned_path = tool_manager_instance.get_blender_executable_path(test_version)

    # Assertions
    assert returned_path is None, \
        "Expected path to be None when executable does not exist."

    # Assert os.path.exists was called exactly once with the correct expected path for Windows
    assert mock_os_path_exists.call_count == 1, \
        f"Expected os.path.exists to be called 1 time, but got {mock_os_path_exists.call_count}."
    mock_os_path_exists.assert_called_once_with(mock_full_exe_path_win)

    print(f"\n[UNIT TEST] get_blender_executable_path_not_found passed.")