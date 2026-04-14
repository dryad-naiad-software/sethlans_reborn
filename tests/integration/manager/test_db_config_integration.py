# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for database configuration builder.

Verifies that ``build_database_config()`` correctly resolves the
priority chain: ``SETHLANS_DATABASE_URL`` > individual env vars >
``manager.ini`` > SQLite default.  Each test uses ``tmp_path`` for
its ``manager.ini`` and ``monkeypatch`` for env vars so nothing
leaks between tests.
"""

import configparser
from pathlib import Path

from sethlans_manager.db_config import build_database_config


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
# Default config: no manager.ini, no env vars → SQLite
# -------------------------------------------------------------------


class TestDefaultSqlite:

    def test_default_produces_sqlite(self, tmp_path):
        """With no INI and no env vars, the engine is sqlite3."""
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_default_sqlite_path_alongside_ini(self, tmp_path):
        """Default SQLite database is in the same dir as manager.ini."""
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
        db_name = result["default"]["NAME"]
        assert Path(db_name).parent == tmp_path
        assert "db.sqlite3" in Path(db_name).name

    def test_default_sqlite_has_timeout(self, tmp_path):
        """SQLite config includes a timeout option."""
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_postgres_url_parsed(self, tmp_path, monkeypatch):
        """``postgres://user:pass@host/db`` is parsed correctly."""
        monkeypatch.setenv(
            "SETHLANS_DATABASE_URL",
            "postgres://pguser:pgpass@dbhost:5432/mydb",
        )
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
        assert result["default"]["PASSWORD"] == "env_secret"

    def test_env_port_overrides_ini(self, tmp_path, monkeypatch):
        """SETHLANS_DB_PORT env var overrides INI port."""
        monkeypatch.setenv("SETHLANS_DB_ENGINE", "postgresql")
        monkeypatch.setenv("SETHLANS_DB_NAME", "db")
        monkeypatch.setenv("SETHLANS_DB_HOST", "h")
        monkeypatch.setenv("SETHLANS_DB_PORT", "9999")
        config = _config_with("database", engine="postgresql", port="5432")
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
        options = result["default"].get("OPTIONS", {})
        assert options.get("connect_timeout") == 10

    def test_mysql_has_connect_timeout(self, tmp_path):
        """MySQL config includes connect_timeout option."""
        config = _config_with(
            "database", engine="mysql", name="db", host="h",
        )
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(config, ini_path)
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
        ini_path = tmp_path / "manager.ini"
        result = build_database_config(_empty_config(), ini_path)
        assert result["default"]["PASSWORD"] == "file_secret"
