# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure ASGI application for the worker web UI.

All handlers are ``async def`` coroutines running in uvicorn's event
loop thread (NF-8). Blocking operations (PBKDF2 in
``auth.validate_password``, lock acquisition) are acceptable given
the single-user local access pattern.

Routes:
  GET  /               — serve static HTML dashboard
  GET  /index.html     — serve static HTML dashboard
  GET  /api/status     — JSON status snapshot
  POST /api/control/*  — authenticated control endpoints
"""

import json
import logging
import os
import threading

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.web_ui.auth import (
    validate_password, set_password,
)
from sethlans_worker_agent.web_ui.config_mutations import apply_config_change
from sethlans_worker_agent.web_ui.http_helpers import (
    send_json, send_html_file, read_body, MAX_REQUEST_BODY,
)
from sethlans_worker_agent.web_ui.setup.gate import setup_gate_wrapper
from sethlans_worker_agent.web_ui.setup.routes import handle_setup_request
from sethlans_worker_agent.web_ui.status import get_status_snapshot

logger = logging.getLogger(__name__)

# Shutdown event reference — bound lazily on first control request
# to avoid triggering agent.py module-level argparse at import time.
_shutdown_event_ref = None
_shutdown_ref_lock = threading.Lock()

# Explicit allowlist of config keys that can be modified via control endpoints.
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
    """Lazily resolve the agent's _shutdown_event."""
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
            # During tests, agent.py may not be loadable due to
            # argparse. Fall back to a never-set event.
            _shutdown_event_ref = threading.Event()
        return _shutdown_event_ref


def _check_auth(headers_dict):
    """Validate Bearer password. Returns True if authorized."""
    auth = headers_dict.get(b'authorization', b'').decode('utf-8', 'replace')
    if not auth.startswith('Bearer '):
        return False
    password = auth[7:]
    return validate_password(password)


async def _handle_get(scope, receive, send):
    """Dispatch GET requests."""
    path = scope['path']
    if path.startswith('/api/setup/') or path.startswith('/setup'):
        await handle_setup_request(scope, receive, send)
    elif path in ('/', '/index.html'):
        await send_html_file(send, _INDEX_PATH)
    elif path == '/api/status':
        snapshot = get_status_snapshot()
        await send_json(send, snapshot)
    else:
        await send_json(send, {'error': 'Not Found'}, 404)


async def _handle_control(scope, receive, send):
    """Dispatch POST /api/control/{action} with auth and shutdown guard."""
    shutdown_event = _get_shutdown_event()

    if shutdown_event.is_set():
        await send_json(send, {'error': 'Server is shutting down'}, 503)
        return

    headers = dict(scope.get('headers', []))
    if not _check_auth(headers):
        await send_json(send, {'error': 'Unauthorized'}, 401)
        return

    body_bytes = await read_body(receive)
    if len(body_bytes) > MAX_REQUEST_BODY:
        await send_json(send, {'error': 'Request body too large'}, 413)
        return

    path = scope['path']
    action = path[len('/api/control/'):]

    if action == 'pause':
        job_processor.pause()
        await send_json(send, {'status': 'paused'})
    elif action == 'resume':
        job_processor.resume()
        await send_json(send, {'status': 'resumed'})
    elif action == 'shutdown':
        await _handle_shutdown(send, shutdown_event)
    elif action == 'set_password':
        await _handle_set_password(send, body_bytes)
    elif action == 'update':
        await _handle_config_update(send, body_bytes)
    else:
        await send_json(send, {'error': 'Not Found'}, 404)


async def _handle_shutdown(send, shutdown_event):
    """Trigger a graceful shutdown of the worker agent."""
    logger.info("Shutdown requested via web UI.")
    await send_json(send, {'status': 'shutting_down'})
    shutdown_event.set()


async def _handle_set_password(send, body_bytes):
    """Set a new password for the worker UI."""
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        await send_json(send, {'error': 'Invalid JSON'}, 400)
        return
    new_password = data.get('password', '')
    if not new_password or len(new_password) < 4:
        await send_json(
            send,
            {'error': 'Password must be at least 4 characters'},
            400,
        )
        return
    set_password(new_password)
    await send_json(send, {'status': 'password_set'})


async def _handle_config_update(send, body_bytes):
    """Process a config update request."""
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        await send_json(send, {'error': 'Invalid JSON'}, 400)
        return

    key = data.get('key', '')
    value = data.get('value')

    if key not in _MUTABLE_CONFIG_KEYS:
        await send_json(
            send,
            {'error': f'Config key {key!r} is not modifiable'},
            403,
        )
        return

    try:
        apply_config_change(key, value)
    except ValueError as e:
        await send_json(send, {'error': str(e)}, 400)
        return

    await send_json(send, {'status': 'updated', 'key': key, 'value': value})


async def _handle_post(scope, receive, send):
    """Dispatch POST requests."""
    path = scope['path']
    if path.startswith('/api/setup/') or path.startswith('/setup'):
        await handle_setup_request(scope, receive, send)
    elif path.startswith('/api/control/'):
        await _handle_control(scope, receive, send)
    else:
        await send_json(send, {'error': 'Not Found'}, 404)


async def _inner_app(scope, receive, send):
    """Core ASGI application (routes only, no middleware)."""
    if scope['type'] != 'http':
        return

    method = scope['method']
    if method == 'GET':
        await _handle_get(scope, receive, send)
    elif method == 'POST':
        await _handle_post(scope, receive, send)
    else:
        await send_json(send, {'error': 'Method Not Allowed'}, 405)


async def app(scope, receive, send):
    """Root ASGI application with setup gate."""
    await setup_gate_wrapper(scope, receive, send, _inner_app)
