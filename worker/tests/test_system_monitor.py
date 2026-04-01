# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest
import subprocess
import json
from unittest.mock import MagicMock

# Import the module and dependencies to be tested/mocked
from sethlans_worker_agent import system_monitor, config, hardware_detection
from sethlans_worker_agent.tool_manager import tool_manager_instance


@pytest.fixture(autouse=True)
def reset_system_monitor_cache():
    """Resets the module-level cache before each test to ensure isolation."""
    hardware_detection._gpu_devices_cache = None
    hardware_detection._gpu_details_cache = None
    hardware_detection._cpu_thread_count_cache = None
    system_monitor._versions_ready = False
    system_monitor.WORKER_ID = None


def test_get_system_info(mocker):
    """Tests that system information is gathered correctly."""
    mocker.patch.object(hardware_detection, 'HOSTNAME', "test-host")
    mocker.patch.object(hardware_detection, 'IP_ADDRESS', "192.168.1.1")
    mocker.patch.object(hardware_detection, 'OS_INFO', "TestOS 11.0")
    mocker.patch.object(
        tool_manager_instance, 'scan_for_local_blenders',
        return_value=[{'version': '4.1.1'}]
    )
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.detect_gpu_devices',
        return_value=['CUDA', 'OPTIX']
    )
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=[{'name': 'RTX 4090'}]
    )

    info = system_monitor.get_system_info()

    assert info['available_tools']['blender'] == ["4.1.1"]
    assert info['available_tools']['gpu_devices'] == ['CUDA', 'OPTIX']
    assert info['available_tools']['gpu_devices_details'][0]['name'] == 'RTX 4090'


def test_detect_gpu_devices_success(mocker):
    """Tests that detect_gpu_devices extracts unique backends."""
    mock_details = [
        {"name": "NVIDIA GeForce RTX 4090", "type": "OPTIX"},
        {"name": "NVIDIA GeForce RTX 4090", "type": "CUDA"},
        {"name": "AMD Radeon PRO W7900", "type": "HIP"}
    ]
    mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=mock_details
    )

    devices = system_monitor.detect_gpu_devices()
    assert devices == ['CUDA', 'HIP', 'OPTIX']


def test_detect_gpu_devices_force_cpu_only_mode(mocker):
    """Ensures FORCE_CPU_ONLY skips GPU detection."""
    mocker.patch.object(config, 'FORCE_CPU_ONLY', True)
    mock_get_details = mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details'
    )

    devices = system_monitor.detect_gpu_devices()
    assert devices == []
    mock_get_details.assert_not_called()


def test_detect_gpu_devices_caches_result(mocker):
    """Ensures GPU detection results are cached after the first call."""
    mock_get_details = mocker.patch(
        'sethlans_worker_agent.hardware_detection.get_gpu_device_details',
        return_value=[]
    )

    system_monitor.detect_gpu_devices()
    mock_get_details.assert_called_once()

    mock_get_details.reset_mock()
    system_monitor.detect_gpu_devices()
    mock_get_details.assert_not_called()


def test_register_with_manager_downloads_required_versions(mocker):
    """Tests registration downloads versions from heartbeat response."""
    mocker.patch.object(system_monitor, 'WORKER_ID', None)
    mocker.patch.object(config, 'API_TOKEN', 'test-token-abc123')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={}
    )

    heartbeat_response = {
        'id': 123,
        'required_blender_versions': [
            {'series': '4.2', 'version': '4.2.19'},
            {'series': '4.5', 'version': '4.5.8'},
        ]
    }
    mock_heartbeat = mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        return_value=heartbeat_response
    )
    mock_sync = mocker.patch(
        'sethlans_worker_agent.version_sync.sync_versions',
        return_value=True
    )

    worker_id = system_monitor.register_with_manager()

    assert worker_id == 123
    mock_heartbeat.assert_called_once()
    mock_sync.assert_called_once_with(heartbeat_response, False, {})
    assert system_monitor._versions_ready is True


def test_register_with_manager_no_versions_ready(mocker):
    """Tests that registration succeeds but versions_ready is False."""
    mocker.patch.object(system_monitor, 'WORKER_ID', None)
    mocker.patch.object(config, 'API_TOKEN', 'test-token-abc123')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={}
    )

    heartbeat_response = {
        'id': 456,
        'required_blender_versions': [
            {'series': '4.5', 'version': '4.5.8'},
        ]
    }
    mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        return_value=heartbeat_response
    )
    mocker.patch(
        'sethlans_worker_agent.version_sync.sync_versions',
        return_value=False
    )

    worker_id = system_monitor.register_with_manager()

    assert worker_id == 456
    assert system_monitor._versions_ready is False


def test_register_with_manager_heartbeat_failure(mocker):
    """Tests registration fails when heartbeat returns None."""
    mocker.patch.object(config, 'API_TOKEN', 'test-token-abc123')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={}
    )
    mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        return_value=None
    )

    worker_id = system_monitor.register_with_manager()
    assert worker_id is None


def test_send_heartbeat_processes_versions(mocker):
    """Tests that send_heartbeat triggers version sync."""
    mocker.patch.object(system_monitor, 'WORKER_ID', 123)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={}
    )

    heartbeat_response = {
        'id': 123,
        'required_blender_versions': [
            {'series': '4.5', 'version': '4.5.8'},
        ]
    }
    mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        return_value=heartbeat_response
    )
    mock_sync = mocker.patch(
        'sethlans_worker_agent.version_sync.sync_versions',
        return_value=True
    )

    active_jobs = {'job1': {'blender_version': '4.2.19'}}
    system_monitor.send_heartbeat(is_busy=True, active_jobs=active_jobs)

    mock_sync.assert_called_once_with(heartbeat_response, True, active_jobs)
    assert system_monitor._versions_ready is True


def test_send_heartbeat_success(mocker):
    """Tests that a heartbeat is sent correctly when registered."""
    mocker.patch.object(system_monitor, 'WORKER_ID', 123)
    mocker.patch.object(config, 'API_TOKEN', 'test-token-abc123')
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={}
    )
    mock_post = mocker.patch('requests.post')
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None
    mock_post.return_value.json.return_value = {'id': 123}
    mocker.patch(
        'sethlans_worker_agent.version_sync.sync_versions',
        return_value=True
    )

    system_monitor.send_heartbeat()
    mock_post.assert_called_once()


def test_are_versions_ready_default():
    """Tests that are_versions_ready defaults to False."""
    assert system_monitor.are_versions_ready() is False


def test_get_gpu_device_details_uses_any_blender(mocker):
    """Tests GPU detection uses any available Blender, not a hardcoded version."""
    mocker.patch.object(
        tool_manager_instance, 'scan_for_local_blenders',
        return_value=[{'version': '4.2.19', 'platform': 'linux-x64'}]
    )
    mocker.patch.object(
        tool_manager_instance, 'get_blender_executable_path',
        return_value='/mock/blender'
    )
    mock_run = mocker.patch('subprocess.run')
    mock_gpu_data = [{"index": 0, "name": "RTX 4090", "type": "OPTIX", "id": "ID"}]
    mock_run.return_value = MagicMock(
        stdout=f"{json.dumps(mock_gpu_data)}\n",
        stderr="", returncode=0
    )
    mocker.patch(
        'sethlans_worker_agent.hardware_detection._filter_preferred_gpus',
        return_value=mock_gpu_data
    )

    details = system_monitor.get_gpu_device_details()
    assert details == mock_gpu_data


def test_get_gpu_device_details_no_blender_installed(mocker):
    """Tests GPU detection returns empty when no Blender is installed."""
    mocker.patch.object(
        tool_manager_instance, 'scan_for_local_blenders',
        return_value=[]
    )

    details = system_monitor.get_gpu_device_details()
    assert details == []


def test_filter_preferred_gpus_with_complex_devices():
    """Tests GPU filtering and preference logic."""
    gtx_cuda_id = 'CUDA_NVIDIA GeForce GTX 1070 Ti_0000:0a:00'
    rtx_cuda_id = 'CUDA_NVIDIA GeForce RTX 3090_0000:05:00'
    raw_devices = [
        {'index': 0, 'name': 'NVIDIA GeForce GTX 1070 Ti',
         'type': 'CUDA', 'id': gtx_cuda_id},
        {'index': 1, 'name': 'NVIDIA GeForce RTX 3090',
         'type': 'CUDA', 'id': rtx_cuda_id},
        {'index': 3, 'name': 'NVIDIA GeForce GTX 1070 Ti',
         'type': 'OPTIX', 'id': gtx_cuda_id + '_OptiX'},
        {'index': 4, 'name': 'NVIDIA GeForce RTX 3090',
         'type': 'OPTIX', 'id': rtx_cuda_id + '_OptiX'},
    ]

    filtered_list = system_monitor._filter_preferred_gpus(raw_devices)
    assert len(filtered_list) == 2

    rtx_device = next((d for d in filtered_list if 'RTX' in d['name']), None)
    gtx_device = next((d for d in filtered_list if 'GTX' in d['name']), None)

    assert rtx_device is not None
    assert gtx_device is not None
    assert rtx_device['type'] == 'OPTIX'
    assert gtx_device['type'] == 'CUDA'


def test_get_gpu_device_details_no_json_in_output(mocker):
    """Tests empty list when script produces no valid JSON."""
    mocker.patch.object(
        tool_manager_instance, 'scan_for_local_blenders',
        return_value=[{'version': '4.5.1', 'platform': 'linux-x64'}]
    )
    mocker.patch.object(
        tool_manager_instance, 'get_blender_executable_path',
        return_value='/mock/blender'
    )
    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value = MagicMock(
        stdout="Blender 4.5.1\nSome warning\nBlender quit\n",
        returncode=0
    )

    details = system_monitor.get_gpu_device_details()
    assert details == []


def test_get_gpu_device_details_failure(mocker):
    """Tests empty list when script execution fails."""
    mocker.patch.object(
        tool_manager_instance, 'scan_for_local_blenders',
        return_value=[{'version': '4.5.1', 'platform': 'linux-x64'}]
    )
    mocker.patch.object(
        tool_manager_instance, 'get_blender_executable_path',
        return_value='/mock/blender'
    )
    mock_run = mocker.patch('subprocess.run')
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")

    details = system_monitor.get_gpu_device_details()
    assert details == []


def test_get_cpu_thread_count(mocker):
    """Tests cpu_count detection and caching."""
    mock_cpu_count = mocker.patch('psutil.cpu_count', return_value=16)

    result1 = system_monitor.get_cpu_thread_count()
    assert result1 == 16
    mock_cpu_count.assert_called_once()

    result2 = system_monitor.get_cpu_thread_count()
    assert result2 == 16
    mock_cpu_count.assert_called_once()


def test_get_cpu_thread_count_handles_exception(mocker):
    """Tests fallback to 1 if psutil fails."""
    mocker.patch('psutil.cpu_count', side_effect=Exception("Test error"))

    result = system_monitor.get_cpu_thread_count()
    assert result == 1
