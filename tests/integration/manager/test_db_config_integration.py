# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for database configuration builder.

Verifies that ``build_database_config()`` correctly resolves the
priority chain: ``SETHLANS_DATABASE_URL`` > individual env vars >
the settings file's ``[database]`` > SQLite default.  Each test passes
its parsed settings in and uses ``monkeypatch`` for env vars so nothing
leaks between tests. The run-time database overrides of the environment
(a CI job against MySQL/MariaDB sets them) are removed before each test.
"""

import configparser
from pathlib import Path

import pytest

from sethlans_manager.db_config import build_database_config
from tests.db_env import clear_db_env


@pytest.fixture(autouse=True)
def _no_database_env(monkeypatch):
    clear_db_env(monkeypatch)


def _empty_config() -> configparser.ConfigParser:
    """Return a fresh, empty ConfigParser."""
    return configparser.ConfigParser()


def _config_with(section: str, **kwargs) -> configparser.ConfigParser:
    """Return a ConfigParser pre-loaded with one section."""
    config = configparser.ConfigParser()
    config.add_section(section)
    for k, v in kwargs.items():
        config.set(section, k, v)
    return config


# -------------------------------------------------------------------
# Default config: no settings file, no env vars → SQLite
# -------------------------------------------------------------------


class TestDefaultSqlite:

    def test_default_produces_sqlite(self, tmp_path):
        """With no INI and no env vars, the engine is sqlite3."""
        result = build_database_config(_empty_config())
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_default_sqlite_path_in_manager_data_dir(self, tmp_path, monkeypatch):
        """Default SQLite file is ``<manager data folder>/db.sqlite3`` (source mode)."""
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(tmp_path / "data"))
        result = build_database_config(_empty_config())
        assert Path(result["default"]["NAME"]) == tmp_path / "data" / "db.sqlite3"

    def test_default_sqlite_path_never_in_settings_folder(self, tmp_path, monkeypatch):
        """The settings file's folder never holds the default database."""
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(tmp_path / "settings" / "sethlans.ini"))
        result = build_database_config(_empty_config())
        assert Path(result["default"]["NAME"]).parent == tmp_path / "data"

    def test_default_sqlite_has_timeout(self, tmp_path):
        """SQLite config includes a timeout option."""
        result = build_database_config(_empty_config())
        options = result["default"].get("OPTIONS", {})
        assert options.get("timeout", 0) > 0


# -------------------------------------------------------------------
# SETHLANS_DATABASE_URL env var (highest priority)
# -------------------------------------------------------------------


class TestDatabaseUrl:

    def test_sqlite_url_sets_engine(self, tmp_path, monkeypatch):
        """``sqlite:///path/to/db`` is parsed correctly."""
        db_path = str(tmp_path / "my.db")
        monkeypatch.setenv(
            "SETHLANS_DATABASE_URL", f"sqlite:///{db_path}",
        )
        result = build_database_config(_empty_config())
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_postgres_url_parsed(self, tmp_path, monkeypatch):
        """``postgres://user:pass@host/db`` is parsed correctly."""
        monkeypatch.setenv(
            "SETHLANS_DATABASE_URL",
            "postgres://pguser:pgpass@dbhost:5432/mydb",
        )
        result = build_database_config(_empty_config())
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "mydb"
        assert db["HOST"] == "dbhost"
        assert db["PORT"] == "5432"
        assert db["USER"] == "pguser"
        assert db["PASSWORD"] == "pgpass"

    def test_mysql_url_parsed(self, tmp_path, monkeypatch):
        """``mysql://user:pass@host/db`` is parsed correctly."""
        monkeypatch.setenv(
            "SETHLANS_DATABASE_URL",
            "mysql://myuser:mypass@sqlhost:3306/mydb",
        )
        result = build_database_config(_empty_config())
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.mysql"
        assert db["NAME"] == "mydb"

    def test_url_overrides_ini_settings(self, tmp_path, monkeypatch):
        """DATABASE_URL takes precedence over INI [database] section."""
        monkeypatch.setenv(
            "SETHLANS_DATABASE_URL",
            "postgres://u:p@h/urldb",
        )
        config = _config_with(
            "database",
            engine="mysql",
            name="ini_db",
            host="ini_host",
        )
        result = build_database_config(config)
        db = result["default"]
        # URL wins over INI.
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "urldb"


# -------------------------------------------------------------------
# Individual env vars override INI values
# -------------------------------------------------------------------


class TestIndividualEnvVars:

    def test_env_engine_overrides_ini(self, tmp_path, monkeypatch):
        """SETHLANS_DB_ENGINE overrides the INI engine."""
        monkeypatch.setenv("SETHLANS_DB_ENGINE", "postgresql")
        monkeypatch.setenv("SETHLANS_DB_NAME", "envdb")
        monkeypatch.setenv("SETHLANS_DB_HOST", "envhost")
        config = _config_with(
            "database", engine="sqlite", name="inidb",
        )
        result = build_database_config(config)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "envdb"
        assert db["HOST"] == "envhost"

    def test_env_password_overrides_ini(self, tmp_path, monkeypatch):
        """SETHLANS_DB_PASSWORD env var overrides INI password."""
        monkeypatch.setenv("SETHLANS_DB_ENGINE", "postgresql")
        monkeypatch.setenv("SETHLANS_DB_NAME", "db")
        monkeypatch.setenv("SETHLANS_DB_HOST", "h")
        monkeypatch.setenv("SETHLANS_DB_PASSWORD", "env_secret")
        config = _config_with(
            "database",
            engine="postgresql",
            password="ini_secret",
        )
        result = build_database_config(config)
        assert result["default"]["PASSWORD"] == "env_secret"

    def test_env_port_overrides_ini(self, tmp_path, monkeypatch):
        """SETHLANS_DB_PORT env var overrides INI port."""
        monkeypatch.setenv("SETHLANS_DB_ENGINE", "postgresql")
        monkeypatch.setenv("SETHLANS_DB_NAME", "db")
        monkeypatch.setenv("SETHLANS_DB_HOST", "h")
        monkeypatch.setenv("SETHLANS_DB_PORT", "9999")
        config = _config_with("database", engine="postgresql", port="5432")
        result = build_database_config(config)
        assert result["default"]["PORT"] == "9999"


# -------------------------------------------------------------------
# INI [database] section
# -------------------------------------------------------------------


class TestIniDatabaseSection:

    def test_ini_postgresql_config(self, tmp_path):
        """INI with [database] engine=postgresql is resolved."""
        config = _config_with(
            "database",
            engine="postgresql",
            name="pgdb",
            host="pghost",
            port="5432",
            user="pguser",
            password="pgpass",
        )
        result = build_database_config(config)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "pgdb"
        assert db["HOST"] == "pghost"
        assert db["USER"] == "pguser"

    def test_ini_mysql_config(self, tmp_path):
        """INI with [database] engine=mysql is resolved."""
        config = _config_with(
            "database",
            engine="mysql",
            name="mydb",
            host="myhost",
        )
        result = build_database_config(config)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.mysql"

    def test_ini_custom_engine_passthrough(self, tmp_path):
        """Unknown engine strings pass through verbatim."""
        config = _config_with(
            "database",
            engine="django.contrib.gis.db.backends.postgis",
            name="gisdb",
            host="gishost",
        )
        result = build_database_config(config)
        db = result["default"]
        assert db["ENGINE"] == (
            "django.contrib.gis.db.backends.postgis"
        )


# -------------------------------------------------------------------
# External DB options
# -------------------------------------------------------------------


class TestExternalDbOptions:

    def test_postgresql_has_connect_timeout(self, tmp_path):
        """PostgreSQL config includes connect_timeout option."""
        config = _config_with(
            "database",
            engine="postgresql",
            name="db",
            host="h",
        )
        result = build_database_config(config)
        options = result["default"].get("OPTIONS", {})
        assert options.get("connect_timeout") == 10

    def test_mysql_has_connect_timeout(self, tmp_path):
        """MySQL config includes connect_timeout option."""
        config = _config_with(
            "database", engine="mysql", name="db", host="h",
        )
        result = build_database_config(config)
        options = result["default"].get("OPTIONS", {})
        assert options.get("connect_timeout") == 10


# -------------------------------------------------------------------
# Docker secrets (_FILE suffix)
# -------------------------------------------------------------------


class TestDockerSecrets:

    def test_password_file_env_var(self, tmp_path, monkeypatch):
        """SETHLANS_DB_PASSWORD_FILE reads password from a file."""
        secret_file = tmp_path / "db_password"
        secret_file.write_text("file_secret\n")
        monkeypatch.setenv("SETHLANS_DB_ENGINE", "postgresql")
        monkeypatch.setenv("SETHLANS_DB_NAME", "db")
        monkeypatch.setenv("SETHLANS_DB_HOST", "h")
        monkeypatch.setenv(
            "SETHLANS_DB_PASSWORD_FILE", str(secret_file),
        )
        result = build_database_config(_empty_config())
        assert result["default"]["PASSWORD"] == "file_secret"
