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

import json
import logging
import os

from sethlans_worker_agent import config
# Import the ToolManager instance and config for testing
from sethlans_worker_agent.tool_manager import tool_manager_instance

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
        'executable_path_in_folder': 'blender.exe'
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
    mock_full_exe_path_win = os.path.join(mock_managed_tools_dir, "blender", f"blender-{test_version}-windows-x64",
                                          "blender.exe")

    # Mock config.MANAGED_TOOLS_DIR
    mocker.patch.object(config, 'MANAGED_TOOLS_DIR', mock_managed_tools_dir)

    # Mock config.CURRENT_PLATFORM_BLENDER_DETAILS for a Windows x64 worker
    mock_config_platform_details = {
        'download_suffix': 'windows-x64',
        'executable_path_in_folder': 'blender.exe'
    }
    mocker.patch.object(config, 'CURRENT_PLATFORM_BLENDER_DETAILS', mock_config_platform_details)

    # Mock os.path.exists to simulate the executable NOT existing
    mock_os_path_exists = mocker.patch('os.path.exists', return_value=False)

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


# --- Test Case 4: test_load_blender_cache_success ---

def test_load_blender_cache_success(mocker):
    """
    Test Case: test_load_blender_cache_success
    Purpose: Verify that _load_blender_cache successfully loads valid JSON data
             from a mocked cache file and populates self.CACHED_BLENDER_DOWNLOAD_INFO.
    Asserts:
        - The method returns True on successful load.
        - The cache is correctly populated with the raw loaded data.
        - Correct os.path.exists and open() calls are made.
    """
    # Dummy cache file content that _load_blender_cache expects to read
    dummy_cache_content = [
        {"version": "4.2.12", "platform_suffix": "windows-x64", "url": "http://example.com/win.zip",
         "hash": "winhash_4.2.12"},
        {"version": "4.1.1", "platform_suffix": "linux-x64", "url": "http://example.com/linux.tar.xz",
         "hash": "linuxhash_4.1.1"},
        {"version": "4.0.0", "platform_suffix": "macos-arm64", "url": "http://example.com/mac.dmg",
         "hash": "machash_4.0.0"}
    ]

    # Mock config.BLENDER_VERSIONS_CACHE_FILE
    mocker.patch.object(config, 'BLENDER_VERSIONS_CACHE_FILE', "/mock/cache/blender_versions_cache.json")

    # Mock os.path.exists to simulate cache file existing
    mock_os_path_exists = mocker.patch('os.path.exists', return_value=True)

    # Mock built-in open() to return a mock file object with our dummy content
    mock_file = mocker.mock_open(read_data=json.dumps(dummy_cache_content))
    mocker.patch('builtins.open', mock_file)

    # Reset instance cache before test (Important for singletons in tests)
    # When _load_blender_cache is called by a test, tool_manager_instance is already created.
    # The _initialized flag ensures __init__ logic runs only once.
    # We just need to ensure CACHED_BLENDER_DOWNLOAD_INFO is clean for this specific test.
    tool_manager_instance.CACHED_BLENDER_DOWNLOAD_INFO = []

    # Run the method under test
    success = tool_manager_instance._load_blender_cache()

    # Assertions
    assert success is True, "_load_blender_cache should return True on successful load."

    # Assert open() was called correctly
    mock_file.assert_called_once_with(config.BLENDER_VERSIONS_CACHE_FILE, 'r')
    mock_file.return_value.read.assert_called_once()  # Ensure content was read

    # Assert os.path.exists was called
    mock_os_path_exists.assert_called_once_with(config.BLENDER_VERSIONS_CACHE_FILE)

    # Assert CACHED_BLENDER_DOWNLOAD_INFO is populated with the full raw data list
    assert tool_manager_instance.CACHED_BLENDER_DOWNLOAD_INFO == dummy_cache_content, \
        "CACHED_BLENDER_DOWNLOAD_INFO should be populated with the full raw data list."

    print(f"\n[UNIT TEST] _load_blender_cache_success passed.")


# --- Test Case 5: test_save_blender_cache_success ---

def test_save_blender_cache_success(mocker):
    """
    Test Case: test_save_blender_cache_success
    Purpose: Verify that _save_blender_cache successfully saves a list of dictionaries
             to a mocked cache file.
    Asserts:
        - The method returns True on successful save.
        - os.makedirs is called correctly.
        - The content written to the mock file matches the expected JSON string.
    """
    # Dummy data to be saved to the cache file
    dummy_data_to_save = [
        {"version": "4.2.12", "platform_suffix": "windows-x64", "url": "http://example.com/win.zip"},
        {"version": "4.1.1", "platform_suffix": "linux-x64", "url": "http://example.com/linux.tar.xz"}
    ]

    # Expected JSON string that would be written by json.dump
    expected_json_output = json.dumps(dummy_data_to_save, indent=4)  # REMOVED + "\n"

    # Mock config.BLENDER_VERSIONS_CACHE_FILE
    mock_cache_file_path = "/mock/cache/blender_versions_cache.json"
    mocker.patch.object(config, 'BLENDER_VERSIONS_CACHE_FILE', mock_cache_file_path)

    # Mock os.makedirs (ensure it's called, but doesn't do anything real)
    mock_os_makedirs = mocker.patch('os.makedirs')

    # Mock built-in open() for writing
    mock_file = mocker.mock_open()  # Creates a mock file object
    mocker.patch('builtins.open', mock_file)

    # Run the method under test
    success = tool_manager_instance._save_blender_cache(dummy_data_to_save)

    # Assertions
    assert success is True, "_save_blender_cache should return True on successful save."

    # Assert os.makedirs was called correctly
    mock_os_makedirs.assert_called_once_with(os.path.dirname(mock_cache_file_path), exist_ok=True)

    # Assert open() was called with correct path and mode
    mock_file.assert_called_once_with(mock_cache_file_path, 'w')

    # Assert the content written to the mock file handle's 'write' method
    # json.dump writes in chunks, so we collect all the write calls and join them
    written_content = "".join(call_arg.args[0] for call_arg in mock_file().write.call_args_list)
    assert written_content == expected_json_output, \
        "The content written to the cache file should match the expected JSON output."

    print(f"\n[UNIT TEST] _save_blender_cache_success passed.")


# --- Test Case 6: test_filter_and_process_major_minor_versions_success ---

def test_filter_and_process_major_minor_versions_success(mocker):
    """
    Test Case: test_filter_and_process_major_minor_versions_success
    Purpose: Verify that _filter_and_process_major_minor_versions correctly selects
             the latest patch version for each (major.minor, platform_suffix) group.
    Asserts:
        - The returned list contains the correct, filtered versions.
        - The count of filtered versions is correct.
    """
    # Raw data as _filter_and_process_major_minor_versions expects it:
    # Keyed by "Major.Minor", Value is a list of version info dicts for that series and all platforms
    raw_versions_data = {
        "4.0": [
            {"version": "4.0.0", "platform_suffix": "windows-x64", "url": "win400", "hash": "h1"},
            {"version": "4.0.1", "platform_suffix": "linux-x64", "url": "lin401", "hash": "h2"},
            {"version": "4.0.2", "platform_suffix": "windows-x64", "url": "win402", "hash": "h3"},
            # Latest win-x64 for 4.0
            {"version": "4.0.0", "platform_suffix": "macos-x64", "url": "mac400", "hash": "h4"},
            # Latest mac-x64 for 4.0
            {"version": "4.0.3", "platform_suffix": "linux-x64", "url": "lin403", "hash": "h5"},
            # Latest linux-x64 for 4.0
        ],
        "4.1": [
            {"version": "4.1.0", "platform_suffix": "windows-x64", "url": "win410", "hash": "h6"},
            # Latest win-x64 for 4.1
            {"version": "4.1.1", "platform_suffix": "macos-arm64", "url": "macarm411", "hash": "h7"},
            # Latest mac-arm64 for 4.1
        ],
        "3.6": [  # This should still be processed by _filter_and_process_major_minor_versions if passed,
            # as its responsibility is *not* to filter by major version < 4.x
            {"version": "3.6.0", "platform_suffix": "windows-x64", "url": "win360", "hash": "h8"}
            # Latest win-x64 for 3.6
        ]
    }

    # Expected output (list of latest versions per (major.minor, platform_suffix) )
    # This list should reflect what _filter_and_process_major_minor_versions *itself* produces,
    # including the 3.6.0 version, as the 4.x+ filter is done at a higher level (generate_and_cache_blender_download_info).
    expected_filtered_list = [
        {"version": "3.6.0", "platform_suffix": "windows-x64", "url": "win360", "hash": "h8"},  # Included for 3.6.0
        {"version": "4.0.0", "platform_suffix": "macos-x64", "url": "mac400", "hash": "h4"},
        {"version": "4.0.2", "platform_suffix": "windows-x64", "url": "win402", "hash": "h3"},
        {"version": "4.0.3", "platform_suffix": "linux-x64", "url": "lin403", "hash": "h5"},
        {"version": "4.1.0", "platform_suffix": "windows-x64", "url": "win410", "hash": "h6"},
        {"version": "4.1.1", "platform_suffix": "macos-arm64", "url": "macarm411", "hash": "h7"},
    ]

    # Run the method under test
    filtered_versions = tool_manager_instance._filter_and_process_major_minor_versions(raw_versions_data)

    # Sort both lists for consistent comparison, as order of appending might vary by dict iteration
    filtered_versions.sort(key=lambda x: (x['version'], x['platform_suffix']))
    expected_filtered_list.sort(key=lambda x: (x['version'], x['platform_suffix']))

    # Assertions
    assert len(filtered_versions) == len(expected_filtered_list), \
        f"Expected {len(expected_filtered_list)} filtered versions, but got {len(filtered_versions)}."
    assert filtered_versions == expected_filtered_list, \
        "The filtered versions list does not match the expected list."

    print(f"\n[UNIT TEST] _filter_and_process_major_minor_versions_success passed.")
