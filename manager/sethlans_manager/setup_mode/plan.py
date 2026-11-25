# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The checked answers (:class:`Plan`) and how they become ``sethlans.ini``.

:func:`write_settings` is one read-modify-write of the settings file under
its lock (``shared.settings_file.update_settings``: owner-only, atomic
replace), done last, after every check (D9). It sets ``[setup]``,
``[paths]``, ``[server] host/port``, replaces ``[database]`` (keeping
``conn_max_age``) and sets or removes ``[first_admin]``. Every other
section and key is kept. The admin password is in it only as a hash.
"""
import datetime
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from shared import settings_file
from shared.frozen_paths import DATA_ROOT_ENV, env_path
from shared.settings_parse import check_value, new_parser, serialise

from sethlans_manager.db_config import build_database_config

SQLITE_ENGINE = "django.db.backends.sqlite3"
KEPT_DATABASE_KEYS = ("conn_max_age",)


@dataclass
class Plan:
    mode: str
    data_root: Path
    server_host: str
    server_port: int
    database: Dict[str, str]
    first_admin: Optional[Dict[str, str]] = None
    warnings: List[str] = field(default_factory=list)


def planned_manager_data_dir(data_root: Path) -> Path:
    """The manager's data folder the running manager will use with ``data_root``.

    Same order as ``shared.frozen_paths.get_data_dir`` with the planned
    ``[paths] data_dir`` in place of the file's.
    """
    override = env_path("SETHLANS_MANAGER_DATA_DIR")
    if override is not None:
        return override
    root = env_path(DATA_ROOT_ENV)
    return (root if root is not None else data_root) / "manager"


def database_entry(database: Dict[str, str], data_root: Path) -> dict:
    """``DATABASES['default']`` the running manager will build from these answers.

    Environment overrides apply exactly as at run time. The default SQLite
    file follows the planned data folder. Raises ``ValueError`` for bad
    TLS keys (``db_tls.DatabaseTLSError``).
    """
    parser = new_parser()
    parser.add_section("database")
    for key, value in database.items():
        parser.set("database", key, value)
    entry = build_database_config(parser)["default"]
    named = os.getenv("SETHLANS_DATABASE_URL") or os.getenv("SETHLANS_DB_NAME") or database.get("name")
    if entry["ENGINE"] == SQLITE_ENGINE and not named:
        entry["NAME"] = str(planned_manager_data_dir(data_root) / "db.sqlite3")
    return entry


def stored(parser, section: str, key: str, default: str = "") -> str:
    return parser.get(section, key, fallback=default).strip()


def _set_all(parser, section: str, values: Dict[str, object]) -> None:
    if not parser.has_section(section):
        parser.add_section(section)
    for key, value in values.items():
        parser.set(section, key, check_value(key, value))


def _replace_database(parser, database: Dict[str, str]) -> None:
    kept = {key: parser.get("database", key) for key in KEPT_DATABASE_KEYS if parser.has_option("database", key)}
    parser.remove_section("database")
    _set_all(parser, "database", {**database, **kept})


def completed_at_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


@dataclass
class WriteResult:
    """``replaced``: the file now holds the plan, even if a later step (the
    folder ``fsync``) failed; ``error``: what failed, if anything."""

    path: Path
    replaced: bool
    error: Optional[str] = None


def _holds(path: Path, expected: Optional[bytes]) -> bool:
    try:
        return expected is not None and path.read_bytes() == expected
    except OSError:
        return False


def write_settings(plan: Plan, path=None) -> WriteResult:
    """Write ``plan`` into ``sethlans.ini`` (module doc). Never raises.

    After a failure the file is read back: when it already holds exactly
    the new content (the replace went through and a step after it
    failed), the result says ``replaced`` with the error beside it.
    """
    path = settings_file.settings_path() if path is None else Path(path)
    written = {}

    def mutate(parser) -> bool:
        _replace_database(parser, plan.database)
        _set_all(parser, settings_file.SECTION_SERVER, {
            settings_file.KEY_SERVER_HOST: plan.server_host,
            settings_file.KEY_SERVER_PORT: plan.server_port,
        })
        _set_all(parser, settings_file.SECTION_PATHS, {settings_file.KEY_DATA_DIR: str(plan.data_root)})
        parser.remove_section(settings_file.SECTION_FIRST_ADMIN)
        if plan.first_admin is not None:
            _set_all(parser, settings_file.SECTION_FIRST_ADMIN, plan.first_admin)
        if parser.has_section(settings_file.SECTION_SETUP):
            for key in settings_file.SETUP_WIZARD_KEYS:
                parser.remove_option(settings_file.SECTION_SETUP, key)
        _set_all(parser, settings_file.SECTION_SETUP, {
            settings_file.KEY_SETUP_VERSION: settings_file.SETUP_FORMAT_VERSION,
            settings_file.KEY_SETUP_MODE: plan.mode,
            settings_file.KEY_SETUP_COMPLETED_AT: completed_at_now(),
        })
        written["content"] = serialise(parser)  # what update_settings writes
        return True

    try:
        settings_file.update_settings(mutate, path)
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        return WriteResult(path, _holds(path, written.get("content")), f"{type(exc).__name__}: {exc}")
    return WriteResult(path, True)
