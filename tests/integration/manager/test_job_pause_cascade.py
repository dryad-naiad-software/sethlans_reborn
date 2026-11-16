# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for job pause: cascades from tiled jobs and
animations, cancel and requeue clearing the pause, the poll index and
permissions.

Split from ``test_job_pause.py`` (file size limit); ``_create_job``
is shared from there.
"""

import pytest
from django.utils import timezone

from workers.models import (
    Animation, Job, JobStatus, TiledJob,
)

from .test_job_pause import JOBS_URL, _create_job

PROJECTS_URL = '/api/projects/'
TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'


# --- AC-14, AC-15: TiledJob cascade pause/unpause ---

@pytest.mark.django_db
class TestTiledJobCascadePause:

    def test_cascade_pause_tiled_job(self, admin_client, asset, project):
        """AC-14: Cascade pause pauses all QUEUED child jobs."""
        tiled = TiledJob.objects.create(
            project=project, name='TilePaus01',
            asset=asset, final_resolution_x=1920,
            final_resolution_y=1080,
        )
        for i in range(4):
            Job.objects.create(
                name=f'TP01Tile{i}',
                asset=asset,
                output_file_pattern='//render/#.png',
                tiled_job=tiled,
                status=JobStatus.QUEUED,
            )

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled.pk}/pause/',
        )
        assert resp.status_code == 200
        assert resp.data['paused'] == 4

        paused = Job.objects.filter(tiled_job=tiled, is_paused=True)
        assert paused.count() == 4

    def test_cascade_pause_skips_non_queued(
        self, admin_client, asset, project, worker_with_token,
    ):
        """Cascade pause only affects QUEUED jobs."""
        worker, worker_client = worker_with_token
        tiled = TiledJob.objects.create(
            project=project, name='TilePaus02',
            asset=asset, final_resolution_x=1920,
            final_resolution_y=1080,
        )
        q_job = Job.objects.create(
            name='TP02Tile0',
            asset=asset,
            output_file_pattern='//render/#.png',
            tiled_job=tiled,
            status=JobStatus.QUEUED,
        )
        r_job = Job.objects.create(
            name='TP02Tile1',
            asset=asset,
            output_file_pattern='//render/#.png',
            tiled_job=tiled,
            status=JobStatus.RENDERING,
            assigned_worker=worker,
        )

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled.pk}/pause/',
        )
        assert resp.data['paused'] == 1

        q_job.refresh_from_db()
        r_job.refresh_from_db()
        assert q_job.is_paused is True
        assert r_job.is_paused is False

    def test_cascade_unpause_tiled_job(
        self, admin_client, asset, project,
    ):
        """AC-15: Cascade unpause unpauses all paused QUEUED child jobs."""
        tiled = TiledJob.objects.create(
            project=project, name='TileUnp01',
            asset=asset, final_resolution_x=1920,
            final_resolution_y=1080,
        )
        for i in range(4):
            Job.objects.create(
                name=f'TU01Tile{i}',
                asset=asset,
                output_file_pattern='//render/#.png',
                tiled_job=tiled,
                status=JobStatus.QUEUED,
                is_paused=True,
            )

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled.pk}/unpause/',
        )
        assert resp.status_code == 200
        assert resp.data['unpaused'] == 4

        unpaused = Job.objects.filter(tiled_job=tiled, is_paused=False)
        assert unpaused.count() == 4


# --- AC-16, AC-17: Animation cascade pause/unpause ---

@pytest.mark.django_db
class TestAnimationCascadePause:

    def test_cascade_pause_animation(
        self, admin_client, asset, project,
    ):
        """AC-16: Cascade pause pauses all QUEUED child jobs."""
        anim = Animation.objects.create(
            project=project, name='AnimPaus01',
            asset=asset,
            output_file_pattern='//render/#.png',
            start_frame=1, end_frame=3,
        )
        for i in range(3):
            Job.objects.create(
                name=f'AP01Frame{i}',
                asset=asset,
                output_file_pattern='//render/#.png',
                animation=anim,
                status=JobStatus.QUEUED,
            )

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{anim.pk}/pause/',
        )
        assert resp.status_code == 200
        assert resp.data['paused'] == 3

    def test_cascade_unpause_animation(
        self, admin_client, asset, project,
    ):
        """AC-17: Cascade unpause unpauses all paused QUEUED child jobs."""
        anim = Animation.objects.create(
            project=project, name='AnimUnp01',
            asset=asset,
            output_file_pattern='//render/#.png',
            start_frame=1, end_frame=3,
        )
        for i in range(3):
            Job.objects.create(
                name=f'AU01Frame{i}',
                asset=asset,
                output_file_pattern='//render/#.png',
                animation=anim,
                status=JobStatus.QUEUED,
                is_paused=True,
            )

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{anim.pk}/unpause/',
        )
        assert resp.status_code == 200
        assert resp.data['unpaused'] == 3


# --- AC-18: cancel_all_jobs includes paused and clears is_paused ---

@pytest.mark.django_db
class TestCancelAllIncludesPaused:

    def test_cancel_all_cancels_paused_jobs(
        self, admin_client, asset, project,
    ):
        """AC-18: cancel_all_jobs cancels paused QUEUED jobs."""
        job_id = _create_job(admin_client, asset, 'CAllPaus1')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        resp = admin_client.post(
            f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/',
        )
        assert resp.status_code == 200
        assert resp.data['canceled'] == 1

        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.is_paused is False


# --- AC-18a: Single cancel clears is_paused ---

@pytest.mark.django_db
class TestSingleCancelClearsPause:

    def test_cancel_paused_job_clears_is_paused(
        self, admin_client, asset,
    ):
        """AC-18a: Cancel on a paused QUEUED job clears is_paused."""
        job_id = _create_job(admin_client, asset, 'CanPaus01')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        resp = admin_client.post(f'{JOBS_URL}{job_id}/cancel/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'CANCELED'

        job = Job.objects.get(pk=job_id)
        assert job.is_paused is False


# --- AC-19: Requeue clears is_paused ---

@pytest.mark.django_db
class TestRequeueClearsPause:

    def test_requeue_clears_is_paused(self, admin_client, asset):
        """AC-19: Requeue clears is_paused to False."""
        job_id = _create_job(admin_client, asset, 'ReqPaus01')

        # Pause, then cancel (to get into cancelable state)
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.CANCELED
        job.is_paused = True  # simulate stale state
        job.completed_at = timezone.now()
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 200

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.is_paused is False


# --- AC-19a: Composite index exists ---

@pytest.mark.django_db
class TestCompositeIndex:

    def test_job_has_status_is_paused_index(self):
        """AC-19a: Job model has composite index on (status, is_paused)."""
        indexes = Job._meta.indexes
        field_sets = [tuple(idx.fields) for idx in indexes]
        assert ('status', 'is_paused') in field_sets


# --- Permission tests ---

@pytest.mark.django_db
class TestPausePermissions:

    def test_worker_cannot_pause_job(
        self, admin_client, worker_with_token, asset,
    ):
        """Pause requires IsAdmin; workers get 403."""
        _, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'PermPaus1')
        resp = worker_client.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code == 403

    def test_worker_cannot_unpause_job(
        self, admin_client, worker_with_token, asset,
    ):
        """Unpause requires IsAdmin; workers get 403."""
        _, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'PermUnp01')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        resp = worker_client.post(f'{JOBS_URL}{job_id}/unpause/')
        assert resp.status_code == 403

    def test_unauthenticated_cannot_pause(self, admin_client, asset):
        """Unauthenticated requests get 401/403."""
        from rest_framework.test import APIClient

        job_id = _create_job(admin_client, asset, 'PermAnon1')
        anon = APIClient()
        resp = anon.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code in (401, 403)
