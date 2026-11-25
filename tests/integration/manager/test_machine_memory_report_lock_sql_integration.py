# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the row locks of ``memory-report/`` (Unreal per-machine
memory test D15, NF-1, D3; ``services.memory_report.record_memory_report``).

SQLite never emits ``FOR UPDATE``; as in
``test_machine_pilot_lock_sql_integration.py`` the connection is told it
supports ``FOR UPDATE OF`` and the lock clause is a trailing SQL comment
(``emulated_row_locks``), so the captured SQL is what PostgreSQL would
receive and still runs here.

Checks: the locking statements come in the order Job -> Worker ->
UnrealMeasurement (one statement, ``ORDER BY`` pk ascending) ->
SequenceSubmission, the last only when a warning is added (also for a
superseded record); a standalone render locks no submission; a 409 locks
only the Job, a 400 the Job and the Worker, an identical repeat only the
Job.  The record lock covers the report's record, its ``covered_by``
record and the pair's current records of another or unknown driver, not
one already on the reported driver nor another worker's, another kind's
or a superseded record; with a null driver only the record and its
covering record.  The locked set is read back by running the captured
statement again.
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql, locked_tables, postgres_lock_error

from workers.models import Job, UnrealMeasurement

from .machine_test_helpers import KIND_AMD, emulated_row_locks, kinds_worker
from .machine_verdict_helpers import NEW_DRIVER, OLD_DRIVER
from .memory_report_helpers import (
    contention, derive_from, keyed_record, piece_record, post_report, rendering_render, report_body,
    self_spill,
)
from .sequence.submission_helpers import claimed_piece
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, ready_worker

JOB, WORKER, RECORD, SUBMISSION = ('workers_job', 'workers_worker', 'workers_unrealmeasurement',
                                   'workers_sequencesubmission')
_FROM = re.compile(r'\bFROM "(workers_\w+)"')


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def caller(worker_with_token):
    worker, client = worker_with_token
    return kinds_worker(worker, KIND, KIND_AMD), client


def _captured(client, job, body, code=200):
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        resp = post_report(client, job, body)
    assert resp.status_code == code, resp.data
    return [(_FROM.search(sql).group(1), sql) for sql in captured_sql(ctx) if 'FOR UPDATE' in sql]


def _tables(locks):
    return [table for table, _sql in locks]


def _locked_pks(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]


@pytest.mark.django_db
class TestLockOrder:

    def test_a_pieces_warning_locks_the_submission_last(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client)
        locks = _captured(client, piece, self_spill(piece_record(submission, worker)))
        assert _tables(locks) == [JOB, WORKER, RECORD, SUBMISSION]
        for table, sql in locks:
            assert locked_tables(sql) in ({table}, None), sql
            assert postgres_lock_error(sql) is None, sql
        assert [locked_tables(sql) for _t, sql in locks[:3]] == [{JOB}, {WORKER}, {RECORD}]

    def test_a_superseded_record_still_locks_in_order(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client)
        record = piece_record(submission, worker)
        UnrealMeasurement.objects.filter(pk=record.pk).update(current=False)
        assert _tables(_captured(client, piece, contention(record))) == [JOB, WORKER, RECORD, SUBMISSION]

    def test_a_standalone_render_locks_no_submission(self, asset, caller):
        job, record = rendering_render(asset, *caller)
        locks = _captured(client=caller[1], job=job, body=self_spill(record))
        assert _tables(locks) == [JOB, WORKER, RECORD]
        [(_table, sql)] = [lock for lock in locks if lock[0] == RECORD]
        assert re.search(r'ORDER BY "workers_unrealmeasurement"\."id" ASC\s*(/\*|$)', sql), sql

    def test_a_409_locks_only_the_job(self, asset, caller):
        job, record = rendering_render(asset, *caller)
        Job.objects.filter(pk=job.pk).update(status='DONE')
        assert _tables(_captured(caller[1], job, report_body(record), code=409)) == [JOB]

    def test_a_400_locks_no_record(self, asset, caller):
        job, record = rendering_render(asset, *caller)
        locks = _captured(caller[1], job, report_body(record, measurement_id=10 ** 9), code=400)
        assert _tables(locks) == [JOB, WORKER]

    def test_an_identical_repeat_locks_only_the_job(self, asset, caller):
        job, record = rendering_render(asset, *caller)
        body = report_body(record)
        assert post_report(caller[1], job, body).status_code == 200
        assert _tables(_captured(caller[1], job, body)) == [JOB]


@pytest.mark.django_db
class TestLockedSet:

    @pytest.fixture
    def derived(self, asset, caller):
        worker, client = caller
        covering = keyed_record(asset, 'e1' * 32, worker, driver_version=NEW_DRIVER)
        job, record = rendering_render(asset, worker, client, driver_version=NEW_DRIVER)
        return job, derive_from(record, covering), covering, worker, client

    def test_the_record_its_covering_and_the_older_drivers(self, derived, asset, make_worker_client):
        job, record, covering, worker, client = derived
        other, _other_client, _token = make_worker_client('mt-lock-report')
        ready_worker(other)
        older = keyed_record(asset, 'e2' * 32, worker, driver_version=OLD_DRIVER)
        unknown = keyed_record(asset, 'e3' * 32, worker, verdict='does_not_fit', driver_version='')
        not_locked = [
            keyed_record(asset, 'e4' * 32, worker, driver_version=NEW_DRIVER),
            keyed_record(asset, 'e5' * 32, other, driver_version=OLD_DRIVER),
            keyed_record(asset, 'e6' * 32, worker, KIND_AMD, driver_version=OLD_DRIVER),
            keyed_record(asset, 'e7' * 32, worker, driver_version=OLD_DRIVER, current=False),
        ]
        locks = dict(_captured(client, job, report_body(record, driver_version=NEW_DRIVER)))
        locked = _locked_pks(locks[RECORD])
        assert locked == sorted(locked)
        assert set(locked) == {record.pk, covering.pk, older.pk, unknown.pk}
        assert not {each.pk for each in not_locked} & set(locked)

    @pytest.mark.parametrize('driver', [None, 'Unknown'], ids=['null', 'unknown'])
    def test_an_unknown_driver_locks_the_record_and_its_covering(self, derived, asset, driver):
        job, record, covering, worker, client = derived
        keyed_record(asset, 'e8' * 32, worker, driver_version=OLD_DRIVER)
        locks = dict(_captured(client, job, report_body(record, driver_version=driver)))
        assert _locked_pks(locks[RECORD]) == sorted([record.pk, covering.pk])
