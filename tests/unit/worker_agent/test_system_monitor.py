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
import logging
from unittest.mock import patch, MagicMock

# Import the function to be tested
from sethlans_worker_agent.system_monitor import get_system_info, send_heartbeat
from sethlans_worker_agent import tool_manager

import platform
import socket
from sethlans_worker_agent import config

logger = logging.getLogger(__name__)


# --- Test Case 1: get_system_info_success ---

def test_get_system_info_success(mocker):
    """
    Test Case: get_system_info_success
    Purpose: Verify that get_system_info correctly gathers basic system information
             (hostname, IP, OS) and calls the tool_manager's scanner.
    Asserts:
        - The returned dictionary contains expected system info.
        - tool_manager_instance.scan_for_blender_versions is called correctly.
    """
    # Mock external dependencies: socket, platform, tool_manager_instance
    mock_hostname = "test-host"
    mock_ip_address = "192.168.1.100"
    mock_os_system = "Windows"
    mock_os_release = "10"
    mock_scanned_tools = {"blender": ["4.1.1", "4.2.0"]}

    mocker.patch('socket.gethostname', return_value=mock_hostname)
    # Mock gethostbyname and its potential error
    mock_gethostbyname = mocker.patch('socket.gethostbyname')
    mock_gethostbyname.return_value = mock_ip_address

    mocker.patch('platform.system', return_value=mock_os_system)
    mocker.patch('platform.release', return_value=mock_os_release)  # For Windows

    # Mock the tool_manager_instance.scan_for_blender_versions method
    # It's an instance method, so we mock on the instance imported
    mocker.patch.object(tool_manager.tool_manager_instance,
                        'scan_for_blender_versions',
                        return_value=mock_scanned_tools)

    system_info = get_system_info()

    # Assert 1: Correct system info is returned
    assert system_info['hostname'] == mock_hostname
    assert system_info['ip_address'] == mock_ip_address
    assert system_info['os'] == f"{mock_os_system} {mock_os_release}"

    # Assert 2: available_tools is correctly populated by the mock
    assert system_info['available_tools'] == mock_scanned_tools

    # Assert 3: External calls were made correctly
    socket.gethostname.assert_called_once()
    mock_gethostbyname.assert_called_once_with(mock_hostname)
    platform.system.assert_called_once()
    platform.release.assert_called_once()
    tool_manager.tool_manager_instance.scan_for_blender_versions.assert_called_once()

    print(f"\n[UNIT TEST] get_system_info_success passed.")


# --- Fixture for mocking requests.post ---
@pytest.fixture
def mock_requests_post(mocker):
    """
    Mocks requests.post to return a mock response for heartbeat.
    """
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"id": 2, "hostname": "TEST-HOST-2", "is_active": True}  # Mimic manager response

    # --- CORRECTED: Return the patched object itself for assertions ---
    patched_post = mocker.patch('requests.post', return_value=mock_response)
    return patched_post
    # --- END CORRECTED ---


# --- Test Case 2: send_heartbeat_success ---

def test_send_heartbeat_success(mocker, mock_requests_post):
    """
    Test Case: send_heartbeat_success
    Purpose: Verify that send_heartbeat correctly sends a POST request
             and updates WORKER_INFO on successful response.
    Asserts:
        - requests.post is called exactly once with the correct URL and payload.
        - system_monitor.WORKER_INFO is updated with data from the mock response.
    """
    # Mock system_info (since send_heartbeat takes it as an argument)
    test_system_info = {
        "hostname": "test-host-abc",
        "ip_address": "192.168.1.5",
        "os": "TestOS 1.0"
    }

    # Mock tool_manager_instance.scan_for_blender_versions as get_system_info calls it internally,
    # but send_heartbeat doesn't directly interact with it for its core logic.
    # We provide an empty list of tools for this test as it's not the focus.
    mocker.patch.object(tool_manager.tool_manager_instance,
                        'scan_for_blender_versions',
                        return_value={})

    # Clear WORKER_INFO before the test to ensure it's updated
    from sethlans_worker_agent import system_monitor  # Re-import to get actual module and modify global
    system_monitor.WORKER_INFO = {}

    send_heartbeat(test_system_info)

    # Assert 1: requests.post was called correctly
    expected_url = f"{config.MANAGER_API_URL}heartbeat/"
    mock_requests_post.assert_called_once_with(expected_url, json=test_system_info, timeout=5)

    # Assert 2: WORKER_INFO is updated
    assert system_monitor.WORKER_INFO.get('id') == 2
    assert system_monitor.WORKER_INFO.get('hostname') == "TEST-HOST-2"

    print(f"\n[UNIT TEST] send_heartbeat_success passed.")