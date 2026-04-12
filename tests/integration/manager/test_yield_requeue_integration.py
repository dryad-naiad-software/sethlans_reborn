# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/jobs/{pk}/yield-requeue/``.

Covers: RENDERING->QUEUED transition, yield_requeue_count increment,
auto_requeue_count unchanged, concurrent requeue attempts (409),
requeue of non-RENDERING job (409), worker ownership check, and
unauthenticated rejection.

Spec references: FR-7a through FR-7d.
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from workers.models import Job, JobStatus

JOBS_URL = '/api/jobs/'


def _requeue_url(job_pk):
    return f'{JOBS_URL}{job_pk}/yield-requeue/'


def _create_rendering_job(asset, worker):
    """Create a RENDERING job assigned to the given worker."""
    return Job.objects.create(
        name='YieldReqJob1',
        asset=asset,
        output_file_pattern='//render/#.png',
        status=JobStatus.RENDERING,
        assigned_worker=worker,
        started_at=timezone.now(),
    )


@pytest.mark.django_db
class TestYieldRequeueSuccess:
    """POST yield-requeue transitions RENDERING -> QUEUED."""

    def test_rendering_job_transitions_to_queued(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)

        resp = client.post(
            _requeue_url(job.pk),
            data={'reason': 'artist_return'},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['status'] == 'requeued'

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED

    def test_assigned_worker_cleared(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'schedule_window'},
            format='json',
        )
        job.refresh_from_db()
        assert job.assigned_worker is None

    def test_started_at_cleared(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)
        assert job.started_at is not None

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        job.refresh_from_db()
        assert job.started_at is None

    def test_error_message_set_to_interrupted(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'artist_return_gpu_contention'},
            format='json',
        )
        job.refresh_from_db()
        assert 'Interrupted' in job.error_message
        assert 'artist_return_gpu_contention' in job.error_message


@pytest.mark.django_db
class TestYieldRequeueCounters:
    """yield_requeue_count incremented; auto_requeue_count unchanged."""

    def test_yield_requeue_count_incremented(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)
        assert job.yield_requeue_count == 0

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        job.refresh_from_db()
        assert job.yield_requeue_count == 1

    def test_yield_requeue_count_accumulates(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)

        # First yield-requeue
        client.post(
            _requeue_url(job.pk),
            data={'reason': 'first'},
            format='json',
        )
        job.refresh_from_db()
        assert job.yield_requeue_count == 1

        # Re-claim and yield again
        job.status = JobStatus.RENDERING
        job.assigned_worker = worker
        job.started_at = timezone.now()
        job.save()

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'second'},
            format='json',
        )
        job.refresh_from_db()
        assert job.yield_requeue_count == 2

    def test_auto_requeue_count_unchanged(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)
        job.auto_requeue_count = 2
        job.save(update_fields=['auto_requeue_count'])

        client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        job.refresh_from_db()
        assert job.auto_requeue_count == 2


@pytest.mark.django_db
class TestYieldRequeueConflict:
    """Concurrent / invalid requeue attempts return 409."""

    def test_already_queued_returns_409(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = _create_rendering_job(asset, worker)

        # First requeue succeeds
        resp1 = client.post(
            _requeue_url(job.pk),
            data={'reason': 'first'},
            format='json',
        )
        assert resp1.status_code == 200

        # Second attempt on the now-QUEUED job returns 409
        resp2 = client.post(
            _requeue_url(job.pk),
            data={'reason': 'second'},
            format='json',
        )
        assert resp2.status_code == 409

    def test_done_job_returns_409(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = Job.objects.create(
            name='DoneYldJob1',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.DONE,
            completed_at=timezone.now(),
        )
        resp = client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code == 409

    def test_error_job_returns_409(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = Job.objects.create(
            name='ErrYldJob01',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.ERROR,
        )
        resp = client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code == 409

    def test_wrong_worker_returns_409(
        self, make_worker_client, asset,
    ):
        owner, owner_client, _ = make_worker_client('owner-host')
        _, other_client, _ = make_worker_client('other-host')

        job = _create_rendering_job(asset, owner)

        # The non-owner worker tries to yield-requeue
        resp = other_client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code == 409


@pytest.mark.django_db
class TestYieldRequeueAuth:
    """Auth and edge-case responses."""

    def test_unauthenticated_returns_401(
        self, worker_with_token, asset,
    ):
        worker, _ = worker_with_token
        job = _create_rendering_job(asset, worker)

        anon = APIClient()
        resp = anon.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code in (401, 403)

    def test_nonexistent_job_returns_404(
        self, worker_with_token,
    ):
        _, client = worker_with_token
        resp = client.post(
            _requeue_url(999999),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code == 404

    def test_admin_without_worker_profile_returns_403(
        self, admin_client, worker_with_token, asset,
    ):
        worker, _ = worker_with_token
        job = _create_rendering_job(asset, worker)

        resp = admin_client.post(
            _requeue_url(job.pk),
            data={'reason': 'test'},
            format='json',
        )
        assert resp.status_code == 403
