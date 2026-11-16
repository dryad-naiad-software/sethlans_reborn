# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the guards around Blender job reconciliation (#247).

* Live reconciliation runs only for heartbeats from the process that
  registered last (``worker_boot_id`` equal to ``Worker.boot_id``).
* One job whose requeue fails does not stop the others, and does not fail
  the registration.
* The counted requeue paths do not double-count, and a shutdown
  hand-back (yield-requeue) is not counted at all
  (test_yield_requeue_worker_stops.py covers the reasons).
* Per-job errors are logged by kind.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.utils import timezone

from workers.models import JobStatus, Worker
from workers.services import worker_reconcile
from workers.views import stuck_jobs

from .test_blender_reconcile import BOOT_A, BOOT_B, LIVE, RESTART, _age, _beat, _claim, _queued_job, _register


def _old_rendering_job(client, worker, asset, name='BlendGuard01'):
    job = _queued_job(asset, name)
    _claim(client, worker, job)
    _age(job)
    return job


def _status_and_count(job):
    job.refresh_from_db()
    return job.status, job.auto_requeue_count


@pytest.mark.django_db
class TestHeartbeatIdentity:

    def test_heartbeat_without_boot_id_does_not_reconcile(self, booted, asset):
        worker, client = booted
        job = _old_rendering_job(client, worker, asset)
        resp = client.post('/api/heartbeat/', {'hostname': worker.hostname, 'active_job_ids': []}, format='json')
        assert resp.status_code == 200
        assert _status_and_count(job) == (JobStatus.RENDERING, 0)

    def test_other_process_does_not_reconcile_and_warns_once(self, booted, asset):
        worker, client = booted
        job = _old_rendering_job(client, worker, asset)
        with patch.object(worker_reconcile, 'logger') as log:
            _beat(client, worker, [], boot_id=BOOT_B)
            _beat(client, worker, [], boot_id=BOOT_B)
        assert _status_and_count(job) == (JobStatus.RENDERING, 0)
        assert log.warning.call_count == 1
        message = log.warning.call_args.args[0] % log.warning.call_args.args[1:]
        assert "Two worker processes are using the same identity" in message
        assert worker.hostname in message

    def test_warning_repeats_after_the_interval(self, booted, monkeypatch):
        worker, client = booted
        clock = iter([1000.0, 1000.0 + worker_reconcile.IDENTITY_WARNING_INTERVAL_SECONDS + 1])
        monkeypatch.setattr(worker_reconcile, '_clock', lambda: next(clock))
        with patch.object(worker_reconcile, 'logger') as log:
            _beat(client, worker, [], boot_id=BOOT_B)
            _beat(client, worker, [], boot_id=BOOT_B)
        assert log.warning.call_count == 2

    def test_no_warning_before_the_row_has_a_boot_id(self, worker_with_token):
        worker, client = worker_with_token
        with patch.object(worker_reconcile, 'logger') as log:
            _beat(client, worker, [], boot_id=BOOT_B)
        log.warning.assert_not_called()

    def test_matching_boot_id_reconciles(self, booted, asset):
        worker, client = booted
        job = _old_rendering_job(client, worker, asset)
        _beat(client, worker, [], boot_id=BOOT_A)
        assert _status_and_count(job) == (JobStatus.QUEUED, 1)


def _fail_for(job_pk, error=RuntimeError):
    """A ``requeue_or_fail`` that raises ``error`` for ``job_pk`` and works for the rest."""
    real = worker_reconcile.requeue_or_fail

    def requeue(job, message):
        if job.pk == job_pk:
            raise error('boom')
        return real(job, message)
    return requeue


@pytest.mark.django_db
class TestOneFailingJob:

    def test_boot_path_reconciles_the_rest_and_registers(self, booted, asset):
        worker, client = booted
        bad = _queued_job(asset, 'BlendBad01')
        good = _queued_job(asset, 'BlendGood01')
        _claim(client, worker, bad)
        _claim(client, worker, good)
        with patch.object(worker_reconcile, 'requeue_or_fail', _fail_for(bad.pk)):
            _register(client, worker, BOOT_B)  # 200, not 503
        assert Worker.objects.get(pk=worker.pk).boot_id == BOOT_B
        assert _status_and_count(bad) == (JobStatus.RENDERING, 0)
        assert _status_and_count(good) == (JobStatus.QUEUED, 1)
        # The skipped job is left to the new process's live reconciliation.
        _age(bad)
        _beat(client, worker, [], boot_id=BOOT_B)
        bad.refresh_from_db()
        assert (bad.status, bad.error_message, bad.auto_requeue_count) == (JobStatus.QUEUED, LIVE, 1)

    def test_live_path_reconciles_the_rest(self, booted, asset):
        worker, client = booted
        bad = _old_rendering_job(client, worker, asset, 'BlendBad02')
        good = _old_rendering_job(client, worker, asset, 'BlendGood02')
        with patch.object(worker_reconcile, 'requeue_or_fail', _fail_for(bad.pk)):
            _beat(client, worker, [])
        assert _status_and_count(bad) == (JobStatus.RENDERING, 0)
        assert _status_and_count(good) == (JobStatus.QUEUED, 1)

    @pytest.mark.parametrize('error, method, path, register', [
        (DatabaseError, 'warning', 'Live', False),
        (RuntimeError, 'exception', 'Live', False),
        (DatabaseError, 'warning', 'Boot', True),
        (RuntimeError, 'exception', 'Boot', True),
    ])
    def test_errors_are_logged_by_kind(self, booted, asset, error, method, path, register):
        worker, client = booted
        bad = _old_rendering_job(client, worker, asset, 'BlendBad03')
        with patch.object(worker_reconcile, 'requeue_or_fail', _fail_for(bad.pk, error)), \
                patch.object(worker_reconcile, 'logger') as log:
            if register:
                _register(client, worker, BOOT_B)
            else:
                _beat(client, worker, [])
        call = getattr(log, method).call_args_list[0]
        message = call.args[0] % call.args[1:]
        assert message.startswith(f"{path} reconciliation of job {bad.pk} ('BlendBad03', BLENDER)")
        assert worker.hostname in message
        if method == 'warning':
            assert call.kwargs == {'exc_info': True}
            log.exception.assert_not_called()


@pytest.mark.django_db
class TestCounting:

    def test_reaper_then_boot_registration_counts_once(self, booted, asset, monkeypatch):
        worker, client = booted
        job = _queued_job(asset)
        _claim(client, worker, job)
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - timedelta(seconds=600))
        monkeypatch.setattr(stuck_jobs, '_last_stuck_check', 0.0)
        assert stuck_jobs.requeue_stuck_jobs() == 1
        _register(client, worker, BOOT_B)
        job.refresh_from_db()
        assert (job.status, job.auto_requeue_count) == (JobStatus.QUEUED, 1)
        assert job.error_message == 'Auto-requeued: assigned worker went offline'
        assert job.error_message != RESTART

    def test_yield_requeued_job_missing_from_the_heartbeat_is_not_counted(self, booted, asset):
        worker, client = booted
        job = _old_rendering_job(client, worker, asset)
        resp = client.post(f'/api/jobs/{job.pk}/yield-requeue/', {'reason': 'worker_shutdown'}, format='json')
        assert resp.status_code == 200, resp.data
        _beat(client, worker, [])
        job.refresh_from_db()
        assert (job.status, job.auto_requeue_count, job.yield_requeue_count) == (JobStatus.QUEUED, 0, 1)
        assert job.error_message == 'Interrupted: worker_shutdown'
