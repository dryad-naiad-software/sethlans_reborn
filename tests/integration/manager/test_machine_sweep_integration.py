# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the heartbeat sweep (``services/machine_sweep``,
``views/heartbeat.py`` on-commit hook; Unreal per-machine memory test D5,
D16, FR-MT-P-3).

Every sweep here runs from a real heartbeat POST: its on-commit callbacks
are captured with pytest-django's ``django_capture_on_commit_callbacks``
and run, as Django runs them after the request's commit.

Covers: a machine that comes online gets one pilot for a key with QUEUED
jobs that it has not tried (an earlier trigger gave it none while it was
offline), and the next heartbeat adds none; only the heartbeat's own
worker is tested; a failed pair gets no pilot, a ``retest_due`` pair a new
one.  When nothing is due the sweep is exactly one read of the jobs (the
manager's share setting is a subquery inside it), with no row lock,
``lock_project`` or ``lock_workers``; a worker without Unreal costs no
query; a worker whose share is not the manager's, or that is at the
20-pilot cap, costs exactly that one read and no lock.  A project whose sweep raises is logged and the
other projects are still swept.
"""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Worker
from workers.services import machine_sweep, machine_tests
from workers.services.unreal_pilot import lock_project

from .machine_test_helpers import (
    HB, emulated_row_locks, heartbeat, locked_ensure, pair_record, render_job,
)
from .unreal_helpers import caps, unreal_asset, unreal_project
from .unreal_pilot_helpers import KIND, pilots, ready_worker

OFFLINE = timedelta(seconds=WORKER_STALENESS_SECONDS + 60)


@pytest.fixture
def job(db):
    """A QUEUED render job (ORM: no pilot was created for it)."""
    return render_job(unreal_asset())


@pytest.fixture
def worker(worker_with_token):
    return ready_worker(worker_with_token[0]), worker_with_token[1]


def _targets(asset):
    return sorted((p.target_worker_id, p.render_settings['unreal.card_kind']) for p in pilots(asset))


def _sweep_queries(client, worker, capture):
    """POST a heartbeat, then run its on-commit callbacks under row-lock emulation; the captured SQL."""
    with capture() as callbacks:
        resp = client.post(HB, {'hostname': worker.hostname}, format='json')
    assert resp.status_code == 200, resp.data
    assert callbacks, "the heartbeat registered no on-commit sweep"
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        for callback in callbacks:
            callback()
    return captured_sql(ctx)


@pytest.mark.django_db
class TestSweepCreates:

    def test_a_machine_that_comes_online_gets_one_pilot(self, job, worker, make_worker_client,
                                                        django_capture_on_commit_callbacks):
        tested, _client = worker
        pair_record(job.asset, job.unreal_target_sha256, tested, verdict='fits')
        new, client, _token = make_worker_client('mt-sweep-new')
        ready_worker(new)
        Worker.objects.filter(pk=new.pk).update(last_seen=timezone.now() - OFFLINE)
        assert locked_ensure(job.asset) == []  # offline: not tried yet, it waits
        heartbeat(client, new, django_capture_on_commit_callbacks)
        [pilot] = pilots(job.asset)
        assert (pilot.target_worker_id, pilot.render_settings['unreal.card_kind']) == (new.pk, KIND)
        assert pilot.unreal_target_sha256 == job.unreal_target_sha256
        heartbeat(client, new, django_capture_on_commit_callbacks)
        assert len(pilots(job.asset)) == 1

    def test_only_the_heartbeat_worker_is_tested(self, job, worker, make_worker_client,
                                                 django_capture_on_commit_callbacks):
        ready_worker(make_worker_client('mt-sweep-other')[0])
        tested, client = worker
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert _targets(job.asset) == [(tested.pk, KIND)]

    def test_a_failed_pair_gets_none(self, job, worker, django_capture_on_commit_callbacks):
        tested, client = worker
        failed = pair_record(job.asset, job.unreal_target_sha256, tested, pilot='ERROR', error='boom')
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert [p.pk for p in pilots(job.asset)] == [failed.pilot_job_id]

    def test_a_retest_due_pair_gets_a_new_pilot(self, job, worker, django_capture_on_commit_callbacks):
        tested, client = worker
        pair_record(job.asset, job.unreal_target_sha256, tested, verdict='fits', stale='driver_changed')
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert _targets(job.asset) == [(tested.pk, KIND)]


@pytest.mark.django_db
class TestCheapSweeps:

    @pytest.fixture
    def spies(self, mocker):
        return (mocker.patch.object(machine_sweep, 'lock_project', wraps=machine_sweep.lock_project),
                mocker.patch.object(machine_sweep, 'lock_workers', wraps=machine_sweep.lock_workers))

    @staticmethod
    def _no_lock(sql, spies):
        assert not [query for query in sql if 'FOR UPDATE' in query], sql
        assert [spy.call_count for spy in spies] == [0, 0]

    @staticmethod
    def _one_job_read(sql):
        assert len(sql) == 1 and '"workers_job"' in sql[0], sql

    def test_nothing_due_is_one_read_without_a_lock(self, job, worker, spies, django_capture_on_commit_callbacks):
        tested, client = worker
        pair_record(job.asset, job.unreal_target_sha256, tested, verdict='fits')
        sql = _sweep_queries(client, tested, django_capture_on_commit_callbacks)
        # Exactly one read of the jobs (D16); the share setting is a subquery inside it.
        self._one_job_read(sql)
        self._no_lock(sql, spies)

    @pytest.mark.parametrize('capabilities', [caps(enabled=False), {}], ids=['disabled', 'none'])
    def test_a_worker_without_unreal_costs_no_query(self, job, worker, spies, capabilities,
                                                    django_capture_on_commit_callbacks):
        tested, client = worker
        Worker.objects.filter(pk=tested.pk).update(unreal_capabilities=capabilities)
        assert _sweep_queries(client, tested, django_capture_on_commit_callbacks) == []
        assert pilots(job.asset) == []

    def test_a_share_mismatch_takes_no_lock(self, job, worker, spies, django_capture_on_commit_callbacks):
        tested, client = worker
        Worker.objects.filter(pk=tested.pk).update(unreal_capabilities=caps(share=r'\\elsewhere.example\other'))
        for _beat in range(2):
            sql = _sweep_queries(client, tested, django_capture_on_commit_callbacks)
            self._one_job_read(sql)
            self._no_lock(sql, spies)
        assert pilots(job.asset) == []

    def test_a_capped_worker_takes_no_lock(self, job, worker, spies, django_capture_on_commit_callbacks):
        tested, client = worker
        filler = unreal_asset(job.asset.project, name='MtSweepCapFill')
        for n in range(machine_tests.MAX_OPEN_PILOTS):
            pair_record(filler, f'{n:064x}', tested, pilot='QUEUED')
        before = len(pilots())
        for _beat in range(2):
            sql = _sweep_queries(client, tested, django_capture_on_commit_callbacks)
            self._one_job_read(sql)
            self._no_lock(sql, spies)
        assert len(pilots()) == before


@pytest.mark.django_db
class TestFailureIsolation:

    @pytest.mark.parametrize('error', [DatabaseError, RuntimeError])
    def test_a_failing_project_does_not_stop_the_others(self, worker, mocker, error,
                                                        django_capture_on_commit_callbacks):
        tested, client = worker
        broken = render_job(unreal_asset(unreal_project(name='MtSweepBroken'), name='MtSweepBrokenAsset'))
        fine = render_job(unreal_asset(unreal_project(name='MtSweepFine'), name='MtSweepFineAsset'))

        def lock(project):
            if project.pk == broken.asset.project_id:
                raise error('simulated failure')
            return lock_project(project)

        mocker.patch.object(machine_sweep, 'lock_project', side_effect=lock)
        logger = mocker.patch.object(machine_sweep, 'logger')
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert pilots(broken.asset) == []
        assert _targets(fine.asset) == [(tested.pk, KIND)]
        [call] = [call for call in logger.method_calls if call[0] in ('warning', 'error', 'exception')]
        assert broken.asset.project_id in call.args
