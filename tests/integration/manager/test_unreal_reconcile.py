# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: boot-time reconciliation through registration (Unreal
foundation FR-RT-12, FR-RT-7 binding, FR-T-3, AC-22).
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from workers.models import Job, JobStatus, Worker
from workers.services import worker_reconcile

from .unreal_helpers import caps, gpu_memory, unreal_asset

BOOT_A = 'a' * 32
BOOT_B = 'b' * 32


def _register(client, hostname, **extra):
    return client.post('/api/heartbeat/', {'hostname': hostname, 'os': 'Windows', **extra}, format='json')


def _rendering(asset, worker, name):
    job = Job.objects.create(name=name, asset=asset, output_file_pattern='//r/f.png')
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
    return job


@pytest.fixture
def worker_jobs(worker_with_token, asset):
    worker, client = worker_with_token
    Worker.objects.filter(pk=worker.pk).update(boot_id=BOOT_A)
    unreal = _rendering(unreal_asset(), worker, 'RecUnreal')
    blender = _rendering(asset, worker, 'RecBlender')
    return worker, client, unreal, blender


def _status(job):
    job.refresh_from_db()
    return job.status


@pytest.mark.django_db
class TestBootReconciliation:

    def test_new_boot_id_requeues_unreal_only(self, worker_jobs):
        worker, client, unreal, blender = worker_jobs
        resp = _register(client, worker.hostname, worker_boot_id=BOOT_B)
        assert resp.status_code == 200, resp.data
        assert (_status(unreal), _status(blender)) == (JobStatus.QUEUED, JobStatus.RENDERING)
        assert unreal.error_message == "Auto-requeued: worker restarted during Unreal render"
        assert unreal.auto_requeue_count == 1
        assert Worker.objects.get(pk=worker.pk).boot_id == BOOT_B
        assert 'boot_id' not in resp.data

    @pytest.mark.parametrize('boot_id', [BOOT_A, 'A' * 32, 'xyz', None, 5])
    def test_same_or_invalid_boot_id_changes_nothing(self, worker_jobs, boot_id):
        worker, client, unreal, _blender = worker_jobs
        extra = {} if boot_id is None else {'worker_boot_id': boot_id}
        assert _register(client, worker.hostname, **extra).status_code == 200
        assert _status(unreal) == JobStatus.RENDERING
        assert Worker.objects.get(pk=worker.pk).boot_id == BOOT_A

    def test_compare_and_swap_reconciles_once(self, worker_jobs):
        worker, _client, unreal, _blender = worker_jobs
        stale = Worker.objects.get(pk=worker.pk)
        assert worker_reconcile.reconcile_worker_restart(Worker.objects.get(pk=worker.pk), BOOT_B) == 1
        Job.objects.filter(pk=unreal.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
        assert worker_reconcile.reconcile_worker_restart(stale, BOOT_B) == 0
        assert _status(unreal) == JobStatus.RENDERING

    def test_job_reassigned_before_the_lock_is_left_alone(self, worker_jobs, make_worker_client):
        worker, client, unreal, _blender = worker_jobs
        other, _other_client, _token = make_worker_client('rec-other')
        real_lock = worker_reconcile.lock_own_rows

        def reassign_then_lock(queryset, *related):
            Job.objects.filter(pk=unreal.pk).update(assigned_worker=other)
            return real_lock(queryset, *related)

        with patch.object(worker_reconcile, 'lock_own_rows', reassign_then_lock):
            assert _register(client, worker.hostname, worker_boot_id=BOOT_B).status_code == 200
        unreal.refresh_from_db()
        assert (unreal.status, unreal.assigned_worker_id, unreal.auto_requeue_count) == (
            JobStatus.RENDERING, other.pk, 0,
        )

    def test_failure_restores_the_boot_id_and_returns_503(self, worker_jobs):
        worker, client, unreal, _blender = worker_jobs
        with patch.object(worker_reconcile, 'requeue_or_fail', side_effect=RuntimeError('boom')):
            resp = _register(client, worker.hostname, worker_boot_id=BOOT_B)
        assert resp.status_code == 503
        assert Worker.objects.get(pk=worker.pk).boot_id == BOOT_A
        assert _status(unreal) == JobStatus.RENDERING
        resp = _register(client, worker.hostname, worker_boot_id=BOOT_B)
        assert resp.status_code == 200
        assert _status(unreal) == JobStatus.QUEUED

    def test_other_workers_hostname_changes_nothing(self, worker_jobs, make_worker_client):
        """#215 binding: A's token posting B's hostname never touches B."""
        victim, _client, unreal, _blender = worker_jobs
        Worker.objects.filter(pk=victim.pk).update(unreal_capabilities=caps(), gpu_memory=gpu_memory(32607))
        _attacker, attacker_client, _token = make_worker_client('rec-attacker')
        resp = _register(attacker_client, victim.hostname, worker_boot_id=BOOT_B,
                         unreal_capabilities={'schema': 1, 'enabled': False, 'installs': []},
                         gpu_memory={})
        assert resp.status_code == 403
        victim.refresh_from_db()
        assert (victim.boot_id, victim.unreal_capabilities, victim.gpu_memory) == (
            BOOT_A, caps(), gpu_memory(32607),
        )
        assert _status(unreal) == JobStatus.RENDERING


@pytest.mark.django_db
def test_reaper_still_uses_its_message(worker_with_token, monkeypatch):
    """The extracted ``requeue_or_fail`` keeps the reaper's behavior."""
    from workers.views import stuck_jobs

    worker, _client = worker_with_token
    job = _rendering(unreal_asset(), worker, 'Reaped')
    Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - timedelta(seconds=600))
    monkeypatch.setattr(stuck_jobs, '_last_stuck_check', 0.0)
    assert stuck_jobs.requeue_stuck_jobs() == 1
    job.refresh_from_db()
    assert (job.status, job.error_message) == (JobStatus.QUEUED, 'Auto-requeued: assigned worker went offline')


@pytest.mark.django_db
class TestBootCutoff:
    """Jobs claimed after the boot reconciliation began belong to the new process."""

    def test_job_reclaimed_by_the_new_process_is_left_alone(self, worker_jobs):
        worker, client, unreal, _blender = worker_jobs
        real_lock = worker_reconcile.lock_own_rows

        def reclaim_then_lock(queryset, *related):
            # The new process claims the requeued job again before the lock.
            Job.objects.filter(pk=unreal.pk).update(started_at=timezone.now())
            return real_lock(queryset, *related)

        with patch.object(worker_reconcile, 'lock_own_rows', reclaim_then_lock):
            assert _register(client, worker.hostname, worker_boot_id=BOOT_B).status_code == 200
        unreal.refresh_from_db()
        assert (unreal.status, unreal.auto_requeue_count) == (JobStatus.RENDERING, 0)

    def test_job_without_started_at_is_requeued(self, worker_jobs):
        worker, client, unreal, _blender = worker_jobs
        Job.objects.filter(pk=unreal.pk).update(started_at=None)
        assert _register(client, worker.hostname, worker_boot_id=BOOT_B).status_code == 200
        assert _status(unreal) == JobStatus.QUEUED

    def test_job_started_before_the_restart_is_requeued(self, worker_jobs):
        worker, client, unreal, _blender = worker_jobs
        Job.objects.filter(pk=unreal.pk).update(started_at=timezone.now() - timedelta(seconds=5))
        assert _register(client, worker.hostname, worker_boot_id=BOOT_B).status_code == 200
        assert _status(unreal) == JobStatus.QUEUED
