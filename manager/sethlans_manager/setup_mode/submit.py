# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/setup/submit/`` after the session check (FR-WZ-2, D9).

Order: validate everything (no writes) -> create or adopt the data folder
(``prepare_data_root``, which checks it again) -> write ``sethlans.ini``,
last. Status: 200 saved; 400 for anything the person can fix (dotted
field keys plus ``non_field_errors``); 500 when the settings file cannot
be written. The caller owns the state machine.
"""
import logging
from typing import Tuple

from django.views.decorators.debug import sensitive_variables

from shared import data_folder

from sethlans_manager.setup_mode import leftovers
from sethlans_manager.setup_mode.fields import NON_FIELD
from sethlans_manager.setup_mode.plan import Plan, write_settings
from sethlans_manager.setup_mode.validate import DATA_FOLDER_FIELD, Context, validate

logger = logging.getLogger(__name__)


def finish_info(plan: Plan, request_host: str, setup_port: int) -> dict:
    """The 200 body: where the manager will answer, and what to tell the person."""
    host = request_host if plan.server_host == "0.0.0.0" else plan.server_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return {
        "ok": True,
        "mode": plan.mode,
        "restarting": True,
        "url": f"https://{host}:{plan.server_port}/",
        "port_changed": plan.server_port != setup_port,
        "first_admin": plan.first_admin["username"] if plan.first_admin else None,
        "warnings": plan.warnings,
        "leftovers": leftovers.leftover_paths(),
    }


@sensitive_variables("answers")
def apply_answers(answers: object, ctx: Context, request_host: str) -> Tuple[int, dict]:
    """Validate and save; return ``(status, body)``."""
    errors, plan = validate(answers, ctx)
    if errors:
        return 400, errors.by_field
    try:
        plan.data_root = data_folder.prepare_data_root(plan.data_root)
    except data_folder.DataFolderError as exc:
        return 400, {DATA_FOLDER_FIELD: [str(exc)]}
    try:
        path = write_settings(plan)
    except (OSError, ValueError) as exc:
        logger.error("Could not write the settings file: %s", exc)
        return 500, {NON_FIELD: [f"Could not write the settings file: {exc}"]}
    logger.info("Setup saved to %s (mode %s, admin %s)", path, plan.mode,
                plan.first_admin["username"] if plan.first_admin else "kept")
    return 200, finish_info(plan, request_host, ctx.setup_port)
