# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the row locks of ``fits_nowhere.check`` (Unreal
per-machine memory test D15, FR-MT-N-1).

SQLite never emits ``FOR UPDATE``; as in the other ``*_lock_sql`` tests
the connection is told it supports ``FOR UPDATE OF`` and the lock clause
is a trailing SQL comment (``emulated_row_locks``), so the captured SQL is
what PostgreSQL would receive and still runs here.

Checks: the check's first statement is the project's write lock, and its
locking statements come in the order Project -> Job (the key's QUEUED
jobs, one statement, ``ORDER BY`` id ascending) -> Worker (every row,
ascending) -> SequenceSubmission, the submission only after every other
lock; no record row is locked.  The Job lock covers exactly the key's
QUEUED pieces and audio jobs.  A standalone render locks no submission; a
check for overrides the project no longer has locks only the project; a
key with no QUEUED job locks no Worker.
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql, locked_tables, postgres_lock_error

from workers.models import Job, Project, UnrealMeasurement

from ..fits_nowhere_helpers import check_key, dnf_record, overrides_of
from ..machine_test_helpers import emulated_row_locks, render_job
from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import measure, ready_worker
from .sound_helpers import audio_jobs_of
from .submission_helpers import done_discovery, pieces_of, submit

PROJECT, JOB, WORKER, SUBMISSION = ('workers_project', 'workers_job', 'workers_worker',
                                    'workers_sequencesubmission')
_FROM = re.compile(r'\bFROM "(workers_\w+)"')
_WRITE = re.compile(r'^\s*(UPDATE|INSERT INTO|DELETE FROM) "(workers_\w+)"')


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def version(admin_client, asset, worker_with_token):
    """A version with sound whose only machine's piece-key result is ``does_not_fit``."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    submission, _ = submit(admin_client, asset, sound=True)
    record = UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker, current=True)
    measure(record, verdict='does_not_fit')
    UnrealMeasurement.objects.filter(pk=record.pk).update(lower_bound_mb=8102, gpu_total_mb=32607,
                                                          gpu_name='Example GPU')
    return submission, pieces_of(submission)[0]


def _captured(job, **kwargs):
    with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
        stopped = check_key(job, **kwargs)
    return stopped, captured_sql(ctx)


def _locks(sql):
    return [(_FROM.search(query).group(1), query) for query in sql if 'FOR UPDATE' in query]


def _tables(sql):
    return [table for table, _query in _locks(sql)]


def _first_write(sql):
    return next(_WRITE.match(query).group(2) for query in sql if _WRITE.match(query))


def _locked_pks(query):
    with connection.cursor() as cursor:
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]


@pytest.mark.django_db
class TestVersionLockOrder:

    def test_project_then_jobs_then_workers_then_the_submission_last(self, version):
        submission, piece = version
        stopped, sql = _captured(piece)
        assert stopped == 4
        assert _first_write(sql) == PROJECT
        tables = _tables(sql)
        assert tables[:3] == [PROJECT, JOB, WORKER], tables
        assert tables[3:] == [SUBMISSION], tables  # one refresh per version, after every other lock
        for table, query in _locks(sql):
            assert locked_tables(query) in ({table}, None), query
            assert postgres_lock_error(query) is None, query

    def test_the_job_lock_is_the_keys_queued_jobs_ascending(self, version, asset):
        submission, piece = version
        Job.objects.filter(pk=pieces_of(submission)[1].pk).update(status='DONE')
        other_key = render_job(asset, name='MtFnLockOther')  # QUEUED, another key: never in the lock
        assert other_key.unreal_target_sha256 != piece.unreal_target_sha256
        stopped, sql = _captured(piece)
        [(_table, job_lock)] = [lock for lock in _locks(sql) if lock[0] == JOB]
        assert re.search(r'ORDER BY "workers_job"\."id" ASC\s*(/\*|$)', job_lock), job_lock
        expected = [piece.pk] + [job.pk for job in audio_jobs_of(submission)]
        assert stopped == 3
        # Read back after the stop: the same statement now finds none (they are ERROR).
        assert _locked_pks(job_lock) == []
        Job.objects.filter(pk__in=expected).update(status='QUEUED')
        assert _locked_pks(job_lock) == sorted(expected)

    def test_workers_are_locked_ascending(self, version):
        _submission, piece = version
        _stopped, sql = _captured(piece)
        [(_table, worker_lock)] = [lock for lock in _locks(sql) if lock[0] == WORKER]
        assert re.search(r'ORDER BY 1 ASC\s*(/\*|$)', worker_lock), worker_lock


@pytest.mark.django_db
class TestEarlyReturns:

    @pytest.fixture
    def standalone(self, asset, worker_with_token):
        worker = ready_worker(worker_with_token[0])
        job = render_job(asset)
        dnf_record(asset, job.unreal_target_sha256, worker)
        return job

    def test_a_standalone_render_locks_no_submission(self, standalone):
        stopped, sql = _captured(standalone)
        assert (stopped, _tables(sql)) == (1, [PROJECT, JOB, WORKER])

    def test_other_overrides_lock_only_the_project(self, standalone, asset):
        old = overrides_of(asset)
        Project.objects.filter(pk=asset.project_id).update(unreal_overrides_sha256='f0' * 32)
        stopped, sql = _captured(standalone, overrides=old)
        assert (stopped, _tables(sql)) == (0, [PROJECT])

    def test_no_queued_job_locks_no_worker(self, standalone):
        Job.objects.filter(pk=standalone.pk).update(status='DONE')
        stopped, sql = _captured(standalone)
        assert (stopped, _tables(sql)) == (0, [PROJECT, JOB])
