# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for job-level pause/unpause.

Cascade, cancel, requeue, index and permission tests are in
``test_job_pause_cascade.py``.

Covers: AC-5 through AC-19a from the job-pause spec.
"""

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus

JOBS_URL = '/api/jobs/'


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
        self, admin_client, asset,
    ):
        """AC-13a: PATCH status QUEUED->RENDERING on paused job is rejected.

        Sent by an admin: a worker PATCH on a job not assigned to it gets
        409 before validation since #247 (test_blender_reconcile.py).
        """
        job_id = _create_job(admin_client, asset, 'SerPaus01')

        # Pause the job
        admin_client.post(f'{JOBS_URL}{job_id}/pause/')

        # Try to PATCH to RENDERING
        resp = admin_client.patch(
            f'{JOBS_URL}{job_id}/',
            data={'status': 'RENDERING'},
            format='json',
        )
        assert resp.status_code == 400
        assert 'paused' in str(resp.data).lower()
