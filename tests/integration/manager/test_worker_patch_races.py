# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker ``PATCH /api/jobs/{id}/`` cannot undo a concurrent cancel/requeue.

Issue #217: the worker PATCH saved the whole row from the copy read at
the start of the request.  A cancel or requeue committed between that
read and the save was overwritten, even by a PATCH that sent only
progress fields.  The race is reproduced by committing the admin change
from ``_check_worker_owns_job``, which the view calls after its read and
before its write.
"""

import pytest

from workers.models import Job, JobStatus
from workers.views.jobs import JobViewSet

from .worker_write_race_helpers import (
    JOBS_URL, cancel_job, create_claimed_job, reassign_job, requeue_job,
    run_after,
)

OWNERSHIP_HOOK = (JobViewSet, '_check_worker_owns_job')

DONE_REPORT = {
    'status': 'DONE',
    'completed_at': '2025-01-01T00:00:00Z',
    'last_output': 'Saved: frame_0001.png',
    'error_message': '',
    'render_time_seconds': 12,
}
PROGRESS_ONLY = {'last_output': 'Fra:1 Mem:12M | Rendering 3/16 tiles'}


def _patch(worker_client, job_id, payload):
    return worker_client.patch(
        f'{JOBS_URL}{job_id}/', data=payload, format='json',
    )


@pytest.mark.django_db(transaction=True)
class TestCancelDuringWorkerPatch:

    @pytest.mark.parametrize('payload', [DONE_REPORT, PROGRESS_ONLY],
                             ids=['done-report', 'progress-only'])
    def test_cancel_survives_and_worker_gets_409(
        self, mocker, admin_client, worker_with_token, asset, payload,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchCancel',
        )
        run_after(mocker, OWNERSHIP_HOOK, cancel_job(admin_client, job_id))

        resp = _patch(worker_client, job_id, payload)

        assert resp.status_code == 409, resp.data
        assert 'error' in resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.completed_at is not None
        assert job.last_output == ''
        assert job.render_time_seconds is None


@pytest.mark.django_db(transaction=True)
class TestRequeueDuringWorkerPatch:

    @pytest.mark.parametrize('payload', [DONE_REPORT, PROGRESS_ONLY],
                             ids=['done-report', 'progress-only'])
    def test_requeue_survives_and_worker_gets_409(
        self, mocker, admin_client, worker_with_token, asset, payload,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchRequeue',
        )
        run_after(mocker, OWNERSHIP_HOOK, requeue_job(job_id))

        resp = _patch(worker_client, job_id, payload)

        assert resp.status_code == 409, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
        assert job.started_at is None
        assert job.completed_at is None
        assert job.last_output == ''

    def test_job_reclaimed_by_other_worker_is_not_touched(
        self, mocker, admin_client, worker_with_token, make_worker_client,
        asset,
    ):
        worker, worker_client = worker_with_token
        other, _client, _token = make_worker_client('otherhost')
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchReassign',
        )
        run_after(mocker, OWNERSHIP_HOOK, reassign_job(job_id, other))

        resp = _patch(worker_client, job_id, DONE_REPORT)

        assert resp.status_code == 409, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.RENDERING
        assert job.assigned_worker == other
        assert job.completed_at is None


@pytest.mark.django_db(transaction=True)
class TestWorkerPatchNormalPath:

    def test_done_report_is_saved(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchDone',
        )

        resp = _patch(worker_client, job_id, DONE_REPORT)

        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'DONE'
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.DONE
        assert job.assigned_worker == worker
        assert job.last_output == DONE_REPORT['last_output']
        assert job.render_time_seconds == 12
        assert job.completed_at is not None

    def test_save_writes_only_the_sent_fields(
        self, mocker, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchFields',
        )
        save_spy = mocker.spy(Job, 'save')

        resp = _patch(worker_client, job_id, DONE_REPORT)

        assert resp.status_code == 200, resp.data
        worker_saves = [
            c for c in save_spy.call_args_list
            if c.args[0].pk == job_id
        ]
        assert worker_saves[0].kwargs['update_fields'] == sorted(DONE_REPORT)

    def test_error_report_is_saved(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchError',
        )

        resp = _patch(worker_client, job_id, {
            'status': 'ERROR',
            'error_message': 'Worker render thread crashed unexpectedly.',
        })

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.ERROR
        assert job.error_message.startswith('Worker render thread')

    def test_worker_cannot_write_admin_fields(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchAdminFld',
        )

        resp = _patch(worker_client, job_id, {
            'name': 'Renamed', 'start_frame': 7, 'last_output': 'x',
        })

        assert resp.status_code == 400, resp.data
        assert set(resp.data) == {'name', 'start_frame'}
        job = Job.objects.get(pk=job_id)
        assert job.name == 'PatchAdminFld'
        assert job.start_frame != 7
        assert job.last_output == ''

    def test_patch_from_unassigned_worker_is_still_403(
        self, admin_client, worker_with_token, make_worker_client, asset,
    ):
        worker, worker_client = worker_with_token
        _other, other_client, _token = make_worker_client('intruder')
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'PatchIntruder',
        )

        resp = _patch(other_client, job_id, DONE_REPORT)

        assert resp.status_code == 403
        assert Job.objects.get(pk=job_id).status == JobStatus.RENDERING
