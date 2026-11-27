# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Synchronous WSGI application for the worker web UI.

Routes:
  GET  /               -- serve static HTML dashboard
  GET  /index.html     -- serve static HTML dashboard
  GET  /api/health/    -- anonymous health probe (boot_id, worker_id, version)
  GET  /api/status     -- legacy dashboard snapshot (DEPRECATED -- new
                         probe consumers MUST use /api/health/; full
                         removal deferred to Spec 4 of the wizard work)
  GET  /api/cards/     -- anonymous graphics card list (runtime/handlers_cards)
  POST /api/control/*  -- authenticated control endpoints (cards/* in
                         runtime/handlers_cards)

The worker has no setup routes (setup-rebuild FR-WK-7): setup runs through
the worker program's one-shot commands (``setup_cli``). Control actions need
the worker page password (``web_ui.auth``, FR-SEC-8); with none set they
answer 403 (FR-WK-4).
"""

import json
import logging
import os
import socket
import threading
from typing import Callable, Iterable

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.web_ui import auth, password_rules
from sethlans_worker_agent.web_ui.config_mutations import apply_config_change
from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    MAX_REQUEST_BODY,
    send_json_wsgi, send_html_file_wsgi, read_body_wsgi,
)
from sethlans_worker_agent.web_ui.status import get_status_snapshot

logger = logging.getLogger(__name__)

# Shutdown event reference -- bound lazily on first control request
# to avoid triggering agent.py module-level argparse at import time.
_shutdown_event_ref = None
_shutdown_ref_lock = threading.Lock()

# Explicit allowlist of config keys that can be modified via control
# endpoints.
_MUTABLE_CONFIG_KEYS = frozenset({
    'POLLING_INTERVAL',
    'HEARTBEAT_INTERVAL',
})

# Path to the static HTML dashboard
_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_INDEX_PATH = os.path.join(_STATIC_DIR, 'index.html')


def _get_shutdown_event():
    """Lazily resolve the agent's ``_shutdown_event``.

    The double-checked lock ensures the first resolved reference
    wins; subsequent calls skip the lock. During tests ``agent.py``
    may fail to import because of its module-level argparse; fall
    back to a never-set event so gate checks remain deterministic.
    """
    global _shutdown_event_ref
    if _shutdown_event_ref is not None:
        return _shutdown_event_ref
    with _shutdown_ref_lock:
        if _shutdown_event_ref is not None:
            return _shutdown_event_ref
        try:
            from sethlans_worker_agent.agent import _shutdown_event
            _shutdown_event_ref = _shutdown_event
        except (ImportError, SystemExit):
            _shutdown_event_ref = threading.Event()
        return _shutdown_event_ref


# The answer for each refused outcome of ``auth.check_bearer``.
_AUTH_REFUSALS = {
    auth.BAD: (401, 'Unauthorized'),
    auth.NO_PASSWORD: (403, auth.NO_PASSWORD_TEXT),
    auth.LIMITED: (429, 'Too many wrong passwords from this address; try again in a minute.'),
    auth.BUSY: (429, 'The worker is busy checking other passwords; try again in a moment.'),
    auth.UNREADABLE: (503, "The worker's settings file could not be read just now; try again."),
}


# ---- GET handlers ------------------------------------------------

def _handle_get(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch GET requests."""
    path = environ.get('PATH_INFO', '') or ''
    if path in ('/', '/index.html'):
        return send_html_file_wsgi(start_response, _INDEX_PATH)
    if path == '/api/health/':
        # Lazy import mirrors the existing _get_shutdown_event pattern
        # and keeps wsgi_app.py import-time-cheap.
        from sethlans_worker_agent.web_ui.runtime.handlers_health import (
            handle_health_wsgi,
        )
        return handle_health_wsgi(environ, start_response)
    if path == '/api/status':
        snapshot = get_status_snapshot()
        return send_json_wsgi(start_response, snapshot)
    if path in ('/api/cards/', '/api/cards'):
        from sethlans_worker_agent.web_ui.runtime.handlers_cards import handle_cards_get
        return handle_cards_get(environ, start_response)
    return send_json_wsgi(start_response, {'error': 'Not Found'}, 404)


# ---- POST control handlers --------------------------------------

def _handle_control(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch POST ``/api/control/{action}`` with auth + shutdown guard."""
    shutdown_event = _get_shutdown_event()

    if shutdown_event.is_set():
        return send_json_wsgi(
            start_response, {'error': 'Server is shutting down'}, 503,
        )

    outcome = auth.check_bearer(environ)
    if outcome != auth.OK:
        status, text = _AUTH_REFUSALS[outcome]
        return send_json_wsgi(start_response, {'error': text}, status)

    body_bytes = read_body_wsgi(environ)
    if len(body_bytes) > MAX_REQUEST_BODY:
        return send_json_wsgi(
            start_response, {'error': 'Request body too large'}, 413,
        )

    path = environ.get('PATH_INFO', '') or ''
    action = path[len('/api/control/'):]

    if action == 'pause':
        job_processor.pause()
        return send_json_wsgi(start_response, {'status': 'paused'})
    if action == 'resume':
        job_processor.resume()
        return send_json_wsgi(start_response, {'status': 'resumed'})
    if action == 'shutdown':
        return _handle_shutdown(start_response, shutdown_event)
    if action == 'set_password':
        return _handle_set_password(start_response, body_bytes)
    if action == 'update':
        return _handle_config_update(start_response, body_bytes)
    if action == 'cards' or action.startswith('cards/'):
        from sethlans_worker_agent.web_ui.runtime.handlers_cards import handle_cards_control
        return handle_cards_control(action, start_response, body_bytes)
    return send_json_wsgi(start_response, {'error': 'Not Found'}, 404)


def _handle_shutdown(
    start_response: Callable, shutdown_event: threading.Event,
) -> Iterable[bytes]:
    """Trigger a graceful shutdown of the worker agent."""
    logger.info("Shutdown requested via web UI.")
    response = send_json_wsgi(start_response, {'status': 'shutting_down'})
    shutdown_event.set()
    return response


def _handle_set_password(
    start_response: Callable, body_bytes: bytes,
) -> Iterable[bytes]:
    """Set a new password for the worker UI."""
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return send_json_wsgi(
            start_response, {'error': 'Invalid JSON'}, 400,
        )
    new_password = data.get('password') if isinstance(data, dict) else None
    problems = password_rules.problems(new_password, socket.gethostname())
    if problems:
        return send_json_wsgi(start_response, {'error': ' '.join(problems)}, 400)
    auth.set_password(new_password)
    return send_json_wsgi(start_response, {'status': 'password_set'})


def _handle_config_update(
    start_response: Callable, body_bytes: bytes,
) -> Iterable[bytes]:
    """Process a config update request."""
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return send_json_wsgi(
            start_response, {'error': 'Invalid JSON'}, 400,
        )

    if not isinstance(data, dict):
        return send_json_wsgi(
            start_response, {'error': 'Invalid JSON'}, 400,
        )
    key = data.get('key', '')
    value = data.get('value')

    if key not in _MUTABLE_CONFIG_KEYS:
        return send_json_wsgi(
            start_response,
            {'error': f'Config key {key!r} is not modifiable'},
            403,
        )

    try:
        apply_config_change(key, value)
    except ValueError as e:
        return send_json_wsgi(start_response, {'error': str(e)}, 400)

    return send_json_wsgi(
        start_response,
        {'status': 'updated', 'key': key, 'value': value},
    )


# ---- POST dispatch -----------------------------------------------

def _handle_post(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch POST requests."""
    path = environ.get('PATH_INFO', '') or ''
    if path.startswith('/api/control/'):
        return _handle_control(environ, start_response)
    return send_json_wsgi(start_response, {'error': 'Not Found'}, 404)


# ---- Top-level WSGI app -----------------------------------------

def app(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Root WSGI application."""
    method = environ.get('REQUEST_METHOD', 'GET')
    if method == 'GET':
        return _handle_get(environ, start_response)
    if method == 'POST':
        return _handle_post(environ, start_response)
    return send_json_wsgi(
        start_response, {'error': 'Method Not Allowed'}, 405,
    )


__all__ = ['app']
