# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A worker's report on an already finished job is a 409, not a 400 (#225).

After an admin cancel the worker stops Blender and reports CANCELED.  The
serializer rejected CANCELED -> CANCELED as an invalid transition with a
400, before the row lock, and the worker logged that 400 at ERROR.  The
assignee's report on a CANCELED, DONE or ERROR job now gets the #217 409,
which the worker logs at INFO.  The tests pass the manager's real
response through the worker's ``update_job_status``.
"""

import json

import pytest
import requests

from sethlans_worker_agent import api_conflict, api_handler
from workers.models import Job, JobStatus

from .worker_write_race_helpers import JOBS_URL, create_claimed_job

CANCELED_REPORT = {
    'status': 'CANCELED',
    'error_message': 'Job was canceled by the manager.',
    'last_output': 'Fra:1 Mem:12M | Rendering 3/16 tiles',
}
DONE_REPORT = {'status': 'DONE', 'render_time_seconds': 12}


def _as_requests_response(drf_response, url):
    resp = requests.Response()
    resp.status_code = drf_response.status_code
    resp._content = json.dumps(drf_response.data).encode()
    resp.headers['Content-Type'] = 'application/json'
    resp.url = url
    return resp


def _worker_report(mocker, worker_client, job_id, payload):
    """Send ``payload`` through the worker's ``update_job_status``.

    Returns ``(manager_response, worker_logger_mock, conflict_logger_mock)``.
    """
    url = f'{JOBS_URL}{job_id}/'
    sent = {}

    def fake_retry(method, _url, json=None, **_kwargs):
        assert method == 'patch'
        sent['resp'] = worker_client.patch(url, data=json, format='json')
        return _as_requests_response(sent['resp'], url)

    mocker.patch.object(api_handler, '_retry_request', side_effect=fake_retry)
    mocker.patch.object(api_handler, 'get_auth_headers', return_value={})
    worker_logger = mocker.patch.object(api_handler, 'logger')
    conflict_logger = mocker.patch.object(api_conflict, 'logger')
    api_handler.update_job_status(job_id, payload)
    return sent['resp'], worker_logger, conflict_logger


@pytest.mark.django_db
class TestReportAfterAdminCancel:

    @pytest.mark.parametrize('payload', [CANCELED_REPORT, DONE_REPORT],
                             ids=['canceled', 'done'])
    def test_report_gets_409_logged_at_info(
        self, mocker, admin_client, worker_with_token, asset, payload,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(admin_client, worker, worker_client, asset, 'CanRep001')
        assert admin_client.post(f'{JOBS_URL}{job_id}/cancel/').status_code == 200

        resp, worker_logger, conflict_logger = _worker_report(
            mocker, worker_client, job_id, payload,
        )

        assert resp.status_code == 409, resp.data
        assert 'CANCELED' in resp.data['error']
        worker_logger.error.assert_not_called()
        conflict_logger.error.assert_not_called()
        conflict_logger.info.assert_called_once()
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.last_output == ''
        assert job.render_time_seconds is None


@pytest.mark.django_db
class TestReportOnOtherFinishedJobs:

    @pytest.mark.parametrize('final_status', [JobStatus.DONE, JobStatus.ERROR])
    def test_repeated_report_gets_409(
        self, mocker, admin_client, worker_with_token, asset, final_status,
    ):
        """A repeated final report (lost acknowledgement) is not an error."""
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(admin_client, worker, worker_client, asset, 'FinRep001')
        Job.objects.filter(pk=job_id).update(status=final_status)

        resp, worker_logger, _conflict = _worker_report(
            mocker, worker_client, job_id, {'status': final_status},
        )

        assert resp.status_code == 409, resp.data
        worker_logger.error.assert_not_called()
        assert Job.objects.get(pk=job_id).status == final_status

    def test_rendering_report_still_saved(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(admin_client, worker, worker_client, asset, 'FinRep003')

        resp = worker_client.patch(f'{JOBS_URL}{job_id}/', data=DONE_REPORT, format='json')

        assert resp.status_code == 200, resp.data
        assert Job.objects.get(pk=job_id).status == JobStatus.DONE
