# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test: a standalone claim takes exactly one row lock, on the
job row (chunk 1 review follow-up to #216's ``test_row_lock_sql.py``).

``test_row_lock_sql`` checks the lock statements that read
``workers_job``.  This test counts **every** ``FOR UPDATE`` statement the
request sends, so a stray lock on a related table (worker, asset,
project, version), for example from a strategy loading a relation with
its own ``select_for_update``, fails here.

SQLite emits no ``FOR UPDATE``; the connection is told it supports row
locks and the clause is compiled as a trailing comment, as in
``test_row_lock_sql``.
"""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import locked_tables, postgres_lock_error
from workers.models import Job, JobStatus

JOBS_URL = '/api/jobs/'


@pytest.fixture(params=[True, False], ids=['for-update-of', 'no-for-update-of'])
def locking_backend(request):
    """Emulate PostgreSQL/MySQL 8 (OF supported) and MariaDB (no OF)."""
    real_for_update_sql = connection.ops.for_update_sql

    def commented(**kwargs):
        return f'/* {real_for_update_sql(**kwargs)} */'

    patches = [
        patch.object(connection.features, 'has_select_for_update', True),
        patch.object(connection.features, 'has_select_for_update_of', request.param),
        patch.object(connection.ops, 'for_update_sql', commented),
    ]
    for p in patches:
        p.start()
    yield request.param
    for p in reversed(patches):
        p.stop()


@pytest.mark.django_db
@pytest.mark.parametrize('outcome', ['claimed', 'version-rejected', 'not-available'])
def test_standalone_claim_issues_exactly_one_lock(worker_with_token, asset, locking_backend, outcome):
    worker, client = worker_with_token
    job = Job.objects.create(name='LockCount', asset=asset, output_file_pattern='//render/#.png')
    if outcome == 'version-rejected':
        worker.available_tools = {'blender': ['3.6.0']}
        worker.save()
    elif outcome == 'not-available':
        Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)

    with CaptureQueriesContext(connection) as ctx:
        resp = client.post(f'{JOBS_URL}{job.pk}/claim/', data={'worker_id': worker.pk}, format='json')

    assert resp.status_code == (200 if outcome == 'claimed' else 409), resp.data
    locks = [q['sql'] for q in ctx.captured_queries if 'FOR UPDATE' in q['sql']]
    assert len(locks) == 1, locks
    sql = locks[0]
    assert 'FROM "workers_job"' in sql
    assert postgres_lock_error(sql) is None, sql
    if locking_backend:
        assert locked_tables(sql) == {Job._meta.db_table}, sql
    else:
        assert 'JOIN' not in sql, sql
