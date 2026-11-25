# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Advanced database section (FR-WZ-5, FR-WZ-7b).

Turns ``answers["database"]`` into the ``sethlans.ini [database]`` keys.
An empty password keeps the stored one (the form never shows it). With
TLS on, the CA file must be an absolute path to a readable file; MySQL
and MariaDB always need it (PyMySQL checks nothing without one), and so
do PostgreSQL's ``verify-full`` and ``verify-ca``. TLS off towards a host
that is not this machine gives a warning, not an error.
"""
import os
from typing import Dict, List, Optional

from django.views.decorators.debug import sensitive_variables

from sethlans_manager.db_tls import DEFAULT_POSTGRES_TLS_MODE, POSTGRES_TLS_MODES
from sethlans_manager.setup_mode import fields
from sethlans_manager.setup_mode.net import is_loopback_host
from sethlans_manager.setup_mode.plan import stored

ENGINES = ("sqlite", "postgresql", "mysql")
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}
MAX_CA_FILE_BYTES = 1024 * 1024
UNENCRYPTED_WARNING = "The database connection to {host} is not encrypted (TLS is off)."


def _ca_file(answers: dict, engine: str, mode: str, errors: fields.Errors) -> str:
    ca = fields.text(answers, "tls_ca_file", "database.tls_ca_file", errors, max_length=4096,
                     label="The CA file") or ""
    needed = engine == "mysql" or mode in ("verify-full", "verify-ca")
    if not ca:
        if needed:
            errors.add("database.tls_ca_file", "A CA file is needed to check the database server's certificate.")
        return ""
    if not os.path.isabs(ca) or not os.path.isfile(ca):
        errors.add("database.tls_ca_file", "The CA file must be the absolute path of an existing file.")
    elif os.path.getsize(ca) > MAX_CA_FILE_BYTES:
        errors.add("database.tls_ca_file", "The CA file is larger than 1 MiB.")
    elif not os.access(ca, os.R_OK):
        errors.add("database.tls_ca_file", "The CA file cannot be read.")
    return ca


def _tls(answers: dict, engine: str, host: str, errors: fields.Errors, warnings: List[str]) -> Dict[str, str]:
    enabled = fields.boolean(answers, "tls", "database.tls", errors, default=not is_loopback_host(host))
    if not enabled:
        if not is_loopback_host(host):
            warnings.append(UNENCRYPTED_WARNING.format(host=host))
        return {"tls": "false"}
    values = {"tls": "true"}
    mode = ""
    if engine == "postgresql":
        mode = fields.text(answers, "tls_mode", "database.tls_mode", errors, label="The TLS mode") \
            or DEFAULT_POSTGRES_TLS_MODE
        if mode not in POSTGRES_TLS_MODES:
            errors.add("database.tls_mode", f"Choose one of {', '.join(POSTGRES_TLS_MODES)}.")
        values["tls_mode"] = mode
    ca = _ca_file(answers, engine, mode, errors)
    if ca:
        values["tls_ca_file"] = ca
    return values


def _sqlite(answers: dict, errors: fields.Errors) -> Dict[str, str]:
    name = fields.text(answers, "name", "database.name", errors, max_length=4096, label="The database file")
    if not name:
        return {"engine": "sqlite"}
    if not os.path.isabs(name):
        errors.add("database.name", "The database file must be an absolute path.")
    return {"engine": "sqlite", "name": name}


@sensitive_variables("answers", "password")
def _external(answers: dict, engine: str, stored_config, errors: fields.Errors,
              warnings: List[str]) -> Dict[str, str]:
    name = fields.text(answers, "name", "database.name", errors, required=True, max_length=128,
                       label="The database name")
    host = fields.text(answers, "host", "database.host", errors, required=True, max_length=253,
                       label="The database host")
    if host and any(ch.isspace() for ch in host):
        errors.add("database.host", "The database host must not contain spaces.")
    port = fields.integer(answers, "port", "database.port", errors, low=1, high=65535,
                          default=DEFAULT_PORTS[engine], label="The database port")
    user = fields.text(answers, "user", "database.user", errors, required=True, max_length=128,
                       label="The database user")
    password = fields.text(answers, "password", "database.password", errors, max_length=1024, strip=False,
                           label="The database password")
    if not password:
        password = stored(stored_config, "database", "password")
    values = {"engine": engine, "name": name or "", "host": host or "", "port": str(port or ""),
              "user": user or ""}
    if password:
        values["password"] = password
    if host:
        values.update(_tls(answers, engine, host, errors, warnings))
    return values


def database_values(answers: dict, stored_config, errors: fields.Errors,
                    warnings: List[str]) -> Optional[Dict[str, str]]:
    """The ``[database]`` keys from the answers, or ``None`` after an error."""
    section = fields.section(answers, "database", errors)
    engine = fields.text(section, "engine", "database.engine", errors, label="The database engine") or "sqlite"
    if engine not in ENGINES:
        errors.add("database.engine", f"Choose one of {', '.join(ENGINES)}.")
        return None
    if engine == "sqlite":
        values = _sqlite(section, errors)
    else:
        values = _external(section, engine, stored_config, errors, warnings)
    return None if errors.has("database") else values
