# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Database configuration builder for ``settings.py``.

Reads the ``[database]`` section from ``manager.ini`` and constructs
the Django ``DATABASES`` dict.  Environment variables override INI
values.  Docker secrets are supported via the ``_FILE`` suffix
convention.

Priority chain:
    ``SETHLANS_DATABASE_URL`` > individual env vars > manager.ini > SQLite default

Env vars:
    ``SETHLANS_DB_ENGINE``, ``SETHLANS_DB_NAME``, ``SETHLANS_DB_HOST``,
    ``SETHLANS_DB_PORT``, ``SETHLANS_DB_USER``, ``SETHLANS_DB_PASSWORD``

Docker secrets (reads file contents):
    ``SETHLANS_DB_PASSWORD_FILE``
"""

import configparser
import os
from pathlib import Path

from shared.frozen_paths import get_data_dir, is_frozen

# Engine aliases for user-friendly names in manager.ini
_ENGINE_MAP = {
    "sqlite": "django.db.backends.sqlite3",
    "postgresql": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
}

# Default ports per engine
_DEFAULT_PORTS = {
    "django.db.backends.postgresql": "5432",
    "django.db.backends.mysql": "3306",
}


def _read_secret_file(env_var: str) -> str | None:
    """Read a Docker secret from a ``_FILE``-suffixed env var."""
    file_path = os.getenv(env_var)
    if file_path and os.path.isfile(file_path):
        return Path(file_path).read_text().strip()
    return None


def _resolve_engine(raw: str) -> str:
    """Map short engine names to full Django backend paths."""
    return _ENGINE_MAP.get(raw.lower(), raw)


def _get_default_sqlite_path(ini_path: Path) -> str:
    """Return the default SQLite database path."""
    if is_frozen():
        return str(get_data_dir("manager") / "db.sqlite3")
    # In source mode, put the DB alongside manager.ini's parent.
    return str(ini_path.parent / "db.sqlite3")


def build_database_config(
    config: configparser.ConfigParser,
    ini_path: Path,
) -> dict:
    """Build Django DATABASES dict from config + env vars.

    Parameters
    ----------
    config : configparser.ConfigParser
        Already-parsed ``manager.ini``.
    ini_path : Path
        Path to ``manager.ini`` (used to derive default SQLite path).

    Returns
    -------
    dict
        A ``DATABASES`` dict suitable for ``django.conf.settings``.
    """
    # Check for composite DATABASE_URL first (highest priority).
    database_url = os.getenv("SETHLANS_DATABASE_URL")
    if database_url:
        return _parse_database_url(database_url, ini_path)

    # Read individual env vars, falling back to manager.ini [database].
    def _get(key: str, default: str = "") -> str:
        env_key = f"SETHLANS_DB_{key.upper()}"
        val = os.getenv(env_key)
        if val is not None:
            return val
        return config.get("database", key, fallback=default)

    engine_raw = _get("engine", "sqlite")
    engine = _resolve_engine(engine_raw)

    # Password: env var > _FILE secret > ini
    password = os.getenv("SETHLANS_DB_PASSWORD")
    if password is None:
        password = _read_secret_file("SETHLANS_DB_PASSWORD_FILE")
    if password is None:
        password = config.get("database", "password", fallback="")

    if engine == "django.db.backends.sqlite3":
        return _build_sqlite(
            _get("name", ""), ini_path,
        )

    name = _get("name")
    host = _get("host")
    port = _get("port", _DEFAULT_PORTS.get(engine, ""))
    user = _get("user")

    return _build_external(
        engine, name, host, port, user, password,
    )


def _build_sqlite(name: str, ini_path: Path) -> dict:
    """Build a SQLite DATABASES config."""
    db_name = name or os.getenv(
        "SETHLANS_DB_NAME",
        _get_default_sqlite_path(ini_path),
    )
    return {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": db_name,
            "OPTIONS": {"timeout": 30},
        },
    }


def _build_external(
    engine: str,
    name: str,
    host: str,
    port: str,
    user: str,
    password: str,
) -> dict:
    """Build an external database DATABASES config."""
    options = {}
    if "postgresql" in engine:
        options = {"connect_timeout": 10}
    elif "mysql" in engine:
        options = {"connect_timeout": 10}

    return {
        "default": {
            "ENGINE": engine,
            "NAME": name,
            "HOST": host,
            "PORT": port,
            "USER": user,
            "PASSWORD": password,
            "OPTIONS": options,
        },
    }


def _parse_database_url(url: str, ini_path: Path) -> dict:
    """Parse a DATABASE_URL string into a DATABASES config.

    Supports: ``sqlite:///path/to/db``, ``postgres://user:pass@host/db``,
    ``mysql://user:pass@host/db``.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    engine_map = {
        "sqlite": "django.db.backends.sqlite3",
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
        "mysql": "django.db.backends.mysql",
    }
    engine = engine_map.get(scheme)
    if not engine:
        engine = scheme  # Allow custom engine paths

    if engine == "django.db.backends.sqlite3":
        db_path = parsed.path or _get_default_sqlite_path(ini_path)
        return _build_sqlite(db_path, ini_path)

    return _build_external(
        engine=engine,
        name=parsed.path.lstrip("/") if parsed.path else "",
        host=parsed.hostname or "",
        port=str(parsed.port) if parsed.port else "",
        user=parsed.username or "",
        password=parsed.password or "",
    )
