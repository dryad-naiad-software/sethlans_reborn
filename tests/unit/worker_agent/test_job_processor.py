# tests/unit/worker_agent/test_job_processor.py
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
# Created by Mario Estrella on 07/24/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

import pytest
import requests
import subprocess
import psutil
import time
from unittest.mock import MagicMock

# Import the function to be tested and its dependencies
from sethlans_worker_agent import job_processor
from sethlans_worker_agent.tool_manager import tool_manager_instance


@pytest.fixture
def mock_popen_setup(mocker):
    """
    A fixture to provide a standard, complex mock setup for subprocess.Popen,
    psutil, and requests for testing execute_blender_job.
    """
    # Mock Popen and the process it returns
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['']
    mocker.patch('time.sleep')  # Prevent delays

    mock_popen = mocker.patch('subprocess.Popen', return_value=mock_process)

    # Mock psutil to control the 'while pid_exists' loop
    # Let it run once, then terminate the loop
    mocker.patch('psutil.pid_exists', side_effect=[True, False])

    # Mock the API check to prevent cancellation logic from triggering
    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mocker.patch('requests.get', return_value=mock_response_rendering)

    # Mock filesystem and tool manager
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value="/mock/tools/blender")
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')

    return mock_popen, mock_process


# --- Tests for execute_blender_job ---

def test_execute_blender_job_success(mock_popen_setup):
    """Tests a standard successful render job execution."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.wait.return_value = 0  # Simulate successful exit code

    mock_job_data = {
        'id': 1, 'name': 'Test Render', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.2.0', 'render_engine': 'CYCLES'
    }

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert success is True
    assert was_canceled is False
    assert stdout.strip() == "Blender render complete."
    assert error_message == ""
    mock_popen.assert_called_once()
    mock_process.wait.assert_called_once()


def test_execute_blender_job_failure(mock_popen_setup):
    """Tests a failed render job execution (non-zero exit code)."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.wait.return_value = 1  # Simulate error exit code
    mock_process.stderr.readline.side_effect = ['Error: Something went wrong.\n', '']

    mock_job_data = {
        'id': 2, 'name': 'Failed Render', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.2.0'
    }

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert was_canceled is False
    assert "Blender exited with code 1" in error_message
    assert "Something went wrong" in error_message
    mock_process.wait.assert_called_once()


def test_execute_blender_job_animation_command(mock_popen_setup):
    """Tests that the command for an animation is constructed correctly."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 3, 'name': 'Test Animation', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 10, 'end_frame': 20,
        'blender_version': '4.2.0', 'render_engine': 'CYCLES'
    }

    job_processor.execute_blender_job(mock_job_data)

    expected_command = [
        "/mock/tools/blender", '--factory-startup', '-b', '/path/to/scene.blend',
        '-o', '/path/to/output/frame_#', '-F', 'PNG', '-E', 'CYCLES',
        '-s', '10', '-e', '20', '-a'
    ]

    called_command = mock_popen.call_args.args[0]
    assert called_command == expected_command


def test_execute_blender_job_tool_unavailable(mocker):
    """Tests that the job fails if the requested Blender version is not found."""
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value=None)
    mock_popen = mocker.patch('subprocess.Popen')

    mock_job_data = {
        'id': 4, 'name': 'Tool Fail Render', 'blender_version': '4.99.0',
        'blend_file_path': '/path/to/scene.blend', 'output_file_pattern': '/path/to/output/frame_####'
    }

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert was_canceled is False
    assert "Could not find or acquire Blender version '4.99.0'" in error_message
    mock_popen.assert_not_called()


# --- Tests for get_and_claim_job ---

def test_get_and_claim_job_success(mocker):
    """Tests the full success workflow: find, claim, execute, and report job."""
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Claim Me'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_available_job], raise_for_status=lambda: None
    ))

    # There are THREE patch calls in the success path, so we need three mock responses.
    mock_patch = mocker.patch('requests.patch')
    mock_patch.side_effect = [
        MagicMock(status_code=200),  # 1. Claim call
        MagicMock(status_code=200),  # 2. Set status to RENDERING
        MagicMock(status_code=200)  # 3. Report final status DONE
    ]

    mock_execute = mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, "output", "", "")  # Simulate success (5-tuple)
    )

    job_processor.get_and_claim_job(mock_worker_id)

    # Assert that all three patch calls were made against the original mock
    assert mock_patch.call_count == 3
    mock_execute.assert_called_once_with(mock_available_job)

    # Verify the payload of the FINAL patch call using the original mock
    final_payload = mock_patch.call_args.kwargs['json']
    assert final_payload['status'] == 'DONE'


def test_get_and_claim_job_no_jobs_available(mocker):
    """Tests that nothing happens if no jobs are available."""
    mock_worker_id = 123
    mocker.patch('requests.get', return_value=MagicMock(json=lambda: [], raise_for_status=lambda: None))
    mock_patch = mocker.patch('requests.patch')
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    job_processor.get_and_claim_job(mock_worker_id)

    mock_patch.assert_not_called()
    mock_execute.assert_not_called()


def test_get_and_claim_job_api_get_fails(mocker):
    """Tests that the function handles a network error when polling for jobs."""
    mock_worker_id = 123
    mocker.patch('requests.get', side_effect=requests.exceptions.RequestException("Connection error"))
    mock_patch = mocker.patch('requests.patch')
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    job_processor.get_and_claim_job(mock_worker_id)

    mock_patch.assert_not_called()
    mock_execute.assert_not_called()


def test_get_and_claim_job_claim_fails(mocker):
    """Tests that job execution does not proceed if the API claim fails."""
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Claim Me'}
    mocker.patch('requests.get',
                 return_value=MagicMock(json=lambda: [mock_available_job], raise_for_status=lambda: None))
    mocker.patch('requests.patch', side_effect=requests.exceptions.RequestException("Claim failed"))
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    job_processor.get_and_claim_job(mock_worker_id)

    mock_execute.assert_not_called()


# --- CANCELLATION TEST ---
def test_execute_blender_job_cancellation(mocker):
    """
    Tests that execute_blender_job correctly handles a cancellation signal from the API,
    kills the subprocess, and returns the correct status.
    """
    # 1. Arrange
    mock_job_data = {
        'id': 99, 'name': 'Cancellable Job', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1,
        'end_frame': 1, 'blender_version': '4.1.1'
    }

    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value="/mock/blender")
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')
    mocker.patch('time.sleep')

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdout.readline.side_effect = ['output line 1\n', '']
    mock_process.stderr.readline.side_effect = ['']
    mock_process.wait.return_value = -9  # Simulate exit code for a killed process
    mocker.patch('subprocess.Popen', return_value=mock_process)

    mocker.patch('psutil.pid_exists', side_effect=[True, True, False])

    # Mock the API status change from RENDERING to CANCELED
    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mock_response_canceled = MagicMock(status_code=200)
    mock_response_canceled.json.return_value = {'status': 'CANCELED'}
    mocker.patch('requests.get', side_effect=[mock_response_rendering, mock_response_canceled])

    # 2. Act
    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    # 3. Assert
    assert success is False, "Success should be False for a canceled job."
    assert was_canceled is True, "was_canceled flag should be True."
    assert error_message == "Job was canceled by user request.", "Error message should indicate cancellation."
    assert stdout == "output line 1\n", "Stdout up to the point of cancellation should be captured."

    mock_process.kill.assert_called_once()
    mock_process.wait.assert_called_once()