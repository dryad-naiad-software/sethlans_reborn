# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the row locks of ``pilot-result/`` (Unreal per-machine
memory test D15, NF-1, D3; ``services.pilot_result.record_pilot_result``).

SQLite never emits ``FOR UPDATE``.  As in ``test_worker_write_lock_sql.py``
the connection is told it supports ``FOR UPDATE OF`` and the lock clause
is written as a trailing SQL comment (``emulated_row_locks``), so the
captured SQL shows what PostgreSQL would receive and still runs here.

Checks: the locking statements come in the order Job -> Worker ->
UnrealMeasurement, one each, each locking only its own table; the
record lock is ``ORDER BY`` primary key ascending; with a non-null driver
it locks the pilot's record and the pair's current records D3 may stale
(another driver, or an unknown one) but not a record that already has
the reported driver, nor another worker's, another kind's or a
superseded record.  The locked set is read back by running the captured
statement again.
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql, locked_tables, postgres_lock_error

from .machine_test_helpers import KIND_AMD, emulated_row_locks, kind_card, pair_record, set_cards
from .machine_verdict_helpers import NEW_DRIVER, OLD_DRIVER
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import claimed_pilot, pilot_body, post_result, ready_worker, record_of

TABLES = ('workers_job', 'workers_worker', 'workers_unrealmeasurement')
_FROM = re.compile(r'\bFROM "(workers_\w+)"')


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


def _locks(ctx):
    """``[(table, sql)]`` of the captured locking statements, in order."""
    return [(_FROM.search(sql).group(1), sql) for sql in captured_sql(ctx) if 'FOR UPDATE' in sql]


def _post_captured(client, job, body):
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        resp = post_result(client, job.pk, body)
    assert resp.status_code == 200, resp.data
    return _locks(ctx)


def _locked_pks(sql):
    """Run a captured record lock again (its lock clause is a comment here); the pks it selects."""
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]


@pytest.mark.django_db
class TestLockOrder:

    def test_job_then_worker_then_records(self, pilot):
        job, _worker, client = pilot
        locks = _post_captured(client, job, pilot_body(driver_version=NEW_DRIVER))
        assert [table for table, _sql in locks] == list(TABLES)
        for table, sql in locks:
            assert locked_tables(sql) == {table}, sql
            assert postgres_lock_error(sql) is None, sql

    def test_records_are_locked_by_ascending_pk(self, pilot):
        job, _worker, client = pilot
        [(_table, sql)] = [lock for lock in _post_captured(client, job, pilot_body()) if lock[0] == TABLES[2]]
        assert re.search(r'ORDER BY "workers_unrealmeasurement"\."id" ASC\s*(/\*|$)', sql), sql

    def test_a_refusal_locks_no_record(self, pilot):
        job, _worker, client = pilot
        with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
            assert post_result(client, job.pk, pilot_body(card_kind=KIND_AMD)).status_code == 400
        assert [table for table, _sql in _locks(ctx)] == list(TABLES[:2])


@pytest.mark.django_db
class TestLockedSet:

    def test_only_the_records_d3_may_stale(self, pilot, asset, make_worker_client):
        job, worker, client = pilot
        other, _other_client, _token = make_worker_client('mt-lock-other')
        ready_worker(other)
        set_cards(worker, [kind_card(record_of(job).card_kind, 0), kind_card(KIND_AMD, 1)])
        older = pair_record(asset, 'a1' * 32, worker, verdict='fits', driver_version=OLD_DRIVER)
        unknown = pair_record(asset, 'a2' * 32, worker, verdict='does_not_fit', driver_version='')
        not_locked = [
            pair_record(asset, 'a3' * 32, worker, verdict='fits', driver_version=NEW_DRIVER),
            pair_record(asset, 'a4' * 32, other, verdict='fits', driver_version=OLD_DRIVER),
            pair_record(asset, 'a5' * 32, worker, KIND_AMD, verdict='fits', driver_version=OLD_DRIVER),
            pair_record(asset, 'a6' * 32, worker, verdict='fits', driver_version=OLD_DRIVER, current=False),
        ]
        locks = dict(_post_captured(client, job, pilot_body(driver_version=NEW_DRIVER)))
        locked = _locked_pks(locks[TABLES[2]])
        assert locked == sorted(locked)
        assert set(locked) == {record_of(job).pk, older.pk, unknown.pk}
        assert not {record.pk for record in not_locked} & set(locked)

    @pytest.mark.parametrize('driver', [None, 'Unknown'], ids=['null', 'unknown'])
    def test_an_unknown_driver_locks_only_the_pilots_record(self, pilot, asset, driver):
        job, worker, client = pilot
        pair_record(asset, 'a1' * 32, worker, verdict='fits', driver_version=OLD_DRIVER)
        locks = dict(_post_captured(client, job, pilot_body(driver_version=driver)))
        assert _locked_pks(locks[TABLES[2]]) == [record_of(job).pk]
