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

# --- NEW: Test data for time parsing ---
VALID_STDOUT = "Blender render complete\nSaved: '/tmp/test.png'\nTime: 00:01:35.25 (Saving: 00:00.10)\n"
NO_TIME_STDOUT = "Blender render complete\n"


@pytest.fixture
def mock_popen_setup(mocker):
    """
    A fixture to provide a standard, complex mock setup for subprocess.Popen,
    psutil, and requests for testing execute_blender_job.
    """
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['']
    mock_process.poll.return_value = 0
    mocker.patch('time.sleep')

    mock_popen = mocker.patch('subprocess.Popen', return_value=mock_process)

    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mocker.patch('requests.get', return_value=mock_response_rendering)

    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value="/mock/tools/blender")
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')

    return mock_popen, mock_process


# --- NEW TESTS for the time parsing logic ---
@pytest.mark.parametrize("stdout, expected_seconds", [
    (VALID_STDOUT, 95),
    ("Some other text... Time: 01:02:03.99 ...more text", 3723),
    (NO_TIME_STDOUT, None),
    ("Invalid Time: 99:99:99.99", None),
    ("", None)
])
def test_parse_render_time(stdout, expected_seconds):
    """Tests the _parse_render_time function with various inputs."""
    result = job_processor._parse_render_time(stdout)
    assert result == expected_seconds


def test_get_and_claim_job_sends_render_time(mocker):
    """
    Tests that the render time is correctly parsed and included in the
    final PATCH request when a job is successfully completed.
    """
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Time Test Job'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_available_job], raise_for_status=lambda: None
    ))

    # Configure the mock to return successful status codes for all patch calls
    mock_patch = mocker.patch('requests.patch')
    mock_patch.return_value = MagicMock(status_code=200)

    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, VALID_STDOUT, "", "")
    )

    job_processor.get_and_claim_job(mock_worker_id)

    assert mock_patch.call_count == 3
    final_call_args = mock_patch.call_args
    assert 'render_time_seconds' in final_call_args.kwargs['json']
    assert final_call_args.kwargs['json']['render_time_seconds'] == 95


# --- Existing Tests ---
def test_execute_blender_job_cpu_success(mock_popen_setup):
    """Tests a standard successful CPU render job execution."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 1, 'name': 'CPU Test Render', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.2.0', 'render_engine': 'CYCLES', 'render_device': 'CPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--factory-startup" in called_command


def test_execute_blender_job_gpu_command(mock_popen_setup):
    """
    Tests that the --factory-startup flag is correctly OMITTED for GPU jobs.
    """
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 5, 'name': 'GPU Test', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.2.0', 'render_engine': 'CYCLES', 'render_device': 'GPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    called_command = mock_popen.call_args.args[0]
    assert "--factory-startup" not in called_command


def test_execute_blender_job_failure(mock_popen_setup):
    """Tests a failed render job execution (non-zero exit code)."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.stderr.readline.side_effect = ['Error: Something went wrong.\n', '']
    mock_process.wait.return_value = 1

    mock_job_data = {
        'id': 2, 'name': 'Failed Render', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1, 'end_frame': 1,
        'blender_version': '4.2.0'
    }

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert "Blender exited with code 1" in error_message


def test_execute_blender_job_animation_command(mock_popen_setup):
    """Tests that the command for an animation is constructed correctly."""
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['Blender render complete.\n', '']
    mock_process.wait.return_value = 0

    mock_job_data = {
        'id': 3, 'name': 'Test Animation', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 10, 'end_frame': 20,
        'blender_version': '4.2.0', 'render_engine': 'CYCLES', 'render_device': 'CPU'
    }

    job_processor.execute_blender_job(mock_job_data)

    expected_command = [
        "/mock/tools/blender", '--factory-startup', '-b', '/path/to/scene.blend',
        '-o', '/path/to/output/frame_####', '-F', 'PNG', '-E', 'CYCLES',
        '-s', '10', '-e', '20', '-a'
    ]

    called_command = mock_popen.call_args.args[0]
    assert called_command == expected_command


def test_execute_blender_job_tool_unavailable(mocker):
    """Tests that the job fails if the requested Blender version is not found."""
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value=None)
    mocker.patch('subprocess.Popen')

    mock_job_data = {
        'id': 4, 'name': 'Tool Fail Render', 'blender_version': '4.99.0',
        'blend_file_path': '/path/to/scene.blend', 'output_file_pattern': '/path/to/output/frame_####'
    }

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert success is False
    assert "Could not find or acquire Blender version '4.99.0'" in error_message


def test_get_and_claim_job_success(mocker):
    """Tests the full success workflow: find, claim, execute, and report job."""
    mock_worker_id = 123
    mock_available_job = {'id': 1, 'name': 'Claim Me'}

    mocker.patch('requests.get', return_value=MagicMock(
        json=lambda: [mock_available_job], raise_for_status=lambda: None
    ))
    mocker.patch('requests.patch', side_effect=[MagicMock(status_code=200)] * 3)
    mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, False, "output", "", "")
    )

    job_processor.get_and_claim_job(mock_worker_id)


def test_execute_blender_job_cancellation(mock_popen_setup, mocker):
    """
    Tests that execute_blender_job correctly handles a cancellation signal from the API.
    """
    mock_popen, mock_process = mock_popen_setup
    mock_process.stdout.readline.side_effect = ['output line 1\n', '']
    mock_process.poll.side_effect = [None, None, 0]
    mock_process.wait.return_value = -9
    mocker.patch('psutil.Process')

    mock_job_data = {
        'id': 99, 'name': 'Cancellable Job', 'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####', 'start_frame': 1,
        'end_frame': 1, 'blender_version': '4.1.1'
    }

    mock_response_rendering = MagicMock(status_code=200)
    mock_response_rendering.json.return_value = {'status': 'RENDERING'}
    mock_response_canceled = MagicMock(status_code=200)
    mock_response_canceled.json.return_value = {'status': 'CANCELED'}
    mocker.patch('requests.get', side_effect=[mock_response_rendering, mock_response_canceled])

    success, was_canceled, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    assert was_canceled is True
    assert error_message == "Job was canceled by user request."