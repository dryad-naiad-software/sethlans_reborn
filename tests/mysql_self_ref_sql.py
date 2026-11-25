# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Test oracle for MySQL error 1093 (issue #318).

MySQL refuses an ``UPDATE`` or ``DELETE`` on table T whose ``WHERE`` or
``SET`` holds a subquery that reads T: "You can't specify target table
'T' for update in FROM clause". MariaDB, PostgreSQL and SQLite accept it.

Django 5.2 works around it only in some cases (checked in
``django/db/models/sql/compiler.py`` and
``django/db/backends/mysql/{compiler,features}.py``):

* ``UPDATE`` with joins in the outer query: ``update_can_self_select`` is
  False on MySQL, so the matching keys are fetched first.
* ``DELETE`` on one table whose filter holds a queryset of the same model:
  ``delete_can_self_reference_subquery`` is False, so the keys are wrapped
  in a materialized derived table (``IN (SELECT * FROM (...) subquery)``).

A single-table ``UPDATE`` filtered with ``Exists``/``pk__in=<queryset>`` of
the same model, or with a same-table ``Subquery`` in ``SET``, is sent as it
is and fails. Tests compile querysets with :func:`mysql_compile_connection`
(PyMySQL is installed, no server is contacted) and check the SQL with
:func:`self_subquery_error`.
"""

import re
from contextlib import contextmanager
from unittest.mock import patch

from django.core.exceptions import EmptyResultSet
from django.db import connections
from django.db.models.sql import DeleteQuery, UpdateQuery
from django.db.models.sql.compiler import SQLDeleteCompiler, SQLUpdateCompiler

_HEAD_RE = re.compile(r'^\s*(?:UPDATE|DELETE(?: `\w+`)? FROM) `(?P<table>\w+)`')
_DERIVED = 'SELECT * FROM ('
# Version string of the CI MySQL service; anything without "mariadb" is MySQL.
MYSQL_SERVER_DATA = {
    'version': '8.4.0', 'sql_mode': 'STRICT_TRANS_TABLES', 'default_storage_engine': 'InnoDB',
    'sql_auto_is_null': False, 'lower_case_table_names': False, 'has_zoneinfo_database': True,
}


def mysql_compile_connection(alias='mysql_compile_only'):
    """A Django MySQL connection object that only compiles SQL."""
    import sethlans_manager.mysql_driver  # noqa: F401  (PyMySQL as MySQLdb)
    from django.db.backends.mysql.base import DatabaseWrapper

    settings = dict(connections['default'].settings_dict)
    settings.update(ENGINE='django.db.backends.mysql', NAME='compile_only',
                    HOST='/nonexistent-compile-only', OPTIONS={})
    conn = DatabaseWrapper(settings, alias=alias)
    conn.__dict__['mysql_server_data'] = dict(MYSQL_SERVER_DATA)

    def _refuse():
        raise AssertionError('compile-only MySQL wrapper tried to connect')

    conn.connect = _refuse
    conn.get_autocommit = lambda: False
    return conn


def update_sql(queryset, conn, **values):
    """Compile ``queryset.update(**values)`` for ``conn`` without running it."""
    query = queryset.query.chain(UpdateQuery)
    query.add_update_values(values)
    query.annotations = {}
    return query.get_compiler(connection=conn).as_sql()[0]


def delete_sql(queryset, conn):
    """Compile the fast-path ``DELETE`` of ``queryset`` for ``conn``."""
    query = queryset.query.chain(DeleteQuery)
    return query.get_compiler(connection=conn).as_sql()[0]


def _closing(sql, start):
    """Index of the parenthesis that closes the one at ``start``."""
    depth = 0
    for index in range(start, len(sql)):
        if sql[index] == '(':
            depth += 1
        elif sql[index] == ')':
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f'Unbalanced parentheses: {sql}')


def _subqueries(sql):
    """Yield the outermost ``(SELECT ...)`` spans, skipping derived tables."""
    index = 0
    while True:
        start = sql.find('(SELECT ', index)
        if start < 0:
            return
        end = _closing(sql, start)
        if not sql.startswith(_DERIVED, start + 1):
            yield sql[start:end + 1]
        index = end + 1


def target_table(sql):
    """The table an ``UPDATE``/``DELETE`` statement writes, else ``None``."""
    match = _HEAD_RE.match(sql)
    return match.group('table') if match else None


def self_subquery_error(sql):
    """Return why MySQL would raise 1093 for this statement, or ``None``."""
    table = target_table(sql)
    if table is None:
        return None
    reads = re.compile(rf'\b(?:FROM|JOIN) `{table}`(?!\w)')
    for span in _subqueries(sql):
        if reads.search(span):
            return f'subquery reads target table {table}: {span[:200]}'
    return None


def _mysql_error(query, conn):
    """The 1093 reason for an UPDATE/DELETE query compiled for MySQL, or ``None``."""
    query = query.clone()
    query.get_initial_alias()
    if isinstance(query, UpdateQuery) and (query.related_updates or query.count_active_tables() > 1):
        return None  # Django fetches the keys first on MySQL (update_can_self_select)
    try:
        sql = query.get_compiler(connection=conn).as_sql()[0]
    except EmptyResultSet:
        return None  # e.g. ``pk__in=[]``: Django sends no statement at all
    return self_subquery_error(sql)


@contextmanager
def mysql_write_checks(execute=True):
    """While active, every UPDATE/DELETE that runs is also compiled for MySQL.

    Yields the list of 1093 reasons found. The statements still run on the
    test database, unless ``execute`` is False: then they are only compiled
    and checked (for a statement the test database itself may refuse).
    """
    conn, errors = mysql_compile_connection(), []
    run_update, run_delete = SQLUpdateCompiler.execute_sql, SQLDeleteCompiler.execute_sql

    def update(compiler, *args, **kwargs):
        errors.extend(filter(None, [_mysql_error(compiler.query, conn)]))
        return run_update(compiler, *args, **kwargs) if execute else 0

    def delete(compiler, *args, **kwargs):
        errors.extend(filter(None, [_mysql_error(compiler.query, conn)]))
        return run_delete(compiler, *args, **kwargs) if execute else 0

    with patch.object(SQLUpdateCompiler, 'execute_sql', update), \
            patch.object(SQLDeleteCompiler, 'execute_sql', delete):
        yield errors
