# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Settings page graphics card endpoints (FR-CW-4).

GET  /api/cards/          -- anonymous, like ``/api/status``; display data only
POST /api/control/cards   -- save the card choice (Bearer password, shutdown
                             guard and body cap are applied by ``wsgi_app``)

**The GET is read-only (S1):** it never raises an alert, stops a job or
requests a slot rebuild. Its card list comes from the shared probe cache
(``web_ui.cards_cache``: at most 5 s old, one probe at a time). The POST
validates the body (FR-CW-5), runs a fresh probe through the same cache
(which refreshes it) and saves through ``cards.choice.save_choice`` after the
probe lock is released.
"""
import logging
from typing import Callable, Iterable

from sethlans_worker_agent.cards import store
from sethlans_worker_agent.web_ui import cards_cache, cards_view
from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi

logger = logging.getLogger(__name__)

SAVED_TEXT = "Saved. The new graphics card choice takes effect when the worker restarts."


def handle_cards_get(environ: dict, start_response: Callable) -> Iterable[bytes]:
    """GET /api/cards/ -- cards, saved choice, revision, warnings (no secrets)."""
    return send_json_wsgi(start_response, cards_view.view(cards_cache.cached_probe(), store.snapshot()))


def handle_cards_control(action: str, start_response: Callable, body: bytes) -> Iterable[bytes]:
    """Dispatch ``POST /api/control/cards[/...]``; ``body`` is already within the size cap."""
    if action != 'cards':
        return send_json_wsgi(start_response, {'error': 'Not Found'}, 404)
    try:
        fields = cards_view.parse_body(body)
    except ValueError as invalid:
        return send_json_wsgi(start_response, {'error': str(invalid)}, 400)
    status, answer = cards_view.save(fields, cards_cache.fresh_probe(), 'settings page')
    if status == 200:
        answer['message'] = SAVED_TEXT
    return send_json_wsgi(start_response, answer, status)
