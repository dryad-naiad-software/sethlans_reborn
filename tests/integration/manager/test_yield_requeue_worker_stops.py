# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: yield-requeue for deliberate worker stops (#246, #247).

* ``worker_shutdown`` is never counted against the auto-requeue limit.
* ``worker_hardware_changed`` is counted (shared ``auto_requeue_count``,
  ERROR above the limit), together with the reaper and reconciliation.
* A hand-back carrying a stale ``worker_boot_id`` (an old process) gets
  409 and leaves the job alone.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus, Worker
from workers.views import stuck_jobs

from .test_blender_reconcile import BOOT_A, BOOT_B, RESTART, _claim, _queued_job, _register

NOT_ASSIGNED = {'error': "Job is no longer assigned to this worker; the update was not applied."}
HARDWARE = "Auto-requeued: worker hardware changed"
CAP = "Max auto-requeue attempts exceeded (3)"


def _yield(client, job, reason, **extra):
    return client.post(f'/api/jobs/{job.pk}/yield-requeue/', {'reason': reason, **extra}, format='json')


def _hand_back(client, worker, job, reason, **extra):
    _claim(client, worker, job)
    resp = _yield(client, job, reason, **extra)
    assert resp.status_code == 200, resp.data
    job.refresh_from_db()
    return resp


@pytest.mark.django_db
class TestShutdown:

    def test_repeated_shutdown_hand_backs_are_never_counted(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        for attempt in range(1, 6):
            resp = _hand_back(client, worker, job, 'worker_shutdown')
            assert resp.data == {'status': 'requeued'}
            assert (job.status, job.assigned_worker_id) == (JobStatus.QUEUED, None)
            assert (job.auto_requeue_count, job.yield_requeue_count) == (0, attempt)
            assert job.error_message == 'Interrupted: worker_shutdown'


@pytest.mark.django_db
class TestHardwareChanged:

    def test_counted_until_the_fourth_ends_in_error(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        for attempt in range(1, 4):
            resp = _hand_back(client, worker, job, 'worker_hardware_changed')
            assert resp.data == {'status': 'requeued'}
            assert (job.status, job.assigned_worker_id, job.error_message) == (JobStatus.QUEUED, None, HARDWARE)
            assert (job.auto_requeue_count, job.yield_requeue_count) == (attempt, 0)
        resp = _hand_back(client, worker, job, 'worker_hardware_changed')
        assert resp.data == {'status': 'error'}
        assert (job.status, job.error_message, job.auto_requeue_count) == (JobStatus.ERROR, CAP, 4)
        assert job.completed_at is not None

    def test_shares_the_count_with_reaper_and_reconciliation(self, booted, asset, monkeypatch):
        worker, client = booted
        job = _queued_job(asset)
        _hand_back(client, worker, job, 'worker_hardware_changed')
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)  # restart: boot reconciliation
        job.refresh_from_db()
        assert (job.auto_requeue_count, job.error_message) == (2, RESTART)
        _hand_back(client, worker, job, 'worker_hardware_changed', worker_boot_id=BOOT_B)
        assert job.auto_requeue_count == 3
        _claim(client, worker, job)
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - timedelta(seconds=600))
        monkeypatch.setattr(stuck_jobs, '_last_stuck_check', 0.0)
        assert stuck_jobs.requeue_stuck_jobs() == 1
        job.refresh_from_db()
        assert (job.status, job.error_message, job.auto_requeue_count) == (JobStatus.ERROR, CAP, 4)


@pytest.mark.django_db
class TestBootId:

    @pytest.mark.parametrize('reason', ['worker_shutdown', 'worker_hardware_changed'])
    def test_matching_boot_id_requeues(self, booted, asset, reason):
        worker, client = booted
        job = _queued_job(asset)
        _hand_back(client, worker, job, reason, worker_boot_id=BOOT_A)
        assert (job.status, job.assigned_worker_id) == (JobStatus.QUEUED, None)

    @pytest.mark.parametrize('reason', ['worker_shutdown', 'worker_hardware_changed'])
    def test_old_process_cannot_requeue_the_new_claim(self, booted, asset, reason):
        """Old process stops N; new process registers and claims N again; old hand-back arrives."""
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        _register(client, worker, BOOT_B)
        _claim(client, worker, job)
        job.refresh_from_db()
        before = (job.status, job.assigned_worker_id, job.started_at, job.auto_requeue_count,
                  job.yield_requeue_count, job.error_message)
        resp = _yield(client, job, reason, worker_boot_id=BOOT_A)
        assert (resp.status_code, resp.data) == (409, NOT_ASSIGNED)
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.started_at, job.auto_requeue_count,
                job.yield_requeue_count, job.error_message) == before
        assert job.status == JobStatus.RENDERING

    def test_absent_boot_id_works_as_before(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        resp = _hand_back(client, worker, job, 'artist_return_app_launch')
        assert resp.data == {'status': 'requeued'}
        assert (job.status, job.error_message, job.yield_requeue_count) == (
            JobStatus.QUEUED, 'Interrupted: artist_return_app_launch', 1)

    def test_unknown_or_malformed_reason_is_stored_as_unknown(self, booted, asset):
        worker, client = booted
        job = _queued_job(asset)
        for raw in ('bogus', ['worker_shutdown']):
            _claim(client, worker, job)
            assert _yield(client, job, raw).status_code == 200
            job.refresh_from_db()
            assert job.error_message == 'Interrupted: unknown'
        assert Job.objects.get(pk=job.pk).auto_requeue_count == 0


@pytest.mark.django_db
def test_yield_event_accepts_the_new_reasons(booted):
    worker, client = booted
    for reason in ('worker_shutdown', 'worker_hardware_changed'):
        resp = client.post(f'/api/heartbeat/{worker.pk}/yield-event/', {
            'reason': reason, 'grace_outcome': 'aborted', 'progress_at_yield': 0.5,
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['reason'] == reason
