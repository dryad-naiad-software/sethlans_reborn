# sethlans_reborn/tests/unit/worker_agent/test_system_monitor.py
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
import requests
import subprocess
import json
from unittest.mock import MagicMock

# Import the module and dependencies to be tested/mocked
from sethlans_worker_agent import system_monitor, config
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import blender_release_parser


@pytest.fixture(autouse=True)
def reset_system_monitor_cache():
    """Resets the module-level cache before each test to ensure isolation."""
    system_monitor._gpu_devices_cache = None
    system_monitor._gpu_details_cache = None


def test_get_system_info(mocker):
    """
    Tests that system information, including GPU devices, is gathered correctly.
    """
    mocker.patch.object(system_monitor, 'HOSTNAME', "test-host")
    mocker.patch.object(system_monitor, 'IP_ADDRESS', "192.168.1.1")
    mocker.patch.object(system_monitor, 'OS_INFO', "TestOS 11.0")
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[{'version': '4.1.1'}])
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA', 'OPTIX'])
    mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[{'name': 'RTX 4090'}])


    info = system_monitor.get_system_info()

    assert info['available_tools']['blender'] == ["4.1.1"]
    assert info['available_tools']['gpu_devices'] == ['CUDA', 'OPTIX']
    assert info['available_tools']['gpu_devices_details'][0]['name'] == 'RTX 4090'


def test_detect_gpu_devices_success(mocker):
    """
    Tests that detect_gpu_devices correctly extracts unique backends from the
    detailed device list.
    """
    # Arrange
    mock_details = [
        {"name": "NVIDIA GeForce RTX 4090", "type": "OPTIX"},
        {"name": "NVIDIA GeForce RTX 4090", "type": "CUDA"},
        {"name": "AMD Radeon PRO W7900", "type": "HIP"}
    ]
    mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=mock_details)

    # Act
    devices = system_monitor.detect_gpu_devices()

    # Assert
    assert devices == ['CUDA', 'HIP', 'OPTIX'] # Should be sorted alphabetically


def test_detect_gpu_devices_force_cpu_only_mode(mocker):
    """
    Ensures that when FORCE_CPU_ONLY is true, GPU detection is skipped and an empty list is returned.
    """
    # Arrange: Force the config setting to True
    mocker.patch.object(config, 'FORCE_CPU_ONLY', True)
    mock_get_details = mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details')

    # Act
    devices = system_monitor.detect_gpu_devices()

    # Assert
    assert devices == []
    mock_get_details.assert_not_called()


def test_detect_gpu_devices_caches_result(mocker):
    """Ensures that GPU detection results are cached after the first call."""
    # Arrange
    mock_get_details = mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[])

    # Act 1: First call should trigger the detailed check
    system_monitor.detect_gpu_devices()

    # Assert 1
    mock_get_details.assert_called_once()

    # Reset mock to check for subsequent calls
    mock_get_details.reset_mock()

    # Act 2: Second call should use the cache
    system_monitor.detect_gpu_devices()

    # Assert 2: The detailed check was NOT called again
    mock_get_details.assert_not_called()


@pytest.mark.parametrize("versions, series, expected", [
    (['4.5.0', '4.5.1', '4.1.0', '4.5.2'], "4.5", "4.5.2"),
    (['4.5.10', '4.5.2', '4.5.9'], "4.5", "4.5.10"),
    (['4.1.0', '5.0.0'], "4.5", None),
    ([], "4.5", None)
])
def test_find_latest_lts_patch(versions, series, expected):
    """Tests the helper function that finds the latest LTS patch version."""
    result = system_monitor._find_latest_lts_patch(versions, series)
    assert result == expected


def test_register_with_manager_lts_success(mocker):
    """
    Tests the full successful registration flow, including finding and downloading the LTS Blender.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', None)
    mocker.patch.object(
        blender_release_parser, 'get_blender_releases', return_value={'4.5.0': {}, '4.5.1': {}}
    )
    mock_ensure_blender = mocker.patch.object(
        tool_manager_instance, 'ensure_blender_version_available', return_value="/path/to/blender-4.5.1"
    )
    mocker.patch('sethlans_worker_agent.system_monitor.get_system_info', return_value={})
    mock_post = mocker.patch('requests.post')
    mock_post.return_value.json.return_value = {'id': 123}

    worker_id = system_monitor.register_with_manager()

    assert worker_id == 123
    mock_ensure_blender.assert_called_once_with('4.5.1')
    mock_post.assert_called_once()


def test_register_with_manager_lts_not_found(mocker):
    """
    Tests that registration fails if no suitable LTS version is found.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', None)
    mocker.patch.object(
        blender_release_parser, 'get_blender_releases', return_value={'4.1.0': {}} # No 4.5 versions
    )
    mock_ensure_blender = mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available')

    worker_id = system_monitor.register_with_manager()

    assert worker_id is None
    mock_ensure_blender.assert_not_called()


def test_send_heartbeat_success(mocker):
    """
    Tests that a heartbeat is sent correctly when the worker is registered.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', 123)
    mock_post = mocker.patch('requests.post')

    system_monitor.send_heartbeat()

    mock_post.assert_called_once()


def test_get_gpu_device_details_success(mocker):
    """
    Tests that get_gpu_device_details successfully calls the detection script
    and parses its JSON output from Blender's multi-line stdout, ignoring stderr.
    """
    # Arrange
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value='/mock/blender')
    mock_run = mocker.patch('subprocess.run')
    mock_gpu_data = [{"name": "NVIDIA GeForce RTX 4090", "type": "OPTIX"}]
    # Simulate the full, multi-line output from Blender, including the new logging to stderr
    mock_blender_stdout = f"Blender 4.5.1\n{json.dumps(mock_gpu_data)}\nBlender quit\n"
    mock_blender_stderr = "[2025-08-05 04:53:27] [INFO] [detect_gpus] Starting GPU detection inside Blender...\n"
    mock_run.return_value = MagicMock(stdout=mock_blender_stdout, stderr=mock_blender_stderr, returncode=0)

    # Act
    details = system_monitor.get_gpu_device_details()

    # Assert
    assert details == mock_gpu_data
    mock_run.assert_called_once()


def test_get_gpu_device_details_no_json_in_output(mocker):
    """
    Tests that an empty list is returned if the script runs but produces
    no valid JSON line in its output.
    """
    # Arrange
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value='/mock/blender')
    mock_run = mocker.patch('subprocess.run')
    mock_blender_output = "Blender 4.5.1\nSome other warning or message\nBlender quit\n"
    mock_run.return_value = MagicMock(stdout=mock_blender_output, returncode=0)

    # Act
    details = system_monitor.get_gpu_device_details()

    # Assert
    assert details == []


def test_get_gpu_device_details_failure(mocker):
    """
    Tests that get_gpu_device_details returns an empty list if the script
    execution fails.
    """
    # Arrange
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value='/mock/blender')
    mock_run = mocker.patch('subprocess.run')
    # Simulate a script failure
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")

    # Act
    details = system_monitor.get_gpu_device_details()

    # Assert
    assert details == []