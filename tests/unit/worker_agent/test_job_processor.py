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
from unittest.mock import MagicMock

# Import the function to be tested and its dependencies
from sethlans_worker_agent import job_processor
from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.tool_manager import tool_manager_instance


# --- Test Case 1: execute_blender_job_success ---

def test_execute_blender_job_success(mocker):
    """
    Test Case: execute_blender_job_success
    Purpose: Verify the function correctly constructs and executes a Blender
             command for a successful render job.
    Asserts:
        - The function returns a success tuple.
        - The subprocess.run command is called with the correct arguments.
        - Tool manager is called to ensure the correct Blender version is available.
    """
    # --- Mock Job Data ---
    mock_job_data = {
        'name': 'Test Render',
        'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####',
        'start_frame': 1,
        'end_frame': 1,
        'blender_version': '4.2.0',
        'render_engine': 'CYCLES'
    }
    mock_blender_executable = "/mock/tools/blender-4.2.0/blender"

    # --- Mock Dependencies ---
    # 1. Mock the tool manager to provide the path to the Blender executable
    mocker.patch.object(
        tool_manager_instance,
        'ensure_blender_version_available',
        return_value=mock_blender_executable
    )

    # 2. Mock the subprocess call to avoid running a real command
    mock_completed_process = MagicMock(spec=subprocess.CompletedProcess)
    mock_completed_process.returncode = 0
    mock_completed_process.stdout = "Blender render complete."
    mock_completed_process.stderr = ""
    mock_subprocess_run = mocker.patch('subprocess.run', return_value=mock_completed_process)

    # 3. Mock filesystem checks for the output directory
    mocker.patch('os.path.exists', return_value=True)
    mock_makedirs = mocker.patch('os.makedirs')

    # --- Run the function under test ---
    success, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    # --- Assertions ---
    assert success is True
    assert stdout == "Blender render complete."
    assert error_message == ""

    # Assert the correct Blender executable was requested and used
    tool_manager_instance.ensure_blender_version_available.assert_called_once_with('4.2.0')

    # Assert the command was constructed correctly
    expected_command = [
        mock_blender_executable,
        '-b', mock_job_data['blend_file_path'],
        '-o', '/path/to/output/frame_#',  # Note: '####' is replaced with '#'
        '-F', 'PNG',
        '-E', 'CYCLES',
        '-f', '1'
    ]
    mock_subprocess_run.assert_called_once()
    assert mock_subprocess_run.call_args.args[0] == expected_command

    # Assert that makedirs was not called since os.path.exists was True
    mock_makedirs.assert_not_called()


# --- Test Case 2: execute_blender_job_failure ---

def test_execute_blender_job_failure(mocker):
    """
    Test Case: execute_blender_job_failure
    Purpose: Verify the function correctly handles a non-zero exit code
             from the Blender subprocess.
    Asserts:
        - The function returns a failure tuple.
        - The returned error message contains the exit code and stderr.
    """
    # --- Mock Job Data ---
    mock_job_data = {
        'name': 'Failed Render',
        'blender_version': '4.2.0',
        'blend_file_path': '/path/to/scene.blend',
        'output_file_pattern': '/path/to/output/frame_####',
        'start_frame': 1,
        'end_frame': 1
    }
    mock_blender_executable = "/mock/tools/blender-4.2.0/blender"

    # --- Mock Dependencies ---
    mocker.patch.object(
        tool_manager_instance,
        'ensure_blender_version_available',
        return_value=mock_blender_executable
    )

    # 2. Mock the subprocess to simulate a FAILED run
    mock_completed_process = MagicMock(spec=subprocess.CompletedProcess)
    mock_completed_process.returncode = 1  # Non-zero exit code
    mock_completed_process.stdout = ""
    mock_completed_process.stderr = "Error: Something went wrong in Blender."
    mocker.patch('subprocess.run', return_value=mock_completed_process)

    mocker.patch('os.path.exists', return_value=True)

    # --- Run the function under test ---
    success, stdout, stderr, error_message = job_processor.execute_blender_job(mock_job_data)

    # --- Assertions ---
    assert success is False
    assert "Blender exited with code 1" in error_message
    assert "Something went wrong in Blender" in error_message

    print(f"\n[UNIT TEST] execute_blender_job_failure passed.")


# --- Test Case 3: get_and_claim_job_success ---

def test_get_and_claim_job_success(mocker):
    """
    Test Case: test_get_and_claim_job_success
    Purpose: Verify the full workflow of finding, claiming, executing,
             and reporting a job successfully.
    Asserts:
        - All API calls (GET and two PATCHes) are made correctly.
        - The internal execute_blender_job function is called.
    """
    # --- Mock Initial State & API Data ---
    # 1. Set the worker ID so the function doesn't skip
    mocker.patch.dict(system_monitor.WORKER_INFO, {'id': 123, 'hostname': 'test-worker'})

    # 2. Data for the various API responses
    mock_available_job = {'id': 1, 'name': 'Claim Me'}
    mock_claimed_job_response = {'id': 1, 'status': 'RENDERING', 'assigned_worker': 123}
    mock_final_report_response = {'id': 1, 'status': 'DONE'}

    # --- Mock Dependencies ---
    # 3. Mock the API calls
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.json.return_value = [mock_available_job]
    mock_get.return_value.raise_for_status.return_value = None

    mock_patch = mocker.patch('requests.patch')
    # Use side_effect to return different responses for the two PATCH calls
    mock_patch.side_effect = [
        MagicMock(json=lambda: mock_claimed_job_response, raise_for_status=lambda: None),  # Claim call
        MagicMock(json=lambda: mock_final_report_response, raise_for_status=lambda: None)  # Report call
    ]

    # 4. Mock the already-tested blender execution function
    mock_execute = mocker.patch(
        'sethlans_worker_agent.job_processor.execute_blender_job',
        return_value=(True, "output", "", "")  # Simulate success
    )

    # --- Run function under test ---
    job_processor.get_and_claim_job()

    # --- Assertions ---
    # 5. Verify all functions and API calls were made correctly
    mock_get.assert_called_once()
    assert mock_patch.call_count == 2
    mock_execute.assert_called_once_with(mock_available_job)

    # Check the payload of the final status report
    final_payload = mock_patch.call_args.kwargs['json']
    assert final_payload['status'] == 'DONE'

    print(f"\n[UNIT TEST] get_and_claim_job_success passed.")

def test_get_and_claim_job_no_jobs_available(mocker):
    """
    Test Case: test_get_and_claim_job_no_jobs_available
    Purpose: Verify the function handles the case where no queued jobs are
             returned by the manager.
    Asserts:
        - Only the initial GET request is made.
        - No PATCH or job execution calls are made.
    """
    # --- Mock Initial State ---
    mocker.patch.dict(system_monitor.WORKER_INFO, {'id': 123, 'hostname': 'test-worker'})

    # --- Mock Dependencies ---
    # 1. Mock the GET request to return an empty list
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.json.return_value = [] # No jobs available
    mock_get.return_value.raise_for_status.return_value = None

    # 2. Mock other functions to ensure they are NOT called
    mock_patch = mocker.patch('requests.patch')
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    # --- Run function under test ---
    job_processor.get_and_claim_job()

    # --- Assertions ---
    mock_get.assert_called_once()
    mock_patch.assert_not_called()
    mock_execute.assert_not_called()

    print(f"\n[UNIT TEST] test_get_and_claim_job_no_jobs_available passed.")


def test_get_and_claim_job_api_get_fails(mocker):
    """
    Test Case: test_get_and_claim_job_api_get_fails
    Purpose: Verify the function handles a network exception when polling for jobs.
    Asserts:
        - The initial GET request is attempted.
        - No subsequent API calls or job executions are made.
    """
    # --- Mock Initial State ---
    mocker.patch.dict(system_monitor.WORKER_INFO, {'id': 123})

    # --- Mock Dependencies ---
    # 1. Mock the GET request to raise a network exception
    mock_get = mocker.patch(
        'requests.get',
        side_effect=requests.exceptions.RequestException("Connection error")
    )

    # 2. Mock other functions to ensure they are NOT called
    mock_patch = mocker.patch('requests.patch')
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    # --- Run function under test ---
    job_processor.get_and_claim_job()

    # --- Assertions ---
    mock_get.assert_called_once()
    mock_patch.assert_not_called()
    mock_execute.assert_not_called()

    print(f"\n[UNIT TEST] test_get_and_claim_job_api_get_fails passed.")

# In tests/unit/worker_agent/test_job_processor.py

def test_get_and_claim_job_claim_fails(mocker):
    """
    Test Case: test_get_and_claim_job_claim_fails
    Purpose: Verify the function handles an error when trying to claim a job.
    Asserts:
        - The GET and PATCH requests are attempted.
        - The job execution function is never called.
    """
    # --- Mock Initial State & API Data ---
    mocker.patch.dict(system_monitor.WORKER_INFO, {'id': 123})
    mock_available_job = {'id': 1, 'name': 'Claim Me'}

    # --- Mock Dependencies ---
    # 1. Mock the GET request to successfully find a job
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.json.return_value = [mock_available_job]
    mock_get.return_value.raise_for_status.return_value = None

    # 2. Mock the PATCH request to fail
    mock_patch = mocker.patch(
        'requests.patch',
        side_effect=requests.exceptions.RequestException("Claim failed")
    )

    # 3. Mock the job execution function to ensure it is NOT called
    mock_execute = mocker.patch('sethlans_worker_agent.job_processor.execute_blender_job')

    # --- Run function under test ---
    job_processor.get_and_claim_job()

    # --- Assertions ---
    mock_get.assert_called_once()
    mock_patch.assert_called_once() # It should try to claim the job once
    mock_execute.assert_not_called()

    print(f"\n[UNIT TEST] test_get_and_claim_job_claim_fails passed.")