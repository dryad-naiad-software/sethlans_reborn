# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pre-filled form of ``GET /api/setup/form/`` (FR-WZ-1, FR-RS-3).

Defaults, or the earlier answers from ``sethlans.ini``, never a stored
secret: the database password shows only as ``password_set``, and no
password hash is returned. The admin accounts come from the configured
database, opened read-only (FR-WZ-11). Problems found while filling the
form go into ``errors``; the form is still returned.
"""
import logging

from shared import data_folder
from shared.settings_file import (
    KEY_DATA_DIR, KEY_SETUP_MODE, SECTION_FIRST_ADMIN, SECTION_PATHS, SECTION_SETUP, SETUP_MODES,
    get_settings_dir,
)

from sethlans_manager.db_config import build_database_config
from sethlans_manager.db_tls import DEFAULT_POSTGRES_TLS_MODE, tls_enabled
from sethlans_manager.setup_mode import db_probe, leftovers, net
from sethlans_manager.setup_mode.fields import ascii_int
from sethlans_manager.setup_mode.plan import stored

logger = logging.getLogger(__name__)

DEFAULT_MODE = "manager_worker"
DEFAULT_PORT = 8080


def _engine(raw: str) -> str:
    raw = raw.lower()
    if "postgres" in raw:
        return "postgresql"
    if "mysql" in raw:
        return "mysql"
    return "sqlite"


def _database(parser) -> dict:
    engine = _engine(stored(parser, "database", "engine", "sqlite"))
    port = stored(parser, "database", "port")
    return {
        "engine": engine,
        "name": stored(parser, "database", "name"),
        "host": stored(parser, "database", "host"),
        "port": ascii_int(port),
        "user": stored(parser, "database", "user"),
        "password": "",
        "password_set": bool(stored(parser, "database", "password")),
        "tls": tls_enabled(stored(parser, "database", "tls", "false")),
        "tls_ca_file": stored(parser, "database", "tls_ca_file"),
        "tls_mode": stored(parser, "database", "tls_mode") or DEFAULT_POSTGRES_TLS_MODE,
    }


def _accounts(parser, errors: list) -> bool:
    """Whether the configured database already has an admin account."""
    try:
        entry = build_database_config(parser)["default"]
        return db_probe.check_database(entry, write_test=False).accounts_exist
    except ValueError as exc:
        errors.append(f"The stored database settings are not usable: {exc}")
    except db_probe.ProbeError as exc:
        errors.append(f"Could not read the configured database: {exc.message}")
    return False


def _data_folder(parser) -> dict:
    path = stored(parser, SECTION_PATHS, KEY_DATA_DIR) or str(get_settings_dir())
    free = data_folder.free_bytes(path)
    return {
        "path": path,
        "free_bytes": free,
        "low_space": free is not None and free < data_folder.LOW_SPACE_BYTES,
    }


def build_form(parser, read_errors=()) -> dict:
    """The form as JSON-ready data; ``parser`` is the current ``sethlans.ini``."""
    errors = list(read_errors)
    mode = stored(parser, SECTION_SETUP, KEY_SETUP_MODE)
    port = stored(parser, "server", "port")
    accounts_exist = _accounts(parser, errors)
    return {
        "mode": mode if mode in SETUP_MODES else DEFAULT_MODE,
        "modes": sorted(SETUP_MODES),
        "hostname": net.hostname(),
        "addresses": net.ipv4_addresses(),
        "bind_addresses": net.bind_addresses(),
        "server": {
            "host": stored(parser, "server", "host") or net.ANY_ADDRESS,
            "port": ascii_int(port) or DEFAULT_PORT,
        },
        "data_folder": _data_folder(parser),
        "database": _database(parser),
        "admin": {
            "accounts_exist": accounts_exist,
            "keep_existing": accounts_exist,
            "username": stored(parser, SECTION_FIRST_ADMIN, "username"),
            "email": stored(parser, SECTION_FIRST_ADMIN, "email"),
            "password": "",
            "replace_password": False,
        },
        "cards": [],
        "managers": [],
        "leftovers": leftovers.leftover_paths(),
        "last_failure": leftovers.last_failure(),
        "errors": errors,
    }
