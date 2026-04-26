# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared test helpers for the worker ``GET /api/health/`` integration
tests.

Module name starts with an underscore so pytest does not collect it
as a test module. Keeps ``test_health_endpoint.py`` under the 300-line
file limit by centralising the WSGI bring-up plumbing. The two
``web_ui_setup_*`` fixtures live in ``conftest.py`` so pytest can
auto-discover them without forcing the test module to import (and
therefore F811-shadow) the fixture names.
"""

import json
import socket
import time
import urllib.error
import urllib.request

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui import auth, server
from sethlans_worker_agent.web_ui.setup.gate import init_gate


# --- Test helpers --------------------------------------------------

def _find_free_port():
    """Find and return a free TCP port on 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _get(url):
    """GET *url* and return ``(status_code, body_dict)``.

    Treats HTTP errors as normal responses (returns the error code +
    parsed body) so tests can assert 503 without try/except.
    """
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode()) if exc.fp else {}
        return exc.code, body


def _wait_for_bind(port, path, expect_status_in):
    """Poll ``http://127.0.0.1:<port><path>`` until Waitress is up.

    The gate-closed fixture variant cannot probe ``/api/status``
    (it returns 503 before bind is even relevant), so callers pass
    the path + acceptable status-code set explicitly.
    """
    url = f'http://127.0.0.1:{port}{path}'
    for _attempt in range(40):
        try:
            status, _body = _get(url)
            if status in expect_status_in:
                return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError(
        f"Waitress did not bind on port {port} within timeout."
    )


# --- Common WSGI bring-up ------------------------------------------

def _bring_up_wsgi(mocker, tmp_path, *, password='test-pw-int-health'):
    """Patch config + auth + hardware mocks; start Waitress.

    Returns ``(port, password)``.  The caller decides whether to
    ``mark_setup_complete()`` (setup-complete variant) or leave the
    gate closed (sentinel-absent variant).  In both variants we call
    ``init_gate(<empty data_dir>)`` so the gate's module-level
    ``_setup_complete`` flag starts from a known-false state derived
    from a sentinel-free directory -- this is the DevOps-LOW-5 hygiene
    requirement so the test isn't accidentally piggybacking on a
    sibling test's leaked gate-open flag.
    """
    mocker.patch.object(config, 'UI_ENABLED', True)
    mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')

    # Auth setup -- a known password so ``/api/control/*`` (not used in
    # these tests) wouldn't surprise-401 a future addition.
    auth.reset_cache()
    mocker.patch.object(config, 'config_file_path', tmp_path / 'config.ini')
    auth.set_password(password)

    # Random loopback port -- avoids contention with other tests.
    port = _find_free_port()
    mocker.patch.object(config, 'WAITRESS_UPSTREAM_PORT', port)

    # Hardware-detection mocks lifted from the sibling
    # ``test_web_ui_server.py`` fixture; ``/api/status`` is exercised
    # in the deprecation regression test, so its dependencies must be
    # stubbed even though ``/api/health/`` itself touches none of them.
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

    # Reset the gate against an empty data dir so the module-level
    # ``_setup_complete`` flag starts False (sentinel absent).  The
    # autouse ``_reset_setup_gate`` fixture in conftest already does
    # this, but calling ``init_gate`` here documents the intent.
    empty_data_dir = tmp_path / 'empty_data_dir'
    empty_data_dir.mkdir()
    init_gate(empty_data_dir)

    # Plaintext Waitress upstream; cert/key args ignored.
    server.start_server(cert_path=None, key_path=None)

    return port, password
