# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``db_probe``: probe settings, error categories, and the check flow with the driver mocked."""
import logging
import re
import sqlite3
from unittest.mock import MagicMock

import pytest

from sethlans_manager.setup_mode import db_probe

PG = {"ENGINE": "django.db.backends.postgresql", "NAME": "s", "HOST": "db", "PORT": "5432", "USER": "u",
      "PASSWORD": "pw-for-probe", "OPTIONS": {"connect_timeout": 10, "sslmode": "verify-full"},
      "CONN_MAX_AGE": 60, "CONN_HEALTH_CHECKS": True}
MYSQL = dict(PG, ENGINE="django.db.backends.mysql", OPTIONS={"connect_timeout": 10, "ssl": {"ca": "/ca"}})


class TestProbeSettings:

    def test_postgres_timeouts_and_no_pooling(self):
        cfg = db_probe.probe_settings(PG)
        assert (cfg["CONN_MAX_AGE"], cfg["CONN_HEALTH_CHECKS"]) == (0, False)
        assert cfg["OPTIONS"] == {"connect_timeout": 5, "sslmode": "verify-full",
                                  "options": "-c statement_timeout=5000"}

    def test_mysql_timeouts_keep_the_tls_options(self):
        assert db_probe.probe_settings(MYSQL)["OPTIONS"] == {
            "connect_timeout": 5, "read_timeout": 5, "write_timeout": 5, "ssl": {"ca": "/ca"}}

    def test_sqlite_opens_read_only(self, tmp_path):
        path = tmp_path / "db.sqlite3"
        cfg = db_probe.probe_settings({"ENGINE": db_probe.SQLITE_ENGINE, "NAME": str(path),
                                       "OPTIONS": {"transaction_mode": "IMMEDIATE"}})
        assert cfg["NAME"] == path.as_uri() + "?mode=ro"
        assert cfg["OPTIONS"] == {"timeout": 5}

    def test_input_is_not_changed(self):
        before = repr(PG)
        db_probe.probe_settings(PG)
        assert repr(PG) == before


class TestCategorize:

    @pytest.mark.parametrize("exc, category", [
        (Exception('FATAL: password authentication failed for user "u"'), "auth_failed"),
        (Exception("Access denied for user 'u'@'h' (using password: YES)"), "auth_failed"),
        (Exception("SSL error: certificate verify failed"), "ssl_error"),
        (Exception("permission denied for schema public"), "permission_denied"),
        (Exception("connection timed out"), "timeout"),
        (Exception('database "x" does not exist'), "db_not_found"),
        (Exception("Unknown database 'x'"), "db_not_found"),
        (Exception("Connection refused"), "host_unreachable"),
        (Exception("could not translate host name"), "host_unreachable"),
        (Exception("something odd"), "generic"),
    ])
    def test_text(self, exc, category):
        assert db_probe.categorize_exception(exc, "") == category

    def test_class_name(self):
        class ConnectionTimeout(Exception):
            pass

        assert db_probe.categorize_exception(ConnectionTimeout("x"), "") == "timeout"

    def test_auth_in_a_host_name_is_not_auth(self):
        assert db_probe.categorize_exception(Exception('could not connect to "authdb.example"'), "") \
            == "host_unreachable"

    def test_password_in_the_text_does_not_decide(self):
        assert db_probe.categorize_exception(Exception("refused sslxx"), "sslxx") == "host_unreachable"

    def test_every_category_has_a_message(self):
        assert set(db_probe.MESSAGES) == {"auth_failed", "host_unreachable", "db_not_found", "permission_denied",
                                          "ssl_error", "timeout", "generic"}
        assert db_probe.ProbeError("nope").message == db_probe.MESSAGES["generic"]


@pytest.fixture
def connection(mocker):
    conn = MagicMock(name="connection")
    conn.ops.quote_name = lambda name: f'"{name}"'
    cursor = conn.cursor.return_value.__enter__.return_value
    conn.introspection.table_names.return_value = ["auth_user", "workers_managersettings", "other"]
    cursor.fetchone.side_effect = [(2,), (1,)]
    handler = mocker.patch.object(db_probe, "ConnectionHandler")
    handler.return_value.__getitem__.return_value = conn
    return conn, cursor, handler


class TestCheckDatabaseMocked:

    def test_connects_with_the_probe_alias(self, connection):
        conn, _cursor, handler = connection
        db_probe.check_database(PG, "boss")
        databases = handler.call_args.args[0]
        assert databases["default"] == {} and databases["probe"]["OPTIONS"]["connect_timeout"] == 5
        handler.return_value.__getitem__.assert_called_once_with("probe")
        conn.ensure_connection.assert_called_once()
        conn.close.assert_called_once()

    def test_creates_and_drops_a_probe_table(self, connection):
        _conn, cursor, _handler = connection
        db_probe.check_database(MYSQL, "boss")
        create, drop = (call.args[0] for call in cursor.execute.call_args_list[:2])
        name = re.fullmatch(r'CREATE TABLE "(sethlans_setup_probe_[0-9a-f]{8})" \(id INTEGER\)', create).group(1)
        assert drop == f'DROP TABLE "{name}"'

    def test_reads_accounts_with_select_only(self, connection):
        _conn, cursor, _handler = connection
        result = db_probe.check_database(PG, "boss", write_test=False)
        assert result == db_probe.AdminAccounts(True, True)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert all(sql.startswith("SELECT ") for sql in statements)
        assert cursor.execute.call_args_list[-1].args[1] == ["boss"]

    def test_no_username_asks_no_name(self, connection):
        _conn, cursor, _handler = connection
        assert db_probe.check_database(PG, write_test=False) == db_probe.AdminAccounts(True, None)
        assert cursor.execute.call_count == 1

    def test_missing_tables_mean_no_accounts(self, connection):
        conn, cursor, _handler = connection
        conn.introspection.table_names.return_value = ["auth_user"]
        assert db_probe.check_database(PG, "boss", write_test=False) == db_probe.AdminAccounts(False, False)

    def test_driver_error_is_categorised_and_redacted(self, connection, caplog):
        conn, _cursor, _handler = connection
        conn.ensure_connection.side_effect = Exception('FATAL: password authentication failed (pw-for-probe)')
        caplog.set_level(logging.INFO, logger=db_probe.__name__)
        with pytest.raises(db_probe.ProbeError) as info:
            db_probe.check_database(PG, "boss")
        assert info.value.category == "auth_failed"
        assert info.value.__cause__ is None and info.value.__suppress_context__ is True
        assert "pw-for-probe" not in caplog.text and "<redacted>" in caplog.text
        conn.close.assert_called_once()


class TestCheckDatabaseSqlite:
    """A stdlib SQLite file in ``tmp_path`` on the probe's private alias (no test database)."""

    @pytest.fixture(autouse=True)
    def _allow_private_alias(self, django_db_blocker):
        with django_db_blocker.unblock():
            yield

    def _db(self, path, superuser=True, tables=("auth_user", "workers_managersettings")):
        db = sqlite3.connect(path)
        for table in tables:
            columns = "id integer, username text, is_superuser bool" if table == "auth_user" else "id integer"
            db.execute(f"create table {table} ({columns})")
        if "auth_user" in tables:
            db.execute("insert into auth_user values (1, 'boss', ?)", (1 if superuser else 0,))
        db.commit()
        db.close()
        return path

    def _cfg(self, path):
        return {"ENGINE": db_probe.SQLITE_ENGINE, "NAME": str(path), "OPTIONS": {}}

    def test_missing_file_has_no_accounts_and_is_not_created(self, tmp_path):
        path = tmp_path / "db.sqlite3"
        assert db_probe.check_database(self._cfg(path), "boss") == db_probe.AdminAccounts(False, False)
        assert not path.exists()

    def test_existing_admin(self, tmp_path):
        path = self._db(tmp_path / "db.sqlite3")
        assert db_probe.check_database(self._cfg(path), "boss") == db_probe.AdminAccounts(True, True)
        assert db_probe.check_database(self._cfg(path), "other") == db_probe.AdminAccounts(True, False)

    def test_no_superuser(self, tmp_path):
        path = self._db(tmp_path / "db.sqlite3", superuser=False)
        assert db_probe.check_database(self._cfg(path)).accounts_exist is False

    def test_file_without_sethlans_tables(self, tmp_path):
        path = self._db(tmp_path / "db.sqlite3", tables=("auth_user",))
        assert db_probe.check_database(self._cfg(path), "boss") == db_probe.AdminAccounts(False, False)

    def test_file_is_left_unchanged(self, tmp_path):
        path = self._db(tmp_path / "db.sqlite3")
        before = path.read_bytes()
        db_probe.check_database(self._cfg(path), "boss")  # write_test defaults on; SQLite is never written
        assert path.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["db.sqlite3"]
