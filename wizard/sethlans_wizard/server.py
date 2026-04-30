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

Phase 1 (Spec 2) extracted route registration into
:mod:`wizard.sethlans_wizard.routes` once new step handlers pushed
this module over the 300-line ceiling.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

import waitress

from wizard.sethlans_wizard.router import Router
from wizard.sethlans_wizard.routes import register_routes

# Frontend static-file root (FR-W-FE2): ``wizard/frontend/static/``.
STATIC_ROOT = (
    Path(__file__).resolve().parent.parent / "frontend" / "static"
)

logger = logging.getLogger(__name__)

# FR-W3 loopback port-range constants (issue #170: 8100 belongs to Caddy).
DEFAULT_WIZARD_PORT = 8099
PORT_SCAN_RANGE = (8099, 8101, 8102, 8103, 8104)
WIZARD_PORT_ENV = "SETHLANS_WIZARD_PORT"

# FR-W12 — Waitress thread count.
WAITRESS_THREADS = 4

# Issue #170: bind loopback only; Caddy fronts public reachability.
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

    Wires the wizard endpoints (auth, topology, network, database,
    admin-user, worker-password, verify, pending-setup, done,
    runtime-ready) plus the static-file mounts. All other paths return
    a JSON 404. *wizard_port* is embedded in the ``.wizard_done``
    marker payload per FR-IPC1; defaults to 0 so the app can be built
    before the listener binds (A6's startup glue passes the resolved
    port).
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(setup_token, (bytes, bytearray)) or not setup_token:
        raise ValueError("setup_token must be non-empty bytes")
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")

    secret_bytes = bytes(ipc_secret)
    router = Router()
    register_routes(
        router,
        data_dir=data_dir,
        setup_token=bytes(setup_token),
        ipc_secret=secret_bytes,
        wizard_port=int(wizard_port),
        static_root=STATIC_ROOT,
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

# Issue #176 — bound for waitress shutdown wait. ``server.close()``
# does NOT always cause ``server.run()`` to return (observed 30-minute
# hang in the wild). We bound the join here at 5 seconds, then fall
# through and let the caller force-exit on the second SIGINT.
_RUN_JOIN_TIMEOUT_SECONDS = 5.0


def run(app: Callable, host: str, port: int) -> None:
    """Run Waitress, returning on shutdown event or escalation.

    Issue #176 fix: ``waitress.server.run()`` runs on a daemon thread;
    the calling thread waits on :data:`_SHUTDOWN_EVENT` (set by the
    signal handler, FR-W17 polite-shutdown paths, or the runtime_ready
    close-hook). On wake we call ``server.close()`` and ``join()`` for
    up to :data:`_RUN_JOIN_TIMEOUT_SECONDS`; if the thread is still
    alive we return and let the caller force-exit.

    Must be called on the main thread so OS signal delivery lands on
    the right thread (Python routes signals to main only).

    Issue #170: TLS termination is in Caddy; this binds plain HTTP on
    loopback. Issue #175: ``trusted_proxy`` allows the X-Forwarded-Proto
    header from Caddy through to handlers.
    """
    logger.info(
        "Wizard Waitress listener bound on http://%s:%d/ (threads=%d)",
        host, port, WAITRESS_THREADS,
    )
    srv = waitress.create_server(
        app,
        host=host,
        port=port,
        threads=WAITRESS_THREADS,
        ident="sethlans-wizard",
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-proto"},
    )
    _SERVER_REF.set(srv)
    # Reset the event so a previous run's shutdown doesn't pre-arm us.
    _SHUTDOWN_EVENT.clear()

    def _serve() -> None:
        try:
            srv.run()
        except Exception:  # noqa: BLE001 — daemon must surface, not swallow
            logger.exception("Waitress run loop raised; signalling shutdown")
            _SHUTDOWN_EVENT.set()

    runner = threading.Thread(
        target=_serve, name="wizard-waitress", daemon=True,
    )
    runner.start()
    try:
        # Issue #176: poll the event in short slices instead of one
        # blocking wait(). Windows' threading.Event.wait() can absorb
        # signals when implemented as a non-alertable wait — polling
        # forces a bytecode boundary every slice so the signal handler
        # actually runs and the next wait observes the set event.
        while not _SHUTDOWN_EVENT.wait(timeout=0.5):
            pass
        try:
            srv.close()
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("server.close() raised on shutdown: %s", exc)
        runner.join(timeout=_RUN_JOIN_TIMEOUT_SECONDS)
        if runner.is_alive():
            logger.warning(
                "Waitress run loop did not exit within %.1fs; "
                "caller must force-exit", _RUN_JOIN_TIMEOUT_SECONDS,
            )
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

# Issue #176 — shutdown event. ``run()`` waits on this; the signal
# handler, FR-W17 polite-shutdown paths, and the runtime_ready
# close-hook all set it via request_shutdown/shutdown_server.
_SHUTDOWN_EVENT = threading.Event()


def get_server_ref() -> _ServerRef:
    """Return the singleton :class:`_ServerRef` (for A4)."""
    return _SERVER_REF


def get_shutdown_event() -> threading.Event:
    """Return the shutdown event ``run()`` waits on (for A4 / tests)."""
    return _SHUTDOWN_EVENT


def request_shutdown() -> None:
    """Wake any in-flight :func:`run` and ask it to exit. Idempotent.

    Issue #176: setting the event is what actually returns control to
    the main thread; a bare ``server.close()`` from a signal handler
    has been observed to hang waitress for 30+ minutes.
    """
    _SHUTDOWN_EVENT.set()


def shutdown_server() -> None:
    """Idempotently close the live waitress server (if any) + signal exit.

    Safe to call before bind (no-op), from any thread, multiple times.
    Used by the FR-W17 polite-shutdown sequence (grace timer, reject
    poller, failsafe). Issue #176: also sets :data:`_SHUTDOWN_EVENT`
    so the main-thread ``run()`` ``.wait()`` returns — a bare
    ``server.close()`` does NOT reliably break the waitress accept loop.
    """
    _SHUTDOWN_EVENT.set()
    srv = _SERVER_REF.get()
    if srv is None:
        return
    try:
        srv.close()
    except Exception as exc:  # noqa: BLE001 — defensive; close races
        logger.warning("server.close() raised during shutdown: %s", exc)


def reset_shutdown_event_for_tests() -> None:
    """Clear :data:`_SHUTDOWN_EVENT`. Test-only."""
    _SHUTDOWN_EVENT.clear()


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
    "get_shutdown_event",
    "request_shutdown",
    "shutdown_server",
    "reset_shutdown_event_for_tests",
]
