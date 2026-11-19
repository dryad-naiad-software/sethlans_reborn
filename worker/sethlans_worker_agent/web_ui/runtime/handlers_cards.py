# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Settings page graphics card endpoints (FR-CW-4).

GET  /api/cards/                -- anonymous, like ``/api/status``; display data only
POST /api/control/cards         -- save the card choice
POST /api/control/cards/dismiss -- dismiss one open alert: ``{"alert_id": "<32 hex>"}``
POST /api/control/cards/unreal  -- the Unreal switch (O14): ``{"enabled": true | false}``; saved for
                                   the next start; 409 when ``SETHLANS_UNREAL_ENABLED`` sets it

The POSTs get the Bearer password check, the shutdown guard and the body
cap from ``wsgi_app``, which hands every ``cards`` action to
``handle_cards_control``.

**The GET is read-only (S1):** it never raises an alert, stops a job or
requests a slot rebuild. Its card list comes from the shared probe cache
(``web_ui.cards_cache``: at most 5 s old, one probe at a time). The save
validates the body (FR-CW-5), runs a fresh probe through the same cache
(which refreshes it) and saves through ``cards.choice.save_choice`` after the
probe lock is released; the dismissal goes through ``choice.dismiss_alerts``.
Both request a live slot rebuild (FR-CL-4).
"""
import json
import logging
import re
from typing import Callable, Iterable

from sethlans_worker_agent import unreal_switch
from sethlans_worker_agent.cards import choice, store
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from sethlans_worker_agent.web_ui import cards_cache, cards_view
from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi

logger = logging.getLogger(__name__)

SAVED_TEXT = "Saved. The new graphics card choice is in use once running GPU jobs on removed cards finish."
DISMISSED_TEXT = "Dismissed. A card that is still missing or changed is alerted again at the next check."
UNKNOWN_ALERT_TEXT = "No open alert has this id. Reload the page."
NOT_DISMISSED_TEXT = "The alert could not be dismissed; see the worker log."
UNREAL_SAVED_TEXT = "Saved. The Unreal switch takes effect at the next worker start."
UNREAL_ENV_TEXT = "This switch is set by an environment variable on this worker."
UNREAL_NOT_SAVED_TEXT = "The Unreal switch could not be saved; see the worker log."
_ALERT_ID = re.compile(r'[0-9a-f]{32}')


def handle_cards_get(environ: dict, start_response: Callable) -> Iterable[bytes]:
    """GET /api/cards/ -- cards, saved choice, revision, warnings (no secrets)."""
    return send_json_wsgi(start_response, cards_view.view(cards_cache.cached_probe(), store.snapshot()))


def handle_cards_control(action: str, start_response: Callable, body: bytes) -> Iterable[bytes]:
    """Dispatch ``POST /api/control/cards[/...]``; ``body`` is already within the size cap."""
    if action == 'cards':
        return _save(start_response, body)
    if action == 'cards/dismiss':
        return _dismiss(start_response, body)
    if action == 'cards/unreal':
        return _unreal_switch(start_response, body)
    return send_json_wsgi(start_response, {'error': 'Not Found'}, 404)


def parse_unreal_body(body: bytes) -> bool:
    """``{"enabled": true | false}`` and nothing else; ``ValueError`` with one plain sentence."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        data = None
    if not isinstance(data, dict) or set(data) != {'enabled'} or not isinstance(data['enabled'], bool):
        raise ValueError("The request must be a JSON object with enabled set to true or false.")
    return data['enabled']


def _unreal_switch(start_response: Callable, body: bytes) -> Iterable[bytes]:
    """O14: save the Unreal switch for the next start; 409 under its environment variable."""
    try:
        enabled = parse_unreal_body(body)
    except ValueError as invalid:
        return send_json_wsgi(start_response, {'error': str(invalid)}, 400)
    try:
        unreal_switch.save(enabled, 'settings page')
    except unreal_switch.EnvOverride:
        return send_json_wsgi(start_response, {'error': UNREAL_ENV_TEXT}, 409)
    except (ConfigLockTimeoutError, OSError):
        logger.error("The Unreal switch could not be saved.", exc_info=True)
        return send_json_wsgi(start_response, {'error': UNREAL_NOT_SAVED_TEXT}, 503)
    answer = {'status': 'ok', 'message': UNREAL_SAVED_TEXT}
    answer.update(unreal_switch.state())
    return send_json_wsgi(start_response, answer)


def _save(start_response: Callable, body: bytes) -> Iterable[bytes]:
    try:
        fields = cards_view.parse_body(body)
    except ValueError as invalid:
        return send_json_wsgi(start_response, {'error': str(invalid)}, 400)
    status, answer = cards_view.save(fields, cards_cache.fresh_probe(), 'settings page')
    if status == 200:
        answer['message'] = SAVED_TEXT
    return send_json_wsgi(start_response, answer, status)


def parse_dismiss_body(body: bytes) -> str:
    """The alert id of a dismiss request (FR-CW-5); ``ValueError`` with one plain sentence."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise ValueError("The request body must be a JSON object.")
    if set(data) != {'alert_id'}:
        raise ValueError("The request must give alert_id and nothing else.")
    alert_id = data['alert_id']
    if not isinstance(alert_id, str) or _ALERT_ID.fullmatch(alert_id) is None:
        raise ValueError("The alert id must be 32 lower-case hexadecimal characters.")
    return alert_id


def _dismiss(start_response: Callable, body: bytes) -> Iterable[bytes]:
    """400 for an invalid body, 404 for an id that names no open alert, 503 when the file cannot be written."""
    try:
        alert_id = parse_dismiss_body(body)
    except ValueError as invalid:
        return send_json_wsgi(start_response, {'error': str(invalid)}, 400)
    try:
        removed = choice.dismiss_alerts([alert_id], 'settings page')
    except (ConfigLockTimeoutError, OSError):
        logger.error("The graphics card alert %s could not be dismissed.", alert_id, exc_info=True)
        return send_json_wsgi(start_response, {'error': NOT_DISMISSED_TEXT}, 503)
    if not removed:
        return send_json_wsgi(start_response, {'error': UNKNOWN_ALERT_TEXT}, 404)
    return send_json_wsgi(start_response, {'status': 'ok', 'message': DISMISSED_TEXT})
