# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the real claim view and stuck-job reaper emit locking
SQL that PostgreSQL accepts (issue #216).

SQLite never emits ``FOR UPDATE``, so the bug was invisible in CI. Here the
SQLite connection is told it supports row locking (and, for the PostgreSQL
case, ``FOR UPDATE OF``), and its ``for_update_sql`` is wrapped in a SQL
comment so SQLite still executes the query. The captured SQL then carries
the exact lock clause Django would send to PostgreSQL / MariaDB, and
``postgres_lock_error`` applies PostgreSQL's outer join rule to it.

Against the pre-#216 code the claim and reaper queries contain
``LEFT OUTER JOIN`` with an unrestricted ``FOR UPDATE``, so the
``postgres_lock_error`` assertions fail.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import workers.views.stuck_jobs as stuck_jobs_module
from tests.row_lock_sql import locked_tables, postgres_lock_error
from workers.models import Job, JobStatus
from workers.views.stuck_jobs import requeue_stuck_jobs

JOBS_URL = '/api/jobs/'


def _emulate_locking(supports_of):
    """Make SQLite compile FOR UPDATE clauses as a trailing SQL comment."""
    real_for_update_sql = connection.ops.for_update_sql

    def commented(**kwargs):
        return f'/* {real_for_update_sql(**kwargs)} */'

    return [
        patch.object(connection.features, 'has_select_for_update', True),
        patch.object(
            connection.features, 'has_select_for_update_of', supports_of,
        ),
        patch.object(connection.ops, 'for_update_sql', commented),
    ]


@pytest.fixture(params=[True, False], ids=['for-update-of', 'no-for-update-of'])
def locking_backend(request):
    """Emulate PostgreSQL/MySQL 8 (OF supported) and MariaDB (no OF)."""
    patches = _emulate_locking(request.param)
    for p in patches:
        p.start()
    yield request.param
    for p in reversed(patches):
        p.stop()


def _lock_queries(ctx):
    return [q['sql'] for q in ctx.captured_queries if 'FOR UPDATE' in q['sql']]


def _assert_locks_only_jobs(sql, supports_of):
    assert postgres_lock_error(sql) is None, sql
    if supports_of:
        assert locked_tables(sql) == {Job._meta.db_table}, sql
    else:
        assert 'JOIN' not in sql, sql


@pytest.mark.django_db
class TestClaimLockSql:

    def test_claim_locks_only_job_row(
        self, worker_with_token, asset, locking_backend,
    ):
        worker, client = worker_with_token
        job = Job.objects.create(
            name='LockSqlClaim', asset=asset,
            output_file_pattern='//render/#.png',
        )
        with CaptureQueriesContext(connection) as ctx:
            resp = client.post(
                f'{JOBS_URL}{job.pk}/claim/',
                data={'worker_id': worker.pk}, format='json',
            )
        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'RENDERING'

        locks = [q for q in _lock_queries(ctx) if 'FROM "workers_job"' in q]
        assert len(locks) == 1
        _assert_locks_only_jobs(locks[0], locking_backend)

    def test_claim_version_check_still_enforced(
        self, worker_with_token, asset, locking_backend,
    ):
        """The lazily loaded relations still drive the version check."""
        worker, client = worker_with_token
        worker.available_tools = {'blender': ['3.6.0']}
        worker.save()
        job = Job.objects.create(
            name='LockSqlVersion', asset=asset,
            output_file_pattern='//render/#.png',
        )
        resp = client.post(
            f'{JOBS_URL}{job.pk}/claim/',
            data={'worker_id': worker.pk}, format='json',
        )
        assert resp.status_code == 409
        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED


@pytest.mark.django_db
class TestReaperLockSql:

    def test_reaper_locks_only_job_row(
        self, worker_with_token, asset, locking_backend, monkeypatch,
    ):
        worker, _client = worker_with_token
        stale = timezone.now() - timedelta(seconds=600)
        job = Job.objects.create(
            name='LockSqlReap', asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING, assigned_worker=worker,
        )
        type(worker).objects.filter(pk=worker.pk).update(last_seen=stale)
        monkeypatch.setattr(stuck_jobs_module, '_last_stuck_check', 0.0)

        with CaptureQueriesContext(connection) as ctx:
            assert requeue_stuck_jobs() == 1

        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None

        locks = [q for q in _lock_queries(ctx) if 'FROM "workers_job"' in q]
        assert len(locks) == 1
        _assert_locks_only_jobs(locks[0], locking_backend)
