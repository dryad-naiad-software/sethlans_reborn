# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``POST /api/jobs/{id}/release/`` (Unreal foundation
FR-RT-11, FR-RT-13, D4, FR-T-3, AC-22).
"""

import pytest

from workers.models import Job, JobStatus

from .unreal_helpers import unreal_asset


def _rendering(asset, worker, name='ReleaseJob'):
    job = Job.objects.create(name=name, asset=asset, output_file_pattern='//r/f.png')
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
    return job


def _release(client, job_pk, reason='shutdown'):
    return client.post(f'/api/jobs/{job_pk}/release/', {'reason': reason}, format='json')


@pytest.mark.django_db
class TestRelease:

    @pytest.mark.parametrize('reason', ['shutdown', 'dispatch_failed', 'share_unavailable'])
    def test_assigned_worker_requeues(self, worker_with_token, reason):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = _release(client, job.pk, reason)
        assert (resp.status_code, resp.data) == (200, {'status': 'QUEUED', 'auto_requeue_count': 1})
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.started_at) == (JobStatus.QUEUED, None, None)
        assert job.error_message == f"Auto-requeued: worker released Unreal job ({reason})"

    def test_fourth_release_is_error(self, worker_with_token):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        for count in (1, 2, 3):
            assert _release(client, job.pk).data == {'status': 'QUEUED', 'auto_requeue_count': count}
            Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
        resp = _release(client, job.pk)
        assert resp.data == {'status': 'ERROR', 'auto_requeue_count': 4}
        job.refresh_from_db()
        assert (job.status, job.error_message) == (JobStatus.ERROR, "Max auto-requeue attempts exceeded (3)")

    def test_repeat_after_success_is_409(self, worker_with_token):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        assert _release(client, job.pk).status_code == 200
        resp = _release(client, job.pk)
        assert (resp.status_code, resp.data) == (409, {'error': "Job is not rendering on this worker."})

    def test_other_worker_and_non_rendering_job(self, worker_with_token, make_worker_client, asset):
        worker, _client = worker_with_token
        _other, other_client, _token = make_worker_client('release-other')
        unreal = _rendering(unreal_asset(), worker)
        blender = _rendering(asset, worker, name='ReleaseBlender')
        queued = Job.objects.create(name='Queued', asset=unreal.asset, output_file_pattern='//r/q.png')
        for job in (unreal, blender, queued):
            resp = _release(other_client, job.pk)
            assert (resp.status_code, resp.data) == (409, {'error': "Job is not rendering on this worker."})
        unreal.refresh_from_db()
        assert unreal.status == JobStatus.RENDERING

    def test_blender_release_is_unsupported(self, worker_with_token, asset):
        worker, client = worker_with_token
        job = _rendering(asset, worker)
        resp = _release(client, job.pk)
        assert (resp.status_code, resp.data) == (409, {'error': "Release is not supported for this renderer."})
        assert Job.objects.get(pk=job.pk).status == JobStatus.RENDERING

    @pytest.mark.parametrize('reason', ['reassigned_abort', 'other', ''])
    def test_bad_reason(self, worker_with_token, reason):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = _release(client, job.pk, reason)
        assert resp.status_code == 400
        assert list(resp.data) == ['reason']
        if reason:
            assert resp.data['reason'] == [f'"{reason}" is not a valid choice.']

    @pytest.mark.parametrize('pk', [999999, 'abc'])
    def test_missing_job(self, worker_with_token, pk):
        _worker, client = worker_with_token
        resp = _release(client, pk)
        assert (resp.status_code, resp.data) == (404, {'error': "Job not found."})

    def test_admin_is_refused(self, admin_client, worker_with_token):
        worker, _client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        assert _release(admin_client, job.pk).status_code == 403
