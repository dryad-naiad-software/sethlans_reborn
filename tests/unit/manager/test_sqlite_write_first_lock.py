# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Two-connection reproduction of SQLITE_BUSY_SNAPSHOT (issue #219, review H1).

A deferred transaction that reads first and writes later fails at once
("database is locked", busy_timeout does not apply) when another
connection commits a write in between, in WAL mode.  The parent-status
refreshes avoid this by making their first statement a write: the
zero-row ``UPDATE ... WHERE ... AND status = '__write_lock__'`` issued by
``parent_status.take_write_lock``.  This test pins that behaviour of
SQLite against a real file-based database with two connections.
"""

import sqlite3
import threading
import time

import pytest

LOCK_SQL = (
    "UPDATE parent SET status = '__write_lock__' "
    "WHERE id = 1 AND status = '__write_lock__'"
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / 'busy_snapshot.db'
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE parent (id INTEGER PRIMARY KEY, status TEXT)')
    conn.execute('CREATE TABLE worker (id INTEGER PRIMARY KEY, last_seen REAL)')
    conn.execute("INSERT INTO parent VALUES (1, 'RENDERING')")
    conn.execute('INSERT INTO worker VALUES (1, 0)')
    conn.close()
    return path


def _connect(path):
    conn = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def _refresh_with_concurrent_write(path, first_statement):
    """Run read -> (other connection writes) -> write; return the outcome."""
    refresher, other = _connect(path), _connect(path)
    errors = []
    try:
        refresher.execute('BEGIN')
        if first_statement:
            refresher.execute(first_statement)
        refresher.execute('SELECT status FROM parent WHERE id = 1').fetchall()

        def heartbeat():
            try:
                other.execute('UPDATE worker SET last_seen = ? WHERE id = 1', (time.time(),))
            except sqlite3.OperationalError as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        writer = threading.Thread(target=heartbeat)
        writer.start()
        writer.join(timeout=0.3)  # with the lock held, the writer must wait
        try:
            refresher.execute("UPDATE parent SET status = 'ASSEMBLING' WHERE id = 1")
            refresher.execute('COMMIT')
            outcome = 'ok'
        except sqlite3.OperationalError as exc:
            refresher.execute('ROLLBACK')
            outcome = str(exc)
        writer.join(timeout=10)
        assert not writer.is_alive()
        assert errors == []
        return outcome
    finally:
        refresher.close()
        other.close()


def test_read_then_write_fails_with_busy_snapshot(db_path):
    """The pattern the refreshes used before the fix."""
    assert _refresh_with_concurrent_write(db_path, None) == 'database is locked'


def test_write_first_waits_and_succeeds(db_path):
    """The ``take_write_lock`` pattern: the other writer waits instead."""
    assert _refresh_with_concurrent_write(db_path, LOCK_SQL) == 'ok'
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute('SELECT status FROM parent').fetchone() == ('ASSEMBLING',)
    finally:
        conn.close()
