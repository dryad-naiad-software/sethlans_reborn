# sethlans_reborn/tests/unit/worker_agent/test_system_monitor.py
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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
import psutil
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
    system_monitor._cpu_thread_count_cache = None


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
    assert devices == ['CUDA', 'HIP', 'OPTIX']  # Should be sorted alphabetically


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
        blender_release_parser, 'get_blender_releases', return_value={'4.1.0': {}}  # No 4.5 versions
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
    and parses its JSON output from Blender's multi-line stdout.
    """
    # Arrange
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value='/mock/blender')
    mock_run = mocker.patch('subprocess.run')
    mock_gpu_data = [{"index": 0, "name": "NVIDIA GeForce RTX 4090", "type": "OPTIX", "id": "OPTIX_..._123"}]
    # Simulate the full, multi-line output from Blender
    mock_blender_output = f"Blender 4.5.1\n{json.dumps(mock_gpu_data)}\nBlender quit\n"
    mock_run.return_value = MagicMock(stdout=mock_blender_output, stderr="", returncode=0)
    # The filter function should be called with the raw data
    mock_filter = mocker.patch('sethlans_worker_agent.system_monitor._filter_preferred_gpus',
                               return_value=mock_gpu_data)

    # Act
    details = system_monitor.get_gpu_device_details()

    # Assert
    assert details == mock_gpu_data
    mock_run.assert_called_once()
    mock_filter.assert_called_once_with(mock_gpu_data)


def test_filter_preferred_gpus_with_complex_devices():
    """
    Tests the GPU filtering, grouping, and new preference logic (RTX->OptiX, GTX->CUDA).
    """
    # Arrange: 2 physical cards.
    # - A GTX 1070 Ti with PCI ID 0000:0a:00, offering both CUDA and OptiX.
    # - An RTX 3090 with PCI ID 0000:05:00, offering both CUDA and OptiX.
    raw_devices = [
        {'index': 0, 'name': 'NVIDIA GeForce GTX 1070 Ti', 'type': 'CUDA', 'id': 'CUDA_NVIDIA GeForce GTX 1070 Ti_0000:0a:00'},
        {'index': 1, 'name': 'NVIDIA GeForce RTX 3090', 'type': 'CUDA', 'id': 'CUDA_NVIDIA GeForce RTX 3090_0000:05:00'},
        {'index': 3, 'name': 'NVIDIA GeForce GTX 1070 Ti', 'type': 'OPTIX', 'id': 'CUDA_NVIDIA GeForce GTX 1070 Ti_0000:0a:00_OptiX'},
        {'index': 4, 'name': 'NVIDIA GeForce RTX 3090', 'type': 'OPTIX', 'id': 'CUDA_NVIDIA GeForce RTX 3090_0000:05:00_OptiX'},
    ]

    # Act
    filtered_list = system_monitor._filter_preferred_gpus(raw_devices)

    # Assert
    assert len(filtered_list) == 2, "Should correctly identify exactly 2 physical GPUs."

    rtx_device = next((d for d in filtered_list if 'RTX' in d['name']), None)
    gtx_device = next((d for d in filtered_list if 'GTX' in d['name']), None)

    assert rtx_device is not None, "RTX 3090 should be in the filtered list."
    assert gtx_device is not None, "GTX 1070 Ti should be in the filtered list."

    # Assert that the correct preference (RTX->OptiX, GTX->CUDA) was applied.
    assert rtx_device['type'] == 'OPTIX', "RTX card should have preferred OptiX."
    assert gtx_device['type'] == 'CUDA', "GTX card should have preferred CUDA."


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


def test_get_cpu_thread_count(mocker):
    """
    Tests that get_cpu_thread_count calls psutil.cpu_count and caches the result.
    """
    mock_cpu_count = mocker.patch('psutil.cpu_count', return_value=16)

    # First call
    result1 = system_monitor.get_cpu_thread_count()
    assert result1 == 16
    mock_cpu_count.assert_called_once()

    # Second call
    result2 = system_monitor.get_cpu_thread_count()
    assert result2 == 16
    # Assert it was NOT called again (cache was used)
    mock_cpu_count.assert_called_once()


def test_get_cpu_thread_count_handles_exception(mocker):
    """
    Tests that get_cpu_thread_count falls back to 1 if psutil fails.
    """
    mock_cpu_count = mocker.patch('psutil.cpu_count', side_effect=Exception("Test error"))

    result = system_monitor.get_cpu_thread_count()
    assert result == 1
    mock_cpu_count.assert_called_once()