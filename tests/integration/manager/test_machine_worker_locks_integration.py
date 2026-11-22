# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the Worker step of the D15 lock order
(``services/worker_locks.lock_workers``; Unreal per-machine memory test
D15, D5, NF-1).

Covers: ``lock_workers`` refuses to run outside ``transaction.atomic()``;
it locks every Worker row in one ascending statement and returns their
pks; a second call in the same transaction sends nothing and returns the
same set (a worker added meanwhile is not in it); a rolled-back savepoint
discards the lock so the next call locks again, while a savepoint rolled
back after the lock keeps it; a new transaction locks again.  The
multi-key callers (CVar PATCH over two keys, the admin retry, a sequence
submission planned at create or left waiting on an open discovery, a
``shots-result/`` that plans a waiting submission) take that one all-Worker lock before any measurement record,
discovery or submission lock, and count the open-pilot cap after it.

SQLite sends no ``FOR UPDATE``; the connection is told it supports row
locks and the clause is compiled as a trailing comment, as in
``test_row_lock_sql.py``.
"""

from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import locked_tables
from workers.models import Job, Worker
from workers.services.worker_locks import lock_workers

from .machine_test_helpers import render_job
from .sequence.sequence_helpers import post_shots, shots_job_of
from .sequence.submission_helpers import done_discovery, small_shots_body, submit
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import claim, pilots, ready_worker, settings as unreal_settings

WORKER_TABLE = 'workers_worker'
LATER_TABLES = ('workers_unrealmeasurement', 'workers_sequencediscovery', 'workers_sequencesubmission')
CVARS = {'r.Streaming.PoolSize': '3000'}


@pytest.fixture
def row_locks():
    """Emulate a backend with ``FOR UPDATE OF`` (PostgreSQL); the clause is a trailing comment."""
    real = connection.ops.for_update_sql

    def commented(**kwargs):
        return f'/* {real(**kwargs)} */'

    with patch.object(connection.features, 'has_select_for_update', True), \
            patch.object(connection.features, 'has_select_for_update_of', True), \
            patch.object(connection.ops, 'for_update_sql', commented):
        yield


def _locked(sql):
    """The table a captured statement locks, or ``None`` for a statement that locks nothing."""
    if 'FOR UPDATE' not in sql:
        return None
    [table] = locked_tables(sql)
    return table


def _worker_locks(ctx):
    return [q['sql'] for q in ctx.captured_queries if _locked(q['sql']) == WORKER_TABLE]


def _assert_all_workers_ascending(sql):
    assert ' WHERE ' not in sql, sql
    # ``values_list('pk')`` orders by its first (only) column, the pk.
    assert sql.startswith(f'SELECT "{WORKER_TABLE}"."id" AS "pk" FROM "{WORKER_TABLE}" ORDER BY 1 ASC'), sql


def _assert_workers_first(ctx, min_records=1, plans=True):
    """One all-Worker lock, before every record, discovery and submission lock and the cap count.

    ``plans``: the request creates pilots, so the cap is counted (after the lock).
    """
    statements = [q['sql'] for q in ctx.captured_queries]
    workers = [i for i, sql in enumerate(statements) if _locked(sql) == WORKER_TABLE]
    assert len(workers) == 1, [statements[i] for i in workers]
    _assert_all_workers_ascending(statements[workers[0]])
    later = [i for i, sql in enumerate(statements) if _locked(sql) in LATER_TABLES]
    records = [i for i in later if _locked(statements[i]) == 'workers_unrealmeasurement']
    assert len(records) >= min_records, statements
    assert all(i > workers[0] for i in later), [statements[i] for i in later if i < workers[0]]
    counts = [i for i, sql in enumerate(statements) if 'COUNT(' in sql and '"workers_job"."target_worker_id"' in sql]
    assert bool(counts) == plans and all(i > workers[0] for i in counts), [statements[i] for i in counts]
    return [_locked(statements[i]) for i in later]


@pytest.mark.django_db(transaction=True)
def test_outside_atomic_raises():
    with pytest.raises(RuntimeError, match=r'lock_workers must run inside transaction\.atomic\(\)'):
        lock_workers()


@pytest.mark.django_db
class TestLockWorkers:

    @pytest.fixture
    def three(self, worker_with_token, make_worker_client):
        others = [make_worker_client(f'mt-lock-{n}')[0] for n in range(2)]
        return [worker_with_token[0], *others]

    def test_locks_every_worker_once_ascending(self, three, row_locks):
        with transaction.atomic():
            with CaptureQueriesContext(connection) as ctx:
                first = lock_workers()
            [sql] = _worker_locks(ctx)
            _assert_all_workers_ascending(sql)
            assert first == frozenset(w.pk for w in three)
            late = Worker.objects.create(hostname='mt-lock-late')
            with CaptureQueriesContext(connection) as ctx:
                again = lock_workers()
            assert ctx.captured_queries == []
            # The set of the first call: a worker added after the lock waits for a later trigger.
            assert again is first and late.pk not in again

    def test_a_rolled_back_savepoint_drops_the_lock(self, three, row_locks):
        with transaction.atomic():
            with pytest.raises(ValueError):
                with transaction.atomic():
                    lock_workers()
                    raise ValueError('roll back the savepoint')
            with CaptureQueriesContext(connection) as ctx:
                assert lock_workers() == frozenset(w.pk for w in three)
            assert len(_worker_locks(ctx)) == 1

    def test_a_savepoint_rolled_back_after_the_lock_keeps_it(self, three, row_locks):
        with transaction.atomic():
            lock_workers()
            with pytest.raises(ValueError):
                with transaction.atomic():
                    raise ValueError('a later savepoint')
            with CaptureQueriesContext(connection) as ctx:
                lock_workers()
            assert _worker_locks(ctx) == []

    def test_a_new_transaction_locks_again(self, three, row_locks):
        for _round in range(2):
            with transaction.atomic():
                with CaptureQueriesContext(connection) as ctx:
                    lock_workers()
                    lock_workers()
                assert len(_worker_locks(ctx)) == 1


@pytest.mark.django_db
class TestMultiKeyCallersLockWorkersFirst:

    @pytest.fixture
    def asset(self, db, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path / 'media')
        return unreal_asset()

    def test_cvar_patch_over_two_keys(self, admin_client, asset, worker_with_token, make_worker_client, row_locks):
        ready_worker(worker_with_token[0])
        ready_worker(make_worker_client('mt-lock-b')[0])
        render_job(asset, name='MtLockOne')
        render_job(asset, name='MtLockTwo', render_settings=unreal_settings(sequence='/Game/Cinematics/Two.Two'))
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.patch(f'/api/projects/{asset.project_id}/', {'unreal_cvar_overrides': CVARS},
                                      format='json')
        assert resp.status_code == 200, resp.data
        assert len(pilots(asset)) == 4
        _assert_workers_first(ctx, min_records=4)

    def test_admin_retry(self, admin_client, asset, worker_with_token, row_locks):
        ready_worker(worker_with_token[0])
        job = render_job(asset)
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.post(f'/api/projects/{asset.project_id}/unreal-measurement/retry/',
                                     {'job_id': job.pk}, format='json')
        assert resp.status_code == 201, resp.data
        _assert_workers_first(ctx)

    def test_submission_planned_at_create(self, admin_client, asset, worker_with_token, row_locks):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        with CaptureQueriesContext(connection) as ctx:
            submission, _resp = submit(admin_client, asset)
        submission.refresh_from_db()
        assert submission.status == 'QUEUED'
        _assert_workers_first(ctx)

    def test_submission_waiting_on_an_open_discovery(self, admin_client, asset, worker_with_token, row_locks):
        """The open discovery is locked (to settle it) after the Worker rows, in ``create_submission``."""
        submit(admin_client, asset)
        with CaptureQueriesContext(connection) as ctx:
            waiting, _resp = submit(admin_client, asset)
        assert waiting.status == 'DISCOVERING'
        assert 'workers_sequencediscovery' in _assert_workers_first(ctx, min_records=0, plans=False)

    def test_shots_result_planning_a_waiting_submission(self, admin_client, asset, worker_with_token, row_locks):
        worker, client = worker_with_token
        submission, _resp = submit(admin_client, asset)
        ready_worker(worker)
        job = shots_job_of(submission.discovery)
        assert claim(client, job, worker).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            resp = post_shots(client, job.pk, small_shots_body())
        assert resp.status_code == 200, resp.data
        submission.refresh_from_db()
        assert submission.status == 'QUEUED'
        assert Job.objects.filter(asset=asset, unreal_kind='pilot', target_worker=worker).count() == 1
        _assert_workers_first(ctx)
