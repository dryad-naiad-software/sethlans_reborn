# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for job-level pause/unpause.

Covers: AC-5 through AC-19a from the job-pause spec.
"""

import pytest
from django.utils import timezone

from workers.models import (
    Animation, Job, JobStatus, TiledJob,
)

JOBS_URL = '/api/jobs/'
PROJECTS_URL = '/api/projects/'
TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'


def _create_job(admin_client, asset, name='PauseJob'):
    """Create a QUEUED job via API and return its ID."""
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


# --- AC-5, AC-6: Job model has is_paused; visible in API ---

@pytest.mark.django_db
class TestJobIsPausedField:

    def test_job_defaults_to_not_paused(self, admin_client, asset):
        """AC-5: Job.is_paused defaults to False."""
        job_id = _create_job(admin_client, asset, 'DefPause1')
        job = Job.objects.get(pk=job_id)
        assert job.is_paused is False

    def test_is_paused_in_api_response(self, admin_client, asset):
        """AC-6: GET /api/jobs/{id}/ includes is_paused."""
        job_id = _create_job(admin_client, asset, 'APIPause1')
        resp = admin_client.get(f'{JOBS_URL}{job_id}/')
        assert resp.status_code == 200
        assert 'is_paused' in resp.data
        assert resp.data['is_paused'] is False


# --- AC-7, AC-8, AC-9: Pause action ---

@pytest.mark.django_db
class TestJobPause:

    def test_pause_queued_job(self, admin_client, asset):
        """AC-7: Pause QUEUED non-paused job returns 200, is_paused=True."""
        job_id = _create_job(admin_client, asset, 'Pause0001')
        resp = admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code == 200
        assert resp.data['is_paused'] is True

        job = Job.objects.get(pk=job_id)
        assert job.is_paused is True
        assert job.status == JobStatus.QUEUED

    def test_pause_rendering_job_returns_409(
        self, admin_client, asset, worker_with_token,
    ):
        """AC-8: Pause RENDERING job returns 409."""
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'Pause0002')
        worker_client.post(
            f'{JOBS_URL}{job_id}/claim/',
            data={'worker_id': worker.pk},
            format='json',
        )
        resp = admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code == 409

    def test_pause_already_paused_returns_409(self, admin_client, asset):
        """AC-9: Pause already-paused job returns 409."""
        job_id = _create_job(admin_client, asset, 'Pause0003')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        resp = admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code == 409
        assert 'already paused' in resp.data['error']

    def test_pause_done_job_returns_409(self, admin_client, asset):
        """Cannot pause a DONE job."""
        job_id = _create_job(admin_client, asset, 'Pause0004')
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.DONE
        job.completed_at = timezone.now()
        job.save()
        resp = admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        assert resp.status_code == 409


# --- AC-10, AC-11: Unpause action ---

@pytest.mark.django_db
class TestJobUnpause:

    def test_unpause_paused_job(self, admin_client, asset):
        """AC-10: Unpause paused job returns 200, is_paused=False."""
        job_id = _create_job(admin_client, asset, 'Unpaus001')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')
        resp = admin_client.post(f'{JOBS_URL}{job_id}/unpause/')
        assert resp.status_code == 200
        assert resp.data['is_paused'] is False

        job = Job.objects.get(pk=job_id)
        assert job.is_paused is False

    def test_unpause_non_paused_returns_409(self, admin_client, asset):
        """AC-11: Unpause non-paused job returns 409."""
        job_id = _create_job(admin_client, asset, 'Unpaus002')
        resp = admin_client.post(f'{JOBS_URL}{job_id}/unpause/')
        assert resp.status_code == 409
        assert 'not paused' in resp.data['error']


# --- AC-12: Worker poll excludes paused jobs ---

@pytest.mark.django_db
class TestPausedJobPolling:

    def test_paused_jobs_excluded_from_worker_poll(
        self, admin_client, worker_with_token, asset,
    ):
        """AC-12: Paused jobs are not returned to polling workers."""
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'PollPaus1')

        # Pause the job
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        # Worker polls
        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'True',
                'available_versions': '4.2.19',
            },
        )
        assert resp.status_code == 200
        names = [j['name'] for j in resp.data]
        assert 'PollPaus1' not in names

    def test_unpaused_jobs_visible_in_poll(
        self, admin_client, worker_with_token, asset,
    ):
        """Unpaused jobs appear in worker polling."""
        worker, worker_client = worker_with_token
        _create_job(admin_client, asset, 'PollVis01')

        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'True',
                'available_versions': '4.2.19',
            },
        )
        assert resp.status_code == 200
        names = [j['name'] for j in resp.data]
        assert 'PollVis01' in names


# --- AC-13: Claim paused job returns 409 ---

@pytest.mark.django_db
class TestClaimPausedJob:

    def test_claim_paused_job_returns_409(
        self, admin_client, worker_with_token, asset,
    ):
        """AC-13: Attempting to claim a paused job returns 409."""
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'ClmPaus01')
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        resp = worker_client.post(
            f'{JOBS_URL}{job_id}/claim/',
            data={'worker_id': worker.pk},
            format='json',
        )
        assert resp.status_code == 409
        assert 'paused' in resp.data['error'].lower()


# --- AC-13a: Serializer rejects QUEUED->RENDERING on paused job ---

@pytest.mark.django_db
class TestSerializerPauseGuard:

    def test_patch_paused_job_to_rendering_rejected(
        self, admin_client, worker_with_token, asset,
    ):
        """AC-13a: PATCH status QUEUED->RENDERING on paused job is rejected."""
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'SerPaus01')

        # Pause the job
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        # Try to PATCH to RENDERING
        resp = worker_client.patch(
            f'{JOBS_URL}{job_id}/',
            data={'status': 'RENDERING'},
            format='json',
        )
        assert resp.status_code == 400
        assert 'paused' in str(resp.data).lower()


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
