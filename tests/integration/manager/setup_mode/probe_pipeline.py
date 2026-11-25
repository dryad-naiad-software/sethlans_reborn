# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The submit's database path, in process: answers -> ``[database]`` keys
(``validate_db.database_values``) -> the run-time ``DATABASES`` entry
(``plan.database_entry``) -> ``db_probe.check_database``.

Also builds SQLite files the way a running manager leaves them: the
``auth_user`` columns Django creates and the ``workers_managersettings``
table, optionally in WAL mode with a writer still open.
"""
import hashlib
import sqlite3
from pathlib import Path

from django.db.utils import ConnectionHandler

from shared.settings_parse import new_parser

from sethlans_manager.setup_mode import db_probe
from sethlans_manager.setup_mode.fields import Errors
from sethlans_manager.setup_mode.plan import database_entry
from sethlans_manager.setup_mode.validate_db import database_values

AUTH_USER_SQLITE = (
    'CREATE TABLE "auth_user" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, '
    '"password" varchar(128) NOT NULL, "last_login" datetime NULL, "is_superuser" bool NOT NULL, '
    '"username" varchar(150) NOT NULL UNIQUE, "last_name" varchar(150) NOT NULL, '
    '"email" varchar(254) NOT NULL, "is_staff" bool NOT NULL, "is_active" bool NOT NULL, '
    '"date_joined" datetime NOT NULL, "first_name" varchar(150) NOT NULL)'
)
MANAGER_SETTINGS_SQLITE = 'CREATE TABLE "workers_managersettings" ("id" integer NOT NULL PRIMARY KEY)'
INSERT_USER = (
    'INSERT INTO "auth_user" ("password", "is_superuser", "username", "last_name", "email", '
    '"is_staff", "is_active", "date_joined", "first_name") VALUES (?, ?, ?, \'\', \'\', ?, 1, '
    '\'2025-01-01 00:00:00\', \'\')'
)


def planned_entry(database: dict, data_root: Path) -> dict:
    """The ``DATABASES['default']`` a submit with ``database`` would test."""
    errors, warnings = Errors(), []
    values = database_values({"database": database}, new_parser(), errors, warnings)
    assert not errors, errors.by_field
    return database_entry(values, data_root)


def probe(blocker, database: dict, data_root: Path, username: str = "", write_test: bool = True):
    entry = planned_entry(database, data_root)
    with blocker.unblock():
        return db_probe.check_database(entry, username, write_test=write_test)


def open_probe_connection(entry: dict):
    """A connection with exactly the probe's settings (caller closes it)."""
    return ConnectionHandler({"default": {}, "probe": db_probe.probe_settings(entry)})["probe"]


def sqlite_db(path: Path, *, users=(), tables=("auth_user", "workers_managersettings"), wal=False,
              keep_open=False):
    """Create a SQLite file; ``users`` is ``(username, is_superuser)`` pairs.

    With ``keep_open`` the writing connection is returned still open
    (in WAL mode the rows then sit in the ``-wal`` file, not yet
    checkpointed into the main file).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if wal:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute('CREATE TABLE "django_migrations" ("id" integer PRIMARY KEY, "app" varchar(255))')
    if "auth_user" in tables:
        conn.execute(AUTH_USER_SQLITE)
    if "workers_managersettings" in tables:
        conn.execute(MANAGER_SETTINGS_SQLITE)
    for username, superuser in users:
        conn.execute(INSERT_USER, ("!", int(superuser), username, int(superuser)))
    conn.commit()
    if keep_open:
        return conn
    conn.close()
    return None


def digests(path: Path) -> dict:
    """SHA-256 of the database file and of its ``-wal`` file when that holds anything.

    A read-only reader of a WAL database may leave an empty ``-wal`` (and
    a ``-shm`` index) behind; an empty ``-wal`` holds no change.
    """
    result = {}
    for candidate in (path, path.with_name(path.name + "-wal")):
        if candidate.exists() and candidate.stat().st_size:
            result[candidate.name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result
