# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard graphics card step (FR-CW-2).

GET  /api/setup/worker/cards/ -- the fresh card list and the saved choice
POST /api/setup/worker/cards/ -- save the choice (FR-CW-5 validation)

The GET is served from the probe cache the settings page uses
(``web_ui.cards_cache``: at most 5 s old, one probe at a time), since the
page polls while Blender's device check runs. The probe joins the OS card
list with the cached Blender device list; it never starts Blender, so until
the Blender step's device check has filled the cache each card's note is
"Blender not checked yet" (or "Blender could not list its devices; see the
log" when that check failed). The GET does not take the setup mutation
lock. The POST takes it (409 while another wizard mutation runs), runs a
fresh probe and saves through ``cards.choice.save_choice``; on success it appends the
``cards_chosen`` checkpoint that ``GET /api/setup/status/`` reports, then saves the
optional ``allow_manager_changes`` switch (ignored while its environment variable
sets it; 503 with the revision when only the switch could not be written). Both
answer 404 once setup is complete.
"""
import logging
from typing import Callable, Iterable

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import store, store_requests
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from sethlans_worker_agent.web_ui import cards_cache, cards_view
from sethlans_worker_agent.web_ui.http_helpers_wsgi import MAX_REQUEST_BODY, read_body_wsgi, send_json_wsgi
from sethlans_worker_agent.web_ui.setup import blender_devices
from sethlans_worker_agent.web_ui.setup.gate import is_in_setup_mode
from sethlans_worker_agent.web_ui.setup.handlers_status import append_wizard_checkpoint, get_current_checkpoints
from sethlans_worker_agent.web_ui.setup.lock import setup_mutation_lock

logger = logging.getLogger(__name__)

CARDS_CHECKPOINT = 'cards_chosen'
SWITCH_NOT_SAVED_TEXT = ("The graphics card choice was saved, but the manager-changes switch could not be saved; "
                         "set it on the settings page.")

_NOT_FOUND = {"error": "Not Found"}
_MUTATION_IN_PROGRESS = {
    "error": "Setup mutation in progress; retry after current operation completes.",
}


def handle_get_cards(environ: dict, start_response: Callable) -> Iterable[bytes]:
    """GET /api/setup/worker/cards/ -- cards, saved choice, revision, warnings."""
    if not is_in_setup_mode():
        return send_json_wsgi(start_response, _NOT_FOUND, 404)
    check = blender_devices.check_state()
    answer = cards_view.view(cards_cache.cached_probe(), store.snapshot(),
                             checking=check == 'running', check_failed=check == 'failed')
    return send_json_wsgi(start_response, answer)


def _save(environ: dict) -> tuple:
    """Validate, probe and save; ``(status, body)``. Caller holds the setup mutation lock."""
    body = read_body_wsgi(environ)
    if len(body) > MAX_REQUEST_BODY:
        return 413, {"error": "Request body too large"}
    try:
        fields = cards_view.parse_body(body, with_switch=True)
    except ValueError as invalid:
        return 400, {"error": str(invalid)}
    status, answer = cards_view.save(fields, cards_cache.fresh_probe(), 'setup wizard')
    if status == 200:
        append_wizard_checkpoint(CARDS_CHECKPOINT)
        answer["checkpoints"] = get_current_checkpoints()
        if cards_view.SWITCH_FIELD in fields:
            status, answer = _save_switch(fields[cards_view.SWITCH_FIELD], answer)
    return status, answer


def _save_switch(allow: bool, answer: dict) -> tuple:
    """Save the manager-changes switch after the choice (FR-CW-2); its environment variable wins silently."""
    try:
        store_requests.set_allow_manager_changes(allow, 'setup wizard')
    except store_requests.SwitchEnvOverride:
        logger.info("The manager-changes switch from the setup wizard is not saved: %s sets it.",
                    config_compute.ENV_ALLOW)
    except (ConfigLockTimeoutError, OSError):
        logger.error("The manager-changes switch from the setup wizard could not be saved.", exc_info=True)
        return 503, {"error": SWITCH_NOT_SAVED_TEXT, "revision": answer["revision"],
                     "checkpoints": answer["checkpoints"]}
    return 200, answer


def handle_post_cards(environ: dict, start_response: Callable) -> Iterable[bytes]:
    """POST /api/setup/worker/cards/ -- save the card choice."""
    if not is_in_setup_mode():
        return send_json_wsgi(start_response, _NOT_FOUND, 404)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(start_response, _MUTATION_IN_PROGRESS, 409)
        if not is_in_setup_mode():  # verification finished between the first check and the lock
            return send_json_wsgi(start_response, _NOT_FOUND, 404)
        status, answer = _save(environ)
    return send_json_wsgi(start_response, answer, status)
