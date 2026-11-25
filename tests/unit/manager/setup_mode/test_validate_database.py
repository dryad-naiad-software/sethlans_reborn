# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The validation table: the database section, its TLS keys and the connection test."""
import pytest

from sethlans_manager.setup_mode import db_probe
from shared.settings_parse import new_parser
from tests.unit.manager.setup_mode._helpers import answers, pg_database

CA_NEEDED = "A CA file is needed to check the database server's certificate."


@pytest.fixture
def ca_file(tmp_path):
    path = tmp_path / "ca.pem"
    path.write_text("-----BEGIN CERTIFICATE-----\n")
    return str(path)


def _db(check, tmp_path, database, ctx=None):
    return check(answers(tmp_path, database=database), ctx)


class TestEngine:

    @pytest.mark.parametrize("engine", ["oracle", "sqlite3", "MySQL"])
    def test_unknown_engine(self, check, tmp_path, engine):
        errors, _plan = _db(check, tmp_path, {"engine": engine})
        assert errors == {"database.engine": ["Choose one of sqlite, postgresql, mysql."]}

    def test_missing_engine_is_sqlite(self, check, tmp_path):
        errors, plan = _db(check, tmp_path, {})
        assert errors == {} and plan.database == {"engine": "sqlite"}

    def test_sqlite_name_must_be_absolute(self, check, tmp_path):
        errors, _plan = _db(check, tmp_path, {"engine": "sqlite", "name": "db.sqlite3"})
        assert errors == {"database.name": ["The database file must be an absolute path."]}

    def test_sqlite_default_file_follows_the_planned_data_folder(self, check, tmp_path, probe):
        check(answers(tmp_path))
        entry = probe.call_args.args[0]
        assert entry["ENGINE"] == "django.db.backends.sqlite3"
        assert entry["NAME"] == str(tmp_path / "sethlans-data" / "manager" / "db.sqlite3")
        assert probe.call_args.args[1] == "boss"

    def test_sqlite_named_file_is_used(self, check, tmp_path, probe):
        name = str(tmp_path / "elsewhere.sqlite3")
        errors, plan = _db(check, tmp_path, {"engine": "sqlite", "name": name})
        assert errors == {} and plan.database == {"engine": "sqlite", "name": name}
        assert probe.call_args.args[0]["NAME"] == name


class TestExternalFields:

    @pytest.mark.parametrize("field, label", [("name", "The database name"), ("host", "The database host"),
                                              ("user", "The database user")])
    def test_required(self, check, tmp_path, field, label):
        errors, _plan = _db(check, tmp_path, pg_database(**{field: ""}))
        assert errors == {f"database.{field}": [f"{label} is required."]}

    def test_host_with_spaces(self, check, tmp_path):
        errors, _plan = _db(check, tmp_path, pg_database(host="db host", tls=False))
        assert errors == {"database.host": ["The database host must not contain spaces."]}

    @pytest.mark.parametrize("port", [0, 65536, "x"])
    def test_bad_port(self, check, tmp_path, port):
        errors, _plan = _db(check, tmp_path, pg_database(port=port))
        assert list(errors) == ["database.port"]

    @pytest.mark.parametrize("engine, port", [("postgresql", "5432"), ("mysql", "3306")])
    def test_default_ports(self, check, tmp_path, engine, port):
        errors, plan = _db(check, tmp_path, pg_database(engine=engine))
        assert errors == {} and plan.database["port"] == port

    def test_keys_for_the_settings_file(self, check, tmp_path):
        _errors, plan = _db(check, tmp_path, pg_database(port=6543))
        assert plan.database == {"engine": "postgresql", "name": "sethlans", "host": "127.0.0.1",
                                 "port": "6543", "user": "seth", "password": "db-secret-pw", "tls": "false"}

    def test_probe_uses_the_runtime_entry(self, check, tmp_path, probe):
        _db(check, tmp_path, pg_database(port=6543))
        entry = probe.call_args.args[0]
        assert (entry["ENGINE"], entry["HOST"], entry["PORT"]) == ("django.db.backends.postgresql", "127.0.0.1", "6543")
        assert entry["PASSWORD"] == "db-secret-pw"

    def test_probe_failure_is_on_the_database_key(self, check, tmp_path, probe):
        probe.side_effect = db_probe.ProbeError("auth_failed")
        errors, plan = _db(check, tmp_path, pg_database())
        assert plan is None
        assert errors == {"database": ["The database refused the user name or password."]}

    def test_database_error_skips_the_probe(self, check, tmp_path, probe):
        _db(check, tmp_path, pg_database(user=""))
        probe.assert_not_called()


class TestStoredPassword:

    def _ctx(self, ctx, password="stored-db-pw"):
        parser = new_parser()
        parser.read_string(f"[database]\nengine = postgresql\npassword = {password}\n")
        ctx.stored = parser
        return ctx

    def test_empty_password_keeps_the_stored_one(self, check, tmp_path, ctx, probe):
        errors, plan = _db(check, tmp_path, pg_database(password=""), self._ctx(ctx))
        assert errors == {}
        assert plan.database["password"] == "stored-db-pw"
        assert probe.call_args.args[0]["PASSWORD"] == "stored-db-pw"

    def test_missing_password_keeps_the_stored_one(self, check, tmp_path, ctx):
        database = pg_database()
        del database["password"]
        _errors, plan = _db(check, tmp_path, database, self._ctx(ctx))
        assert plan.database["password"] == "stored-db-pw"

    def test_new_password_replaces_it(self, check, tmp_path, ctx):
        _errors, plan = _db(check, tmp_path, pg_database(password="new-db-pw"), self._ctx(ctx))
        assert plan.database["password"] == "new-db-pw"

    def test_password_is_not_stripped(self, check, tmp_path):
        _errors, plan = _db(check, tmp_path, pg_database(password=" spaced pw "))
        assert plan.database["password"] == " spaced pw "

    def test_no_password_anywhere_writes_no_key(self, check, tmp_path):
        _errors, plan = _db(check, tmp_path, pg_database(password=""))
        assert "password" not in plan.database


class TestTls:

    def test_remote_host_defaults_to_tls_and_needs_a_ca(self, check, tmp_path):
        errors, _plan = _db(check, tmp_path, pg_database(host="db.example"))
        assert errors == {"database.tls_ca_file": [CA_NEEDED]}

    def test_loopback_defaults_to_no_tls_without_warning(self, check, tmp_path):
        errors, plan = _db(check, tmp_path, pg_database(host="localhost"))
        assert errors == {} and plan.database["tls"] == "false" and plan.warnings == []

    def test_tls_off_to_a_remote_host_warns(self, check, tmp_path):
        errors, plan = _db(check, tmp_path, pg_database(host="192.0.2.1", tls=False))
        assert errors == {}
        assert plan.warnings == ["The database connection to 192.0.2.1 is not encrypted (TLS is off)."]

    @pytest.mark.parametrize("mode", ["verify-full", "verify-ca"])
    def test_postgres_verifying_modes_need_a_ca(self, check, tmp_path, mode):
        errors, _plan = _db(check, tmp_path, pg_database(tls=True, tls_mode=mode))
        assert errors == {"database.tls_ca_file": [CA_NEEDED]}

    def test_postgres_require_needs_no_ca(self, check, tmp_path, probe):
        errors, plan = _db(check, tmp_path, pg_database(tls=True, tls_mode="require"))
        assert errors == {}
        assert plan.database["tls_mode"] == "require" and "tls_ca_file" not in plan.database
        assert probe.call_args.args[0]["OPTIONS"]["sslmode"] == "require"

    def test_postgres_default_mode_is_verify_full(self, check, tmp_path, ca_file, probe):
        _errors, plan = _db(check, tmp_path, pg_database(tls=True, tls_ca_file=ca_file))
        assert plan.database["tls_mode"] == "verify-full"
        options = probe.call_args.args[0]["OPTIONS"]
        assert (options["sslmode"], options["sslrootcert"]) == ("verify-full", ca_file)

    @pytest.mark.parametrize("mode, warning", [
        ("require", "TLS mode require encrypts the connection to db.example but does not check its certificate, "
                    "so another server could pose as it; verify-full checks both."),
        ("verify-ca", "TLS mode verify-ca checks the certificate of db.example but not its host name; "
                      "verify-full checks both."),
    ])
    def test_weaker_postgres_modes_warn(self, check, tmp_path, ca_file, mode, warning):
        errors, plan = _db(check, tmp_path, pg_database(host="db.example", tls=True, tls_mode=mode,
                                                        tls_ca_file=ca_file))
        assert errors == {} and plan.warnings == [warning]

    def test_verify_full_does_not_warn(self, check, tmp_path, ca_file):
        _errors, plan = _db(check, tmp_path, pg_database(host="db.example", tls=True, tls_ca_file=ca_file))
        assert plan.warnings == []

    def test_bad_postgres_mode(self, check, tmp_path, ca_file):
        errors, _plan = _db(check, tmp_path, pg_database(tls=True, tls_mode="allow", tls_ca_file=ca_file))
        assert errors == {"database.tls_mode": ["Choose one of verify-full, verify-ca, require."]}

    def test_mysql_always_needs_a_ca(self, check, tmp_path):
        errors, _plan = _db(check, tmp_path, pg_database(engine="mysql", tls=True))
        assert errors == {"database.tls_ca_file": [CA_NEEDED]}

    def test_mysql_with_a_ca(self, check, tmp_path, ca_file, probe):
        errors, plan = _db(check, tmp_path, pg_database(engine="mysql", tls=True, tls_ca_file=ca_file))
        assert errors == {}
        assert plan.database["tls_ca_file"] == ca_file and "tls_mode" not in plan.database
        options = probe.call_args.args[0]["OPTIONS"]
        assert options["ssl"] == {"ca": ca_file}
        assert "local_infile" not in options

    def test_ca_must_be_absolute_and_exist(self, check, tmp_path):
        for value in ("ca.pem", str(tmp_path / "missing.pem"), str(tmp_path)):
            errors, _plan = _db(check, tmp_path, pg_database(engine="mysql", tls=True, tls_ca_file=value))
            assert errors == {"database.tls_ca_file": ["The CA file must be the absolute path of an existing file."]}

    def test_ca_over_1_mib(self, check, tmp_path):
        big = tmp_path / "big.pem"
        big.write_bytes(b"a" * (1024 * 1024 + 1))
        errors, _plan = _db(check, tmp_path, pg_database(engine="mysql", tls=True, tls_ca_file=str(big)))
        assert errors == {"database.tls_ca_file": ["The CA file is larger than 1 MiB."]}

    def test_tls_must_be_a_boolean(self, check, tmp_path):
        errors, _plan = _db(check, tmp_path, pg_database(tls="yes"))
        assert errors["database.tls"] == ["Must be true or false."]

    def test_env_tls_mode_that_breaks_the_entry_is_reported(self, check, tmp_path, ca_file, monkeypatch):
        monkeypatch.setenv("SETHLANS_DB_TLS_MODE", "bogus")
        errors, _plan = _db(check, tmp_path, pg_database(tls=True, tls_ca_file=ca_file))
        assert errors == {"database.tls": ["[database] tls_mode must be one of verify-full, verify-ca, require"]}
