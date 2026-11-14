# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker PATCH and upload lock the job row, and only the job row (#217).

SQLite never emits ``FOR UPDATE``.  As in ``test_row_lock_sql.py`` (issue
#216), the SQLite connection is told it supports row locking and its
``for_update_sql`` is wrapped in a SQL comment, so the captured SQL shows
the lock clause PostgreSQL / MariaDB would receive.  Before #217 neither
view ran a locking query at all.
"""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import locked_tables, postgres_lock_error
from workers.models import Job

from .worker_write_race_helpers import (
    JOBS_URL, create_claimed_job, png_upload,
)


@pytest.fixture(params=[True, False], ids=['for-update-of', 'no-for-update-of'])
def locking_backend(request):
    """Emulate PostgreSQL/MySQL 8 (OF supported) and MariaDB (no OF)."""
    real_for_update_sql = connection.ops.for_update_sql

    def commented(**kwargs):
        return f'/* {real_for_update_sql(**kwargs)} */'

    patches = [
        patch.object(connection.features, 'has_select_for_update', True),
        patch.object(
            connection.features, 'has_select_for_update_of', request.param,
        ),
        patch.object(connection.ops, 'for_update_sql', commented),
    ]
    for p in patches:
        p.start()
    yield request.param
    for p in reversed(patches):
        p.stop()


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _single_job_lock(ctx, supports_of):
    locks = [q['sql'] for q in ctx.captured_queries
             if 'FOR UPDATE' in q['sql'] and 'FROM "workers_job"' in q['sql']]
    assert len(locks) == 1, locks
    sql = locks[0]
    assert postgres_lock_error(sql) is None, sql
    if supports_of:
        assert locked_tables(sql) == {Job._meta.db_table}, sql
    assert 'JOIN' not in sql, sql
    return sql


@pytest.mark.django_db
class TestWorkerWriteLockSql:

    def test_worker_patch_locks_only_the_job_row(
        self, admin_client, worker_with_token, asset, locking_backend,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'LockPatch',
        )

        with CaptureQueriesContext(connection) as ctx:
            resp = worker_client.patch(
                f'{JOBS_URL}{job_id}/',
                data={'status': 'DONE'}, format='json',
            )

        assert resp.status_code == 200, resp.data
        _single_job_lock(ctx, locking_backend)

    def test_upload_locks_only_the_job_row(
        self, media_root, admin_client, worker_with_token, asset,
        locking_backend,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'LockUpload',
        )

        with CaptureQueriesContext(connection) as ctx:
            resp = worker_client.post(
                f'{JOBS_URL}{job_id}/upload_output/',
                data={'output_file': png_upload()}, format='multipart',
            )

        assert resp.status_code == 200, resp.data
        _single_job_lock(ctx, locking_backend)
