# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Uvicorn launch helpers for the manager.

Split out of ``run_manager.py`` so the entry script can stay under the
300-line file ceiling now that it runs two listeners in parallel (main
HTTPS + loopback plaintext for the tray helper).

Phase 2 of the waitress-migration spec
(``development/specs/waitress-migration-manager.md``) replaced the
loopback ASGI listener with a Waitress thread:

* Main listener — uvicorn (ASGI, HTTPS) on ``<host>:<port>``.
* Loopback listener — Waitress (WSGI, plaintext) on
  ``127.0.0.1:<loopback_port>`` serving ``urls_loopback``.

The two listeners share the same Django MIDDLEWARE stack; the
``UrlconfOriginMiddleware`` inserted very early in ``settings.py`` pins
``request.urlconf`` based on which port the request arrived on.

Coexistence rules during Phase 2:

* Waitress is started with ``install_signal_handlers=False`` — uvicorn
  owns SIGTERM / SIGINT.
* Waitress runs in a dedicated Python thread so the main thread stays
  asyncio-owned.
* On uvicorn shutdown we set a module-level ``threading.Event`` which
  the Waitress thread monitors, then ``server.close()`` the Waitress
  listener and join the thread before ``asyncio.run(...)`` returns.

Thumbnail-signal hazard (historical, resolved in Phase 4): the prior
disconnect/connect pattern in ``workers/signal_helpers.py`` was not
thread-safe.  Phase 4 replaced it with a per-thread context manager
(``_skip_thumbnail_signals``) so any route served via Waitress is now
signal-safe.

See ``development/specs/tray-helper-unified.md`` FR-22 / FR-22a for the
historical context of the loopback listener.
"""

from __future__ import annotations

import asyncio
import ssl
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

import uvicorn
import waitress

# Module-level handles so shutdown code (uvicorn signal hooks or the
# test suite) can stop the Waitress listener cleanly.
_waitress_server: Optional[object] = None
_waitress_thread: Optional[threading.Thread] = None
_waitress_shutdown_event: threading.Event = threading.Event()


def launch(
    host: str,
    port: int | str,
    cert_path: Path,
    key_path: Path,
    dev_mode: bool,
    manager_dir: Path,
    get_loopback_port: Callable[[], str],
) -> None:
    """Hand control to uvicorn (plus a Waitress loopback thread).

    Production mode:

    1. Start Waitress in a dedicated thread bound to
       ``127.0.0.1:<loopback_port>`` serving the Django WSGI app.  The
       URLconf is pinned to ``urls_loopback`` by
       ``UrlconfOriginMiddleware`` based on ``SERVER_PORT``.
    2. Start uvicorn on ``<host>:<port>`` serving the full ASGI app.

    Dev mode (``--dev``, uvicorn hot reload) keeps the single-server
    path because uvicorn's reloader is not compatible with a daemon
    Waitress thread in the reloader parent (it would get forked into
    each child process).  The tray helper is not exercised under dev.

    On Windows, force ``SelectorEventLoop`` to dodge the Proactor
    socket leak (GitHub #77).
    """
    ssl_common = {
        "ssl_keyfile": str(key_path),
        "ssl_certfile": str(cert_path),
        "ssl_version": ssl.PROTOCOL_TLS_SERVER,
    }
    extra: dict = {}
    if sys.platform == "win32":
        extra["loop"] = (
            "sethlans_manager.event_loop_factory:new_selector_event_loop"
        )

    if dev_mode:
        print(
            "[INFO] --dev mode: loopback tray-helper listener disabled "
            "(not compatible with uvicorn --reload).",
        )
        uvicorn.run(
            "sethlans_manager.asgi:application",
            host=host,
            port=int(port),
            reload=True,
            reload_dirs=[str(manager_dir)],
            **ssl_common,
            **extra,
        )
        return

    loopback_port = int(get_loopback_port())
    _start_waitress_loopback(loopback_port)

    if sys.platform == "win32":
        _install_selector_policy()

    main_config = uvicorn.Config(
        "sethlans_manager.asgi:application",
        host=host,
        port=int(port),
        **ssl_common,
    )
    main_server = uvicorn.Server(main_config)
    print(
        f"[INFO] Loopback status listener (Waitress) on "
        f"http://127.0.0.1:{loopback_port}/api/status/public/",
    )

    async def _serve_main() -> None:
        try:
            await main_server.serve()
        finally:
            _stop_waitress_loopback()

    try:
        asyncio.run(_serve_main())
    finally:
        # Defensive — asyncio.run raising before the try-finally above
        # still needs to stop the Waitress thread.
        _stop_waitress_loopback()


def _start_waitress_loopback(loopback_port: int) -> None:
    """Start the Waitress loopback server in a dedicated daemon thread.

    The Waitress server serves the full Django WSGI application; it's
    the ``UrlconfOriginMiddleware`` that narrows the URLconf down to
    ``urls_loopback`` based on the incoming ``SERVER_PORT``.

    ``install_signal_handlers=False`` is mandatory — uvicorn owns
    SIGTERM/SIGINT and any Waitress signal handling would race with
    uvicorn's shutdown sequence.  ``trusted_proxy`` is left unset
    (default ``None``) so Waitress refuses to honour any X-Forwarded-*
    header from the client — this mirrors the
    ``USE_X_FORWARDED_PORT = False`` settings pin.
    """
    global _waitress_server, _waitress_thread

    _waitress_shutdown_event.clear()
    from sethlans_manager.wsgi import application as wsgi_app

    _waitress_server = waitress.create_server(
        wsgi_app,
        host="127.0.0.1",
        port=loopback_port,
        threads=4,
        channel_timeout=60,
        connection_limit=200,
        ident="sethlans-manager-loopback",
    )
    _waitress_thread = threading.Thread(
        target=_waitress_run,
        name="manager-loopback-waitress",
        daemon=True,
    )
    _waitress_thread.start()


def _waitress_run() -> None:
    """Entry point for the Waitress loopback thread."""
    assert _waitress_server is not None  # nosec - set by start fn
    try:
        _waitress_server.run()
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"[ERROR] Loopback Waitress thread crashed: {exc}",
            file=sys.stderr,
        )


def _stop_waitress_loopback(join_timeout: float = 5.0) -> None:
    """Signal the Waitress loopback thread to stop and join it.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _waitress_server, _waitress_thread

    if _waitress_server is None:
        return

    _waitress_shutdown_event.set()
    try:
        _waitress_server.close()
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"[WARNING] Error closing loopback Waitress server: {exc}",
            file=sys.stderr,
        )

    if _waitress_thread is not None:
        _waitress_thread.join(timeout=join_timeout)
        if _waitress_thread.is_alive():
            print(
                "[WARNING] Loopback Waitress thread did not exit within "
                f"{join_timeout}s; continuing shutdown.",
                file=sys.stderr,
            )

    _waitress_server = None
    _waitress_thread = None


def _install_selector_policy() -> None:
    """Install a Windows event-loop policy producing SelectorEventLoops.

    ``uvicorn.Config.loop`` is honoured only when uvicorn itself creates
    the event loop (i.e. ``uvicorn.run``).  When we run the main server
    via ``asyncio.run`` we create the loop ourselves, so the policy
    must be installed up front.
    """
    from sethlans_manager.event_loop_factory import (
        new_selector_event_loop,
    )

    class _SelectorPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            return new_selector_event_loop()

    asyncio.set_event_loop_policy(_SelectorPolicy())
