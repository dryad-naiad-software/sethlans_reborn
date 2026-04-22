# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 integration test: SQLite WAL pragmas are applied to each
``connection_created`` event.

Django's test runner uses ``:memory:`` for SQLite test DBs, and
``PRAGMA journal_mode=WAL`` silently falls back to ``memory`` on an
in-memory DB (SQLite refuses WAL for non-file stores).  Production
uses a file-backed DB where the pragma sticks.  To verify the handler
behaviour against a file-backed DB without changing the global test
configuration, we open a second SQLite connection through Django
pointed at a tmp-file DB and re-fire the ``connection_created`` signal.

Postgres / MySQL overlays are skipped (the handler no-ops for them).
"""

import sqlite3

import pytest
from django.db import connection
from django.db.backends.signals import connection_created
from django.db.backends.sqlite3.base import DatabaseWrapper


pytestmark = pytest.mark.django_db


@pytest.fixture
def skip_unless_sqlite():
    if connection.vendor != "sqlite":
        pytest.skip(
            f"WAL pragma test applies to sqlite only "
            f"(got vendor={connection.vendor!r})"
        )


def _open_file_sqlite_wrapper(db_path):
    """Build a Django sqlite DatabaseWrapper pointing at ``db_path``.

    Returns the wrapper with an open connection; caller must
    ``.close()`` when done.  Uses Django's real wrapper so the
    ``connection_created`` signal actually fires, exercising the
    Phase 4 hook end-to-end.
    """
    settings_dict = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(db_path),
        "OPTIONS": {"timeout": 30},
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "TIME_ZONE": None,
        "TEST": {},
    }
    wrapper = DatabaseWrapper(settings_dict, alias="phase4_file")
    # Opening a connection fires ``connection_created``.
    wrapper.ensure_connection()
    return wrapper


def test_wal_journal_mode_applied_on_file_backed_sqlite(
    tmp_path, skip_unless_sqlite,
):
    """On a real file-backed SQLite connection, journal_mode is WAL.

    ``:memory:`` databases silently ignore the WAL pragma — but
    production always uses a file-backed DB, so this test validates
    the end-to-end path.
    """
    db_path = tmp_path / "phase4.sqlite3"
    wrapper = _open_file_sqlite_wrapper(db_path)
    try:
        with wrapper.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
    finally:
        wrapper.close()
    assert mode.lower() == "wal", (
        f"Expected journal_mode=wal on file-backed sqlite, got {mode!r}"
    )


def test_busy_timeout_matches_reconciled_value(skip_unless_sqlite):
    """PRAGMA busy_timeout == SQLITE_BUSY_TIMEOUT_MS (30000).

    Works on in-memory DBs too — unlike journal_mode, busy_timeout
    applies regardless of the store.
    """
    from sethlans_manager.db_config import _SQLITE_BUSY_TIMEOUT_MS
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout;")
        value = cursor.fetchone()[0]
    assert value == _SQLITE_BUSY_TIMEOUT_MS, (
        f"Expected busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}, got {value}"
    )


def test_synchronous_pragma_is_normal(skip_unless_sqlite):
    """PRAGMA synchronous returns 1 (NORMAL)."""
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA synchronous;")
        value = cursor.fetchone()[0]
    assert value == 1, f"Expected synchronous=1 (NORMAL), got {value}"


def test_handler_registered_with_stable_uid():
    """Idempotency: the handler registers with a stable dispatch_uid
    so repeated imports or app-ready calls don't multiply signal fires.
    """
    uids = [
        pair[0] for pair in connection_created.receivers
    ]
    matches = [
        u for u in uids
        if isinstance(u, tuple) and any(
            "sqlite_pragmas" in str(x) for x in u
        )
    ]
    assert matches, (
        "connection_created has no sqlite_pragmas handler registered"
    )


def test_handler_noop_on_non_sqlite_vendor_does_not_crash():
    """Simulate a non-sqlite connection reaching the handler — it
    must silently no-op (Phase 4 contract: Postgres / MySQL overlays
    unaffected)."""
    from sethlans_manager.db_hooks import _apply_sqlite_pragmas

    class _FakePgConn:
        vendor = "postgresql"

        def cursor(self):
            raise AssertionError(
                "handler must NOT open a cursor on non-sqlite"
            )

    # Must not raise.
    _apply_sqlite_pragmas(sender=None, connection=_FakePgConn())


def test_raw_sqlite_connection_does_not_auto_wal():
    """Sanity: a bare ``sqlite3.connect`` (no Django wrapper) does
    NOT auto-apply WAL — the pragma only fires through Django's
    connection signal, which is what the Phase 4 hook listens on.
    """
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        finally:
            conn.close()
        # Default journal mode on a fresh sqlite file is "delete",
        # never "wal" unless something explicitly set it.
        assert mode.lower() != "wal", (
            f"Expected non-wal on bare sqlite, got {mode!r}"
        )
    finally:
        os.unlink(path)
