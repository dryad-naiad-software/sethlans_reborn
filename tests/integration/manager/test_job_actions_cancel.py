# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for cancel hardening, cancel_all_jobs, and
upload_output CANCELED rejection.

Covers:
- Cancel with select_for_update (AC-23)
- cancel_all_jobs bulk cancel + parent cascade (AC-4, AC-4a, AC-4b, AC-5)
- upload_output returns 409 for CANCELED job (AC-7a)
"""

import io

import pytest
from django.utils import timezone
from PIL import Image

from workers.models import (
    Animation, Job, JobStatus, TiledJob,
)
from workers.models.jobs import TiledJobStatus

JOBS_URL = '/api/jobs/'
PROJECTS_URL = '/api/projects/'


def _create_job(admin_client, asset, name='CancelJob'):
    """Create a QUEUED job via API."""
    resp = admin_client.post(
        JOBS_URL,
        data={
            'name': name,
            'asset_id': asset.pk,
            'output_file_pattern': '//render/#.png',
        },
        format='json',
    )
    assert resp.status_code == 201
    return resp.data['id']


def _make_test_image():
    """Create a minimal valid PNG in-memory."""
    buf = io.BytesIO()
    img = Image.new('RGB', (64, 64), color='red')
    img.save(buf, format='PNG')
    buf.seek(0)
    buf.name = 'output.png'
    return buf


@pytest.mark.django_db
class TestCancelHardening:
    """AC-23: Cancel uses select_for_update inside atomic."""

    def test_cancel_queued_job_succeeds(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'CanHrd001')
        resp = admin_client.post(f'{JOBS_URL}{job_id}/cancel/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'CANCELED'

        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.completed_at is not None

    def test_cancel_rendering_job_succeeds(
        self, admin_client, asset, worker_with_token,
    ):
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'CanHrd002')

        worker_client.post(
            f'{JOBS_URL}{job_id}/claim/',
            data={'worker_id': worker.pk},
            format='json',
        )

        resp = admin_client.post(f'{JOBS_URL}{job_id}/cancel/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'CANCELED'

    def test_cancel_done_job_returns_409(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'CanHrd003')
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.DONE
        job.completed_at = timezone.now()
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/cancel/')
        assert resp.status_code == 409


@pytest.mark.django_db
class TestCancelAllJobs:
    """AC-4/AC-5/AC-6: cancel_all_jobs endpoint."""

    def test_cancels_queued_and_rendering_jobs(
        self, admin_client, asset, project, worker_with_token,
    ):
        """AC-4: Cancels QUEUED + RENDERING, returns count."""
        worker, worker_client = worker_with_token

        # Create 3 QUEUED jobs
        for i in range(3):
            _create_job(admin_client, asset, f'AllCan0{i}')

        # Claim 2 to make them RENDERING
        jobs = Job.objects.filter(asset=asset).order_by('pk')[:2]
        for j in jobs:
            worker_client.post(
                f'{JOBS_URL}{j.pk}/claim/',
                data={'worker_id': worker.pk},
                format='json',
            )

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.status_code == 200
        assert resp.data['canceled'] == 3

        # Verify all jobs are CANCELED with fields cleared
        for j in Job.objects.filter(asset=asset):
            assert j.status == JobStatus.CANCELED
            assert j.completed_at is not None
            assert j.assigned_worker is None

    def test_cancel_all_returns_zero_when_no_active_jobs(
        self, admin_client, asset, project,
    ):
        """AC-5: Returns {canceled: 0} when all jobs are terminal."""
        job_id = _create_job(admin_client, asset, 'AllDone01')
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.DONE
        job.completed_at = timezone.now()
        job.save()

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.status_code == 200
        assert resp.data['canceled'] == 0

    def test_cancel_all_returns_zero_for_empty_project(
        self, admin_client, project,
    ):
        """No jobs at all returns 0."""
        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.status_code == 200
        assert resp.data['canceled'] == 0

    def test_cancel_all_updates_parent_tiled_job(
        self, admin_client, asset, project,
    ):
        """AC-4a: Parent TiledJob set to ERROR when children all terminal."""
        tiled = TiledJob.objects.create(
            project=project,
            name='TileCancel01',
            asset=asset,
            final_resolution_x=1920,
            final_resolution_y=1080,
            status=TiledJobStatus.RENDERING,
        )
        # Create child jobs linked to the tiled job
        for i in range(4):
            Job.objects.create(
                name=f'Tile0{i}Can',
                asset=asset,
                output_file_pattern='//render/#.png',
                tiled_job=tiled,
                status=JobStatus.QUEUED,
            )

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.data['canceled'] == 4

        tiled.refresh_from_db()
        assert tiled.status == TiledJobStatus.ERROR
        assert tiled.completed_at is not None

    def test_cancel_all_updates_parent_animation(
        self, admin_client, asset, project,
    ):
        """AC-4b: Parent Animation set to ERROR when children all terminal."""
        anim = Animation.objects.create(
            project=project,
            name='AnimCancel1',
            asset=asset,
            output_file_pattern='//render/#.png',
            start_frame=1,
            end_frame=3,
            status=JobStatus.RENDERING,
        )
        for i in range(3):
            Job.objects.create(
                name=f'AnimF{i}Can',
                asset=asset,
                output_file_pattern='//render/#.png',
                animation=anim,
                status=JobStatus.QUEUED,
            )

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.data['canceled'] == 3

        anim.refresh_from_db()
        assert anim.status == JobStatus.ERROR
        assert anim.completed_at is not None

    def test_cancel_all_skips_already_done_tiled_parent(
        self, admin_client, asset, project,
    ):
        """Already-DONE TiledJob parent is not changed to ERROR."""
        tiled = TiledJob.objects.create(
            project=project,
            name='TileDone01',
            asset=asset,
            final_resolution_x=1920,
            final_resolution_y=1080,
        )
        Job.objects.create(
            name='TDoneChild',
            asset=asset,
            output_file_pattern='//render/#.png',
            tiled_job=tiled,
            status=JobStatus.DONE,
            completed_at=timezone.now(),
        )
        # Force TiledJob to DONE after signal sets it to RENDERING
        TiledJob.objects.filter(pk=tiled.pk).update(
            status=TiledJobStatus.DONE,
            completed_at=timezone.now(),
        )

        admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )

        tiled.refresh_from_db()
        assert tiled.status == TiledJobStatus.DONE


@pytest.mark.django_db
class TestUploadOutputCanceledRejection:
    """AC-7a: upload_output returns 409 for CANCELED job."""

    def test_upload_output_canceled_job_returns_409(
        self, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'UpCancel1')

        # Claim, then force to CANCELED (simulates race)
        worker_client.post(
            f'{JOBS_URL}{job_id}/claim/',
            data={'worker_id': worker.pk},
            format='json',
        )
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.CANCELED
        job.save()

        img = _make_test_image()
        resp = worker_client.post(
            f'{JOBS_URL}{job_id}/upload_output/',
            data={'output_file': img},
            format='multipart',
        )
        assert resp.status_code == 409
        assert 'canceled' in resp.data['error'].lower()
