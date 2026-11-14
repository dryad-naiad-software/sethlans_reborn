# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
SQLite ``atomic()`` blocks open with ``BEGIN IMMEDIATE`` (issue #225).

In WAL mode a deferred transaction that reads first and writes later fails
at once with "database is locked" (SQLITE_BUSY_SNAPSHOT, which
``busy_timeout`` does not cover) when another connection commits a write
in between.  ``OPTIONS["transaction_mode"] = "IMMEDIATE"`` makes every
``atomic()`` take the write lock at BEGIN, so the other writer waits on
``busy_timeout`` instead and the read-then-write succeeds.

The two-connection test runs a real Django ``DatabaseWrapper`` built from
``db_config._build_sqlite`` (the dict ``settings.DATABASES`` uses) against
a file-based database, with a raw ``sqlite3`` connection as the
concurrent writer.
"""

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from django.db import OperationalError, connection, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper

from sethlans_manager.db_config import _build_sqlite

ALIAS = 'issue225_immediate'


@pytest.fixture
def skip_unless_sqlite():
    if connection.vendor != 'sqlite':
        pytest.skip(f'SQLite only (got vendor={connection.vendor!r})')


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / 'immediate.sqlite3'
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE parent (id INTEGER PRIMARY KEY, status TEXT)')
    conn.execute('CREATE TABLE worker (id INTEGER PRIMARY KEY, last_seen REAL)')
    conn.execute("INSERT INTO parent VALUES (1, 'RENDERING')")
    conn.execute('INSERT INTO worker VALUES (1, 0)')
    conn.close()
    return path


@pytest.fixture
def django_file_connection(db_path, django_db_blocker):
    """A Django connection to ``db_path`` built from the production config."""
    settings_dict = dict(_build_sqlite(str(db_path), Path('manager.ini'))['default'])
    settings_dict.update({
        'ATOMIC_REQUESTS': False, 'AUTOCOMMIT': True, 'CONN_MAX_AGE': 0,
        'CONN_HEALTH_CHECKS': False, 'TIME_ZONE': None, 'TEST': {},
        'USER': '', 'PASSWORD': '', 'HOST': '', 'PORT': '',
    })
    wrapper = DatabaseWrapper(settings_dict, alias=ALIAS)
    connections[ALIAS] = wrapper
    with django_db_blocker.unblock():
        try:
            yield wrapper
        finally:
            wrapper.close()
            del connections[ALIAS]


def test_sqlite_config_sets_immediate_transaction_mode():
    options = _build_sqlite('db.sqlite3', Path('manager.ini'))['default']['OPTIONS']
    assert options['transaction_mode'] == 'IMMEDIATE'


@pytest.mark.django_db
def test_test_database_uses_immediate_mode(skip_unless_sqlite):
    """The suites run on the same config, so they exercise IMMEDIATE too."""
    connection.ensure_connection()
    assert connection.settings_dict['OPTIONS']['transaction_mode'] == 'IMMEDIATE'
    assert connection.transaction_mode == 'IMMEDIATE'


# ``django_db`` keeps this test in pytest-django's database group, ahead of
# the non-database tests: the Playwright tests among those leave an event
# loop running on the main thread, and Django refuses sync DB access then.
@pytest.mark.django_db
def test_atomic_read_then_write_waits_for_concurrent_commit(
    skip_unless_sqlite, db_path, django_file_connection,
):
    """An atomic read-then-write survives a commit from another connection.

    Before the fix the deferred ``BEGIN`` let the other connection commit
    between the read and the write, and the write raised "database is
    locked" at once.
    """
    other = sqlite3.connect(db_path, timeout=10, isolation_level=None,
                            check_same_thread=False)
    writer_errors = []

    def heartbeat():
        try:
            other.execute('UPDATE worker SET last_seen = ? WHERE id = 1', (time.time(),))
        except sqlite3.OperationalError as exc:  # pragma: no cover - diagnostic
            writer_errors.append(exc)

    writer = threading.Thread(target=heartbeat)
    try:
        started = time.monotonic()
        with transaction.atomic(using=ALIAS):
            with django_file_connection.cursor() as cursor:
                cursor.execute('SELECT status FROM parent WHERE id = 1')
                assert cursor.fetchone() == ('RENDERING',)
            writer.start()
            writer.join(timeout=0.3)
            writer_waited = writer.is_alive()
            try:
                with django_file_connection.cursor() as cursor:
                    cursor.execute("UPDATE parent SET status = 'ASSEMBLING' WHERE id = 1")
            except OperationalError as exc:
                pytest.fail(
                    f'read-then-write failed after {time.monotonic() - started:.3f}s: {exc}'
                )
        writer.join(timeout=10)
        assert not writer.is_alive()
        assert writer_errors == []
        assert writer_waited, 'the concurrent writer did not wait for the IMMEDIATE lock'
        status = other.execute('SELECT status FROM parent WHERE id = 1').fetchone()
        last_seen = other.execute('SELECT last_seen FROM worker WHERE id = 1').fetchone()
        assert status == ('ASSEMBLING',)
        assert last_seen[0] > 0
    finally:
        other.close()
