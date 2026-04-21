# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 (Waitress migration) unit tests for ``db_config``.

Covers:
* Postgres config gets ``CONN_MAX_AGE`` + ``CONN_HEALTH_CHECKS`` with
  correct defaults and env/INI overrides.
* SQLite config retains ``OPTIONS.timeout`` and that value matches the
  ``busy_timeout`` PRAGMA applied by the ``connection_created`` hook.
* ``_apply_sqlite_pragmas`` executes the three WAL pragmas for sqlite
  vendors and is a no-op for non-sqlite vendors.
* Invalid ``conn_max_age`` falls back to the default.
"""

import configparser
from unittest.mock import MagicMock

import pytest

from sethlans_manager import db_hooks
from sethlans_manager.db_config import (
    _DEFAULT_CONN_MAX_AGE,
    _SQLITE_BUSY_TIMEOUT_MS,
    _SQLITE_TIMEOUT_SECONDS,
    _resolve_conn_max_age,
    build_database_config,
)
from sethlans_manager.db_hooks import _apply_sqlite_pragmas


@pytest.fixture
def ini_path(tmp_path):
    return tmp_path / "manager.ini"


@pytest.fixture
def clean_env(monkeypatch):
    for key in (
        "SETHLANS_DATABASE_URL",
        "SETHLANS_DB_ENGINE",
        "SETHLANS_DB_NAME",
        "SETHLANS_DB_HOST",
        "SETHLANS_DB_PORT",
        "SETHLANS_DB_USER",
        "SETHLANS_DB_PASSWORD",
        "SETHLANS_DB_PASSWORD_FILE",
        "SETHLANS_MANAGER_DB_CONN_MAX_AGE",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _pg_config() -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    c.add_section("database")
    c.set("database", "engine", "postgresql")
    c.set("database", "name", "pgdb")
    c.set("database", "host", "pghost")
    return c


# ---------------------------------------------------------------------
# CONN_MAX_AGE + CONN_HEALTH_CHECKS on external DBs
# ---------------------------------------------------------------------


class TestConnMaxAge:

    def test_postgres_default_conn_max_age(
        self, ini_path, clean_env,
    ):
        result = build_database_config(_pg_config(), ini_path)
        db = result["default"]
        assert db["CONN_MAX_AGE"] == _DEFAULT_CONN_MAX_AGE == 60
        assert db["CONN_HEALTH_CHECKS"] is True

    def test_conn_max_age_env_override(
        self, ini_path, clean_env,
    ):
        clean_env.setenv("SETHLANS_MANAGER_DB_CONN_MAX_AGE", "120")
        result = build_database_config(_pg_config(), ini_path)
        assert result["default"]["CONN_MAX_AGE"] == 120

    def test_conn_max_age_ini_override(
        self, ini_path, clean_env,
    ):
        cfg = _pg_config()
        cfg.set("database", "conn_max_age", "45")
        result = build_database_config(cfg, ini_path)
        assert result["default"]["CONN_MAX_AGE"] == 45

    def test_env_beats_ini(self, ini_path, clean_env):
        cfg = _pg_config()
        cfg.set("database", "conn_max_age", "45")
        clean_env.setenv("SETHLANS_MANAGER_DB_CONN_MAX_AGE", "300")
        result = build_database_config(cfg, ini_path)
        assert result["default"]["CONN_MAX_AGE"] == 300

    def test_conn_max_age_zero_allowed(
        self, ini_path, clean_env,
    ):
        """CONN_MAX_AGE=0 means 'reconnect every request' (Django idiom)."""
        clean_env.setenv("SETHLANS_MANAGER_DB_CONN_MAX_AGE", "0")
        result = build_database_config(_pg_config(), ini_path)
        assert result["default"]["CONN_MAX_AGE"] == 0

    def test_invalid_value_falls_back_to_default(
        self, ini_path, clean_env,
    ):
        clean_env.setenv(
            "SETHLANS_MANAGER_DB_CONN_MAX_AGE", "not-a-number",
        )
        result = build_database_config(_pg_config(), ini_path)
        assert result["default"]["CONN_MAX_AGE"] == _DEFAULT_CONN_MAX_AGE

    def test_negative_value_falls_back_to_default(
        self, ini_path, clean_env,
    ):
        clean_env.setenv("SETHLANS_MANAGER_DB_CONN_MAX_AGE", "-5")
        result = build_database_config(_pg_config(), ini_path)
        assert result["default"]["CONN_MAX_AGE"] == _DEFAULT_CONN_MAX_AGE

    def test_sqlite_does_not_get_conn_max_age(
        self, ini_path, clean_env,
    ):
        """SQLite config should NOT carry CONN_MAX_AGE."""
        cfg = configparser.ConfigParser()
        result = build_database_config(cfg, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"
        assert "CONN_MAX_AGE" not in db
        assert "CONN_HEALTH_CHECKS" not in db


class TestResolveConnMaxAge:

    def test_default_when_unset(self, clean_env):
        cfg = configparser.ConfigParser()
        assert _resolve_conn_max_age(cfg) == _DEFAULT_CONN_MAX_AGE

    def test_empty_string_uses_default(self, clean_env):
        cfg = configparser.ConfigParser()
        cfg.add_section("database")
        cfg.set("database", "conn_max_age", "")
        assert _resolve_conn_max_age(cfg) == _DEFAULT_CONN_MAX_AGE


# ---------------------------------------------------------------------
# SQLite timeout / busy_timeout reconciliation
# ---------------------------------------------------------------------


class TestSqliteTimeoutReconciliation:

    def test_options_timeout_matches_busy_timeout_pragma(
        self, ini_path, clean_env,
    ):
        """The Python-layer timeout and engine-layer busy_timeout
        must agree — otherwise the shorter one silently wins."""
        cfg = configparser.ConfigParser()
        result = build_database_config(cfg, ini_path)
        options = result["default"]["OPTIONS"]
        assert options["timeout"] == _SQLITE_TIMEOUT_SECONDS
        # busy_timeout pragma is in milliseconds
        assert _SQLITE_BUSY_TIMEOUT_MS == options["timeout"] * 1000

    def test_sqlite_timeout_is_thirty_seconds(
        self, ini_path, clean_env,
    ):
        """Documented Phase 4 contract: 30 s lock-wait."""
        assert _SQLITE_TIMEOUT_SECONDS == 30
        assert _SQLITE_BUSY_TIMEOUT_MS == 30_000


# ---------------------------------------------------------------------
# ``_apply_sqlite_pragmas`` signal handler
# ---------------------------------------------------------------------


class TestApplySqlitePragmas:

    def test_no_op_for_postgres(self):
        """Postgres connection must not receive SQLite PRAGMAs."""
        conn = MagicMock()
        conn.vendor = "postgresql"
        _apply_sqlite_pragmas(sender=None, connection=conn)
        conn.cursor.assert_not_called()

    def test_no_op_for_mysql(self):
        conn = MagicMock()
        conn.vendor = "mysql"
        _apply_sqlite_pragmas(sender=None, connection=conn)
        conn.cursor.assert_not_called()

    def test_no_op_for_unknown_vendor(self):
        conn = MagicMock()
        conn.vendor = "oracle"
        _apply_sqlite_pragmas(sender=None, connection=conn)
        conn.cursor.assert_not_called()

    def test_no_op_when_vendor_attr_missing(self):
        conn = object()  # no .vendor attr
        # Must not raise.
        _apply_sqlite_pragmas(sender=None, connection=conn)

    def test_sqlite_runs_three_pragmas(self):
        conn = MagicMock()
        conn.vendor = "sqlite"
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor

        _apply_sqlite_pragmas(sender=None, connection=conn)

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("journal_mode=WAL" in s for s in executed), executed
        assert any(
            f"busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}" in s
            for s in executed
        ), executed
        assert any("synchronous=NORMAL" in s for s in executed), executed
        assert len(executed) == 3

    def test_pragma_exception_is_swallowed(self, caplog):
        """PRAGMA failures must not crash the connect path."""
        conn = MagicMock()
        conn.vendor = "sqlite"
        conn.cursor.side_effect = RuntimeError("read-only FS")

        # Must NOT raise.
        _apply_sqlite_pragmas(sender=None, connection=conn)

    def test_register_hooks_is_idempotent(self):
        """Calling register_connection_hooks twice must not duplicate."""
        from django.db.backends.signals import connection_created
        before = len(connection_created.receivers)
        db_hooks.register_connection_hooks()
        db_hooks.register_connection_hooks()
        after = len(connection_created.receivers)
        # dispatch_uid dedupes; at most +1 regardless of call count.
        assert after - before <= 1
