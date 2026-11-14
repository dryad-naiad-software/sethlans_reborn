# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker handling of the manager's 409 "job no longer yours" (issue #217).

The manager answers 409 to an output upload or status PATCH when the job
was canceled, requeued or reassigned while this worker rendered it.  The
worker logs that at INFO, removes its local output as on success, and
does not send the DONE report that would follow the upload.
"""

import pytest

from sethlans_worker_agent import api_conflict, api_handler, api_upload, job_lifecycle
from sethlans_worker_agent.api_conflict import JobNoLongerAssigned, is_job_conflict


def _response(mocker, status_code, body=None):
    resp = mocker.Mock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    resp.raise_for_status = mocker.Mock()
    return resp


@pytest.fixture
def conflict_logger(mocker):
    return mocker.patch.object(api_conflict, 'logger')


class TestIsJobConflict:

    def test_409_is_a_conflict_logged_at_info(self, mocker, conflict_logger):
        resp = _response(mocker, 409, {'error': 'Job is in CANCELED status'})

        assert is_job_conflict(resp, 5, 'output upload') is True
        conflict_logger.info.assert_called_once()
        assert 'CANCELED' in conflict_logger.info.call_args.args[-1]
        conflict_logger.error.assert_not_called()
        conflict_logger.warning.assert_not_called()

    @pytest.mark.parametrize('code', [200, 400, 401, 403, 404, 500])
    def test_other_codes_are_not_conflicts(self, mocker, conflict_logger, code):
        assert is_job_conflict(_response(mocker, code), 5, 'x') is False
        conflict_logger.info.assert_not_called()

    def test_non_json_body_is_tolerated(self, mocker, conflict_logger):
        resp = _response(mocker, 409)
        resp.json.side_effect = ValueError('no json')

        assert is_job_conflict(resp, 5, 'status update') is True


class TestUploadConflict:

    def test_409_raises_job_no_longer_assigned(self, mocker, tmp_path):
        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG data')
        mocker.patch('sethlans_worker_agent.config.MANAGER_API_URL', 'https://m:8080/api/')
        mocker.patch.object(api_upload, 'get_auth_headers', return_value={})
        mocker.patch.object(api_upload, 'handle_auth_response', return_value=False)
        retain = mocker.patch.object(api_upload, 'retain_failed_upload')
        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            return_value=_response(mocker, 409, {'error': 'canceled'}),
        )

        with pytest.raises(JobNoLongerAssigned):
            api_upload.upload_render_output(9, str(out_file))
        retain.assert_not_called()


class TestFinalizeOnConflict:

    def test_conflict_removes_output_and_reports_nothing(self, mocker, tmp_path):
        out_dir = tmp_path / 'output'
        out_dir.mkdir()
        out_file = out_dir / 'render.exr'
        out_file.write_bytes(b'EXR')
        thumb = out_dir / 'thumb.png'
        thumb.write_bytes(b'PNG')
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            side_effect=JobNoLongerAssigned(1),
        )

        result = job_lifecycle._finalize_and_upload(
            True, False, 1, str(out_file), thumbnail_path=str(thumb),
        )

        assert result is None
        assert not out_file.exists()
        assert not thumb.exists()
        assert not out_dir.exists()

    def test_lifecycle_skips_done_patch_after_conflict(self, mocker, tmp_path):
        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG')
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, 'Time: 1.00s', '', None,
                          str(out_file), None, None, 'aborted'),
        )
        mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output',
            side_effect=JobNoLongerAssigned(31),
        )
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        mocker.patch('sethlans_worker_agent.job_processor.get_worker_capacity', return_value=None)

        job_lifecycle._process_claimed_job_inner({'id': 31, 'name': 'Gone'})

        update.assert_not_called()
        assert not out_file.exists()


class TestStatusUpdateConflict:

    def test_409_is_logged_at_info_not_error(self, mocker, conflict_logger):
        mocker.patch('sethlans_worker_agent.config.MANAGER_API_URL', 'https://m:8080/api/')
        mocker.patch.object(api_handler, 'get_auth_headers', return_value={})
        mocker.patch.object(api_handler, 'handle_auth_response', return_value=False)
        resp = _response(mocker, 409, {'error': 'no longer assigned'})
        mocker.patch.object(api_handler, '_retry_request', return_value=resp)
        handler_logger = mocker.patch.object(api_handler, 'logger')

        api_handler.update_job_status(4, {'status': 'DONE'})

        resp.raise_for_status.assert_not_called()
        handler_logger.error.assert_not_called()
        conflict_logger.info.assert_called_once()
