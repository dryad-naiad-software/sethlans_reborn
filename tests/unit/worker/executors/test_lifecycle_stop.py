# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reporting a Blender render the worker stopped (#246, #247).

A render stopped for a worker shutdown is handed back with
``POST jobs/{id}/yield-requeue/`` (reason ``worker_shutdown`` or
``worker_hardware_changed``), with no yield event, no status and no upload;
a failed or 409 hand-back ends quietly. A render stopped because the job is
no longer ours sends nothing. Every local resource is released and the
partial output removed. A 409 on the final status PATCH is "no longer
assigned": the local output is removed and nothing else is recorded.
"""
import pytest
import requests

from sethlans_worker_agent import blender_hand_back, blender_stop, boot_id, job_processor
from sethlans_worker_agent.api_conflict import NO_LONGER_ASSIGNED
from sethlans_worker_agent.job_lifecycle import process_claimed_job

from . import test_lifecycle_release as _release
from ._fakes import blender_job, install_capacity

# The lifecycle tests' claim helper.
_claim = _release._claim


@pytest.fixture()
def api(mocker):
    """The lifecycle tests' manager mocks, plus the hand-back and yield-event calls."""
    mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
    mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
    calls = {
        'poll': mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs'),
        'update': mocker.patch('sethlans_worker_agent.api_handler.update_job_status'),
        'upload': mocker.patch('sethlans_worker_agent.api_handler.upload_render_output', return_value=True),
    }
    calls['post'] = mocker.patch('sethlans_worker_agent.api_handler._retry_request',
                                 return_value=mocker.Mock(status_code=200))
    mocker.patch('sethlans_worker_agent.api_handler.get_auth_headers', return_value={})
    mocker.patch('sethlans_worker_agent.config.MANAGER_API_URL', 'https://m:8080/api/')
    calls['yield_event'] = mocker.patch('sethlans_worker_agent.api_handler.post_yield_event')
    return calls


def _stopped_by(reason, started_at=None):
    def execute(job_data, **_kwargs):
        job_data[blender_stop.STOPPED_KEY] = reason
        if started_at is not None:
            job_data[blender_stop.STARTED_AT_KEY] = started_at
        return (False, False, False, 'Fra:1', 'killed', 'Blender exited with code 1.',
                None, None, None, 'aborted')
    return execute


def _run_stopped(api, mocker, reason, job=None, started_at=None):
    cap = install_capacity()
    mocker.patch('sethlans_worker_agent.blender_executor.execute_blender_job',
                 side_effect=_stopped_by(reason, started_at))
    process_claimed_job(_claim(api, job or blender_job(5)))
    return cap


def _assert_nothing_reported(api, cap):
    api['update'].assert_not_called()
    api['upload'].assert_not_called()
    api['yield_event'].assert_not_called()
    assert cap.has_no_reservations()
    assert job_processor.get_unfinished_job_ids() == []
    assert job_processor.get_active_jobs_snapshot() == {}
    assert job_processor.get_recent_jobs_snapshot() == []


class TestHandBack:

    @pytest.mark.parametrize('reason', [
        blender_stop.WORKER_SHUTDOWN, blender_stop.WORKER_HARDWARE_CHANGED, blender_stop.BLENDER_VERSION_MISSING,
    ])
    def test_shutdown_stop_posts_yield_requeue_with_its_reason(self, api, mocker, reason):
        """#246, #247; BS-WK-11: a missing Blender version is handed back once with ``blender_version_missing``."""
        cap = _run_stopped(api, mocker, reason)

        api['post'].assert_called_once()
        method, url = api['post'].call_args.args
        assert (method, url) == ('post', 'https://m:8080/api/jobs/5/yield-requeue/')
        assert api['post'].call_args.kwargs['json'] == {'reason': reason, 'worker_boot_id': boot_id.BOOT_ID}
        assert api['post'].call_args.kwargs['timeout'] == blender_hand_back.HAND_BACK_TIMEOUT
        _assert_nothing_reported(api, cap)

    @pytest.mark.parametrize('outcome', [
        requests.exceptions.ConnectionError('down'), 409, 500,
    ], ids=['unreachable', 'already-gone', 'refused'])
    def test_failed_or_409_hand_back_ends_quietly(self, api, mocker, outcome):
        if isinstance(outcome, Exception):
            api['post'].side_effect = outcome
        else:
            api['post'].return_value = mocker.Mock(status_code=outcome)

        cap = _run_stopped(api, mocker, blender_stop.WORKER_SHUTDOWN)

        api['post'].assert_called_once()
        _assert_nothing_reported(api, cap)

    def test_no_longer_ours_sends_nothing(self, api, mocker):
        cap = _run_stopped(api, mocker, blender_stop.NOT_ASSIGNED)

        api['post'].assert_not_called()
        _assert_nothing_reported(api, cap)

    @pytest.mark.parametrize('written_after_start', [True, False], ids=['ours', 'older-file'])
    def test_partial_output_only_if_this_render_could_have_written_it(
            self, api, mocker, tmp_path, written_after_start):
        import os
        mocker.patch('sethlans_worker_agent.config.WORKER_OUTPUT_DIR', str(tmp_path))
        (tmp_path / 'render').mkdir()
        frame = tmp_path / 'render' / 'frame_0003.png'
        frame.write_bytes(b'png')
        os.utime(frame, (1000.0, 1000.0))  # written at t=1000
        started_at = 900.0 if written_after_start else 1100.0
        job = blender_job(6, output_file_pattern='//render/frame_####.png', start_frame=3, end_frame=3)

        _run_stopped(api, mocker, blender_stop.WORKER_SHUTDOWN, job, started_at=started_at)

        assert frame.exists() is not written_after_start
        api['upload'].assert_not_called()


class TestOtherOutcomes:

    def test_user_cancel_still_reports_canceled(self, api, mocker):
        install_capacity()
        mocker.patch('sethlans_worker_agent.blender_executor.execute_blender_job',
                     return_value=(False, True, False, '', '', 'Job was canceled by user request.',
                                   None, None, None, 'aborted'))

        process_claimed_job(_claim(api, blender_job(8)))

        assert api['update'].call_args.args[1]['status'] == 'CANCELED'
        api['post'].assert_not_called()

    def test_409_on_the_status_patch_removes_local_output(self, api, mocker, tmp_path):
        install_capacity()
        out = tmp_path / 'out' / 'frame_0001.png'
        out.parent.mkdir()
        out.write_bytes(b'png')
        api['upload'].return_value = False  # upload failed (not a 409): file kept for now
        api['update'].return_value = NO_LONGER_ASSIGNED
        mocker.patch('sethlans_worker_agent.blender_executor.execute_blender_job',
                     return_value=(True, False, False, '', '', '', str(out), None, None, None))

        process_claimed_job(_claim(api, blender_job(10)))

        api['update'].assert_called_once()
        assert not out.exists()
        assert job_processor.get_recent_jobs_snapshot() == []
