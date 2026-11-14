# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``workers.services.row_locks`` (issue #216).

The locking querysets are compiled with a real PostgreSQL compiler (the
psycopg driver is installed, but no server is contacted), so the SQL that
PostgreSQL would receive is checked under SQLite in CI.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connections

from tests.row_lock_sql import locked_tables, postgres_lock_error
from workers.models import Job
from workers.services import row_locks
from workers.services.row_locks import lock_own_rows

pytest.importorskip('psycopg')
from django.db.backends.postgresql.base import (  # noqa: E402
    DatabaseWrapper as PostgresWrapper,
)

# The exact select_related lookups used by the claim and the reaper.
CLAIM_RELATED = ('blender_version', 'asset__project__blender_version')
REAPER_RELATED = ('assigned_worker',)


@pytest.fixture
def pg_conn():
    """A PostgreSQL connection object that only compiles SQL."""
    settings = dict(connections['default'].settings_dict)
    settings.update(
        ENGINE='django.db.backends.postgresql',
        NAME='compile_only', HOST='/nonexistent-compile-only', OPTIONS={},
    )
    conn = PostgresWrapper(settings, alias='pg_compile_only')
    # select_for_update() refuses to compile in autocommit mode; asking
    # the real connection would open a socket.
    conn.get_autocommit = lambda: False
    conn.connect = lambda: pytest.fail('compile-only PG wrapper tried to connect')
    return conn


def _pg_sql(queryset, conn):
    """Compile ``queryset.get(pk=1)``-style SQL for PostgreSQL."""
    query = queryset.filter(pk=1).query
    sql, _params = query.get_compiler(connection=conn).as_sql()
    return sql


def _lock_with(conn, *related):
    with patch.object(row_locks, 'connections', {'default': conn}):
        return lock_own_rows(Job.objects, *related)


class TestOracleCatchesTheBug:
    """The pre-#216 querysets are flagged when compiled for PostgreSQL."""

    @pytest.mark.parametrize('related', [CLAIM_RELATED, REAPER_RELATED])
    def test_old_lock_pattern_is_rejected(self, pg_conn, related):
        old = Job.objects.select_for_update().select_related(*related)
        sql = _pg_sql(old, pg_conn)
        assert 'LEFT OUTER JOIN' in sql
        assert postgres_lock_error(sql) is not None


class TestPostgresPath:
    """Backends with FOR UPDATE OF lock the job table and keep the joins."""

    @pytest.mark.parametrize('related', [CLAIM_RELATED, REAPER_RELATED])
    def test_locks_only_job_table(self, pg_conn, related):
        sql = _pg_sql(_lock_with(pg_conn, *related), pg_conn)
        assert 'LEFT OUTER JOIN' in sql
        assert locked_tables(sql) == {Job._meta.db_table}
        assert postgres_lock_error(sql) is None

    def test_queryset_shape(self, pg_conn):
        qs = _lock_with(pg_conn, *CLAIM_RELATED)
        assert qs.query.select_for_update
        assert qs.query.select_for_update_of == ('self',)
        assert set(qs.query.select_related) == {'blender_version', 'asset'}


class TestNoForUpdateOfPath:
    """Backends that lock but cannot use OF (MariaDB) drop the joins."""

    def _mariadb_like(self):
        features = SimpleNamespace(
            has_select_for_update=True, has_select_for_update_of=False,
        )
        return SimpleNamespace(features=features)

    def test_lock_without_joins(self):
        qs = _lock_with(self._mariadb_like(), *CLAIM_RELATED)
        assert qs.query.select_for_update
        assert qs.query.select_for_update_of == ()
        assert qs.query.select_related is False

    def test_compiled_sql_has_no_join(self, pg_conn):
        qs = _lock_with(self._mariadb_like(), *CLAIM_RELATED)
        sql = _pg_sql(qs, pg_conn)
        assert 'JOIN' not in sql
        assert locked_tables(sql) is None
        assert postgres_lock_error(sql) is None


class TestSqlitePath:
    """SQLite ignores FOR UPDATE; the joins are kept (same query count)."""

    def test_keeps_select_related(self):
        qs = lock_own_rows(Job.objects, *CLAIM_RELATED)
        assert qs.query.select_for_update
        assert qs.query.select_for_update_of == ('self',)
        assert set(qs.query.select_related) == {'blender_version', 'asset'}

    def test_no_related(self):
        qs = lock_own_rows(Job.objects)
        assert qs.query.select_related is False
        assert qs.query.select_for_update_of == ('self',)
