# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared migration steps for the manager migration round-trip tests (#328).

Not a test module.  ``migrate_to`` moves the test database to the given
migration targets and returns the historical apps at that state;
``migrate_to_latest`` moves it to the latest leaf nodes.

MariaDB drops a column instantly (MDEV-28760, MDEV-17494): the dropped
column's trace stays in the table until the table is rebuilt, and the
row-size check still counts it.  The round-trip tests roll the same tables
back and forward many times, so the traces pile up until a forward
``ADD COLUMN`` fails with error 1118 (row size too large).  After every
migration that went backwards on MariaDB, each table is rebuilt with
``ALTER TABLE ... FORCE``, which clears the traces.  MySQL, SQLite and
PostgreSQL are left alone.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def migrate_to(targets):
    """Migrate to *targets*; return the historical apps at that state."""
    executor = _executor()
    _apply(executor, targets)
    return executor.loader.project_state(targets).apps


def migrate_to_latest():
    """Migrate to the latest leaf nodes of the migration graph."""
    executor = _executor()
    _apply(executor, executor.loader.graph.leaf_nodes())


def _executor():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _apply(executor, targets):
    went_backwards = any(backwards for _, backwards in executor.migration_plan(targets))
    executor.migrate(targets)
    if went_backwards and connection.vendor == 'mysql' and connection.mysql_is_mariadb:
        _rebuild_tables()


def _rebuild_tables():
    """Rebuild every table so MariaDB forgets instantly dropped columns (#328)."""
    with connection.cursor() as cursor:
        for name in connection.introspection.table_names(cursor):
            cursor.execute(f'ALTER TABLE {connection.ops.quote_name(name)} FORCE')
