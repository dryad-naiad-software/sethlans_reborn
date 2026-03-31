# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/24/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/worker_agent/test_job_processor.py

import pytest

from sethlans_worker_agent import job_processor

# --- Test data for time parsing ---
VALID_STDOUT_UNDER_AN_HOUR = (
    "Blender render complete\n"
    "Saved: '/tmp/test.png'\n"
    "Time: 01:35.25 (Saving: 00:00.10)\n"
)
VALID_STDOUT_OVER_AN_HOUR = (
    "Blender render complete\n"
    "Saved: '/tmp/test.png'\n"
    "Time: 01:02:03.99 (Saving: 00:00.00)\n"
)
VALID_STDOUT_SUB_SECOND = (
    "Render complete.\nTime: 00:00.32 (Saving: 00:00.14)\n"
)
NO_TIME_STDOUT = "Blender render complete\n"
PROGRESS_BAR_TIME_STDOUT = (
    "Fra:1 Mem:158.90M | Time:00:09.53 | Remaining:00:20.23"
)


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
    mock_poll = mocker.patch(
        'sethlans_worker_agent.job_processor.poll_and_claim_job'
    )
    mock_process_func = mocker.patch(
        'sethlans_worker_agent.job_processor.process_claimed_job'
    )
    mock_thread = mocker.patch('threading.Thread')
    worker_id = 99

    # Case 1: A job is found and claimed
    mock_job_data = {'id': 1, 'name': 'Test Job'}
    mock_poll.return_value = mock_job_data
    job_processor.get_and_claim_job(worker_id)
    mock_poll.assert_called_once_with(worker_id)

    mock_thread.assert_called_once_with(
        target=mock_process_func, args=(mock_job_data,), name='job-1'
    )
    mock_thread.return_value.start.assert_called_once()

    # Case 2: No job is found, so no thread should be created
    mock_poll.reset_mock()
    mock_thread.reset_mock()
    mock_poll.return_value = None
    job_processor.get_and_claim_job(worker_id)
    mock_poll.assert_called_once_with(worker_id)

    mock_thread.assert_not_called()
    mock_thread.return_value.start.assert_not_called()


class TestProcessClaimedJob:
    @pytest.fixture
    def mock_process_deps(self, mocker):
        """Mocks dependencies for process_claimed_job."""
        mock_execute = mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job'
        )
        mock_update_status = mocker.patch(
            'sethlans_worker_agent.api_handler.update_job_status'
        )
        mock_upload = mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output'
        )
        mock_os_remove = mocker.patch('os.remove')
        mocker.patch('os.path.dirname', return_value='/mock/output')
        mocker.patch('os.listdir', return_value=[])
        mocker.patch('os.rmdir')
        mocker.patch('os.path.exists', return_value=True)
        return mock_execute, mock_update_status, mock_upload, mock_os_remove

    def test_process_job_success_workflow(self, mocker, mock_process_deps):
        """
        Tests the entire successful workflow: render, upload, report DONE.
        Also verifies that the initial status update includes the
        `started_at` timestamp.
        """
        mock_execute, mock_update_status, mock_upload, mock_os_remove = (
            mock_process_deps
        )
        mock_execute.return_value = (
            True, False, VALID_STDOUT_UNDER_AN_HOUR, "", "",
            "/mock/output/file.png"
        )
        mock_upload.return_value = True
        mock_job_data = {'id': 1, 'name': 'Success Job'}

        job_processor.process_claimed_job(mock_job_data)

        update_calls = mock_update_status.call_args_list
        assert len(update_calls) == 2
        initial_payload = update_calls[0].args[1]
        assert initial_payload['status'] == 'RENDERING'
        assert 'started_at' in initial_payload
        assert isinstance(initial_payload['started_at'], str)

        final_payload = update_calls[1].args[1]
        assert final_payload['status'] == 'DONE'
        assert final_payload['render_time_seconds'] == 96

        mock_upload.assert_called_once_with(1, "/mock/output/file.png")
        mock_os_remove.assert_called_once_with("/mock/output/file.png")

    def test_process_job_failure_workflow(self, mock_process_deps):
        """Tests the failure workflow: render fails, report ERROR."""
        mock_execute, mock_update_status, mock_upload, mock_os_remove = (
            mock_process_deps
        )
        mock_execute.return_value = (
            False, False, "", "Blender crashed", "Blender crashed", None
        )
        mock_job_data = {'id': 2, 'name': 'Failure Job'}

        job_processor.process_claimed_job(mock_job_data)

        final_payload = mock_update_status.call_args.args[1]
        assert final_payload['status'] == 'ERROR'
        assert final_payload['error_message'] == 'Blender crashed'

        mock_upload.assert_not_called()
        mock_os_remove.assert_not_called()
