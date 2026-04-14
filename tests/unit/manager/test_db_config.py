# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/sethlans_manager/db_config.py``.

Covers default SQLite fallback, INI ``[database]`` section parsing,
engine aliases, env var overrides, ``DATABASE_URL`` parsing,
``_FILE`` suffix for Docker secrets, and partial config fallback.
"""

import configparser

import pytest

from sethlans_manager.db_config import (
    _resolve_engine,
    build_database_config,
)


@pytest.fixture
def ini_path(tmp_path):
    """Path to a temporary manager.ini."""
    return tmp_path / "manager.ini"


@pytest.fixture
def empty_config():
    """An empty ConfigParser (no database section)."""
    return configparser.ConfigParser()


# ---- Default (no config) returns SQLite ----------------------------------

class TestDefaultSqlite:

    def test_no_config_returns_sqlite(
        self, empty_config, ini_path, monkeypatch,
    ):
        # Clear any SETHLANS env vars
        for key in list(monkeypatch._patches if hasattr(
            monkeypatch, '_patches',
        ) else []):
            pass
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_ENGINE', raising=False)
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        monkeypatch.delenv('SETHLANS_DB_HOST', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PORT', raising=False)
        monkeypatch.delenv('SETHLANS_DB_USER', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PASSWORD', raising=False)
        monkeypatch.delenv(
            'SETHLANS_DB_PASSWORD_FILE', raising=False,
        )
        result = build_database_config(empty_config, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_default_sqlite_uses_db_sqlite3_name(
        self, empty_config, ini_path, monkeypatch,
    ):
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_ENGINE', raising=False)
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        # Mock is_frozen to return False for source mode
        monkeypatch.setattr(
            'sethlans_manager.db_config.is_frozen', lambda: False,
        )
        result = build_database_config(empty_config, ini_path)
        db = result["default"]
        assert db["NAME"].endswith("db.sqlite3")


# ---- INI [database] section ----------------------------------------------

class TestIniDatabaseSection:

    def test_postgresql_from_ini(
        self, ini_path, monkeypatch,
    ):
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_ENGINE', raising=False)
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        monkeypatch.delenv('SETHLANS_DB_HOST', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PORT', raising=False)
        monkeypatch.delenv('SETHLANS_DB_USER', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PASSWORD', raising=False)
        monkeypatch.delenv(
            'SETHLANS_DB_PASSWORD_FILE', raising=False,
        )
        config = configparser.ConfigParser()
        config.add_section('database')
        config.set('database', 'engine', 'postgresql')
        config.set('database', 'name', 'sethlans_db')
        config.set('database', 'host', 'db.example.com')
        config.set('database', 'port', '5432')
        config.set('database', 'user', 'admin')
        config.set('database', 'password', 'secret')
        result = build_database_config(config, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "sethlans_db"
        assert db["HOST"] == "db.example.com"
        assert db["PORT"] == "5432"
        assert db["USER"] == "admin"
        assert db["PASSWORD"] == "secret"

    def test_mysql_from_ini(self, ini_path, monkeypatch):
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_ENGINE', raising=False)
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        monkeypatch.delenv('SETHLANS_DB_HOST', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PORT', raising=False)
        monkeypatch.delenv('SETHLANS_DB_USER', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PASSWORD', raising=False)
        monkeypatch.delenv(
            'SETHLANS_DB_PASSWORD_FILE', raising=False,
        )
        config = configparser.ConfigParser()
        config.add_section('database')
        config.set('database', 'engine', 'mysql')
        config.set('database', 'name', 'mydb')
        config.set('database', 'host', 'localhost')
        config.set('database', 'port', '3306')
        config.set('database', 'user', 'root')
        config.set('database', 'password', 'pw')
        result = build_database_config(config, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.mysql"
        assert db["OPTIONS"] == {"connect_timeout": 10}


# ---- Engine aliases ------------------------------------------------------

class TestEngineAliases:

    @pytest.mark.parametrize("alias,expected", [
        ("sqlite", "django.db.backends.sqlite3"),
        ("postgresql", "django.db.backends.postgresql"),
        ("mysql", "django.db.backends.mysql"),
    ])
    def test_alias_mapping(self, alias, expected):
        assert _resolve_engine(alias) == expected

    def test_custom_engine_passes_through(self):
        custom = "my.custom.backend"
        assert _resolve_engine(custom) == custom

    def test_case_insensitive(self):
        assert _resolve_engine("PostgreSQL") == (
            "django.db.backends.postgresql"
        )


# ---- Env var overrides ---------------------------------------------------

class TestEnvVarOverrides:

    def test_env_overrides_ini(self, ini_path, monkeypatch):
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv(
            'SETHLANS_DB_PASSWORD_FILE', raising=False,
        )
        monkeypatch.setenv('SETHLANS_DB_ENGINE', 'postgresql')
        monkeypatch.setenv('SETHLANS_DB_NAME', 'env_db')
        monkeypatch.setenv('SETHLANS_DB_HOST', 'env-host')
        monkeypatch.setenv('SETHLANS_DB_PORT', '5433')
        monkeypatch.setenv('SETHLANS_DB_USER', 'env_user')
        monkeypatch.setenv('SETHLANS_DB_PASSWORD', 'env_pass')
        config = configparser.ConfigParser()
        config.add_section('database')
        config.set('database', 'engine', 'sqlite')
        config.set('database', 'name', 'ini_db')
        result = build_database_config(config, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "env_db"
        assert db["HOST"] == "env-host"
        assert db["PASSWORD"] == "env_pass"


# ---- DATABASE_URL parsing ------------------------------------------------

class TestDatabaseUrl:

    def test_sqlite_url(self, ini_path, monkeypatch):
        monkeypatch.setenv(
            'SETHLANS_DATABASE_URL', 'sqlite:///tmp/test.db',
        )
        monkeypatch.setattr(
            'sethlans_manager.db_config.is_frozen', lambda: False,
        )
        result = build_database_config(
            configparser.ConfigParser(), ini_path,
        )
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"

    def test_postgres_url(self, ini_path, monkeypatch):
        monkeypatch.setenv(
            'SETHLANS_DATABASE_URL',
            'postgres://user:pass@host:5432/mydb',
        )
        result = build_database_config(
            configparser.ConfigParser(), ini_path,
        )
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "mydb"
        assert db["HOST"] == "host"
        assert db["PORT"] == "5432"
        assert db["USER"] == "user"
        assert db["PASSWORD"] == "pass"

    def test_mysql_url(self, ini_path, monkeypatch):
        monkeypatch.setenv(
            'SETHLANS_DATABASE_URL',
            'mysql://admin:secret@db.local:3306/app',
        )
        result = build_database_config(
            configparser.ConfigParser(), ini_path,
        )
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.mysql"
        assert db["NAME"] == "app"

    def test_postgresql_scheme(self, ini_path, monkeypatch):
        monkeypatch.setenv(
            'SETHLANS_DATABASE_URL',
            'postgresql://u:p@h:5432/db',
        )
        result = build_database_config(
            configparser.ConfigParser(), ini_path,
        )
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"


# ---- _FILE suffix for Docker secrets -------------------------------------

class TestDockerSecrets:

    def test_password_file_read(
        self, tmp_path, ini_path, monkeypatch,
    ):
        secret_file = tmp_path / "db_password"
        secret_file.write_text("docker_secret_pw\n")
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PASSWORD', raising=False)
        monkeypatch.setenv('SETHLANS_DB_ENGINE', 'postgresql')
        monkeypatch.setenv('SETHLANS_DB_NAME', 'mydb')
        monkeypatch.setenv('SETHLANS_DB_HOST', 'db')
        monkeypatch.setenv('SETHLANS_DB_USER', 'user')
        monkeypatch.setenv('SETHLANS_DB_PORT', '5432')
        monkeypatch.setenv(
            'SETHLANS_DB_PASSWORD_FILE', str(secret_file),
        )
        result = build_database_config(
            configparser.ConfigParser(), ini_path,
        )
        db = result["default"]
        assert db["PASSWORD"] == "docker_secret_pw"


# ---- Missing/partial config falls back to SQLite -------------------------

class TestFallback:

    def test_partial_config_without_engine_defaults_sqlite(
        self, ini_path, monkeypatch,
    ):
        monkeypatch.delenv('SETHLANS_DATABASE_URL', raising=False)
        monkeypatch.delenv('SETHLANS_DB_ENGINE', raising=False)
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        monkeypatch.delenv('SETHLANS_DB_HOST', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PORT', raising=False)
        monkeypatch.delenv('SETHLANS_DB_USER', raising=False)
        monkeypatch.delenv('SETHLANS_DB_PASSWORD', raising=False)
        monkeypatch.delenv(
            'SETHLANS_DB_PASSWORD_FILE', raising=False,
        )
        monkeypatch.setattr(
            'sethlans_manager.db_config.is_frozen', lambda: False,
        )
        # Config with a database section but no engine key
        config = configparser.ConfigParser()
        config.add_section('database')
        config.set('database', 'name', '')
        result = build_database_config(config, ini_path)
        db = result["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"
