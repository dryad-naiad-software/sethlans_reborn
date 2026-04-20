# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Synchronous WSGI application for the worker web UI.

Phase 4b of the Waitress migration: this module was historically an
async ASGI app driven directly by uvicorn. It is now a sync WSGI
application. During Phase 4 (4b-4g), uvicorn continues to drive the
stack via the ``asgiref.wsgi.WsgiToAsgi`` bridge installed at
``server.py``; Phase 5 swaps the bootstrap to Waitress and removes
the bridge.

Routes:
  GET  /               -- serve static HTML dashboard
  GET  /index.html     -- serve static HTML dashboard
  GET  /api/status     -- JSON status snapshot
  POST /api/control/*  -- authenticated control endpoints
  *    /api/setup/*    -- setup wizard routes (gated by setup sentinel)
  *    /setup{,/}      -- setup wizard HTML

The sync WSGI dispatcher calls setup routes via
``setup_gate_wrapper_wsgi`` which gates non-setup traffic with
``503 Service Unavailable`` until the setup sentinel is written.
"""

import json
import logging
import os
import threading
from typing import Callable, Iterable

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.web_ui.auth import (
    validate_password, set_password,
)
from sethlans_worker_agent.web_ui.config_mutations import apply_config_change
from sethlans_worker_agent.web_ui.http_helpers import MAX_REQUEST_BODY
from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, send_html_file_wsgi, read_body_wsgi,
)
from sethlans_worker_agent.web_ui.setup.gate import setup_gate_wrapper_wsgi
from sethlans_worker_agent.web_ui.setup.routes import (
    handle_setup_request_wsgi,
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
    'FORCE_CPU_ONLY',
    'FORCE_GPU_ONLY',
    'GPU_MODE',
})

# Path to the static HTML dashboard
_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_INDEX_PATH = os.path.join(_STATIC_DIR, 'index.html')


def _get_shutdown_event():
    """Lazily resolve the agent's ``_shutdown_event``.

    The double-checked lock preserves the behaviour of the legacy
    async app: the first resolved reference wins, subsequent calls
    skip the lock. During tests ``agent.py`` may fail to import
    because of its module-level argparse; fall back to a
    never-set event so gate checks remain deterministic.
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


def _check_auth(environ: dict) -> bool:
    """Validate the ``Authorization: Bearer <password>`` header."""
    auth = environ.get('HTTP_AUTHORIZATION', '') or ''
    if not auth.startswith('Bearer '):
        return False
    password = auth[7:]
    return validate_password(password)


# ---- GET handlers ------------------------------------------------

def _handle_get(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Dispatch GET requests (setup routes handled via gate)."""
    path = environ.get('PATH_INFO', '') or ''
    if path in ('/', '/index.html'):
        return send_html_file_wsgi(start_response, _INDEX_PATH)
    if path == '/api/status':
        snapshot = get_status_snapshot()
        return send_json_wsgi(start_response, snapshot)
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

    if not _check_auth(environ):
        return send_json_wsgi(
            start_response, {'error': 'Unauthorized'}, 401,
        )

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
    new_password = data.get('password', '') if isinstance(data, dict) else ''
    if not new_password or len(new_password) < 4:
        return send_json_wsgi(
            start_response,
            {'error': 'Password must be at least 4 characters'},
            400,
        )
    set_password(new_password)
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

def _inner_app(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Core WSGI application (routes only, no setup gate).

    The setup gate wraps this callable at the outer ``app`` entry
    point. Setup routes short-circuit here so they flow through the
    gate's allowed-prefix pass-through.
    """
    path = environ.get('PATH_INFO', '') or ''
    if path.startswith('/api/setup/') or path.startswith('/setup'):
        return handle_setup_request_wsgi(environ, start_response)

    method = environ.get('REQUEST_METHOD', 'GET')
    if method == 'GET':
        return _handle_get(environ, start_response)
    if method == 'POST':
        return _handle_post(environ, start_response)
    return send_json_wsgi(
        start_response, {'error': 'Method Not Allowed'}, 405,
    )


def app(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """Root WSGI application with setup gate."""
    return setup_gate_wrapper_wsgi(environ, start_response, _inner_app)


__all__ = ['app']
