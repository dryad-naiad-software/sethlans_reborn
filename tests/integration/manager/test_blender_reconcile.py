# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: restart and live reconciliation of Blender jobs (#247).

A RENDERING Blender job goes back to the queue when its worker registers
with a new boot id, or when the worker's heartbeat stops listing it for
longer than the grace period. Jobs are claimed through the real claim
endpoint so ``started_at`` and the parent signals are as in production.
"""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from workers.constants import LIVE_RECONCILE_GRACE_SECONDS
from workers.models import Animation, Job, JobStatus, TiledJob, Worker

BOOT_A = 'a' * 32
BOOT_B = 'b' * 32
JOBS_URL = '/api/jobs/'
RESTART = "Auto-requeued: worker restarted during Blender render"
NOT_ASSIGNED = "Job is no longer assigned to this worker; the update was not applied."
LIVE = "Auto-requeued: worker is not running this job"
OLD = timedelta(seconds=LIVE_RECONCILE_GRACE_SECONDS + 5)


def _queued_job(asset, name='BlendRec01'):
    return Job.objects.create(
        name=name, asset=asset, output_file_pattern='//r/f_####.png',
        start_frame=1, end_frame=1,
    )


def _claim(client, worker, job):
    resp = client.post(f'{JOBS_URL}{job.pk}/claim/', {'worker_id': worker.pk}, format='json')
    assert resp.status_code == 200, resp.data


def _age(job, delta=OLD):
    Job.objects.filter(pk=job.pk).update(started_at=timezone.now() - delta)


def _register(client, worker, boot_id):
    resp = client.post('/api/heartbeat/', {
        'hostname': worker.hostname, 'os': 'Windows', 'worker_boot_id': boot_id,
        'available_tools': {'blender': ['4.2.19']},
    }, format='json')
    assert resp.status_code == 200, resp.data


def _beat(client, worker, active_ids, boot_id=BOOT_A):
    resp = client.post('/api/heartbeat/', {
        'hostname': worker.hostname, 'active_job_ids': active_ids, 'worker_boot_id': boot_id,
    }, format='json')
    assert resp.status_code == 200, resp.data


@pytest.mark.django_db
class TestBootReconciliation:

    def test_new_boot_id_requeues_the_job(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.started_at) == (JobStatus.QUEUED, None, None)
        assert (job.error_message, job.auto_requeue_count) == (RESTART, 1)

    def test_same_boot_id_keeps_the_job(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_A)
        job.refresh_from_db()
        assert job.status == JobStatus.RENDERING

    def test_job_claimed_by_the_new_process_is_kept(self, booted, asset):
        worker, client = booted
        _register(client, worker, BOOT_B)
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)  # a retried registration
        job.refresh_from_db()
        assert job.status == JobStatus.RENDERING

    def test_cap_sets_error(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        Job.objects.filter(pk=job.pk).update(auto_requeue_count=3)
        _register(client, worker, BOOT_B)
        job.refresh_from_db()
        assert (job.status, job.auto_requeue_count) == (JobStatus.ERROR, 4)
        assert job.error_message == "Max auto-requeue attempts exceeded (3)"


@pytest.mark.django_db
class TestLiveReconciliation:

    def test_unlisted_job_past_the_grace_is_requeued(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _age(job)
        _beat(client, worker, [])
        job.refresh_from_db()
        assert (job.status, job.error_message, job.auto_requeue_count) == (JobStatus.QUEUED, LIVE, 1)

    def test_listed_job_is_kept(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _age(job)
        _beat(client, worker, [job.pk])
        job.refresh_from_db()
        assert job.status == JobStatus.RENDERING

    def test_job_claimed_moments_ago_is_kept(self, booted, asset):
        """A heartbeat built before the claim was recorded does not list it."""
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _beat(client, worker, [])
        job.refresh_from_db()
        assert (job.status, job.auto_requeue_count) == (JobStatus.RENDERING, 0)


def _late_writes(client, job):
    """The old run's final report and upload, as the worker sends them."""
    report = client.patch(f'{JOBS_URL}{job.pk}/', {
        'status': 'DONE', 'completed_at': '2026-11-16T00:00:00Z',
        'last_output': 'Fra:1 Saved f_0001.png', 'error_message': '',
        'render_time_seconds': 12,
    }, format='json')
    upload = client.post(f'{JOBS_URL}{job.pk}/upload_output/', {
        'output_file': SimpleUploadedFile('f_0001.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 32),
    }, format='multipart')
    return report, upload


@pytest.mark.django_db
class TestLateWritesFromTheOldRun:
    """Neither the report nor the upload of the old run reaches the job."""

    def test_on_the_requeued_job(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)
        report, upload = _late_writes(client, job)
        # 409 "no longer assigned", before validation and before storing (#247).
        assert (report.status_code, upload.status_code) == (409, 409)
        assert report.data == upload.data == {'error': NOT_ASSIGNED}
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.last_output) == (JobStatus.QUEUED, None, '')
        assert not job.output_file

    def test_on_the_job_claimed_by_another_worker(self, booted, asset, make_worker_client):
        worker, client = booted
        other, other_client, _token = make_worker_client('rec-other')
        Worker.objects.filter(pk=other.pk).update(available_tools={'blender': ['4.2.19']})
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)
        _claim(other_client, other, job)
        report, upload = _late_writes(client, job)
        assert (report.status_code, upload.status_code) == (409, 409)
        assert report.data == upload.data == {'error': NOT_ASSIGNED}
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.last_output) == (JobStatus.RENDERING, other.pk, '')
        assert not job.output_file

    def test_non_worker_caller_keeps_403(self, booted, asset, django_user_model):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        plain = APIClient()
        plain.force_authenticate(user=django_user_model.objects.create_user(username='plainuser'))
        report, upload = _late_writes(plain, job)
        assert (report.status_code, upload.status_code) == (403, 403)
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.last_output) == (JobStatus.RENDERING, worker.pk, '')
        assert not job.output_file


def _tiled_job(admin_client, project, asset):
    resp = admin_client.post('/api/tiled-jobs/', {
        'name': 'RecTiled01', 'project': str(project.pk), 'asset_id': asset.pk,
        'final_resolution_x': 800, 'final_resolution_y': 600,
        'tile_count_x': 2, 'tile_count_y': 2,
    }, format='json')
    assert resp.status_code == 201, resp.data
    return TiledJob.objects.get(pk=resp.data['id'])


def _animation(admin_client, project, asset, end_frame):
    resp = admin_client.post('/api/animations/', {
        'name': 'RecAnim01', 'project': str(project.pk), 'asset_id': asset.pk,
        'output_file_pattern': 'anim_####.png', 'start_frame': 1,
        'end_frame': end_frame, 'frame_step': 1, 'tiling_config': 'NONE',
    }, format='json')
    assert resp.status_code == 201, resp.data
    return Animation.objects.get(pk=resp.data['id'])


@pytest.mark.django_db
class TestChildren:

    def test_tile_is_requeued_and_the_parent_stays_rendering(self, booted, admin_client, project, asset):
        worker, client = booted
        tiled = _tiled_job(admin_client, project, asset)
        tile = tiled.jobs.order_by('pk').first()
        _claim(client, worker, tile)
        assert TiledJob.objects.get(pk=tiled.pk).status == JobStatus.RENDERING
        _register(client, worker, BOOT_B)
        tile.refresh_from_db()
        assert (tile.status, tile.error_message) == (JobStatus.QUEUED, RESTART)
        tiled.refresh_from_db()
        assert (tiled.status, tiled.error_message) == (JobStatus.RENDERING, '')
        assert set(tiled.jobs.values_list('status', flat=True)) == {JobStatus.QUEUED}

    def test_animation_frame_is_requeued_and_the_parent_stays_rendering(
            self, booted, admin_client, project, asset):
        worker, client = booted
        animation = _animation(admin_client, project, asset, end_frame=2)
        frame_job = animation.jobs.order_by('pk').first()
        _claim(client, worker, frame_job)
        _age(frame_job)
        _beat(client, worker, [])
        frame_job.refresh_from_db()
        assert (frame_job.status, frame_job.error_message) == (JobStatus.QUEUED, LIVE)
        animation.refresh_from_db()
        assert (animation.status, animation.error_message) == (JobStatus.RENDERING, '')

    def test_capped_frame_fails_the_animation(self, booted, admin_client, project, asset):
        worker, client = booted
        animation = _animation(admin_client, project, asset, end_frame=1)
        frame_job = animation.jobs.get()
        _claim(client, worker, frame_job)
        Job.objects.filter(pk=frame_job.pk).update(auto_requeue_count=3)
        _register(client, worker, BOOT_B)
        frame_job.refresh_from_db()
        assert frame_job.status == JobStatus.ERROR
        animation.refresh_from_db()
        assert animation.status == JobStatus.ERROR
        assert "Max auto-requeue attempts exceeded (3)" in animation.error_message
