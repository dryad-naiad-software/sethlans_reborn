# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Waitress WSGI app + server bootstrap for the wizard (Spec 1).

FR-W12 (Waitress, ``threads=4``, single handoff-state lock invariant)
and FR-W3 port resolution. After the issue #170 consolidation the
wizard binds **plain HTTP on loopback only**; the launcher's Caddy
supervisor terminates TLS in front (mirrors manager + worker). This
eliminates the listener-socket-TLS-wrapping accept-loop corruption
that aborted browser handshakes triggered (#167).

Lock-coupling rule (CRITICAL — CONC-v23-MED-1): the handoff-state
lock MUST NOT be held when the in-flight probe lock owned by
``handlers/runtime_ready.py`` is acquired. Once the in-flight lock is
held, the handoff-state lock may be acquired and released in short
critical sections — never the reverse.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

import waitress

from wizard.sethlans_wizard.handlers.auth import make_auth_handler
from wizard.sethlans_wizard.handlers.done import make_done_handler
from wizard.sethlans_wizard.handlers.health import make_health_handler
from wizard.sethlans_wizard.handlers.launcher_log_path import (
    make_launcher_log_path_handler,
)
from wizard.sethlans_wizard.handlers.runtime_ready import (
    make_runtime_ready_handler,
)
from wizard.sethlans_wizard.handlers.static_files import (
    make_index_handler,
    make_static_handler,
)
from wizard.sethlans_wizard.handlers.topology import make_topology_handler
from wizard.sethlans_wizard.router import Router

# Frontend static-file root (FR-W-FE2). The wizard repo layout puts
# ``wizard/frontend/static/`` two levels above this module
# (``wizard/sethlans_wizard/server.py`` → up to ``wizard/`` → into
# ``frontend/static/``).
STATIC_ROOT = (
    Path(__file__).resolve().parent.parent / "frontend" / "static"
)

logger = logging.getLogger(__name__)

# FR-W3 loopback port-range constants. Public so A6 / launcher can
# import them when wiring the loopback scan logic. Issue #170 moved
# the wizard off the public TLS port (8100, now Caddy's) onto a
# separate loopback range so the two listeners don't clash.
DEFAULT_WIZARD_PORT = 8099
PORT_SCAN_RANGE = (8099, 8101, 8102, 8103, 8104)
WIZARD_PORT_ENV = "SETHLANS_WIZARD_PORT"

# FR-W12 — Waitress thread count.
WAITRESS_THREADS = 4

# Bind address per issue #170: loopback only — Caddy fronts the
# wizard for public reachability.
WIZARD_BIND_HOST = "127.0.0.1"


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
# create_app — public WSGI factory
# ---------------------------------------------------------------------

def create_app(
    data_dir: Path,
    setup_token: bytes,
    ipc_secret: bytes,
    wizard_port: int = 0,
) -> Callable:
    """Return the wizard's top-level WSGI app.

    Wires the four wizard endpoints (auth, topology, done,
    runtime-ready). All other paths return a JSON 404. *wizard_port*
    is embedded in the ``.wizard_done`` marker payload per FR-IPC1;
    defaults to 0 so the app can be built before the listener binds
    (A6's startup glue passes the resolved port).
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(setup_token, (bytes, bytearray)) or not setup_token:
        raise ValueError("setup_token must be non-empty bytes")
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")

    secret_bytes = bytes(ipc_secret)
    router = Router()
    # FR-W14 cold-boot probe (issue #160). Anonymous, exact-match,
    # registered first so it can never be shadowed by a static mount.
    router.add("/api/health/", make_health_handler())
    router.add("/api/wizard/auth/", make_auth_handler(bytes(setup_token)))
    router.add("/api/wizard/topology/", make_topology_handler(data_dir))
    router.add(
        "/api/wizard/done/",
        make_done_handler(data_dir, secret_bytes, wizard_port=wizard_port),
    )
    router.add(
        "/api/wizard/runtime-ready/",
        make_runtime_ready_handler(data_dir, secret_bytes),
    )
    router.add(
        "/api/wizard/launcher-log-path/",
        make_launcher_log_path_handler(data_dir),
    )
    # Frontend pages and vendored assets (B2 / FR-W-FE2). Static routes
    # are prefix-matched; API routes above keep their exact-match
    # semantics so a stray ``/api/wizard/auth/extra`` cannot collide
    # with a real handler. The /static/js/ mount carries per-page
    # scripts split out of the HTML files (FR-W-FE7, Phase F2).
    for subdir in ("vendor", "css", "js"):
        prefix = f"/static/{subdir}/"
        router.add(
            prefix,
            make_static_handler(STATIC_ROOT / subdir, prefix),
            exact=False,
        )
    router.add("/", make_index_handler(STATIC_ROOT))
    router.add(
        "/topology",
        make_index_handler(STATIC_ROOT, "topology.html"),
    )
    router.add(
        "/redirecting",
        make_index_handler(STATIC_ROOT, "redirecting.html"),
    )

    def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return router.dispatch(environ, start_response)

    # Stash router + config so tests can introspect; bytes never logged.
    app._router = router  # type: ignore[attr-defined]
    app._data_dir = data_dir  # type: ignore[attr-defined]
    app._ipc_secret = secret_bytes  # type: ignore[attr-defined]
    app._wizard_port = int(wizard_port)  # type: ignore[attr-defined]
    return app


# ---------------------------------------------------------------------
# run — Waitress launcher (plain HTTP, loopback)
# ---------------------------------------------------------------------

def run(app: Callable, host: str, port: int) -> None:
    """Run Waitress on the current thread, plain HTTP, loopback only.

    Blocks until the Waitress event loop exits. The launcher (A6) is
    expected to call this on a dedicated thread or to treat the wizard
    as the main process. The wizard's polite-shutdown sequence
    (FR-W17, A4) calls ``server.close()`` on the returned-and-stashed
    server reference; A3 keeps the runtime path minimal.

    Issue #170: TLS termination has moved to the launcher's Caddy
    supervisor. Waitress no longer needs a TLS listener — the wizard
    listens plain HTTP on loopback and Caddy reverse-proxies to it.
    """
    logger.info(
        "Wizard Waitress listener bound on http://%s:%d/ (threads=%d)",
        host, port, WAITRESS_THREADS,
    )
    server = waitress.create_server(
        app,
        host=host,
        port=port,
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


def shutdown_server() -> None:
    """Idempotently close the live waitress server, if any.

    Safe to call before the server is bound (no-op), from any thread,
    and multiple times. Used by the FR-W17 polite-shutdown sequence:
    the response-wrapper grace timer (close()-hook), the
    ``.wizard_reject`` polling thread, and the 5-minute failsafe timer
    all funnel through this helper so the call site stays simple.
    """
    srv = _SERVER_REF.get()
    if srv is None:
        return
    try:
        srv.close()
    except Exception as exc:  # noqa: BLE001 — defensive; close races
        logger.warning("server.close() raised during shutdown: %s", exc)


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
    "shutdown_server",
]
