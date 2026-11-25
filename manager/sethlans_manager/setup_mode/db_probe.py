# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The database connection test and the read-only admin check (FR-WZ-7, FR-WZ-11).

Both use Django's own backend on a private, one-use alias, never the
run-time ``connections``: ``ConnectionHandler({"default": {},
"probe_<hex>": cfg})``. (``ConnectionHandler`` refuses a set of databases
without ``default``; an empty ``default`` becomes the dummy backend.) The
alias is new for every probe because Django checks the server version
once per alias per process (``RAN_DB_VERSION_CHECK``); a server older
than Django supports raises ``NotSupportedError``, reported as
``version_unsupported``. ``cfg`` is the ``DATABASES['default']`` the
running manager will build (``db_config.build_database_config``), so the
test uses the same driver and ``OPTIONS``, TLS included, with these
bounds:

* PostgreSQL: ``connect_timeout`` 5 s, client TCP keepalives
  (``keepalives``, ``keepalives_idle``, ``keepalives_interval``,
  ``keepalives_count``) and ``tcp_user_timeout`` (libpq 12 or later),
  then ``SET statement_timeout`` on the connection. No ``options``
  start-up parameter, which PgBouncer refuses.
* MySQL, MariaDB (PyMySQL): connect, read and write timeouts of 5 s.

Then: PostgreSQL, MySQL, MariaDB create a probe table with a primary key
(accepted under MySQL ``sql_require_primary_key`` and MariaDB
``innodb_force_primary_key``), drop it (in ``finally`` once it exists),
and read the admin accounts with ``SELECT`` only.

SQLite: no table, and no database file, is created; a missing file is
not opened. An existing file is opened read-only (``file:<path>?mode=ro``;
the backend connects with ``uri=True``), so its data is never changed.
For a WAL-mode database SQLite may still create its WAL index files next
to it (an empty ``<name>-wal`` and a ``<name>-shm``), as any reader does;
they are SQLite's own and are left alone, since another process may have
them open. ``immutable=1`` would avoid them but would miss rows still
only in the ``-wal`` file (a superuser created just before a crash), so
it is not used.

A missing database file, or a database without the ``auth_user`` and
``workers_managersettings`` tables, has no accounts yet; only active
superusers count. Driver errors become one of a fixed set of categories,
matched on the driver's own text; that text is logged with the password
removed and never returned.
"""
import copy
import logging
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.db.utils import ConnectionHandler, NotSupportedError
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5
SETHLANS_TABLES = frozenset({"auth_user", "workers_managersettings"})
SQLITE_ENGINE = "django.db.backends.sqlite3"
POSTGRES_KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": PROBE_TIMEOUT_SECONDS,
    "keepalives_interval": 2,
    "keepalives_count": 2,
    "tcp_user_timeout": PROBE_TIMEOUT_SECONDS * 1000,  # milliseconds
}

MESSAGES = {
    "auth_failed": "The database refused the user name or password.",
    "host_unreachable": "Could not reach the database server.",
    "db_not_found": "The database does not exist.",
    "permission_denied": "The database user may not create tables in this database.",
    "ssl_error": "The TLS connection to the database failed (check the CA file and the TLS mode).",
    "timeout": f"No connection to the database within {PROBE_TIMEOUT_SECONDS} seconds (is it running and reachable?).",
    "version_unsupported": ("The database server is too old. Sethlans needs PostgreSQL 14, MySQL 8.0.11 "
                            "or MariaDB 10.5, or later."),
    "generic": "Could not connect to the database.",
}


@dataclass
class AdminAccounts:
    """What the configured database holds (``exists`` is None when no name was asked)."""

    accounts_exist: bool = False
    username_exists: Optional[bool] = None


class ProbeError(Exception):
    def __init__(self, category: str, detail: str = ""):
        super().__init__(category)
        self.category = category
        message = MESSAGES.get(category, MESSAGES["generic"])
        self.message = f"{message} ({detail})" if detail else message


def _redact(text: str, password: str) -> str:
    return text.replace(password, "<redacted>") if password else text


def categorize_exception(exc: BaseException) -> str:
    """Map a driver exception to a category, from its class name and raw text.

    "auth" in a host name must not read as an authentication failure, so
    the text signals are whole phrases. The text is not redacted here:
    a password must not change the category (it is redacted for the log).
    """
    if isinstance(exc, NotSupportedError):
        return "version_unsupported"
    cause = exc.__cause__ or exc
    name = f"{type(exc).__name__} {type(cause).__name__}".lower()
    text = f"{exc} {cause}".lower()
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
    """A copy of ``cfg`` for one short probe: no pooling, bounded waits."""
    cfg = copy.deepcopy(cfg)
    cfg["CONN_MAX_AGE"] = 0
    cfg["CONN_HEALTH_CHECKS"] = False
    options = cfg.setdefault("OPTIONS", {})
    engine = cfg.get("ENGINE", "")
    if "postgresql" in engine:
        options["connect_timeout"] = PROBE_TIMEOUT_SECONDS
        options.update(POSTGRES_KEEPALIVES)
    elif "mysql" in engine:
        for key in ("connect_timeout", "read_timeout", "write_timeout"):
            options[key] = PROBE_TIMEOUT_SECONDS
    elif engine == SQLITE_ENGINE:
        cfg["OPTIONS"] = {"timeout": PROBE_TIMEOUT_SECONDS}
        cfg["NAME"] = Path(cfg["NAME"]).as_uri() + "?mode=ro"
    return cfg


@contextmanager
def _open(cfg: dict):
    alias = f"probe_{secrets.token_hex(8)}"
    connection = ConnectionHandler({"default": {}, alias: probe_settings(cfg)})[alias]
    try:
        connection.ensure_connection()
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = {PROBE_TIMEOUT_SECONDS * 1000}")
        yield connection
    finally:
        connection.close()


def _read_admins(connection, username: str) -> AdminAccounts:
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if not SETHLANS_TABLES <= set(connection.introspection.table_names(cursor)):
            return AdminAccounts(False, False if username else None)
        cursor.execute(f"SELECT COUNT(*) FROM {quote('auth_user')} "
                       f"WHERE {quote('is_superuser')} = %s AND {quote('is_active')} = %s", [True, True])
        superusers = cursor.fetchone()[0]
        exists = None
        if username:
            cursor.execute(f"SELECT 1 FROM {quote('auth_user')} WHERE {quote('username')} = %s", [username])
            exists = cursor.fetchone() is not None
    return AdminAccounts(superusers > 0, exists)


def _read_with_probe_table(connection, username: str) -> AdminAccounts:
    """Create the probe table, read the accounts, and drop the table whatever happens."""
    table = connection.ops.quote_name(f"sethlans_setup_probe_{secrets.token_hex(4)}")
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    try:
        return _read_admins(connection, username)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE {table}")


@sensitive_variables("cfg", "password")
def check_database(cfg: dict, username: str = "", *, write_test: bool = True) -> AdminAccounts:
    """Test ``cfg`` and read its admin accounts; raise :class:`ProbeError`.

    ``write_test`` creates and drops a probe table (PostgreSQL and
    MySQL at submit). SQLite data is never written (see the module doc for
    its WAL index files); a missing file has no accounts and is not created.
    """
    password = cfg.get("PASSWORD") or ""
    if cfg.get("ENGINE") == SQLITE_ENGINE:
        if not Path(cfg["NAME"]).is_file():
            return AdminAccounts(False, False if username else None)
        write_test = False
    try:
        with _open(cfg) as connection:
            if write_test:
                return _read_with_probe_table(connection, username)
            return _read_admins(connection, username)
    except Exception as exc:  # noqa: BLE001 - driver errors vary; categorised below
        category = categorize_exception(exc)
        logger.info("Database check failed (%s): %s", category, _redact(str(exc), password))
        detail = str(exc) if category == "version_unsupported" else ""
        raise ProbeError(category, detail) from None
