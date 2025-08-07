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
# tests/unit/worker_agent/test_job_processor.py

import pytest
import threading
from unittest.mock import MagicMock, call

# Import the function to be tested and its dependencies
from sethlans_worker_agent import job_processor, config, api_handler
from sethlans_worker_agent.tool_manager import tool_manager_instance

# --- Test data for time parsing ---
VALID_STDOUT_UNDER_AN_HOUR = "Blender render complete\nSaved: '/tmp/test.png'\nTime: 01:35.25 (Saving: 00:00.10)\n"
VALID_STDOUT_OVER_AN_HOUR = "Blender render complete\nSaved: '/tmp/test.png'\nTime: 01:02:03.99 (Saving: 00:00.00)\n"
VALID_STDOUT_SUB_SECOND = "Render complete.\nTime: 00:00.32 (Saving: 00:00.14)\n"
NO_TIME_STDOUT = "Blender render complete\n"
PROGRESS_BAR_TIME_STDOUT = "Fra:1 Mem:158.90M | Time:00:09.53 | Remaining:00:20.23"


@pytest.fixture(autouse=True)
def reset_job_processor_state():
    """Fixture to reset module-level state and ensure locks are released."""
    job_processor._gpu_assignment_map.clear()
    # Ensure the lock is always released in case a previous test failed uncleanly
    if job_processor._cpu_lock.locked():
        job_processor._cpu_lock.release()
    yield
    job_processor._gpu_assignment_map.clear()
    if job_processor._cpu_lock.locked():
        job_processor._cpu_lock.release()


@pytest.mark.parametrize("stdout, expected_seconds", [
    (VALID_STDOUT_SUB_SECOND, 1),
    (VALID_STDOUT_UNDER_AN_HOUR, 96),
    (VALID_STDOUT_OVER_AN_HOUR, 3724),
    ("Some other text...\nTime: 01:02:03.99 (Saving: 00:00.00)", 3724),
    ("Another line...\nTime: 12.34 (Saving: 00.01)", None),
    (NO_TIME_STDOUT, None),
    (PROGRESS_BAR_TIME_STDOUT, None),
    ("", None)
])
def test_parse_render_time(stdout, expected_seconds):
    """Tests the _parse_render_time function with various inputs."""
    result = job_processor._parse_render_time(stdout)
    assert result == expected_seconds


def test_get_and_claim_job_dispatches_thread(mocker):
    """
    Tests that the get_and_claim_job function correctly dispatches the
    processing of a claimed job to a new thread.
    """
    mock_poll = mocker.patch('sethlans_worker_agent.job_processor.poll_and_claim_job')
    mock_process_func = mocker.patch('sethlans_worker_agent.job_processor.process_claimed_job')
    mock_thread = mocker.patch('threading.Thread')
    worker_id = 99

    # Case 1: A job is found and claimed
    mock_job_data = {'id': 1, 'name': 'Test Job'}
    mock_poll.return_value = mock_job_data
    job_processor.get_and_claim_job(worker_id)
    mock_poll.assert_called_once_with(worker_id)

    # Assert that a Thread was created with the correct target and args
    mock_thread.assert_called_once_with(target=mock_process_func, args=(mock_job_data,))
    # Assert that the created thread instance was started
    mock_thread.return_value.start.assert_called_once()

    # Case 2: No job is found, so no thread should be created
    mock_poll.reset_mock()
    mock_thread.reset_mock()
    mock_poll.return_value = None
    job_processor.get_and_claim_job(worker_id)
    mock_poll.assert_called_once_with(worker_id)

    # Assert that no thread was created or started
    mock_thread.assert_not_called()
    mock_thread.return_value.start.assert_not_called()


# --- NEW: Test suite for process_claimed_job ---
class TestProcessClaimedJob:
    @pytest.fixture
    def mock_process_deps(self, mocker):
        """Mocks dependencies for process_claimed_job."""
        mock_execute = mocker.patch('sethlans_worker_agent.blender_executor.execute_blender_job')
        mock_update_status = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        mock_upload = mocker.patch('sethlans_worker_agent.api_handler.upload_render_output')
        mock_os_remove = mocker.patch('os.remove')
        mocker.patch('os.path.dirname', return_value='/mock/output')
        mocker.patch('os.listdir', return_value=[]) # Simulate empty dir for rmdir
        mocker.patch('os.rmdir')
        mocker.patch('os.path.exists', return_value=True)
        return mock_execute, mock_update_status, mock_upload, mock_os_remove

    def test_process_job_success_workflow(self, mock_process_deps):
        """
        Tests the entire successful workflow: render, upload, report DONE.
        """
        mock_execute, mock_update_status, mock_upload, mock_os_remove = mock_process_deps
        mock_execute.return_value = (True, False, VALID_STDOUT_UNDER_AN_HOUR, "", "", "/mock/output/file.png")
        mock_upload.return_value = True
        mock_job_data = {'id': 1, 'name': 'Success Job'}

        job_processor.process_claimed_job(mock_job_data)

        # Assert status updates
        update_calls = mock_update_status.call_args_list
        assert len(update_calls) == 2
        # First call sets status to RENDERING
        assert update_calls[0].args == (1, {'status': 'RENDERING'})
        # Second call reports final status
        final_payload = update_calls[1].args[1]
        assert final_payload['status'] == 'DONE'
        assert final_payload['render_time_seconds'] == 96

        # Assert upload and cleanup
        mock_upload.assert_called_once_with(1, "/mock/output/file.png")
        mock_os_remove.assert_called_once_with("/mock/output/file.png")

    def test_process_job_failure_workflow(self, mock_process_deps):
        """
        Tests the failure workflow: render fails, report ERROR.
        """
        mock_execute, mock_update_status, mock_upload, mock_os_remove = mock_process_deps
        mock_execute.return_value = (False, False, "", "Blender crashed", "Blender crashed", None)
        mock_job_data = {'id': 2, 'name': 'Failure Job'}

        job_processor.process_claimed_job(mock_job_data)

        # Assert final status update
        final_payload = mock_update_status.call_args.args[1]
        assert final_payload['status'] == 'ERROR'
        assert final_payload['error_message'] == 'Blender crashed'

        # Assert no upload or cleanup happened
        mock_upload.assert_not_called()
        mock_os_remove.assert_not_called()


# --- REFACTORED: Test suite for poll_and_claim_job ---
class TestPollAndClaimJob:
    @pytest.fixture
    def mock_poll_deps(self, mocker):
        """Mocks dependencies for poll_and_claim_job."""
        mocker.patch.object(config, 'FORCE_CPU_ONLY', False)
        mocker.patch.object(config, 'FORCE_GPU_ONLY', False)
        mocker.patch.object(config, 'GPU_SPLIT_MODE', False) # Default to off
        mock_poll_api = mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs', return_value=None)
        mock_detect_gpu = mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices')
        return mock_poll_api, mock_detect_gpu

    def test_poll_in_force_cpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_CPU_ONLY mode should poll for gpu_available=false."""
        mock_poll_api, _ = mock_poll_deps
        mocker.patch.object(config, 'FORCE_CPU_ONLY', True)

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert call_params.get('gpu_available') == 'false'

    def test_poll_in_force_gpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_GPU_ONLY mode should poll for gpu_available=true."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA'] # Simulate GPU present
        mocker.patch.object(config, 'FORCE_GPU_ONLY', True)

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert call_params.get('gpu_available') == 'true'

    def test_poll_in_default_mode(self, mock_poll_deps):
        """A normal worker (no force flags) should not specify gpu_available, making it flexible."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']  # Simulate a GPU-capable worker

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert 'gpu_available' not in call_params

    def test_get_next_available_gpu(self, mocker):
        """Tests the logic for finding the next free GPU index."""
        # Mock having 2 detected GPUs
        mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[{}, {}])

        # Case 1: No GPUs are busy
        job_processor._gpu_assignment_map.clear()
        assert job_processor._get_next_available_gpu() == 0

        # Case 2: GPU 0 is busy
        job_processor._gpu_assignment_map = {0: 123}
        assert job_processor._get_next_available_gpu() == 1

        # Case 3: All GPUs are busy
        job_processor._gpu_assignment_map = {0: 123, 1: 456}
        assert job_processor._get_next_available_gpu() is None

    def test_claim_job_in_split_mode_when_gpus_are_busy(self, mocker, mock_poll_deps):
        """
        In GPU split mode, if all GPUs are busy, the worker should not attempt to claim a job.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA', 'CUDA']
        mocker.patch.object(config, 'GPU_SPLIT_MODE', True)
        mocker.patch('sethlans_worker_agent.job_processor._get_next_available_gpu', return_value=None)
        mock_claim_job_api = mocker.patch('sethlans_worker_agent.api_handler.claim_job')

        # Provide a job for the worker to find
        mock_poll_api.return_value = [{'id': 1, 'render_device': 'GPU'}]

        result = job_processor.poll_and_claim_job(1)

        # Assert that the claim (PATCH request) was never made and None was returned
        assert result is None
        mock_claim_job_api.assert_not_called()

    def test_claim_job_skips_when_cpu_busy(self, mocker, mock_poll_deps):
        """
        Tests that if a CPU job is available but the CPU lock is held,
        the worker skips the claim.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = []  # Simulate no GPUs
        mock_claim_api = mocker.patch('sethlans_worker_agent.api_handler.claim_job')
        mock_poll_api.return_value = [{'id': 5, 'render_device': 'CPU'}]

        # Acquire the lock to simulate a busy CPU
        job_processor._cpu_lock.acquire()
        try:
            result = job_processor.poll_and_claim_job(1)
            assert result is None
            mock_claim_api.assert_not_called()
        finally:
            job_processor._cpu_lock.release()

    def test_claim_job_skips_any_job_when_cpu_only_and_busy(self, mocker, mock_poll_deps):
        """
        Tests that an 'ANY' device job is correctly skipped on a CPU-only
        worker if the CPU lock is busy.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = []  # CRITICAL: Simulate a CPU-only worker
        mock_claim_api = mocker.patch('sethlans_worker_agent.api_handler.claim_job')

        # Provide an 'ANY' device job
        mock_poll_api.return_value = [{'id': 6, 'render_device': 'ANY'}]

        # Acquire the lock to simulate a busy CPU
        job_processor._cpu_lock.acquire()
        try:
            result = job_processor.poll_and_claim_job(1)
            assert result is None
            mock_claim_api.assert_not_called()
        finally:
            job_processor._cpu_lock.release()

    def test_poll_and_claim_job_returns_data_on_success(self, mocker, mock_poll_deps):
        """
        Verifies that on a successful claim, the function returns the job data dictionary.
        """
        mock_poll_api, _ = mock_poll_deps
        mock_job = {'id': 1, 'name': 'Claim Me'}
        mock_poll_api.return_value = [mock_job]
        mock_claim_job_api = mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)

        result = job_processor.poll_and_claim_job(1)

        assert result is not None
        assert result['id'] == 1
        assert result['name'] == 'Claim Me'
        mock_claim_job_api.assert_called_once_with(1, 1)