# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/setup/submit/`` after the session check (FR-WZ-2, D9).

Order: validate everything (no writes) -> create or adopt the data folder
(``prepare_data_root``, which checks it again) -> build the 200 body ->
write ``sethlans.ini``, last. Once the file holds the new settings the
submit counts as saved, whatever fails after that (it is logged), so
nothing after the replace can send the state back to ``IDLE``.

Outcomes (every error body has an ``error`` key):

* 200 saved: the finish body, already JSON-encoded;
* 400 ``{"error": "invalid", "fields": {<dotted field>: [messages]}}``
  for anything the person can fix;
* 500 ``{"error": "settings_unwritable", "message": ...}`` when the
  settings file cannot be written.
"""
import json
import logging
from dataclasses import dataclass
from typing import Union

from django.views.decorators.debug import sensitive_variables

from shared import data_folder

from sethlans_manager.setup_mode import leftovers
from sethlans_manager.setup_mode.plan import Plan, write_settings
from sethlans_manager.setup_mode.validate import DATA_FOLDER_FIELD, Context, validate

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    status: int
    body: Union[dict, bytes]   # bytes: the encoded 200 body
    saved: bool = False


def invalid(fields: dict) -> Outcome:
    return Outcome(400, {"error": "invalid", "fields": fields})


def settings_unwritable(message: str) -> Outcome:
    return Outcome(500, {"error": "settings_unwritable", "message": message})


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
def apply_answers(answers: object, ctx: Context, request_host: str) -> Outcome:
    """Validate and save. Raises only before anything is written."""
    errors, plan = validate(answers, ctx)
    if errors:
        return invalid(errors.by_field)
    try:
        plan.data_root = data_folder.prepare_data_root(plan.data_root)
    except data_folder.DataFolderError as exc:
        return invalid({DATA_FOLDER_FIELD: [str(exc)]})
    body = json.dumps(finish_info(plan, request_host, ctx.setup_port)).encode("utf-8")
    result = write_settings(plan)
    if not result.replaced:
        logger.error("Could not write the settings file %s: %s", result.path, result.error)
        return settings_unwritable(f"Could not write the settings file {result.path}: {result.error}")
    if result.error:
        logger.warning("The settings file %s was replaced, but a step after it failed: %s",
                       result.path, result.error)
    logger.info("Setup saved to %s (mode %s, admin %s)", result.path, plan.mode,
                plan.first_admin["username"] if plan.first_admin else "kept")
    return Outcome(200, body, saved=True)
