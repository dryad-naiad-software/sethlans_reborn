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
    assert mock_get_executable_path_helper.call_count == 4 # CORRECTED COUNT TO 4

    mock_get_executable_path_helper.assert_any_call("blender-3.6.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.0.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.1.0-windows-x64")
    mock_get_executable_path_helper.assert_any_call("blender-4.1.1-windows-x64")

    print(f"\n[UNIT TEST] scan_for_blender_versions_success_windows_x64 passed.")
