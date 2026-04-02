# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the worker web UI HTTP server.

Starts a real ThreadingHTTPServer on a random port and exercises
the status, pause/resume, and authentication endpoints using
urllib from stdlib.
"""

import json
import logging
import urllib.request
import urllib.error

import pytest

from sethlans_worker_agent import config, job_processor
from sethlans_worker_agent.web_ui import auth, server

logger = logging.getLogger(__name__)


@pytest.fixture()
def web_ui(mocker, tmp_path):
    """
    Start the web UI server on a random high port.

    Patches config to enable UI and sets a known token.
    Yields a dict with base_url and token. Stops server on teardown.
    """
    test_token = "test-integration-token-abc123"
    mocker.patch.object(config, 'UI_ENABLED', True)
    mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')
    mocker.patch.object(config, 'UI_TOKEN', test_token)

    # Reset cached token so get_token() reads from config
    auth._token = None

    # Find a free port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    mocker.patch.object(config, 'UI_PORT', port)

    # Mock hardware detection to avoid Blender dependency
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_gpu_device_details',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
        return_value=8,
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.tool_manager_instance'
        '.scan_for_local_blenders',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.system_monitor.WORKER_ID',
        42,
    )

    server.start_server()

    yield {
        'base_url': f'http://127.0.0.1:{port}',
        'token': test_token,
    }

    server.stop_server()


def _get(url, headers=None):
    """Make a GET request and return (status, body_dict)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        return e.code, body


def _post(url, data=None, headers=None):
    """Make a POST request and return (status, body_dict)."""
    body_bytes = json.dumps(data or {}).encode()
    all_headers = {'Content-Type': 'application/json'}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(
        url, data=body_bytes, headers=all_headers, method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        return e.code, body


# -- Status endpoint --

def test_status_returns_valid_json(web_ui):
    """GET /api/status returns valid JSON with expected fields."""
    url = f"{web_ui['base_url']}/api/status"
    status, body = _get(url)

    assert status == 200
    assert 'worker' in body
    assert 'hardware' in body
    assert 'config' in body
    assert 'active_jobs' in body
    assert 'gpu_allocation' in body
    assert 'tools' in body


def test_status_contains_worker_info(web_ui):
    """Status response includes worker hostname and ID."""
    url = f"{web_ui['base_url']}/api/status"
    status, body = _get(url)

    assert status == 200
    worker = body['worker']
    assert 'hostname' in worker
    assert 'worker_id' in worker
    assert 'registration_status' in worker


def test_status_no_auth_required(web_ui):
    """Status endpoint does not require authentication."""
    url = f"{web_ui['base_url']}/api/status"
    status, body = _get(url)
    assert status == 200


# -- Pause/Resume endpoints --

def test_pause_toggles_state(web_ui):
    """POST /api/control/pause sets paused state."""
    assert job_processor.is_paused() is False

    url = f"{web_ui['base_url']}/api/control/pause"
    headers = {'Authorization': f"Bearer {web_ui['token']}"}
    status, body = _post(url, headers=headers)

    assert status == 200
    assert body['status'] == 'paused'
    assert job_processor.is_paused() is True


def test_resume_toggles_state(web_ui):
    """POST /api/control/resume clears paused state."""
    job_processor.pause()
    assert job_processor.is_paused() is True

    url = f"{web_ui['base_url']}/api/control/resume"
    headers = {'Authorization': f"Bearer {web_ui['token']}"}
    status, body = _post(url, headers=headers)

    assert status == 200
    assert body['status'] == 'resumed'
    assert job_processor.is_paused() is False


# -- Authentication --

def test_control_endpoints_require_bearer_token(web_ui):
    """Control endpoints without token return 401."""
    url = f"{web_ui['base_url']}/api/control/pause"
    status, body = _post(url)

    assert status == 401
    assert body['error'] == 'Unauthorized'


def test_control_endpoints_reject_wrong_token(web_ui):
    """Control endpoints with wrong token return 401."""
    url = f"{web_ui['base_url']}/api/control/pause"
    headers = {'Authorization': 'Bearer wrong-token'}
    status, body = _post(url, headers=headers)

    assert status == 401
    assert body['error'] == 'Unauthorized'


def test_control_endpoints_reject_non_bearer_auth(web_ui):
    """Control endpoints with non-Bearer auth return 401."""
    url = f"{web_ui['base_url']}/api/control/pause"
    headers = {'Authorization': f"Token {web_ui['token']}"}
    status, body = _post(url, headers=headers)

    assert status == 401
