# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The database connection test and the read-only admin check (FR-WZ-7, FR-WZ-11).

Both use Django's own backend on a private alias, never the run-time
``connections``: ``ConnectionHandler({"default": {}, "probe": cfg})``.
(``ConnectionHandler`` refuses a set of databases without ``default``;
an empty ``default`` becomes the dummy backend.) ``cfg`` is the
``DATABASES['default']`` the running manager will build
(``db_config.build_database_config``), so the test uses the same driver
and ``OPTIONS``, TLS included, with 5 s timeouts.

* PostgreSQL, MySQL, MariaDB: connect, create and drop a probe table,
  then read the admin accounts with ``SELECT`` only.
* SQLite: no table, and no database file, is created; a missing file is
  not opened. An existing file is opened read-only
  (``file:<path>?mode=ro``; the backend connects with ``uri=True``), so
  its data is never changed. For a WAL-mode database SQLite may still
  create its WAL index files next to it (an empty ``<name>-wal`` and a
  ``<name>-shm``), as any reader does; they are SQLite's own and are left
  alone, since another process may have them open. ``immutable=1``
  would avoid them but would miss rows still only in the ``-wal`` file
  (a superuser created just before a crash), so it is not used.

A missing database file, or a database without the ``auth_user`` and
``workers_managersettings`` tables, has no accounts yet. Driver errors
become one of a fixed set of categories; the driver's text is logged
with the password removed and never returned.
"""
import copy
import logging
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.db.utils import ConnectionHandler
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5
SETHLANS_TABLES = frozenset({"auth_user", "workers_managersettings"})
SQLITE_ENGINE = "django.db.backends.sqlite3"

MESSAGES = {
    "auth_failed": "The database refused the user name or password.",
    "host_unreachable": "Could not reach the database server.",
    "db_not_found": "The database does not exist.",
    "permission_denied": "The database user may not create tables in this database.",
    "ssl_error": "The TLS connection to the database failed (check the CA file and the TLS mode).",
    "timeout": f"No connection to the database within {PROBE_TIMEOUT_SECONDS} seconds (is it running and reachable?).",
    "generic": "Could not connect to the database.",
}


@dataclass
class AdminAccounts:
    """What the configured database holds (``exists`` is None when no name was asked)."""

    accounts_exist: bool = False
    username_exists: Optional[bool] = None


class ProbeError(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category
        self.message = MESSAGES.get(category, MESSAGES["generic"])


def _redact(text: str, password: str) -> str:
    return text.replace(password, "<redacted>") if password else text


def categorize_exception(exc: BaseException, password: str) -> str:
    """Map a driver exception to a category, from its class name and text.

    "auth" in a host name must not read as an authentication failure, so
    the text signals are whole phrases.
    """
    cause = exc.__cause__ or exc
    name = f"{type(exc).__name__} {type(cause).__name__}".lower()
    text = _redact(f"{exc} {cause}", password).lower()
    auth_phrases = ("authentication failed", "password authentication", "auth_method", "auth method")
    if "auth" in name or any(p in text for p in auth_phrases) or "password" in text:
        return "auth_failed"
    if "ssl" in name or "ssl" in text or "tls" in text or "certificate" in text:
        return "ssl_error"
    if "permission" in text or "denied" in text or "privilege" in text:
        return "permission_denied"
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return "timeout"
    if "database" in text and ("not exist" in text or "unknown" in text or "not found" in text):
        return "db_not_found"
    if "could not connect" in text or "refused" in text or "host" in text or "resolve" in text:
        return "host_unreachable"
    return "generic"


def probe_settings(cfg: dict) -> dict:
    """A copy of ``cfg`` for one short probe: no pooling, 5 s timeouts."""
    cfg = copy.deepcopy(cfg)
    cfg["CONN_MAX_AGE"] = 0
    cfg["CONN_HEALTH_CHECKS"] = False
    options = cfg.setdefault("OPTIONS", {})
    engine = cfg.get("ENGINE", "")
    if "postgresql" in engine:
        options["connect_timeout"] = PROBE_TIMEOUT_SECONDS
        options.setdefault("options", f"-c statement_timeout={PROBE_TIMEOUT_SECONDS * 1000}")
    elif "mysql" in engine:
        for key in ("connect_timeout", "read_timeout", "write_timeout"):
            options[key] = PROBE_TIMEOUT_SECONDS
    elif engine == SQLITE_ENGINE:
        cfg["OPTIONS"] = {"timeout": PROBE_TIMEOUT_SECONDS}
        cfg["NAME"] = Path(cfg["NAME"]).as_uri() + "?mode=ro"
    return cfg


@contextmanager
def _open(cfg: dict):
    connection = ConnectionHandler({"default": {}, "probe": probe_settings(cfg)})["probe"]
    try:
        yield connection
    finally:
        connection.close()


def _read_admins(connection, username: str) -> AdminAccounts:
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if not SETHLANS_TABLES <= set(connection.introspection.table_names(cursor)):
            return AdminAccounts(False, False if username else None)
        cursor.execute(f"SELECT COUNT(*) FROM {quote('auth_user')} WHERE {quote('is_superuser')} = %s", [True])
        superusers = cursor.fetchone()[0]
        exists = None
        if username:
            cursor.execute(f"SELECT 1 FROM {quote('auth_user')} WHERE {quote('username')} = %s", [username])
            exists = cursor.fetchone() is not None
    return AdminAccounts(superusers > 0, exists)


def _create_and_drop(connection) -> None:
    table = connection.ops.quote_name(f"sethlans_setup_probe_{secrets.token_hex(4)}")
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {table} (id INTEGER)")
        cursor.execute(f"DROP TABLE {table}")


@sensitive_variables("cfg", "password")
def check_database(cfg: dict, username: str = "", *, write_test: bool = True) -> AdminAccounts:
    """Test ``cfg`` and read its admin accounts; raise :class:`ProbeError`.

    ``write_test`` creates and drops a probe table (PostgreSQL and MySQL
    at submit). SQLite data is never written (see the module doc for its
    WAL index files); a missing file has no accounts and is not created.
    """
    password = cfg.get("PASSWORD") or ""
    if cfg.get("ENGINE") == SQLITE_ENGINE:
        if not Path(cfg["NAME"]).is_file():
            return AdminAccounts(False, False if username else None)
        write_test = False
    try:
        with _open(cfg) as connection:
            connection.ensure_connection()
            if write_test:
                _create_and_drop(connection)
            return _read_admins(connection, username)
    except Exception as exc:  # noqa: BLE001 - driver errors vary; categorised below
        category = categorize_exception(exc, password)
        logger.info("Database check failed (%s): %s", category, _redact(str(exc), password))
        raise ProbeError(category) from None
