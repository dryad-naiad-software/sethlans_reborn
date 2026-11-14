# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Admin ``PATCH /api/jobs/{id}/`` cannot undo a concurrent worker report.

Issue #217 (review follow-up): the admin PATCH also saved the whole row
from the copy read at the start of the request, so a worker's DONE
committed between that read and the save went back to RENDERING.  The
race is reproduced by committing the worker's report from the serializer
validation hook, which runs after the view's read and before its write.
"""

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus

from .worker_write_race_helpers import JOBS_URL, create_claimed_job, run_after

VALIDATION_HOOK = 'workers.serializers.jobs.validate_output_pattern_extension'


def _worker_reports_done(job_id):
    def _done():
        Job.objects.filter(pk=job_id).update(
            status=JobStatus.DONE, completed_at=timezone.now(),
            render_time_seconds=5, last_output='Saved: frame.png',
        )
    return _done


def _admin_patch(admin_client, job_id, payload):
    return admin_client.patch(
        f'{JOBS_URL}{job_id}/', data=payload, format='json',
    )


@pytest.mark.django_db(transaction=True)
class TestAdminPatch:

    def test_admin_patch_saves_admin_fields(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'AdminPatch',
        )

        resp = _admin_patch(
            admin_client, job_id, {'name': 'AdminRenamed', 'last_output': 'note'},
        )

        assert resp.status_code == 200, resp.data
        assert resp.data['name'] == 'AdminRenamed'
        job = Job.objects.get(pk=job_id)
        assert job.name == 'AdminRenamed'
        assert job.last_output == 'note'
        assert job.status == JobStatus.RENDERING
        assert job.assigned_worker == worker

    def test_worker_done_during_admin_patch_survives(
        self, mocker, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'AdminRace',
        )
        run_after(mocker, VALIDATION_HOOK, _worker_reports_done(job_id))

        resp = _admin_patch(admin_client, job_id, {'name': 'AdminRenamed2'})

        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'DONE'
        job = Job.objects.get(pk=job_id)
        assert job.name == 'AdminRenamed2'
        assert job.status == JobStatus.DONE
        assert job.render_time_seconds == 5
        assert job.completed_at is not None
        assert job.assigned_worker == worker

    def test_admin_status_change_is_revalidated_after_lock(
        self, mocker, admin_client, worker_with_token, asset,
    ):
        """RENDERING -> CANCELED was valid at read time; DONE -> CANCELED is not."""
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'AdminStatus',
        )
        run_after(mocker, VALIDATION_HOOK, _worker_reports_done(job_id))

        resp = _admin_patch(admin_client, job_id, {'status': 'CANCELED', 'name': 'Renamed'})

        assert resp.status_code == 409, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.DONE
        assert job.name == 'AdminStatus'

    def test_admin_save_writes_only_the_sent_fields(
        self, mocker, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'AdminFields',
        )
        save_spy = mocker.spy(Job, 'save')

        resp = _admin_patch(admin_client, job_id, {'name': 'Renamed', 'last_output': 'o'})

        assert resp.status_code == 200, resp.data
        saves = [c for c in save_spy.call_args_list if c.args[0].pk == job_id]
        assert saves[0].kwargs['update_fields'] == ['last_output', 'name']
