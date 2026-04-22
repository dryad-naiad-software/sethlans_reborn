# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the worker web UI Waitress upstream.

Starts a real Waitress plaintext loopback upstream on a random port
and exercises the status, pause/resume, and authentication
endpoints. Caddy/TLS is NOT in the test loop — post-migration the
worker web UI is a plaintext WSGI upstream on
``config.WAITRESS_UPSTREAM_PORT``; Caddy terminates TLS on
``config.CADDY_PUBLIC_TLS_PORT`` out-of-band.
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.request

import pytest

from sethlans_worker_agent import config, job_processor
from sethlans_worker_agent.web_ui import auth, server
from sethlans_worker_agent.web_ui.setup.gate import mark_setup_complete

logger = logging.getLogger(__name__)


def _find_free_port():
    """Find and return a free TCP port on 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def web_ui(mocker, tmp_path):
    """
    Start the Waitress plaintext upstream on a random high port.

    Patches ``config.WAITRESS_UPSTREAM_PORT`` so Waitress binds on a
    free loopback port. Sets a known PBKDF2-hashed password via the
    auth module. Yields a dict with base_url and password token.
    Stops server on teardown.
    """
    test_password = "test-integration-pw123"
    mocker.patch.object(config, 'UI_ENABLED', True)
    mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')

    # Reset auth cache, then set a known password (hashed)
    auth.reset_cache()
    # Patch config_file_path so set_password writes to tmp
    mocker.patch.object(config, 'config_file_path', tmp_path / 'config.ini')
    auth.set_password(test_password)

    # Find a free port for the Waitress upstream
    port = _find_free_port()
    mocker.patch.object(config, 'WAITRESS_UPSTREAM_PORT', port)

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

    # cert_path/key_path are ignored by Waitress; start_server's
    # signature still accepts them for legacy compatibility.
    server.start_server(cert_path=None, key_path=None)

    # Bypass the setup gate so integration tests can reach /api/status
    mark_setup_complete()

    # Wait for Waitress to bind and accept connections (plaintext)
    for _attempt in range(20):
        try:
            urllib.request.urlopen(
                f'http://127.0.0.1:{port}/api/status',
                timeout=1,
            )
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)

    yield {
        'base_url': f'http://127.0.0.1:{port}',
        'token': test_password,
    }

    server.stop_server()
    auth.reset_cache()


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
    status, _body = _get(url)
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
    status, _body = _post(url, headers=headers)

    assert status == 401
