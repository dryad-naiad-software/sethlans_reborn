# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for cascade requeue on TiledJob and Animation endpoints.

Covers: POST /api/tiled-jobs/{id}/requeue/ and POST /api/animations/{id}/requeue/
— requeues all ERROR/CANCELED child jobs back to QUEUED, resets fields,
skips DONE/QUEUED/RENDERING jobs, and enforces admin permissions.

Fixes #43.
"""

import pytest
from django.utils import timezone

from workers.models import Animation, Job, JobStatus, TiledJob

TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'


def _create_tiled_job(admin_client, project, asset, name='CascTiled1'):
    """Helper: create a 2x2 TiledJob (4 child jobs) via API."""
    resp = admin_client.post(
        TILED_JOBS_URL,
        data={
            'name': name,
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
        },
        format='json',
    )
    assert resp.status_code == 201
    return TiledJob.objects.get(name=name)


def _create_animation(admin_client, project, asset, name='CascAnim01'):
    """Helper: create a 3-frame animation (3 child jobs) via API."""
    resp = admin_client.post(
        ANIMATIONS_URL,
        data={
            'name': name,
            'project': str(project.pk),
            'asset_id': asset.pk,
            'output_file_pattern': '//render/f_####.png',
            'start_frame': 1,
            'end_frame': 3,
            'frame_step': 1,
        },
        format='json',
    )
    assert resp.status_code == 201
    return Animation.objects.get(name=name)


@pytest.mark.django_db
class TestTiledJobCascadeRequeue:
    """Cascade requeue on TiledJobViewSet."""

    def test_requeues_error_child_jobs(
        self, admin_client, project, asset,
    ):
        """ERROR child jobs are requeued back to QUEUED."""
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjReq0001')
        jobs = list(Job.objects.filter(tiled_job=tiled_job))

        # Put all 4 tiles in ERROR
        for job in jobs:
            job.status = JobStatus.ERROR
            job.error_message = 'render failed'
            job.last_output = 'partial output'
            job.started_at = timezone.now()
            job.completed_at = timezone.now()
            job.auto_requeue_count = 2
            job.save()

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 4

        for job in Job.objects.filter(tiled_job=tiled_job):
            assert job.status == JobStatus.QUEUED
            assert job.assigned_worker is None
            assert job.started_at is None
            assert job.completed_at is None
            assert job.error_message == ''
            assert job.last_output == ''
            assert job.auto_requeue_count == 0
            assert job.is_paused is False

    def test_requeues_canceled_child_jobs(
        self, admin_client, project, asset,
    ):
        """CANCELED child jobs are requeued."""
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjReq0002')
        jobs = list(Job.objects.filter(tiled_job=tiled_job))

        for job in jobs:
            job.status = JobStatus.CANCELED
            job.completed_at = timezone.now()
            job.save()

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 4

    def test_skips_done_and_queued_child_jobs(
        self, admin_client, project, asset,
    ):
        """DONE and QUEUED child jobs are not affected by cascade requeue."""
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjReq0003')
        jobs = list(Job.objects.filter(tiled_job=tiled_job))

        # 1 DONE, 1 QUEUED (default), 2 ERROR
        jobs[0].status = JobStatus.DONE
        jobs[0].completed_at = timezone.now()
        jobs[0].save()
        # jobs[1] stays QUEUED (default)
        jobs[2].status = JobStatus.ERROR
        jobs[2].save()
        jobs[3].status = JobStatus.CANCELED
        jobs[3].save()

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 2

        # DONE job remains DONE
        jobs[0].refresh_from_db()
        assert jobs[0].status == JobStatus.DONE

        # Originally QUEUED job remains QUEUED
        jobs[1].refresh_from_db()
        assert jobs[1].status == JobStatus.QUEUED

    def test_returns_zero_when_nothing_to_requeue(
        self, admin_client, project, asset,
    ):
        """When all child jobs are QUEUED, requeue count is 0."""
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjReq0004')

        resp = admin_client.post(
            f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 0


@pytest.mark.django_db
class TestAnimationCascadeRequeue:
    """Cascade requeue on AnimationViewSet."""

    def test_requeues_error_child_jobs(
        self, admin_client, project, asset,
    ):
        """ERROR child jobs are requeued back to QUEUED."""
        animation = _create_animation(admin_client, project, asset, 'AnReq0001')
        jobs = list(Job.objects.filter(animation=animation))
        assert len(jobs) == 3

        for job in jobs:
            job.status = JobStatus.ERROR
            job.error_message = 'render crashed'
            job.last_output = 'some output'
            job.started_at = timezone.now()
            job.completed_at = timezone.now()
            job.auto_requeue_count = 1
            job.save()

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{animation.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 3

        for job in Job.objects.filter(animation=animation):
            assert job.status == JobStatus.QUEUED
            assert job.assigned_worker is None
            assert job.started_at is None
            assert job.completed_at is None
            assert job.error_message == ''
            assert job.last_output == ''
            assert job.auto_requeue_count == 0
            assert job.is_paused is False

    def test_requeues_canceled_child_jobs(
        self, admin_client, project, asset,
    ):
        """CANCELED child jobs are requeued."""
        animation = _create_animation(admin_client, project, asset, 'AnReq0002')

        Job.objects.filter(animation=animation).update(
            status=JobStatus.CANCELED,
            completed_at=timezone.now(),
        )

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{animation.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 3

    def test_skips_done_and_queued_child_jobs(
        self, admin_client, project, asset,
    ):
        """DONE and QUEUED child jobs are not affected."""
        animation = _create_animation(admin_client, project, asset, 'AnReq0003')
        jobs = list(Job.objects.filter(animation=animation))

        # 1 DONE, 1 QUEUED (default), 1 ERROR
        jobs[0].status = JobStatus.DONE
        jobs[0].completed_at = timezone.now()
        jobs[0].save()
        # jobs[1] stays QUEUED
        jobs[2].status = JobStatus.ERROR
        jobs[2].save()

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{animation.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 1

        jobs[0].refresh_from_db()
        assert jobs[0].status == JobStatus.DONE

        jobs[1].refresh_from_db()
        assert jobs[1].status == JobStatus.QUEUED

    def test_returns_zero_when_nothing_to_requeue(
        self, admin_client, project, asset,
    ):
        """When all child jobs are QUEUED, requeue count is 0."""
        animation = _create_animation(admin_client, project, asset, 'AnReq0004')

        resp = admin_client.post(
            f'{ANIMATIONS_URL}{animation.pk}/requeue/',
        )
        assert resp.status_code == 200
        assert resp.data['requeued'] == 0


@pytest.mark.django_db
class TestCascadeRequeuePermissions:
    """Admin-only enforcement for cascade requeue endpoints."""

    def test_worker_cannot_requeue_tiled_job(
        self, admin_client, project, asset, worker_with_token,
    ):
        _, worker_client = worker_with_token
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjPerm001')

        resp = worker_client.post(
            f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/',
        )
        assert resp.status_code == 403

    def test_worker_cannot_requeue_animation(
        self, admin_client, project, asset, worker_with_token,
    ):
        _, worker_client = worker_with_token
        animation = _create_animation(admin_client, project, asset, 'AnPerm001')

        resp = worker_client.post(
            f'{ANIMATIONS_URL}{animation.pk}/requeue/',
        )
        assert resp.status_code == 403

    def test_unauthenticated_cannot_requeue_tiled_job(
        self, admin_client, project, asset,
    ):
        from rest_framework.test import APIClient
        tiled_job = _create_tiled_job(admin_client, project, asset, 'TjPerm002')
        anon = APIClient()
        resp = anon.post(f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/')
        assert resp.status_code in (401, 403)

    def test_unauthenticated_cannot_requeue_animation(
        self, admin_client, project, asset,
    ):
        from rest_framework.test import APIClient
        animation = _create_animation(admin_client, project, asset, 'AnPerm002')
        anon = APIClient()
        resp = anon.post(f'{ANIMATIONS_URL}{animation.pk}/requeue/')
        assert resp.status_code in (401, 403)
