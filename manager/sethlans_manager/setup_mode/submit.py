# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/setup/submit/`` after the session check (FR-WZ-2, D9).

Order: validate everything (no writes; the Worker section runs
``--setup-cards``) -> create or adopt the data folder
(``prepare_data_root``, which checks it again) -> for ``manager_worker``
and ``worker``, ``--setup-apply`` (it enrolls first in the ``worker`` mode
unless the enrollment is kept, then saves the worker's settings) -> build
the 200 body -> write ``sethlans.ini``, last. A failed worker command
writes nothing to ``sethlans.ini``, so nothing is marked finished (OQ-7).
Once the file holds the new settings the submit counts as saved, whatever
fails after that (it is logged), so nothing after the replace can send
the state back to ``IDLE``.

Outcomes (every error body has an ``error`` key):

* 200 saved: the finish body, already JSON-encoded;
* 400 ``{"error": "invalid", "message": "Some answers need fixing.",
  "fields": {<dotted field>: [messages]}, "codes": {<dotted field>:
  [machine codes]}}`` for anything the person can fix (``codes`` holds
  only the fields with a machine code, e.g. ``"admin.username":
  ["account_exists"]``, and may be empty), and 400 ``{"error": <worker code>,
  "message", "fields"}`` when the worker program refused (``worker_outcome``);
* 500 ``{"error": "settings_unwritable", "message": ...}`` when the
  settings file cannot be written, and 500 ``{"error": "worker_failed" |
  "worker_timeout" | "worker_unavailable" | "worker_bad_answer", "message"}``
  when a worker command broke.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional, Union

from django.views.decorators.debug import sensitive_variables

from shared import data_folder

from sethlans_manager.setup_mode import leftovers, worker_cli, worker_outcome
from sethlans_manager.setup_mode.plan import Plan, write_settings
from sethlans_manager.setup_mode.validate import DATA_FOLDER_FIELD, Context, validate
from sethlans_manager.setup_mode.validate_worker import WorkerCliFailure

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    status: int
    body: Union[dict, bytes]   # bytes: the encoded 200 body
    saved: bool = False


INVALID_MESSAGE = "Some answers need fixing."


def invalid(fields: dict, codes: Optional[dict] = None) -> Outcome:
    return Outcome(400, {"error": "invalid", "message": INVALID_MESSAGE, "fields": fields, "codes": codes or {}})


def settings_unwritable(message: str) -> Outcome:
    return Outcome(500, {"error": "settings_unwritable", "message": message})


def worker_failure(result: worker_cli.CliResult) -> Outcome:
    status, body = worker_outcome.failure(result)
    return Outcome(status, body)


def _manager_url(plan: Plan, request_host: str) -> str:
    host = request_host if plan.server_host == "0.0.0.0" else plan.server_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{plan.server_port}/"


def finish_info(plan: Plan, request_host: str, setup_port: int, manager_url=None) -> dict:
    """The 200 body: where the manager will answer (``url`` is ``None`` in the
    ``worker`` mode, which starts no manager here), and what to tell the person."""
    runs_manager = plan.runs_manager
    return {
        "ok": True,
        "mode": plan.mode,
        "restarting": runs_manager,
        "url": _manager_url(plan, request_host) if runs_manager else None,
        "port_changed": runs_manager and plan.server_port != setup_port,
        "first_admin": plan.first_admin["username"] if plan.first_admin else None,
        "worker": worker_outcome.finish(plan.worker_result, manager_url, plan.enrollment),
        "warnings": plan.warnings,
        "leftovers": leftovers.leftover_paths(),
    }


@sensitive_variables("payload")
def _apply_worker(plan: Plan) -> Optional[Outcome]:
    """Run ``--setup-apply``; an error outcome, or ``None`` once the worker saved."""
    payload, plan.worker_apply = plan.worker_apply, None
    result = worker_cli.apply(payload, plan.data_root)
    if not result.ok:
        logger.warning("Setup not saved: the worker program answered %s", result.error)
        return worker_failure(result)
    plan.worker_result = result.answer
    return None


@sensitive_variables("answers")
def apply_answers(answers: object, ctx: Context, request_host: str) -> Outcome:
    """Validate and save. Raises only before ``sethlans.ini`` is replaced (the
    worker's own settings may already be saved by ``--setup-apply``)."""
    try:
        errors, plan = validate(answers, ctx)
    except WorkerCliFailure as failure:
        logger.warning("Setup not saved: the worker program answered %s", failure.result.error)
        return worker_failure(failure.result)
    if errors:
        return invalid(errors.by_field, errors.codes)
    try:
        plan.data_root = data_folder.prepare_data_root(plan.data_root)
    except data_folder.DataFolderError as exc:
        return invalid({DATA_FOLDER_FIELD: [str(exc)]})
    manager_url = (plan.worker_apply or {}).get("manager_url")
    if plan.worker_apply is not None:
        failed = _apply_worker(plan)
        if failed is not None:
            return failed
    body = json.dumps(finish_info(plan, request_host, ctx.setup_port, manager_url)).encode("utf-8")
    result = write_settings(plan)
    if not result.replaced:
        logger.error("Could not write the settings file %s: %s", result.path, result.error)
        return settings_unwritable(f"Could not write the settings file {result.path}: {result.error}")
    if result.error:
        logger.warning("The settings file %s was replaced, but a step after it failed: %s",
                       result.path, result.error)
    logger.info("Setup saved to %s (mode %s, admin %s)", result.path, plan.mode,
                plan.first_admin["username"] if plan.first_admin else "kept" if plan.runs_manager else "none")
    return Outcome(200, body, saved=True)
