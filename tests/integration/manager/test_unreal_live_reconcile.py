# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: live reconciliation from ``active_job_ids`` (Unreal
foundation FR-RT-17, FR-T-3, AC-36).
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.utils import timezone

from workers.constants import UNREAL_LIVE_RECONCILE_GRACE_SECONDS
from workers.models import Job, JobStatus, Worker
from workers.services import worker_reconcile

from .unreal_helpers import unreal_asset

OLD = timedelta(seconds=UNREAL_LIVE_RECONCILE_GRACE_SECONDS + 5)


def _rendering(asset, worker, name, age=OLD):
    job = Job.objects.create(name=name, asset=asset, output_file_pattern='//r/f.png')
    Job.objects.filter(pk=job.pk).update(
        status=JobStatus.RENDERING, assigned_worker=worker, started_at=timezone.now() - age,
    )
    return job


def _beat(client, hostname, **extra):
    resp = client.post('/api/heartbeat/', {'hostname': hostname, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return resp


def _status(job):
    job.refresh_from_db()
    return job.status


@pytest.mark.django_db
class TestLiveReconciliation:

    def test_unlisted_old_unreal_job_is_requeued(self, worker_with_token):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker, 'LiveOld')
        _beat(client, worker.hostname, active_job_ids=[])
        assert _status(job) == JobStatus.QUEUED
        assert job.error_message == "Auto-requeued: worker is not running this job"

    def test_recent_listed_and_blender_jobs_are_kept(self, worker_with_token, asset):
        worker, client = worker_with_token
        shared = unreal_asset()
        recent = _rendering(shared, worker, 'LiveRecent', age=timedelta(seconds=5))
        listed = _rendering(shared, worker, 'LiveListed')
        blender = _rendering(asset, worker, 'LiveBlender')
        _beat(client, worker.hostname, active_job_ids=[listed.pk])
        assert [_status(j) for j in (recent, listed, blender)] == [JobStatus.RENDERING] * 3

    @pytest.mark.parametrize('extra', [
        {}, {'active_job_ids': 'x'}, {'active_job_ids': list(range(1, 258))},
        {'active_job_ids': ['1']}, {'active_job_ids': [True]}, {'active_job_ids': [10 ** 30]},
    ])
    def test_absent_or_invalid_key_reconciles_nothing(self, worker_with_token, extra):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker, 'LiveInvalid')
        _beat(client, worker.hostname, **extra)
        assert _status(job) == JobStatus.RENDERING

    def test_job_reassigned_before_the_lock_is_left_alone(self, worker_with_token, make_worker_client):
        worker, client = worker_with_token
        other, _other_client, _token = make_worker_client('live-other')
        job = _rendering(unreal_asset(), worker, 'LiveMoved')
        real_lock = worker_reconcile.lock_own_rows

        def reassign_then_lock(queryset, *related):
            Job.objects.filter(pk=job.pk).update(assigned_worker=other)
            return real_lock(queryset, *related)

        with patch.object(worker_reconcile, 'lock_own_rows', reassign_then_lock):
            _beat(client, worker.hostname, active_job_ids=[])
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id) == (JobStatus.RENDERING, other.pk)

    def test_job_reclaimed_by_the_same_worker_before_the_lock_is_left_alone(self, worker_with_token):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker, 'LiveReclaimed')
        real_lock = worker_reconcile.lock_own_rows

        def reclaim_then_lock(queryset, *related):
            Job.objects.filter(pk=job.pk).update(started_at=timezone.now())
            return real_lock(queryset, *related)

        with patch.object(worker_reconcile, 'lock_own_rows', reclaim_then_lock):
            _beat(client, worker.hostname, active_job_ids=[])
        assert _status(job) == JobStatus.RENDERING

    def test_other_workers_jobs_are_not_touched(self, worker_with_token, make_worker_client):
        worker, client = worker_with_token
        other, _other_client, _token = make_worker_client('live-bystander')
        job = _rendering(unreal_asset(), other, 'LiveBystander')
        _beat(client, worker.hostname, active_job_ids=[])
        assert _status(job) == JobStatus.RENDERING


@pytest.mark.django_db
class TestAnchorAndFailure:

    def test_window_is_anchored_to_the_heartbeat_arrival(self, worker_with_token):
        worker, _client = worker_with_token
        job = _rendering(unreal_asset(), worker, 'LiveAnchor', age=timedelta(seconds=200))
        job.refresh_from_db()
        arrival = job.started_at + timedelta(seconds=UNREAL_LIVE_RECONCILE_GRACE_SECONDS - 10)
        assert worker_reconcile.reconcile_live(worker, [], now=arrival) == 0
        assert _status(job) == JobStatus.RENDERING
        assert worker_reconcile.reconcile_live(worker, []) == 1
        assert _status(job) == JobStatus.QUEUED

    def test_heartbeat_passes_its_arrival_time(self, worker_with_token):
        worker, client = worker_with_token
        before = timezone.now()
        with patch.object(worker_reconcile, 'reconcile_live', return_value=0) as live:
            _beat(client, worker.hostname, active_job_ids=[])
        now = live.call_args.kwargs['now']
        assert before <= now <= timezone.now()
        assert now == Worker.objects.get(pk=worker.pk).last_seen

    def test_database_error_still_returns_the_heartbeat(self, worker_with_token):
        worker, client = worker_with_token
        with patch.object(worker_reconcile, 'reconcile_live', side_effect=DatabaseError('locked')):
            resp = _beat(client, worker.hostname, active_job_ids=[])
        assert resp.data['hostname'] == worker.hostname
