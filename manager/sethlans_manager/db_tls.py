# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
TLS ``OPTIONS`` for an external database (FR-WZ-7b).

Keys of ``sethlans.ini [database]`` (env: ``SETHLANS_DB_<KEY>``):

* ``tls``: ``true`` or ``false`` (default ``false``);
* ``tls_ca_file``: absolute path of the CA certificate file;
* ``tls_mode``: PostgreSQL only, ``verify-full`` (default), ``verify-ca``
  or ``require``.

PostgreSQL (psycopg): ``{'sslmode': <mode>, 'sslrootcert': <ca>}``.
MySQL and MariaDB (PyMySQL): ``{'ssl': {'ca': <ca>}}``, which makes
PyMySQL check the certificate and the host name. PyMySQL with TLS and no
CA file checks neither, so for MySQL a CA file is required.
``local_infile`` is never set. The setup form and the running manager
build their ``OPTIONS`` here, so the connection test uses the same ones.
"""
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

TLS_KEYS = ("tls", "tls_ca_file", "tls_mode")
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_url_warning_logged = False
POSTGRES_TLS_MODES = ("verify-full", "verify-ca", "require")
DEFAULT_POSTGRES_TLS_MODE = "verify-full"


class DatabaseTLSError(ValueError):
    """The ``[database]`` TLS keys do not make a usable setting."""


def warn_if_url_overrides_tls(config) -> None:
    """Log once per process when ``SETHLANS_DATABASE_URL`` hides set TLS keys.

    The URL replaces the whole ``[database]`` configuration, TLS included;
    this only says so. Behaviour is unchanged.
    """
    global _url_warning_logged
    if _url_warning_logged:
        return
    present = [key for key in TLS_KEYS
               if os.getenv(f"SETHLANS_DB_{key.upper()}") is not None or config.has_option("database", key)]
    if present:
        _url_warning_logged = True
        logger.warning("SETHLANS_DATABASE_URL is set, so the database TLS settings (%s) are ignored; "
                       "the URL wins.", ", ".join(present))


def tls_enabled(raw: str) -> bool:
    return (raw or "").strip().lower() in TRUE_WORDS


def tls_options(engine: str, get: Callable[[str, str], str]) -> dict:
    """Return the TLS ``OPTIONS`` for ``engine`` from ``get(key, default)``."""
    if not tls_enabled(get("tls", "false")):
        return {}
    ca_file = get("tls_ca_file", "").strip()
    if "postgresql" in engine:
        mode = get("tls_mode", DEFAULT_POSTGRES_TLS_MODE).strip() or DEFAULT_POSTGRES_TLS_MODE
        if mode not in POSTGRES_TLS_MODES:
            raise DatabaseTLSError(f"[database] tls_mode must be one of {', '.join(POSTGRES_TLS_MODES)}")
        options = {"sslmode": mode}
        if ca_file:
            options["sslrootcert"] = ca_file
        return options
    if "mysql" in engine:
        if not ca_file:
            raise DatabaseTLSError("[database] tls = true needs tls_ca_file for MySQL and MariaDB")
        return {"ssl": {"ca": ca_file}}
    return {}
