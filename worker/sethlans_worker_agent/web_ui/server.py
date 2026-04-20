# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
HTTPS server lifecycle for the worker web UI.

Launches uvicorn in a daemon thread with TLS 1.2 minimum enforced
via ``shared.tls_utils.build_ssl_context``.
"""

import logging
import ssl
import threading

import uvicorn
from asgiref.wsgi import WsgiToAsgi

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui.auth import is_password_configured
from shared.tls_utils import build_ssl_context

logger = logging.getLogger(__name__)

_server = None
_server_thread = None


def start_server(cert_path, key_path):
    """Start uvicorn in a daemon thread with TLS.

    The SSLContext is built via ``shared.tls_utils.build_ssl_context()``
    which enforces TLS 1.2 minimum. After uvicorn creates its Config,
    we pre-load it and replace its SSL context with ours to guarantee
    the TLS version floor.
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

    # Phase 4 migration shim: the top-level ``app`` is now a sync
    # WSGI callable. Wrap it with ``asgiref.wsgi.WsgiToAsgi`` so
    # uvicorn can still drive the stack. Phase 5 replaces uvicorn
    # with Waitress and deletes this wrapper.
    from sethlans_worker_agent.web_ui.asgi_app import app as wsgi_app
    asgi_app = WsgiToAsgi(wsgi_app)

    uvicorn_config = uvicorn.Config(
        app=asgi_app,
        host=config.UI_BIND_ADDRESS,
        port=config.UI_PORT,
        ssl_keyfile=str(key_path),
        ssl_certfile=str(cert_path),
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        log_level="warning",
    )

    # Pre-load the config so we can replace its SSL context with one
    # that enforces TLS 1.2 minimum via shared.tls_utils.
    uvicorn_config.load()
    uvicorn_config.ssl = build_ssl_context(cert_path, key_path)

    _server = uvicorn.Server(uvicorn_config)
    _server_thread = threading.Thread(
        target=_server.run,
        name='web-ui-server',
        daemon=True,
    )
    _server_thread.start()
    logger.info(
        "Worker Web UI started on https://%s:%d",
        config.UI_BIND_ADDRESS, config.UI_PORT,
    )


def stop_server():
    """Signal uvicorn to shut down, join thread with timeout.

    If the join times out after 5 seconds, log a warning.
    The orphaned thread cannot dispatch control actions because
    the ASGI app returns 503 once ``_shutdown_event`` is set (FR-12).
    """
    global _server, _server_thread
    if _server is None:
        return
    logger.info("Stopping Worker Web UI server...")
    _server.should_exit = True
    if _server_thread is not None:
        _server_thread.join(timeout=5.0)
        if _server_thread.is_alive():
            logger.warning(
                "Web UI server thread did not terminate within "
                "5 seconds. Proceeding with shutdown."
            )
    _server = None
    _server_thread = None
