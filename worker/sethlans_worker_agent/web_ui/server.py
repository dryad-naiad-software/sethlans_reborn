# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plaintext WSGI server lifecycle for the worker web UI.

Phase 5a of the Waitress migration: uvicorn has been replaced with
Waitress. Waitress serves the sync WSGI ``app`` callable directly —
the ``asgiref.wsgi.WsgiToAsgi`` bridge from Phase 4b is gone.

Interim-state note: Waitress does NOT terminate TLS. This module now
binds plaintext on the loopback bind address. External HTTPS access
is provided by Caddy in front of Waitress; Caddy supervision is
wired in Phase 5b. Unit/integration tests drive Waitress directly on
loopback during the 5a window.
"""

import logging
import threading

import waitress

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui.auth import is_password_configured

logger = logging.getLogger(__name__)

_server = None
_server_thread = None


def start_server(cert_path, key_path):
    """Start Waitress in a dedicated daemon thread (plaintext loopback).

    ``cert_path`` and ``key_path`` are accepted but unused by this
    function during the 5a/5b transition window — Caddy will read
    them in Phase 5b. Retained here so call sites (agent.py and
    integration fixtures) do not need to change as 5a lands.

    Waitress config (spec Phase 5, line 354):
      - threads=8
      - channel_timeout=300 seconds
      - connection_limit=1000
      - max_request_body_size=4096 bytes (matches MAX_REQUEST_BODY
        invariant — defense-in-depth against oversized bodies)

    Waitress runs in a daemon thread so the worker agent's main
    thread retains signal ownership (mirrors the prior uvicorn
    pattern). Waitress's ``BaseWSGIServer.run`` does not install
    signal handlers, so launching it from a non-main thread is
    safe — only ``waitress.serve`` (unused here) does that.
    """
    global _server, _server_thread

    # cert_path/key_path unused in 5a; Caddy will consume them in 5b.
    del cert_path, key_path

    if not config.UI_ENABLED:
        logger.info("Worker Web UI is disabled.")
        return

    if not is_password_configured():
        logger.info(
            "No custom UI password set. Using default. "
            "Change it via the dashboard."
        )

    # Sync WSGI callable (Phase 4b). Waitress serves it natively —
    # no WsgiToAsgi bridge required.
    from sethlans_worker_agent.web_ui.asgi_app import app as wsgi_app

    # Phase 5b: Waitress binds on a loopback upstream port; Caddy
    # reverse-proxies public TLS + loopback vhosts to this upstream.
    # ``UI_BIND_ADDRESS`` is forced to 127.0.0.1 here because the
    # upstream must not be externally reachable — only Caddy speaks
    # to it. Operators who override ``UI_BIND_ADDRESS`` still have
    # their preference respected for the advertised (public) URL
    # computed in ``system_monitor`` via ``UI_PORT``.
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
        target=_server.run,
        name='web-ui-server',
        daemon=True,
    )
    _server_thread.start()
    logger.info(
        "Worker Web UI (Waitress) started on http://127.0.0.1:%d "
        "(plaintext upstream; TLS terminated by Caddy on public port %d)",
        config.WAITRESS_UPSTREAM_PORT, config.CADDY_PUBLIC_TLS_PORT,
    )


def stop_server():
    """Signal Waitress to shut down, join thread with timeout.

    Waitress exposes ``server.close()`` which closes the listening
    socket; the ``asyncore.loop`` call inside ``run()`` then exits
    cleanly. If the join times out after 5 seconds, log a warning.
    The orphaned thread cannot dispatch control actions because
    the WSGI app returns 503 once ``_shutdown_event`` is set (FR-12).
    """
    global _server, _server_thread
    if _server is None:
        return
    logger.info("Stopping Worker Web UI server...")
    try:
        _server.close()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Error closing Waitress server: %s", exc)
    if _server_thread is not None:
        _server_thread.join(timeout=5.0)
        if _server_thread.is_alive():
            logger.warning(
                "Web UI server thread did not terminate within "
                "5 seconds. Proceeding with shutdown."
            )
    _server = None
    _server_thread = None
