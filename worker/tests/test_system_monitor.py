# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

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


def test_send_heartbeat_includes_available_tools(mocker):
    """
    Tests that periodic heartbeats include the full system info
    (with available_tools) so the manager gets updated version lists.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', 123)
    mocker.patch.object(config, 'API_TOKEN', 'test-token-abc123')

    mock_sys_info = {
        'hostname': 'test-host',
        'ip_address': '192.168.1.1',
        'os': 'TestOS',
        'available_tools': {
            'blender': ['4.5.0', '4.5.1'],
            'gpu_devices': ['CUDA'],
            'gpu_devices_details': [{'name': 'Test GPU'}]
        }
    }
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value=mock_sys_info
    )

    mock_heartbeat = mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        return_value={'id': 123}
    )
    mocker.patch(
        'sethlans_worker_agent.version_sync.sync_versions',
        return_value=True
    )

    system_monitor.send_heartbeat()

    mock_heartbeat.assert_called_once()
    sent_payload = mock_heartbeat.call_args[0][0]
    assert 'available_tools' in sent_payload
    assert sent_payload['available_tools']['blender'] == ['4.5.0', '4.5.1']
