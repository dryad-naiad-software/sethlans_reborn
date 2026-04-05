# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Embedded HTTP server for the worker web UI.

Uses stdlib http.server.ThreadingHTTPServer in a daemon thread.
Serves the static HTML dashboard, a JSON status endpoint, and
authenticated control endpoints for pause/resume/shutdown/config changes.
"""

import json
import logging
import os
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from sethlans_worker_agent import config, job_processor
from sethlans_worker_agent.web_ui.auth import (
    validate_password, is_password_configured, set_password,
)
from sethlans_worker_agent.web_ui.status import get_status_snapshot

logger = logging.getLogger(__name__)

# Maximum request body size for POST endpoints (4 KB)
MAX_REQUEST_BODY = 4096

# Explicit allowlist of config keys that can be modified via control endpoints.
# MANAGER_HOST, MANAGER_PORT, MANAGER_URL are NEVER modifiable.
_MUTABLE_CONFIG_KEYS = frozenset({
    'POLLING_INTERVAL',
    'HEARTBEAT_INTERVAL',
    'FORCE_CPU_ONLY',
    'FORCE_GPU_ONLY',
    'GPU_SPLIT_MODE',
})

# Path to the static HTML dashboard
_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
_INDEX_PATH = os.path.join(_STATIC_DIR, 'index.html')

# Lock serializing all config mutations to prevent race conditions
# (e.g., two concurrent requests toggling FORCE_CPU/GPU both to True).
_config_lock = threading.Lock()

# Module-level server reference for shutdown
_server = None
_server_thread = None


def _validate_force_modes(force_cpu, force_gpu):
    """Enforce mutual exclusion: at most one can be True."""
    if force_cpu and force_gpu:
        raise ValueError(
            "force_cpu and force_gpu cannot both be enabled"
        )


class WorkerRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the worker web UI."""

    def log_message(self, format, *args):
        """Route HTTP log messages through the Python logging system."""
        logger.debug(format, *args)

    def _send_json(self, data, status=200):
        """Send a JSON response."""
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        """Send an HTML file response."""
        try:
            with open(path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, 'Not Found')

    def _check_auth(self):
        """Validate Bearer password. Returns True if authorized."""
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            self._send_json({'error': 'Unauthorized'}, 401)
            return False
        password = auth[7:]
        if not validate_password(password):
            self._send_json({'error': 'Unauthorized'}, 401)
            return False
        return True

    def _read_body(self):
        """Read and validate POST body size. Returns bytes or None."""
        length_str = self.headers.get('Content-Length', '0')
        try:
            length = int(length_str)
        except ValueError:
            self.send_error(400, 'Invalid Content-Length')
            return None
        if length < 0 or length > MAX_REQUEST_BODY:
            self._send_json({'error': 'Request body too large'}, 413)
            return None
        return self.rfile.read(length)

    def do_GET(self):
        """Handle GET requests."""
        if self.path in ('/', '/index.html'):
            self._send_html(_INDEX_PATH)
        elif self.path == '/api/status':
            snapshot = get_status_snapshot()
            self._send_json(snapshot)
        else:
            self.send_error(404, 'Not Found')

    def do_POST(self):
        """Handle POST requests for control endpoints."""
        if not self.path.startswith('/api/control/'):
            self.send_error(404, 'Not Found')
            return

        if not self._check_auth():
            return

        body_bytes = self._read_body()
        if body_bytes is None:
            return

        action = self.path[len('/api/control/'):]

        if action == 'pause':
            job_processor.pause()
            self._send_json({'status': 'paused'})
        elif action == 'resume':
            job_processor.resume()
            self._send_json({'status': 'resumed'})
        elif action == 'shutdown':
            self._handle_shutdown()
        elif action == 'set_password':
            self._handle_set_password(body_bytes)
        elif action == 'update':
            self._handle_config_update(body_bytes)
        else:
            self.send_error(404, 'Unknown action')

    def _handle_shutdown(self):
        """Trigger a graceful shutdown of the worker agent."""
        from sethlans_worker_agent.agent import _shutdown_event
        logger.info("Shutdown requested via web UI.")
        self._send_json({'status': 'shutting_down'})
        _shutdown_event.set()

    def _handle_set_password(self, body_bytes):
        """Set a new password for the worker UI."""
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            self._send_json({'error': 'Invalid JSON'}, 400)
            return
        new_password = data.get('password', '')
        if not new_password or len(new_password) < 4:
            self._send_json(
                {'error': 'Password must be at least 4 characters'},
                400,
            )
            return
        set_password(new_password)
        self._send_json({'status': 'password_set'})

    def _handle_config_update(self, body_bytes):
        """Process a config update request."""
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            self._send_json({'error': 'Invalid JSON'}, 400)
            return

        key = data.get('key', '')
        value = data.get('value')

        if key not in _MUTABLE_CONFIG_KEYS:
            self._send_json(
                {'error': f'Config key {key!r} is not modifiable'},
                403
            )
            return

        try:
            _apply_config_change(key, value)
        except ValueError as e:
            self._send_json({'error': str(e)}, 400)
            return

        self._send_json({'status': 'updated', 'key': key, 'value': value})


def _parse_bool(value):
    """Parse a boolean from JSON payload."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return bool(value)


def _apply_config_change(key, value):
    """Apply a validated config change. Raises ValueError on bad input."""
    with _config_lock:
        if key == 'POLLING_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Polling interval must be 1-3600')
            config.JOB_POLLING_INTERVAL_SECONDS = val
        elif key == 'HEARTBEAT_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Heartbeat interval must be 1-3600')
            config.HEARTBEAT_INTERVAL_SECONDS = val
        elif key == 'FORCE_CPU_ONLY':
            val = _parse_bool(value)
            new_gpu = config.FORCE_GPU_ONLY
            if val:
                new_gpu = False  # Mutual exclusion
            _validate_force_modes(val, new_gpu)
            config.FORCE_CPU_ONLY = val
            config.FORCE_GPU_ONLY = new_gpu
        elif key == 'FORCE_GPU_ONLY':
            val = _parse_bool(value)
            new_cpu = config.FORCE_CPU_ONLY
            if val:
                new_cpu = False  # Mutual exclusion
            _validate_force_modes(new_cpu, val)
            config.FORCE_CPU_ONLY = new_cpu
            config.FORCE_GPU_ONLY = val
        elif key == 'GPU_SPLIT_MODE':
            config.GPU_SPLIT_MODE = _parse_bool(value)


def start_server():
    """
    Start the web UI HTTP server in a daemon thread.

    If no custom password is set, the default password is used and
    the dashboard prompts the user to change it on first load.
    """
    global _server, _server_thread

    if not config.UI_ENABLED:
        logger.info("Worker Web UI is disabled.")
        return

    if not is_password_configured():
        logger.info(
            "No custom UI password set. Using default. "
            "Change it via the dashboard."
        )

    bind_addr = config.UI_BIND_ADDRESS
    port = config.UI_PORT

    try:
        _server = ThreadingHTTPServer(
            (bind_addr, port), WorkerRequestHandler
        )
        _server.socket.settimeout(30)
        _server_thread = threading.Thread(
            target=_server.serve_forever,
            name='web-ui-server',
            daemon=True,
        )
        _server_thread.start()
        logger.info(
            "Worker Web UI started on http://%s:%d", bind_addr, port
        )
    except OSError as e:
        logger.error("Failed to start Worker Web UI: %s", e)


def stop_server():
    """Shut down the web UI HTTP server."""
    global _server, _server_thread
    if _server is not None:
        logger.info("Stopping Worker Web UI server...")
        _server.shutdown()
        _server = None
        _server_thread = None
