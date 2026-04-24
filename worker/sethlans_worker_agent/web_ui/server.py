# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plaintext WSGI server lifecycle for the worker web UI.

Waitress serves the sync WSGI ``app`` callable. Waitress does NOT
terminate TLS: this module binds plaintext on the loopback bind
address, and external HTTPS access is provided by a Caddy reverse
proxy in front of Waitress. Unit/integration tests drive Waitress
directly on loopback.
"""

import errno
import logging
import threading

import waitress
from waitress import wasyncore

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui.auth import is_password_configured

logger = logging.getLogger(__name__)

_server = None
_server_thread = None
# Set while stop_server() is tearing down the Waitress server so the
# run-loop wrapper can distinguish expected EBADF races (platform
# quirk on macOS/Linux during fd teardown) from real crashes.
_shutting_down = False


def start_server(cert_path, key_path):
    """Start Waitress in a dedicated daemon thread (plaintext loopback).

    ``cert_path`` and ``key_path`` are accepted but unused by this
    function during the 5a/5b transition window — Caddy will read
    them in Phase 5b. Retained here so call sites (agent.py and
    integration fixtures) do not need to change as 5a lands.

    Waitress config:
      - threads=8
      - channel_timeout=300 seconds
      - connection_limit=1000
      - max_request_body_size=4096 bytes (matches MAX_REQUEST_BODY
        invariant — defense-in-depth against oversized bodies)

    Waitress runs in a daemon thread so the worker agent's main
    thread retains signal ownership. Waitress's
    ``BaseWSGIServer.run`` does not install signal handlers, so
    launching it from a non-main thread is safe — only
    ``waitress.serve`` (unused here) does that.
    """
    global _server, _server_thread

    # cert_path/key_path are consumed by the Caddy reverse proxy
    # out-of-band; Waitress itself serves plaintext on loopback.
    del cert_path, key_path

    if not config.UI_ENABLED:
        logger.info("Worker Web UI is disabled.")
        return

    if not is_password_configured():
        logger.info(
            "No custom UI password set. Using default. "
            "Change it via the dashboard."
        )

    from sethlans_worker_agent.web_ui.wsgi_app import app as wsgi_app

    # Waitress binds on a loopback upstream port; Caddy
    # reverse-proxies public TLS + loopback vhosts to this upstream.
    # The upstream must not be externally reachable — only Caddy
    # speaks to it. Operators who override ``UI_BIND_ADDRESS`` still
    # have their preference respected for the advertised (public)
    # URL computed in ``system_monitor`` via ``UI_PORT``.
    _server = waitress.create_server(
        wsgi_app,
        host='127.0.0.1',
        port=config.WAITRESS_UPSTREAM_PORT,
        threads=8,
        channel_timeout=300,
        connection_limit=1000,
        max_request_body_size=4096,
    )

    _server_thread = threading.Thread(
        target=_run_server,
        args=(_server,),
        name='web-ui-server',
        daemon=True,
    )
    _server_thread.start()
    logger.info(
        "Worker Web UI (Waitress) started on http://127.0.0.1:%d "
        "(plaintext upstream; TLS terminated by Caddy on public port %d)",
        config.WAITRESS_UPSTREAM_PORT, config.CADDY_PUBLIC_TLS_PORT,
    )


def _run_server(server):
    """Run the Waitress asyncore loop with an EBADF safety net.

    On macOS (and, less often, Linux) the asyncore loop can be
    blocked in ``select()`` when ``stop_server`` tears down the
    listening/trigger sockets. The next iteration of the loop may
    observe an already-closed file descriptor and raise
    ``OSError(EBADF)`` before the ``while map`` guard has a chance
    to exit. We catch that specific race here when ``_shutting_down``
    is set; any other error during normal operation is re-raised so
    the pytest ``PytestUnhandledThreadExceptionWarning`` hook
    surfaces genuine failures.
    """
    try:
        server.run()
    except OSError as exc:
        if _shutting_down and exc.errno == errno.EBADF:
            logger.debug(
                "Suppressed expected EBADF during Web UI shutdown race."
            )
            return
        raise


def _drain_and_close(server):
    """Perform the three-step Waitress teardown sequence.

    Factored out of :func:`stop_server` to keep cyclomatic
    complexity under the project's flake8 threshold. Each step is
    defensively wrapped so a failure in one does not prevent the
    next from running — on shutdown we want best-effort cleanup.
    """
    # 1. Drain worker threads before tearing down sockets.
    try:
        server.task_dispatcher.shutdown()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Error shutting down task dispatcher: %s", exc)

    # 2. Wake the asyncore loop so it returns from select() before
    #    we start closing file descriptors it is watching.
    try:
        server.pull_trigger()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("pull_trigger during shutdown failed: %s", exc)

    # 3. Close all fds in the shared map (listener, trigger,
    #    channels) and empty the map so ``while map`` exits.
    try:
        wasyncore.close_all(server._map, ignore_all=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Error closing asyncore map: %s", exc)


def stop_server():
    """Shut down Waitress cleanly and join its daemon thread.

    Shutdown sequence (mirrors ``MultiSocketServer.close`` in
    waitress.server, plus a trigger-poke to unblock ``select()``):

      1. Set the ``_shutting_down`` flag so the run-loop wrapper
         recognises the subsequent EBADF race as expected.
      2. ``task_dispatcher.shutdown()`` — drain worker threads so
         no WSGI task is mid-flight when the listener goes away.
      3. ``pull_trigger()`` — write a byte to the trigger pipe to
         wake ``asyncore.loop`` out of ``select.select()`` before we
         start closing file descriptors it is watching. This is the
         core fix for GH-120 on macOS/Linux: closing an fd while
         another thread is blocked on ``select()`` on that fd raises
         ``EBADF``; poking the trigger first lets the loop exit
         cleanly.
      4. ``wasyncore.close_all(map, ignore_all=True)`` — close the
         listener, trigger, and any lingering channels, then empty
         the map. The ``while map`` guard in ``wasyncore.loop`` then
         returns on its next tick.
      5. Join the thread with a 5-second timeout. The orphaned
         thread cannot dispatch control actions because the WSGI
         app returns 503 once ``_shutdown_event`` is set (FR-12).
    """
    global _server, _server_thread, _shutting_down
    if _server is None:
        return
    logger.info("Stopping Worker Web UI server...")
    _shutting_down = True
    try:
        _drain_and_close(_server)
        if _server_thread is not None:
            _server_thread.join(timeout=5.0)
            if _server_thread.is_alive():
                logger.warning(
                    "Web UI server thread did not terminate within "
                    "5 seconds. Proceeding with shutdown."
                )
    finally:
        _shutting_down = False
        _server = None
        _server_thread = None
