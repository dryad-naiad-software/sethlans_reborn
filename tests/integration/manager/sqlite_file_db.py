# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Run thread-concurrency tests on a file SQLite database, like production.

The SQLite test database is an in-memory shared-cache database
(``file:memorydb_default?mode=memory&cache=shared``).  Its connections
share one page cache and lock whole tables against each other: while one
connection has written to a table in an open transaction, another that
reads or writes that table gets SQLITE_LOCKED ("database table is locked")
at once.  ``busy_timeout`` and ``BEGIN IMMEDIATE`` do not wait on
SQLITE_LOCKED, so two request threads fail at random (issue #272).

Production runs on a file database in WAL mode without a shared cache:
readers never wait for a writer, and a second writer waits on
``busy_timeout``.  SQLITE_LOCKED cannot come from two connections there.
A test that runs real threads against the database therefore has to use a
file database to test what production does.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.db import connection


@contextmanager
def file_backed_sqlite(directory):
    """Move the test database into a WAL file under ``directory``.

    The rows created so far are copied into the file.  Until the block
    exits, the main thread and every new thread-local connection use the
    file; then the in-memory database, kept open so it is not destroyed,
    is restored.  On other database vendors this does nothing.

    Yields:
        The file's path, or ``None`` on other vendors.
    """
    if connection.vendor != 'sqlite':
        yield None
        return
    path = str(Path(directory) / 'race.sqlite3')
    connection.ensure_connection()
    target = sqlite3.connect(path)
    connection.connection.backup(target)
    target.execute('PRAGMA journal_mode=WAL')
    target.close()
    memory, original = connection.connection, connection.settings_dict['NAME']
    # Thread-local connections are built from this same settings dict.
    connection.connection = None
    connection.settings_dict['NAME'] = path
    try:
        yield path
    finally:
        connection.close()
        connection.settings_dict['NAME'] = original
        connection.connection = memory


@pytest.fixture
def sqlite_file_database(transactional_db, tmp_path):
    """Run the test on a file copy of the SQLite test database."""
    with file_backed_sqlite(tmp_path) as path:
        yield path


@contextmanager
def job_row_write_lock(database_name, job_pk):
    """Hold an open write on the job's row from a separate raw connection.

    ``BEGIN IMMEDIATE`` takes the database write lock and the ``UPDATE``
    takes the ``workers_job`` table write lock of a shared cache; both are
    held until the block exits, then rolled back.
    """
    holder = sqlite3.connect(
        database_name, uri=database_name.startswith('file:'),
        timeout=30, isolation_level=None, check_same_thread=False,
    )
    try:
        holder.execute('BEGIN IMMEDIATE')
        holder.execute('UPDATE workers_job SET name = name WHERE id = ?', (job_pk,))
        yield
    finally:
        holder.execute('ROLLBACK')
        holder.close()
