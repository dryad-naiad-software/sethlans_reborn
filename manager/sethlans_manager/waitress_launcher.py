# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Waitress launch orchestration for the Sethlans Manager (Phase 5).

Phase 5 of ``development/specs/waitress-migration-manager.md`` wired
the manager's serving path to Waitress+WSGI with two loopback
listeners:

* ``127.0.0.1:<public_port>``   — public-origin, fronted by Caddy's
  public TLS vhost.
* ``127.0.0.1:<internal_port>`` — internal-origin, fronted by Caddy's
  loopback plaintext vhost (tray helper route).

The two listeners share the same Django WSGI application; the
``UrlconfOriginMiddleware`` (installed very early in ``MIDDLEWARE``)
pins ``request.urlconf`` to ``urls_loopback`` when the incoming
``SERVER_PORT`` matches the internal listener.

Threading model:

* Each listener runs in a dedicated daemon thread.
* The main thread blocks on a ``threading.Event`` that is set by
  ``SIGINT`` / ``SIGTERM`` handlers, by a listener-thread crash, or by
  an explicit call to :func:`stop_waitress_listeners` (tests).
* On shutdown, ``server.close()`` is called on both listeners and each
  thread is joined with a bounded timeout.

Waitress itself is started with ``install_signal_handlers=False``
(launcher owns signals) and ``trusted_proxy`` left unset (explicit —
no ``X-Forwarded-*`` header trust; this pins the port-detection the
urlconf-origin middleware relies on).
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

import waitress

from sethlans_manager.waitress_config import get_waitress_tuning

logger = logging.getLogger(__name__)

# Module-level state so signal handlers (and tests) can drive the
# listeners from outside ``launch()``.
_servers: list = []
_threads: list = []
_shutdown_event = threading.Event()


def _listener_thread_main(server, label: str) -> None:
    """Entry point for a Waitress listener thread.

    Logs unhandled exceptions and sets the module-level shutdown event
    so the main thread unblocks if one of the listeners crashes.
    """
    try:
        server.run()
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"[ERROR] Waitress {label} listener crashed: {exc}",
            file=sys.stderr,
        )
        _shutdown_event.set()


def _install_signal_handlers() -> None:
    """Install ``SIGTERM`` / ``SIGINT`` handlers that trigger shutdown.

    Waitress is spawned with ``install_signal_handlers=False`` so this
    process (or the launcher that supervises it) owns signal handling.
    On POSIX, ``SIGTERM`` and ``SIGINT`` both trigger graceful shutdown.
    On Windows, ``SIGINT`` is reliably deliverable; ``SIGTERM`` is best
    effort (the launcher's Job-Object contract handles the actual
    termination when the user-mode handler cannot run).
    """
    def _handler(signum, _frame):  # pragma: no cover - exercised via kill
        print(
            f"\n[INFO] Received signal {signum}; stopping Waitress "
            f"listeners...",
            file=sys.stderr,
        )
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass


def stop_waitress_listeners(join_timeout: float = 10.0) -> None:
    """Close both Waitress listeners and join their threads.

    Safe to call multiple times — subsequent calls are no-ops against
    the already-drained module-level lists.
    """
    for srv in _servers:
        try:
            srv.close()
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"[WARNING] Error closing Waitress server: {exc}",
                file=sys.stderr,
            )
    for th in _threads:
        th.join(timeout=join_timeout)
        if th.is_alive():
            print(
                f"[WARNING] Waitress thread {th.name!r} did not exit "
                f"within {join_timeout}s; continuing shutdown.",
                file=sys.stderr,
            )
    _servers.clear()
    _threads.clear()


def launch(
    public_port: int,
    internal_port: int,
    ini_path: Path,
) -> None:
    """Start two Waitress listeners and block on the shutdown event.

    :param public_port: Public-origin plaintext port (Caddy public vhost
        proxies here).
    :param internal_port: Internal-origin plaintext port (Caddy loopback
        vhost proxies here).
    :param ini_path: Path to ``manager.ini`` — used to resolve tuning
        overrides (see :mod:`sethlans_manager.waitress_config`).
    """
    from sethlans_manager.wsgi import application as wsgi_app

    tuning = get_waitress_tuning(ini_path)

    # Let Caddy's ``X-Forwarded-Proto`` header reach Django so
    # ``SECURE_PROXY_SSL_HEADER`` can resolve the original scheme
    # to HTTPS and DRF's FileField builds correct absolute URLs
    # (media/asset downloads).
    #
    # Waitress 3.x defaults to ``clear_untrusted_proxy_headers=True``,
    # which strips every ``X-Forwarded-*`` header when no
    # ``trusted_proxy`` is configured. We can't USE ``trusted_proxy``
    # here: Waitress normalises ``SERVER_PORT`` to 443/80 based on
    # the forwarded scheme, which would shatter
    # ``UrlconfOriginMiddleware``'s port-based URLconf split.
    #
    # Disabling the untrusted-header scrub keeps Waitress's socket-
    # derived ``SERVER_PORT`` intact AND lets the forwarded proto
    # through for Django to interpret. Safety: Waitress binds
    # ``127.0.0.1`` only, so the only source of these headers is
    # Caddy (our sole upstream); no external client can reach the
    # listener directly. Caddy's ``reverse_proxy`` also strips any
    # incoming ``X-Forwarded-*`` from external requests before
    # setting its own trusted values.
    proxy_kwargs = {
        "clear_untrusted_proxy_headers": False,
    }

    public_server = waitress.create_server(
        wsgi_app,
        host="127.0.0.1",
        port=public_port,
        ident="sethlans-manager-public",
        **proxy_kwargs,
        **tuning,
    )
    internal_server = waitress.create_server(
        wsgi_app,
        host="127.0.0.1",
        port=internal_port,
        ident="sethlans-manager-loopback",
        **proxy_kwargs,
        **tuning,
    )
    _servers.extend([public_server, internal_server])

    public_thread = threading.Thread(
        target=_listener_thread_main,
        args=(public_server, "public"),
        name="manager-waitress-public",
        daemon=True,
    )
    internal_thread = threading.Thread(
        target=_listener_thread_main,
        args=(internal_server, "loopback"),
        name="manager-waitress-loopback",
        daemon=True,
    )
    _threads.extend([public_thread, internal_thread])

    public_thread.start()
    internal_thread.start()

    print(
        f"[INFO] Public Waitress listener on "
        f"http://127.0.0.1:{public_port}/ (Caddy terminates TLS)",
    )
    print(
        f"[INFO] Loopback Waitress listener on "
        f"http://127.0.0.1:{internal_port}/api/status/public/",
    )

    _install_signal_handlers()

    try:
        # Block until a signal handler or a listener-thread crash sets
        # the event. ``wait()`` releases the GIL so the listener threads
        # can actually run.
        _shutdown_event.wait()
    finally:
        stop_waitress_listeners()
