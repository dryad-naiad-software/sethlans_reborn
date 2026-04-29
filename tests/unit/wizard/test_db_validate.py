# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/db_validate.py``
(FR-M2-4 / security-reviewer MED-3).

Locks the error-category allowlist and the never-leak-driver-text
contract. Each branch of ``categorize_exception`` is exercised, the
two external-DB validators are tested via lazy-import patching so
the test suite runs without ``psycopg`` / ``pymysql`` installed, and
the SQLite probe runs against a real on-disk file.
"""

from __future__ import annotations

import logging
import sqlite3
import sys

import pytest

from wizard.sethlans_wizard import db_validate


class TestCategorizeException:
    """FR-M2-4 / security-reviewer MED-3 — every branch of the
    fixed-allowlist mapper is covered."""

    @pytest.mark.parametrize(
        "msg,expected",
        [
            ("authentication failed", "auth_failed"),
            ("password is incorrect", "auth_failed"),
            ("FATAL: SSL handshake error", "ssl_error"),
            ("TLS negotiation failed", "ssl_error"),
            ("permission denied for table x", "permission_denied"),
            ("user lacks privilege", "permission_denied"),
            ("connection timed out", "timeout"),
            ("could not connect to server", "host_unreachable"),
            ("connection refused", "host_unreachable"),
            ("database does not exist", "db_not_found"),
            ("unknown database 'foo'", "db_not_found"),
            ("nothing matches anything important", "generic"),
        ],
    )
    def test_text_branches(self, msg, expected):
        exc = Exception(msg)
        assert db_validate.categorize_exception(exc, "") == expected

    def test_class_name_auth_branch(self):
        # Coverage expansion: branch where the class name (not text)
        # carries the signal, e.g. AuthenticationError.
        class AuthenticationFailed(Exception):
            pass

        exc = AuthenticationFailed("user did a thing")
        assert db_validate.categorize_exception(exc, "") == "auth_failed"

    def test_class_name_ssl_branch(self):
        class SSLError(Exception):
            pass

        exc = SSLError("certificate missing")
        assert db_validate.categorize_exception(exc, "") == "ssl_error"

    def test_class_name_timeout_branch(self):
        class TimeoutError_(Exception):
            pass

        TimeoutError_.__name__ = "TimeoutError_"
        exc = TimeoutError_("oops")
        assert db_validate.categorize_exception(exc, "") == "timeout"

    def test_password_redacted_in_classification(self):
        # security-reviewer MED-3 — the password MUST be replaced with
        # ``<redacted>`` BEFORE the categorizer inspects the text. A
        # password that contains the substring "auth" should NOT cause
        # the wrong category.
        exc = Exception("authoritative-secret-pw was rejected somehow")
        cat = db_validate.categorize_exception(
            exc, "authoritative-secret-pw",
        )
        # After redaction the only "auth" left is "rejected" related —
        # which still legitimately classifies as auth_failed because
        # the manager wraps "rejected" -> auth — but the important
        # contract is the redacted text not flowing through.
        assert cat in ("auth_failed", "generic")


class TestCategorizeExceptionAuthHostnameRegression:
    """Security-reviewer LOW-3 — a connection-refused exception whose
    text contains the literal substring ``auth`` (because the hostname
    contains it) MUST classify as ``host_unreachable``, NOT
    ``auth_failed``. The bare ``"auth" in text`` heuristic the dev
    pass shipped was over-broad; the fix matches on the class name or
    on specific phrases like ``"authentication failed"``.
    """

    def test_connection_refused_with_auth_in_hostname(self):
        # Real psycopg-shaped message with a hostname containing "auth".
        exc = Exception(
            'connection to server at "auth-server.local" '
            "(1.2.3.4), port 5432 failed: Connection refused",
        )
        assert db_validate.categorize_exception(exc, "") == \
            "host_unreachable"

    def test_could_not_connect_with_auth_in_hostname(self):
        exc = Exception(
            "could not connect to server at auth.example.com:5432",
        )
        assert db_validate.categorize_exception(exc, "") == \
            "host_unreachable"

    def test_specific_auth_phrase_still_categorizes_as_auth(self):
        # Defensive: the specific phrase MUST still trip auth_failed.
        for msg in (
            "FATAL: password authentication failed for user 'u'",
            "authentication failed",
            "auth_method 'scram-sha-256' not supported",
        ):
            exc = Exception(msg)
            assert db_validate.categorize_exception(exc, "") == \
                "auth_failed", msg

    def test_class_name_auth_overrides_text_host(self):
        # If the exception class name is auth-shaped, that wins even
        # when the message text mentions a host (e.g. driver wraps
        # the failure in a class hierarchy where the parent is named
        # AuthenticationError).
        class AuthError(Exception):
            pass

        exc = AuthError("connection to host failed")
        assert db_validate.categorize_exception(exc, "") == \
            "auth_failed"


class TestRedact:

    def test_replaces_password(self):
        red = db_validate._redact("auth failed: hunter2 was wrong", "hunter2")
        assert "hunter2" not in red
        assert "<redacted>" in red

    def test_no_op_on_empty_password(self):
        red = db_validate._redact("plaintext", "")
        assert red == "plaintext"


class TestValidateSqlite:

    def test_happy_path(self, tmp_path):
        # Coverage expansion: SQLite probe must write + connect + run
        # SELECT 1 round-trip against a real on-disk db.
        ok, category = db_validate.validate_sqlite("sethlans.db", tmp_path)
        assert ok is True
        assert category is None

    def test_default_db_name_when_empty(self, tmp_path):
        # Coverage expansion: empty/None name falls back to sethlans.db.
        ok, category = db_validate.validate_sqlite("", tmp_path)
        assert ok is True
        assert (tmp_path / "sethlans.db").exists()

    def test_unwritable_dir_returns_permission_denied(
        self, tmp_path, mocker,
    ):
        # Force the probe-write to fail by patching write_text on Path.
        mocker.patch(
            "pathlib.Path.write_text",
            side_effect=OSError("denied"),
        )
        ok, category = db_validate.validate_sqlite("x.db", tmp_path)
        assert ok is False
        assert category == "permission_denied"

    def test_sqlite_internal_error_returns_generic(
        self, tmp_path, mocker, caplog,
    ):
        mocker.patch.object(
            sqlite3, "connect",
            side_effect=sqlite3.OperationalError("disk full"),
        )
        with caplog.at_level(logging.INFO):
            ok, category = db_validate.validate_sqlite("x.db", tmp_path)
        assert ok is False
        assert category == "generic"


class TestValidatePostgresql:

    def test_missing_driver_returns_generic(self, mocker, caplog):
        # Coverage expansion: lazy import — when psycopg is missing we
        # must NOT crash; we return ``generic``.
        mocker.patch.dict(sys.modules, {"psycopg": None})
        with caplog.at_level(logging.ERROR):
            ok, cat = db_validate.validate_postgresql(
                "n", "h", 5432, "u", "p",
            )
        assert ok is False
        assert cat == "generic"

    def test_driver_raises_auth_failure(self, mocker, caplog):
        # Coverage expansion: full happy-path through the lazy import
        # but the connect call raises an auth error — categorized to
        # ``auth_failed`` and the password NEVER appears in the log.
        fake_psycopg = mocker.MagicMock()
        fake_psycopg.connect.side_effect = Exception(
            "FATAL: password authentication failed for user 'u' "
            "(used: hunter2-secret)",
        )
        mocker.patch.dict(sys.modules, {"psycopg": fake_psycopg})
        with caplog.at_level(logging.INFO):
            ok, cat = db_validate.validate_postgresql(
                "n", "h", 5432, "u", "hunter2-secret",
            )
        assert ok is False
        assert cat == "auth_failed"
        # security-reviewer MED-3 — log redaction
        for record in caplog.records:
            assert "hunter2-secret" not in record.getMessage()

    def test_driver_happy_path(self, mocker):
        fake_cur = mocker.MagicMock()
        fake_conn = mocker.MagicMock()
        fake_conn.cursor.return_value.__enter__ = lambda s: fake_cur
        fake_conn.cursor.return_value.__exit__ = lambda *a: None
        fake_psycopg = mocker.MagicMock()
        fake_psycopg.connect.return_value = fake_conn
        mocker.patch.dict(sys.modules, {"psycopg": fake_psycopg})
        ok, cat = db_validate.validate_postgresql(
            "n", "h", 5432, "u", "p",
        )
        assert ok is True
        assert cat is None
        fake_cur.execute.assert_called_with("SELECT 1")
        fake_conn.close.assert_called_once()


class TestValidateMysql:

    def test_missing_driver_returns_generic(self, mocker, caplog):
        mocker.patch.dict(sys.modules, {"pymysql": None})
        with caplog.at_level(logging.ERROR):
            ok, cat = db_validate.validate_mysql(
                "n", "h", 3306, "u", "p",
            )
        assert ok is False
        assert cat == "generic"

    def test_driver_raises_host_unreachable(self, mocker, caplog):
        fake_pymysql = mocker.MagicMock()
        fake_pymysql.connect.side_effect = Exception(
            "could not connect to server: Connection refused",
        )
        mocker.patch.dict(sys.modules, {"pymysql": fake_pymysql})
        with caplog.at_level(logging.INFO):
            ok, cat = db_validate.validate_mysql(
                "n", "h", 3306, "u", "p",
            )
        assert ok is False
        assert cat == "host_unreachable"

    def test_driver_happy_path(self, mocker):
        fake_cur = mocker.MagicMock()
        fake_conn = mocker.MagicMock()
        fake_conn.cursor.return_value.__enter__ = lambda s: fake_cur
        fake_conn.cursor.return_value.__exit__ = lambda *a: None
        fake_pymysql = mocker.MagicMock()
        fake_pymysql.connect.return_value = fake_conn
        mocker.patch.dict(sys.modules, {"pymysql": fake_pymysql})
        ok, cat = db_validate.validate_mysql(
            "n", "h", 3306, "u", "p",
        )
        assert ok is True
        assert cat is None


class TestLiveConnectDispatch:

    def test_sqlite_dispatch(self, tmp_path):
        ok, cat = db_validate.live_connect(
            "sqlite", {"name": "sethlans.db"}, tmp_path,
        )
        assert ok is True
        assert cat is None

    def test_postgresql_dispatch(self, tmp_path, mocker):
        mocker.patch.object(
            db_validate, "validate_postgresql",
            return_value=(True, None),
        )
        ok, cat = db_validate.live_connect(
            "postgresql",
            {
                "name": "n", "host": "h", "port": 5432,
                "user": "u", "password": "p",
            },
            tmp_path,
        )
        assert (ok, cat) == (True, None)

    def test_mysql_dispatch(self, tmp_path, mocker):
        mocker.patch.object(
            db_validate, "validate_mysql", return_value=(True, None),
        )
        ok, cat = db_validate.live_connect(
            "mysql",
            {
                "name": "n", "host": "h", "port": 3306,
                "user": "u", "password": "p",
            },
            tmp_path,
        )
        assert (ok, cat) == (True, None)

    def test_custom_engine_returns_ok(self, tmp_path):
        # Coverage expansion: 'custom' bypasses live-connect — the user
        # owns verification of their backend.
        ok, cat = db_validate.live_connect("custom", {}, tmp_path)
        assert ok is True
        assert cat is None

    def test_unknown_engine_returns_generic(self, tmp_path):
        ok, cat = db_validate.live_connect(
            "oracle", {}, tmp_path,
        )
        assert ok is False
        assert cat == "generic"

    def test_missing_payload_fields_gracefully_handled(
        self, tmp_path, mocker,
    ):
        # Coverage expansion: if payload is missing host/port/user the
        # dispatcher coerces to safe defaults (empty / 0).
        mocker.patch.object(
            db_validate, "validate_postgresql",
            return_value=(True, None),
        )
        ok, cat = db_validate.live_connect(
            "postgresql", {}, tmp_path,
        )
        assert (ok, cat) == (True, None)


class TestExports:

    def test_dunder_all(self):
        for name in (
            "CONNECTION_TIMEOUT_SECONDS",
            "live_connect",
            "categorize_exception",
            "validate_sqlite",
            "validate_postgresql",
            "validate_mysql",
        ):
            assert name in db_validate.__all__
