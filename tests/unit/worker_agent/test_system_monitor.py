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
from unittest.mock import MagicMock

# Import the module and dependencies to be tested/mocked
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance


def test_get_system_info(mocker):
    """
    Tests that system information, including GPU devices, is gathered correctly.
    """
    # Mock the module-level constants and dependent functions
    mocker.patch.object(system_monitor, 'HOSTNAME', "test-host")
    mocker.patch.object(system_monitor, 'IP_ADDRESS', "192.168.1.1")
    mocker.patch.object(system_monitor, 'OS_INFO', "TestOS 11.0")
    mocker.patch.object(
        tool_manager_instance,
        'scan_for_local_blenders',
        return_value=[{'version': '4.1.1'}]
    )
    # Mock the new GPU detection function
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=['CUDA', 'OPTIX'])

    info = system_monitor.get_system_info()

    # Assert the output is correctly structured
    assert info['hostname'] == "test-host"
    assert info['os'] == "TestOS 11.0"
    assert info['available_tools']['blender'] == ["4.1.1"]
    assert info['available_tools']['gpu_devices'] == ['CUDA', 'OPTIX']


# --- NEW: Tests for detect_gpu_devices ---

def test_detect_gpu_devices_success(mocker):
    """
    Tests successful GPU detection when Blender runs correctly.
    """
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[{'version': '4.1.1'}])
    mocker.patch.object(tool_manager_instance, 'get_blender_executable_path', return_value='/mock/blender')
    mock_run = mocker.patch('subprocess.run')
    # Simulate a successful run with comma-separated output from Blender
    mock_run.return_value = MagicMock(stdout='CUDA,OPTIX\n', stderr='', check_returncode=MagicMock())

    devices = system_monitor.detect_gpu_devices()

    assert devices == ['CUDA', 'OPTIX']
    mock_run.assert_called_once()

def test_detect_gpu_devices_no_blender(mocker):
    """
    Tests that detection returns an empty list if no Blender install is found.
    """
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    mock_run = mocker.patch('subprocess.run')

    devices = system_monitor.detect_gpu_devices()

    assert devices == []
    mock_run.assert_not_called()

def test_detect_gpu_devices_subprocess_error(mocker):
    """
    Tests that detection returns an empty list if the Blender command fails.
    """
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[{'version': '4.1.1'}])
    mocker.patch.object(tool_manager_instance, 'get_blender_executable_path', return_value='/mock/blender')
    # Simulate a process error
    mocker.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'cmd'))

    devices = system_monitor.detect_gpu_devices()

    assert devices == []

# --- Existing tests for registration and heartbeat ---

def test_register_with_manager_success(mocker):
    """
    Tests successful registration with the manager API.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', None)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={'hostname': 'test-host'}
    )
    mock_post = mocker.patch('requests.post')
    mock_post.return_value.json.return_value = {'id': 123}
    mock_post.return_value.raise_for_status.return_value = None

    worker_id = system_monitor.register_with_manager()

    assert worker_id == 123
    assert system_monitor.WORKER_ID == 123

def test_send_heartbeat_success(mocker):
    """
    Tests that a heartbeat is sent correctly when the worker is registered.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', 123)
    mock_post = mocker.patch('requests.post')

    system_monitor.send_heartbeat()

    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs['json'] == {'hostname': system_monitor.HOSTNAME}