# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the Job requeue endpoint.

Covers: POST /api/jobs/{id}/requeue/ — valid transitions (ERROR->QUEUED,
CANCELED->QUEUED), invalid transitions (QUEUED, RENDERING, DONE return 409),
field resets, and permission enforcement.

Acceptance criteria: AC-1, AC-2, AC-3, AC-21, AC-23.
"""

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus

JOBS_URL = '/api/jobs/'


def _create_job(admin_client, asset, name='RequeueJob'):
    """Helper: create a QUEUED job via API and return its ID."""
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


@pytest.mark.django_db
class TestRequeueFromError:
    """AC-1: Requeue an ERROR job resets all fields, returns 200."""

    def test_requeue_error_job_returns_200(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'ErrReq001')

        # Force to ERROR with populated fields
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.ERROR
        job.error_message = 'render failed'
        job.last_output = 'some partial output'
        job.started_at = timezone.now()
        job.completed_at = timezone.now()
        job.auto_requeue_count = 2
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'QUEUED'

    def test_requeue_error_clears_all_fields(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'ErrReq002')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.ERROR
        job.error_message = 'render failed'
        job.last_output = 'some partial output'
        job.started_at = timezone.now()
        job.completed_at = timezone.now()
        job.auto_requeue_count = 2
        job.save()

        admin_client.post(f'{JOBS_URL}{job_id}/requeue/')

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
        assert job.started_at is None
        assert job.completed_at is None
        assert job.error_message == ''
        assert job.last_output == ''
        assert job.auto_requeue_count == 0


@pytest.mark.django_db
class TestRequeueFromCanceled:
    """AC-2: Requeue a CANCELED job works identically to ERROR."""

    def test_requeue_canceled_job_returns_200(
        self, admin_client, asset,
    ):
        job_id = _create_job(admin_client, asset, 'CanReq001')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.CANCELED
        job.completed_at = timezone.now()
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 200
        assert resp.data['status'] == 'QUEUED'

    def test_requeue_canceled_resets_fields(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'CanReq002')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.CANCELED
        job.error_message = 'user canceled'
        job.completed_at = timezone.now()
        job.auto_requeue_count = 1
        job.save()

        admin_client.post(f'{JOBS_URL}{job_id}/requeue/')

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
        assert job.started_at is None
        assert job.completed_at is None
        assert job.error_message == ''
        assert job.auto_requeue_count == 0


@pytest.mark.django_db
class TestRequeueInvalidTransitions:
    """AC-3: Requeue from QUEUED, RENDERING, or DONE returns 409."""

    def test_requeue_queued_job_returns_409(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'QueuedRq1')

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 409
        assert 'error' in resp.data

    def test_requeue_rendering_job_returns_409(
        self, admin_client, asset, worker_with_token,
    ):
        worker, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'RenderRq1')

        # Claim to set status to RENDERING
        worker_client.post(
            f'{JOBS_URL}{job_id}/claim/',
            data={'worker_id': worker.pk},
            format='json',
        )

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 409

    def test_requeue_done_job_returns_409(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'DoneReq01')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.DONE
        job.completed_at = timezone.now()
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 409


@pytest.mark.django_db
class TestRequeueReturnsSerializedJob:
    """Verify the response body contains all expected job fields."""

    def test_response_contains_job_fields(self, admin_client, asset):
        job_id = _create_job(admin_client, asset, 'SerReq001')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.ERROR
        job.save()

        resp = admin_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 200
        assert 'id' in resp.data
        assert 'name' in resp.data
        assert resp.data['name'] == 'SerReq001'
        assert resp.data['status'] == 'QUEUED'


@pytest.mark.django_db
class TestRequeueNotFound:
    """Requeue a nonexistent job returns 404."""

    def test_requeue_missing_job_returns_404(self, admin_client):
        resp = admin_client.post(f'{JOBS_URL}999999/requeue/')
        assert resp.status_code == 404


@pytest.mark.django_db
class TestRequeuePermissions:
    """AC-21: Requeue requires IsAdmin. Workers get 403."""

    def test_worker_cannot_requeue(
        self, admin_client, worker_with_token, asset,
    ):
        _, worker_client = worker_with_token
        job_id = _create_job(admin_client, asset, 'PermReq01')

        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.ERROR
        job.save()

        resp = worker_client.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code == 403

    def test_unauthenticated_cannot_requeue(
        self, admin_client, asset,
    ):
        from rest_framework.test import APIClient

        job_id = _create_job(admin_client, asset, 'PermReq02')
        job = Job.objects.get(pk=job_id)
        job.status = JobStatus.ERROR
        job.save()

        anon = APIClient()
        resp = anon.post(f'{JOBS_URL}{job_id}/requeue/')
        assert resp.status_code in (401, 403)
