# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one validation of the setup answers (D10, FR-WZ-5).

:func:`validate` checks every section, tests the database (FR-WZ-7),
reads its admin accounts read-only (FR-WZ-11) and hashes the admin
password (D6). It writes nothing; the data folder is only checked here
and is created by ``shared.data_folder.prepare_data_root`` at apply time,
which runs the same checks again.

The answers have the shape ``GET /api/setup/form/`` returns, so the form
can be sent back as received after editing. Keys the form only reports
(``hostname``, ``addresses``, ``bind_addresses``, ``modes``, ``cards``,
``managers``, ``leftovers``, ``last_failure``, ``errors``,
``data_folder.free_bytes`` and ``low_space``, ``database.password_set``,
``admin.accounts_exist``) are never read, so they are ignored.

This build sets up the ``manager`` mode only; ``manager_worker`` and
``worker`` need the worker program's setup commands.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set, Tuple

from django.views.decorators.debug import sensitive_variables

from shared import data_folder
from shared.settings_file import SETUP_MODES

from sethlans_manager.admin_fields import clean_username
from sethlans_manager.db_tls import DatabaseTLSError
from sethlans_manager.setup_mode import db_probe, fields, net
from sethlans_manager.setup_mode.plan import Plan, database_entry
from sethlans_manager.setup_mode.validate_admin import first_admin
from sethlans_manager.setup_mode.validate_db import database_values

SUPPORTED_MODES = ("manager",)
DATA_FOLDER_FIELD = "data_folder.path"
DATA_FOLDER_SHAPE = 'The data folder must be sent as {"path": "<absolute folder>"}, as the form gives it.'
LOW_SPACE_WARNING = "Only {free} GB are free on the drive of {path}; renders and tools need more (20 GB or more)."


@dataclass
class Context:
    """What validation needs besides the answers."""

    stored: object                    # the current sethlans.ini (RawConfigParser)
    setup_port: int                   # the port setup itself is reached on (exempt from "in use")
    reserved_ports: Set[int] = field(default_factory=set)  # the manager's loopback listeners


def _mode(answers: dict, errors: fields.Errors) -> Optional[str]:
    mode = fields.text(answers, "mode", "mode", errors, required=True, label="The mode")
    if mode is None:
        return None
    if mode not in SETUP_MODES:
        errors.add("mode", f"Choose one of {', '.join(sorted(SETUP_MODES))}.")
        return None
    if mode not in SUPPORTED_MODES:
        errors.add("mode", "This build can set up the manager mode only.")
        return None
    return mode


def _server(answers: dict, ctx: Context, errors: fields.Errors) -> Tuple[Optional[str], Optional[int]]:
    server = fields.section(answers, "server", errors)
    host = fields.text(server, "host", "server.host", errors, required=True, label="The bind address")
    if host is not None and host not in net.bind_addresses():
        errors.add("server.host", "Choose 0.0.0.0, 127.0.0.1 or an address of this machine.")
        host = None
    port = fields.integer(server, "port", "server.port", errors, low=1024, high=65535, label="The port")
    if port is None:
        return host, None
    if port in ctx.reserved_ports:
        errors.add("server.port", f"Port {port} is used by the manager's own loopback listeners.")
        return host, None
    if host is not None and port != ctx.setup_port and not net.port_is_free(host, port):
        errors.add("server.port", f"Port {port} is in use on {host}.")
        return host, None
    return host, port


def _data_root(answers: dict, errors: fields.Errors, warnings) -> Optional[Path]:
    """``data_folder`` is the form's object; only ``path`` is read (the rest is read-only)."""
    section = answers.get("data_folder")
    if not isinstance(section, dict):
        errors.add("data_folder", DATA_FOLDER_SHAPE)
        return None
    raw = fields.text(section, "path", DATA_FOLDER_FIELD, errors, required=True, max_length=4096,
                      label="The data folder")
    if raw is None:
        return None
    problems = data_folder.check_data_root(raw)
    for problem in problems:
        errors.add(DATA_FOLDER_FIELD, problem)
    if problems:
        return None
    root = Path(raw)
    free = data_folder.free_bytes(root)
    if free is not None and free < data_folder.LOW_SPACE_BYTES:
        warnings.append(LOW_SPACE_WARNING.format(free=free // 1000 ** 3, path=root))
    return root


def _check_database(database, root: Path, username: str, errors: fields.Errors):
    """Build the run-time ``DATABASES`` entry, test it, read its admins."""
    try:
        entry = database_entry(database, root)
    except DatabaseTLSError as exc:
        errors.add("database.tls", str(exc))
        return None
    try:
        return db_probe.check_database(entry, username)
    except db_probe.ProbeError as exc:
        errors.add("database", exc.message)
        return None


@sensitive_variables("answers")
def validate(answers: object, ctx: Context) -> Tuple[fields.Errors, Optional[Plan]]:
    """Check everything; return the errors and, when there are none, the plan."""
    errors = fields.Errors()
    if not isinstance(answers, dict):
        errors.add(fields.NON_FIELD, "The form must be a JSON object.")
        return errors, None
    warnings = []
    mode = _mode(answers, errors)
    host, port = _server(answers, ctx, errors)
    root = _data_root(answers, errors, warnings)
    database = database_values(answers, ctx.stored, errors, warnings)
    accounts = None
    if database is not None and root is not None:
        admin = answers.get("admin") if isinstance(answers.get("admin"), dict) else {}
        username = admin.get("username") if isinstance(admin.get("username"), str) else ""
        # The probe looks up the name the first start will use: normalized the same way.
        accounts = _check_database(database, root, clean_username(username)[0], errors)
    # Checked even when the database failed, so every error shows at once;
    # without ``accounts`` the account checks are skipped (an error is
    # already recorded), and the password is hashed only when all is well.
    admin_values = first_admin(answers, accounts, errors)
    if accounts is None and not errors:  # pragma: no cover - every path to None records an error
        errors.add(fields.NON_FIELD, "The database could not be checked.")
    if errors:
        return errors, None
    return errors, Plan(mode, root, host, port, database, admin_values, warnings)
