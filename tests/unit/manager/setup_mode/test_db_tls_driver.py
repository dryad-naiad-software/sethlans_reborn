# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``db_tls`` OPTIONS for psycopg and PyMySQL, ``build_database_config`` and ``mysql_driver``."""
import ast
import sys
from pathlib import Path

import pytest
from django.db.utils import ConnectionHandler

from sethlans_manager import db_tls
from sethlans_manager.db_config import build_database_config
from shared.settings_parse import new_parser

PG = "django.db.backends.postgresql"
MYSQL = "django.db.backends.mysql"
MANAGER_PACKAGE = Path(__file__).resolve().parents[4] / "manager" / "sethlans_manager"


def _getter(values):
    return lambda key, default="": values.get(key, default)


class TestTlsOptions:

    @pytest.mark.parametrize("raw, on", [("true", True), (" Yes ", True), ("1", True), ("on", True),
                                         ("false", False), ("", False), (None, False), ("enabled", False)])
    def test_tls_enabled(self, raw, on):
        assert db_tls.tls_enabled(raw) is on

    @pytest.mark.parametrize("engine", [PG, MYSQL, "django.db.backends.sqlite3"])
    def test_off_gives_nothing(self, engine):
        assert db_tls.tls_options(engine, _getter({"tls": "false", "tls_ca_file": "/ca.pem"})) == {}

    def test_postgres_default_mode_with_ca(self):
        options = db_tls.tls_options(PG, _getter({"tls": "true", "tls_ca_file": " /ca.pem "}))
        assert options == {"sslmode": "verify-full", "sslrootcert": "/ca.pem"}

    @pytest.mark.parametrize("mode", ["verify-full", "verify-ca", "require"])
    def test_postgres_modes(self, mode):
        options = db_tls.tls_options(PG, _getter({"tls": "true", "tls_mode": mode}))
        assert options == {"sslmode": mode}

    def test_postgres_empty_mode_is_the_default(self):
        assert db_tls.tls_options(PG, _getter({"tls": "true", "tls_mode": " "}))["sslmode"] == "verify-full"

    @pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "VERIFY-FULL"])
    def test_postgres_weak_or_unknown_modes_are_refused(self, mode):
        with pytest.raises(db_tls.DatabaseTLSError, match="tls_mode must be one of"):
            db_tls.tls_options(PG, _getter({"tls": "true", "tls_mode": mode}))

    def test_mysql_with_ca(self):
        assert db_tls.tls_options(MYSQL, _getter({"tls": "true", "tls_ca_file": "/ca.pem"})) == {"ssl": {"ca": "/ca.pem"}}

    def test_mysql_without_ca_is_refused(self):
        with pytest.raises(db_tls.DatabaseTLSError, match="needs tls_ca_file"):
            db_tls.tls_options(MYSQL, _getter({"tls": "true"}))

    def test_mysql_ignores_the_postgres_mode(self):
        options = db_tls.tls_options(MYSQL, _getter({"tls": "true", "tls_ca_file": "/ca", "tls_mode": "x"}))
        assert options == {"ssl": {"ca": "/ca"}}

    def test_error_is_a_value_error(self):
        assert issubclass(db_tls.DatabaseTLSError, ValueError)


def _config(text):
    parser = new_parser()
    parser.read_string(text)
    return build_database_config(parser)["default"]


class TestBuildDatabaseConfig:

    def test_mysql_tls_options_and_no_local_infile(self):
        entry = _config("[database]\nengine = mysql\nname = s\nhost = db\nuser = u\ntls = true\ntls_ca_file = /ca.pem\n")
        assert entry["OPTIONS"] == {"connect_timeout": 10, "ssl": {"ca": "/ca.pem"}}

    def test_postgres_tls_options(self):
        entry = _config("[database]\nengine = postgresql\nname = s\nhost = db\nuser = u\ntls = true\n"
                        "tls_ca_file = /ca.pem\ntls_mode = verify-ca\n")
        assert entry["OPTIONS"] == {"connect_timeout": 10, "sslmode": "verify-ca", "sslrootcert": "/ca.pem"}

    def test_env_overrides_the_tls_keys(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_DB_TLS", "true")
        monkeypatch.setenv("SETHLANS_DB_TLS_CA_FILE", "/env-ca.pem")
        entry = _config("[database]\nengine = mysql\nname = s\nhost = db\nuser = u\ntls = false\n")
        assert entry["OPTIONS"]["ssl"] == {"ca": "/env-ca.pem"}

    def test_no_tls_keys_no_tls_options(self):
        entry = _config("[database]\nengine = postgresql\nname = s\nhost = db\nuser = u\n")
        assert entry["OPTIONS"] == {"connect_timeout": 10}

    def test_bad_tls_keys_raise(self):
        with pytest.raises(db_tls.DatabaseTLSError):
            _config("[database]\nengine = mysql\nname = s\nhost = db\nuser = u\ntls = true\n")


URL_WARNING = ("SETHLANS_DATABASE_URL is set, so the database TLS settings ({keys}) are ignored; "
               "the URL wins.")


@pytest.fixture
def url_warnings(monkeypatch, caplog):
    """A fresh once-per-process flag; returns the db_tls warning lines logged so far."""
    monkeypatch.setattr(db_tls, "_url_warning_logged", False)
    caplog.set_level("WARNING", logger=db_tls.__name__)
    return lambda: [r.getMessage() for r in caplog.records if r.name == db_tls.__name__]


def _parser(text=""):
    parser = new_parser()
    parser.read_string(text)
    return parser


class TestUrlOverridesTlsWarning:

    def test_tls_keys_in_the_file_warn(self, url_warnings):
        db_tls.warn_if_url_overrides_tls(_parser("[database]\ntls = true\ntls_ca_file = /ca.pem\n"))
        assert url_warnings() == [URL_WARNING.format(keys="tls, tls_ca_file")]

    def test_tls_keys_in_the_env_warn(self, url_warnings, monkeypatch):
        monkeypatch.setenv("SETHLANS_DB_TLS_MODE", "require")
        db_tls.warn_if_url_overrides_tls(_parser())
        assert url_warnings() == [URL_WARNING.format(keys="tls_mode")]

    def test_warns_once_per_process(self, url_warnings):
        config = _parser("[database]\ntls = false\n")
        for _ in range(3):
            db_tls.warn_if_url_overrides_tls(config)
        assert url_warnings() == [URL_WARNING.format(keys="tls")]

    @pytest.mark.parametrize("text", ["", "[database]\nengine = mysql\nname = s\n"])
    def test_silent_without_tls_keys(self, url_warnings, text):
        db_tls.warn_if_url_overrides_tls(_parser(text))
        assert url_warnings() == []

    def test_silence_does_not_use_up_the_warning(self, url_warnings):
        db_tls.warn_if_url_overrides_tls(_parser())
        db_tls.warn_if_url_overrides_tls(_parser("[database]\ntls = true\n"))
        assert url_warnings() == [URL_WARNING.format(keys="tls")]

    def test_build_with_a_url_warns_and_ignores_the_tls_keys(self, url_warnings, monkeypatch, tmp_path):
        monkeypatch.setenv("SETHLANS_DATABASE_URL", f"sqlite:///{(tmp_path / 'db.sqlite3').as_posix()}")
        entry = _config("[database]\nengine = mysql\ntls = true\ntls_ca_file = /ca.pem\n")
        assert entry["ENGINE"] == "django.db.backends.sqlite3"
        assert "ssl" not in entry.get("OPTIONS", {})
        assert url_warnings() == [URL_WARNING.format(keys="tls, tls_ca_file")]

    def test_build_without_a_url_does_not_warn(self, url_warnings):
        _config("[database]\nengine = mysql\nname = s\nhost = db\nuser = u\ntls = true\ntls_ca_file = /ca.pem\n")
        assert url_warnings() == []


class TestMysqlDriver:

    def test_mysqldb_is_pymysql(self):
        import sethlans_manager.mysql_driver  # noqa: F401

        import MySQLdb
        assert MySQLdb.__name__ == "pymysql"
        assert sys.modules["MySQLdb"] is sys.modules["pymysql"]

    def test_version_passes_djangos_check(self):
        import MySQLdb
        assert MySQLdb.version_info >= (1, 4, 3)

    def test_django_mysql_backend_loads(self):
        from django.db.backends.mysql import base

        assert base.Database.__name__ == "pymysql"

    def test_backend_passes_the_tls_options_to_the_driver(self):
        cfg = {"ENGINE": MYSQL, "NAME": "s", "HOST": "db", "PORT": "3306", "USER": "u", "PASSWORD": "pw",
               "OPTIONS": {"ssl": {"ca": "/ca.pem"}}}
        connection = ConnectionHandler({"default": {}, "probe": cfg})["probe"]
        params = connection.get_connection_params()
        assert params["ssl"] == {"ca": "/ca.pem"}
        assert "local_infile" not in params


def _first_statement(name):
    tree = ast.parse((MANAGER_PACKAGE / name).read_text(encoding="utf-8"))
    body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    return body[0]


class TestImportOrder:

    def test_settings_base_imports_the_driver_first(self):
        first = _first_statement("settings_base.py")
        assert isinstance(first, ast.ImportFrom)
        assert (first.module, [alias.name for alias in first.names]) == ("sethlans_manager", ["mysql_driver"])

    @pytest.mark.parametrize("name", ["settings.py", "settings_setup.py"])
    def test_both_settings_modules_start_with_settings_base(self, name):
        first = _first_statement(name)
        assert isinstance(first, ast.ImportFrom)
        assert first.module == "sethlans_manager.settings_base"
