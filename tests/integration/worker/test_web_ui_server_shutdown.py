# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the Waitress shutdown sequence (GH-120).

The worker web UI's Waitress daemon thread previously raised
``OSError: [Errno 9] Bad file descriptor`` on teardown, especially on
macOS, because ``_server.close()`` tore down listening/trigger fds
while the asyncore loop was still blocked in ``select()``. These
tests guard the clean-shutdown sequence implemented in
``sethlans_worker_agent/web_ui/server.py``:

  1. ``task_dispatcher.shutdown()`` drains worker threads.
  2. ``pull_trigger()`` wakes the asyncore loop.
  3. ``wasyncore.close_all`` empties the socket map cleanly.
  4. Thread joins within 5 seconds with no unhandled thread
     exceptions (``PytestUnhandledThreadExceptionWarning`` is
     promoted to an error by pytest.ini).
"""

import socket
import threading
import time

import pytest

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui import auth, server
from sethlans_worker_agent.web_ui.setup.gate import mark_setup_complete


def _find_free_port():
    """Find and return a free TCP port on 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def configured_env(mocker, tmp_path):
    """Configure Waitress dependencies without starting the server.

    Yields nothing; tests call ``server.start_server`` directly so
    they can drive start/stop cycles explicitly.
    """
    mocker.patch.object(config, 'UI_ENABLED', True)
    mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')

    auth.reset_cache()
    mocker.patch.object(config, 'config_file_path', tmp_path / 'config.ini')
    auth.set_password('shutdown-test-pw123')

    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_gpu_device_details',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
        return_value=4,
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.tool_manager_instance'
        '.scan_for_local_blenders',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.system_monitor.WORKER_ID',
        7,
    )

    mark_setup_complete()

    yield

    # Belt-and-suspenders: ensure no server is left running across
    # tests even if an assertion failed mid-cycle.
    server.stop_server()
    auth.reset_cache()


def _start_and_wait(port):
    """Start Waitress on ``port`` and wait for it to bind."""
    server.start_server(cert_path=None, key_path=None)
    # Probe the listener directly; any TCP connection that succeeds
    # proves the accept loop is running.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(
                ('127.0.0.1', port), timeout=0.5,
            ):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(
        f"Waitress did not bind to 127.0.0.1:{port} within 5 seconds"
    )


def test_stop_server_returns_within_timeout(configured_env, mocker):
    """``stop_server`` must return within 5 seconds under normal load."""
    port = _find_free_port()
    mocker.patch.object(config, 'WAITRESS_UPSTREAM_PORT', port)

    _start_and_wait(port)

    start = time.monotonic()
    server.stop_server()
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, (
        f"stop_server() took {elapsed:.2f}s, expected < 5s"
    )
    # After a clean shutdown the daemon thread must actually be gone,
    # not merely "stop_server returned quickly".
    active_names = [t.name for t in threading.enumerate()]
    assert 'web-ui-server' not in active_names, (
        f"web-ui-server thread still alive after stop_server: "
        f"{active_names}"
    )


def test_start_stop_cycle_no_thread_exceptions(
    configured_env, mocker, recwarn,
):
    """Repeated start/stop cycles must not raise in the daemon thread.

    pytest.ini promotes ``PytestUnhandledThreadExceptionWarning`` to
    an error, so an EBADF leak in the daemon thread would fail this
    test even without an explicit assertion. We additionally capture
    thread exceptions via ``threading.excepthook`` for a clearer
    diagnostic, since the pytest warning fires at interpreter exit.
    """
    port = _find_free_port()
    mocker.patch.object(config, 'WAITRESS_UPSTREAM_PORT', port)

    # Capture any exception raised in a non-main thread during the
    # test body. Restore the previous hook afterwards so we don't
    # leak test state into neighbouring tests.
    thread_errors = []
    previous_hook = threading.excepthook

    def _capture(args):
        thread_errors.append(args)
        previous_hook(args)

    threading.excepthook = _capture
    try:
        for cycle in range(5):
            _start_and_wait(port)
            server.stop_server()
            # Give the OS a beat to reclaim the port on slow CI.
            time.sleep(0.05)
    finally:
        threading.excepthook = previous_hook

    assert not thread_errors, (
        "Unhandled exception(s) in web-ui-server thread during "
        f"start/stop cycles: {thread_errors!r}"
    )

    # ``recwarn`` is populated lazily; any
    # PytestUnhandledThreadExceptionWarning that slipped through the
    # excepthook net should be visible here too.
    bad_filters = [
        w for w in recwarn.list
        if 'PytestUnhandledThread' in type(w.category).__name__
        or 'PytestUnhandledThread' in getattr(w.category, '__name__', '')
    ]
    assert not bad_filters, (
        f"Unexpected thread-exception warnings: {bad_filters!r}"
    )
