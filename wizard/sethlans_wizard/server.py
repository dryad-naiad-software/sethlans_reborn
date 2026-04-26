# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Waitress WSGI app + server bootstrap for the wizard (Spec 1 / A3).

Implements the FR-W12 server choice (Waitress, ``threads=4``, single
handoff-state lock invariant) and the FR-W3 port-resolution helper.

TLS: Waitress has no native TLS support, so :func:`run` builds an
``ssl.SSLContext.wrap_socket(server_side=True)`` from the FR-W4 cert
and passes the wrapped socket to ``waitress.create_server`` via
``sockets=[…]`` so the wasyncore loop never sees a raw TCP connection
that skips the TLS handshake.

Lock ordering (CRITICAL — CONC-v23-MED-1): the handoff-state lock from
:func:`wizard.sethlans_wizard.auth_state.get_handoff_lock` is the
single lock declared by FR-W12 and MUST be acquired BEFORE A4's
in-flight probe lock — never the reverse.

A3 wires only the auth route; A4 will register topology / done /
runtime-ready handlers on the same :class:`Router`.
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

import waitress

from wizard.sethlans_wizard.handlers.auth import make_auth_handler

logger = logging.getLogger(__name__)

# FR-W3 port-range constants. Public so A6 can import them when wiring
# the launcher / scan logic.
DEFAULT_WIZARD_PORT = 8100
PORT_SCAN_RANGE = (8100, 8101, 8102, 8103, 8104)
WIZARD_PORT_ENV = "SETHLANS_WIZARD_PORT"

# FR-W12 — Waitress thread count.
WAITRESS_THREADS = 4

# Bind address per FR-W3 (LAN-bound, NOT operator-controllable).
WIZARD_BIND_HOST = "0.0.0.0"


def resolve_port(env: Optional[dict] = None) -> int:
    """Return the wizard's target listen port.

    Honours ``SETHLANS_WIZARD_PORT`` from *env* (defaults to
    ``os.environ``). Falls back to :data:`DEFAULT_WIZARD_PORT`. A
    malformed env value triggers ``ValueError`` so the launcher can
    surface the misconfiguration clearly (FR-W3 "bind failure causes
    immediate non-zero exit with a clear log message").
    """
    source = env if env is not None else os.environ
    raw = source.get(WIZARD_PORT_ENV)
    if raw is None or raw == "":
        return DEFAULT_WIZARD_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{WIZARD_PORT_ENV} must be an integer; got {raw!r}",
        ) from exc
    if not (1 <= port <= 65535):
        raise ValueError(
            f"{WIZARD_PORT_ENV} out of range (1..65535); got {port}",
        )
    return port


# ---------------------------------------------------------------------
# Router — maps PATH_INFO to a per-route WSGI handler
# ---------------------------------------------------------------------

class Router:
    """Tiny path-prefix dispatcher used by :func:`create_app`.

    Routes are registered most-specific-first; the first prefix that
    matches ``PATH_INFO`` wins. Unknown paths get a JSON 404.

    Kept deliberately simple — the wizard only ever exposes a small
    handful of endpoints (FR-W7, FR-W8, FR-W9, FR-W14 plus static
    pages from FR-W-FE2). No regex, no method dispatch table.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[str, Callable]] = []

    def add(self, prefix: str, handler: Callable) -> None:
        """Register WSGI *handler* for an exact-match path *prefix*."""
        if not prefix.startswith("/"):
            raise ValueError(f"route prefix must start with '/': {prefix!r}")
        self._routes.append((prefix, handler))

    def dispatch(
        self,
        environ: dict,
        start_response: Callable,
    ) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "") or ""
        for prefix, handler in self._routes:
            if path == prefix:
                return handler(environ, start_response)
        return _send_404(start_response)


def _send_404(start_response: Callable) -> Iterable[bytes]:
    body = b'{"error": "Not Found"}'
    start_response(
        "404 Not Found",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


# ---------------------------------------------------------------------
# create_app — public WSGI factory
# ---------------------------------------------------------------------

def create_app(
    data_dir: Path,
    setup_token: bytes,
    ipc_secret: bytes,
) -> Callable:
    """Return the wizard's top-level WSGI app.

    Args:
        data_dir: The wizard's per-user data directory (FR-W16
            canonicalised).
        setup_token: Setup token bytes read from
            ``<data_dir>/wizard/.setup_token`` via
            ``ipc.read_secret_file`` (which also unlinks the file per
            SEC-MED-11). Held in the auth handler's closure.
        ipc_secret: HMAC secret bytes for the marker IPC. A3 does not
            consume this directly; it is threaded through so A4's
            done / runtime-ready handlers (which DO consume it) can
            be wired in without changing the ``create_app`` signature.

    Returns:
        A WSGI callable. A3 wires only ``POST /api/wizard/auth/``;
        every other path returns a JSON 404 until A4 lands.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(setup_token, (bytes, bytearray)) or not setup_token:
        raise ValueError("setup_token must be non-empty bytes")
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")

    router = Router()
    router.add("/api/wizard/auth/", make_auth_handler(bytes(setup_token)))

    # The router instance is closed over by the returned callable so
    # tests can introspect it via the `_router` attribute below.
    def app(
        environ: dict, start_response: Callable,
    ) -> Iterable[bytes]:
        return router.dispatch(environ, start_response)

    # Stash the router + the data_dir + ipc_secret on the app for A4
    # to extend. ``ipc_secret`` is bytes, retained in memory only;
    # never logged.
    app._router = router  # type: ignore[attr-defined]
    app._data_dir = data_dir  # type: ignore[attr-defined]
    app._ipc_secret = bytes(ipc_secret)  # type: ignore[attr-defined]
    return app


# ---------------------------------------------------------------------
# run — Waitress launcher with TLS-wrapped socket
# ---------------------------------------------------------------------

def _build_tls_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """Construct a server-side TLS context from cert + key files.

    Server-side default: ``PROTOCOL_TLS_SERVER`` with the platform's
    default ciphers. Client cert verification is disabled — the wizard
    is short-lived, behind a setup-token gate, and serves a self-signed
    cert; mutual TLS is out of scope for Spec 1.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def _bind_tls_socket(
    host: str,
    port: int,
    ctx: ssl.SSLContext,
) -> socket.socket:
    """Create a listening TCP socket and TLS-wrap it (server side).

    The wrapped socket is what we hand to Waitress via ``sockets=[…]``
    so Waitress's wasyncore loop never sees a raw TCP connection that
    skips the TLS handshake.

    On bind failure the raw socket is closed and the original
    ``OSError`` is re-raised.
    """
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # FR-W3: SO_REUSEADDR=False so we never silently steal a port.
        raw.bind((host, port))
        raw.listen(socket.SOMAXCONN)
    except OSError:
        raw.close()
        raise
    return ctx.wrap_socket(raw, server_side=True)


def run(
    app: Callable,
    host: str,
    port: int,
    cert_path: Path,
    key_path: Path,
) -> None:
    """Bind a TLS-wrapped socket and run Waitress on the current thread.

    Blocks until the Waitress event loop exits. The launcher (A6) is
    expected to call this on a dedicated thread or to treat the wizard
    as the main process. The wizard's polite-shutdown sequence
    (FR-W17, A4) calls ``server.close()`` on the returned-and-stashed
    server reference; A3 keeps the runtime path minimal.
    """
    ctx = _build_tls_context(cert_path, key_path)
    sock = _bind_tls_socket(host, port, ctx)
    logger.info(
        "Wizard Waitress listener bound on https://%s:%d/ (threads=%d)",
        host, port, WAITRESS_THREADS,
    )
    server = waitress.create_server(
        app,
        sockets=[sock],
        threads=WAITRESS_THREADS,
        ident="sethlans-wizard",
    )
    # Stash the server reference so A4's polite-shutdown can call
    # server.close() from another thread without re-importing this
    # module.
    _SERVER_REF.set(server)
    try:
        server.run()
    finally:
        _SERVER_REF.clear()


class _ServerRef:
    """Thread-safe slot for the live Waitress server (A4 closes via this)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server = None

    def set(self, server) -> None:
        with self._lock:
            self._server = server

    def get(self):
        with self._lock:
            return self._server

    def clear(self) -> None:
        with self._lock:
            self._server = None


_SERVER_REF = _ServerRef()


def get_server_ref() -> _ServerRef:
    """Return the singleton :class:`_ServerRef` (for A4)."""
    return _SERVER_REF


__all__ = [
    "DEFAULT_WIZARD_PORT",
    "PORT_SCAN_RANGE",
    "WIZARD_PORT_ENV",
    "WIZARD_BIND_HOST",
    "WAITRESS_THREADS",
    "Router",
    "create_app",
    "resolve_port",
    "run",
    "get_server_ref",
]
