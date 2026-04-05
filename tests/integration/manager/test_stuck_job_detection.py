# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for stuck job detection and auto-requeue.

Tests call requeue_stuck_jobs() directly with real model instances
rather than going through the heartbeat endpoint, to isolate the
detection logic from heartbeat auth and enrollment concerns.

Covers: AC-6, AC-6a, AC-6b, AC-6c, AC-7.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus, Worker
from workers.views.stuck_jobs import (
    _MAX_AUTO_REQUEUE,
    requeue_stuck_jobs,
)

JOBS_URL = '/api/jobs/'


@pytest.fixture(autouse=True)
def _reset_debounce():
    """Reset the module-level debounce timestamp before each test."""
    import workers.views.stuck_jobs as mod
    mod._last_stuck_check = 0.0


@pytest.fixture
def stale_worker(db):
    """Create a worker whose last_seen is well past staleness.

    Worker.last_seen uses auto_now=True so we must use update()
    after creation to set a stale timestamp.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username='stale_worker_user')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='stale-host',
        user=user,
        is_active=True,
    )
    stale_time = timezone.now() - timedelta(seconds=200)
    Worker.objects.filter(pk=worker.pk).update(last_seen=stale_time)
    worker.refresh_from_db()
    return worker


@pytest.fixture
def fresh_worker(db):
    """Create a worker whose last_seen is recent (auto_now handles it)."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username='fresh_worker_user')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='fresh-host',
        user=user,
        is_active=True,
    )
    return worker


@pytest.mark.django_db
class TestStuckJobRequeue:
    """AC-6: RENDERING job assigned to stale worker gets requeued."""

    def test_stuck_job_requeued_to_queued(
        self, asset, stale_worker,
    ):
        job = Job.objects.create(
            name='StuckJob001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
            last_output='partial render log',
        )

        count = requeue_stuck_jobs()
        assert count == 1

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
        assert job.started_at is None
        assert job.completed_at is None
        assert 'Auto-requeued' in job.error_message
        assert job.auto_requeue_count == 1

    def test_stuck_job_preserves_last_output(
        self, asset, stale_worker,
    ):
        """AC-7: last_output is NOT cleared on auto-requeue."""
        job = Job.objects.create(
            name='StuckKeep01',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
            last_output='diagnostic output preserved',
        )

        requeue_stuck_jobs()
        job.refresh_from_db()
        assert job.last_output == 'diagnostic output preserved'

    def test_fresh_worker_job_not_requeued(
        self, asset, fresh_worker,
    ):
        """Jobs assigned to active workers are left alone."""
        job = Job.objects.create(
            name='FreshJob001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=fresh_worker,
            started_at=timezone.now(),
        )

        count = requeue_stuck_jobs()
        assert count == 0

        job.refresh_from_db()
        assert job.status == JobStatus.RENDERING


@pytest.mark.django_db
class TestStuckJobMaxRequeue:
    """AC-6a: Exceeding max auto-requeue sets job to ERROR."""

    def test_exceeds_max_requeue_sets_error(
        self, asset, stale_worker,
    ):
        job = Job.objects.create(
            name='MaxReq0001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
            auto_requeue_count=_MAX_AUTO_REQUEUE,
        )

        count = requeue_stuck_jobs()
        assert count == 1

        job.refresh_from_db()
        assert job.status == JobStatus.ERROR
        assert job.completed_at is not None
        assert 'Max auto-requeue attempts exceeded' in job.error_message
        assert job.auto_requeue_count == _MAX_AUTO_REQUEUE + 1

    def test_at_max_still_requeues(self, asset, stale_worker):
        """At exactly max count, still requeues (exceeds on > max)."""
        job = Job.objects.create(
            name='AtMax00001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
            auto_requeue_count=_MAX_AUTO_REQUEUE - 1,
        )

        requeue_stuck_jobs()
        job.refresh_from_db()
        # count was 2 before, incremented to 3 which == _MAX, not > _MAX
        assert job.status == JobStatus.QUEUED
        assert job.auto_requeue_count == _MAX_AUTO_REQUEUE


@pytest.mark.django_db
class TestStuckJobRechecks:
    """AC-6b: Re-checks status and worker staleness after lock."""

    def test_completed_job_not_requeued(self, asset, stale_worker):
        """Job that transitions to DONE before lock is skipped."""
        job = Job.objects.create(
            name='ReCheck001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
        )

        # Simulate the job completing between filter and lock
        job.status = JobStatus.DONE
        job.completed_at = timezone.now()
        job.save()

        count = requeue_stuck_jobs()
        assert count == 0

        job.refresh_from_db()
        assert job.status == JobStatus.DONE


@pytest.mark.django_db
class TestStuckJobDebounce:
    """AC-6c: Stuck check runs at most once per 30 seconds."""

    def test_second_call_within_interval_is_noop(
        self, asset, stale_worker,
    ):
        Job.objects.create(
            name='Debounce01',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
        )

        # First call processes
        count1 = requeue_stuck_jobs()
        assert count1 == 1

        # Create another stuck job
        Job.objects.create(
            name='Debounce02',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
        )

        # Second call is debounced — returns 0
        count2 = requeue_stuck_jobs()
        assert count2 == 0

    def test_call_after_interval_processes(
        self, asset, stale_worker,
    ):
        """After manually advancing the debounce, next call works."""
        import workers.views.stuck_jobs as mod

        Job.objects.create(
            name='DebAdv0001',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
        )

        requeue_stuck_jobs()

        # Reset debounce to allow another run
        mod._last_stuck_check = 0.0

        Job.objects.create(
            name='DebAdv0002',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=stale_worker,
            started_at=timezone.now() - timedelta(seconds=200),
        )

        count = requeue_stuck_jobs()
        assert count == 1
