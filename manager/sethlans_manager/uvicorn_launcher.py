# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Uvicorn launch helpers for the manager.

Split out of ``run_manager.py`` so the entry script can stay under the
300-line file ceiling now that it runs two listeners in parallel (main
HTTPS + loopback plaintext for the tray helper).

See ``development/specs/tray-helper-unified.md`` FR-22 / FR-22a for
why there are two listeners.
"""

from __future__ import annotations

import asyncio
import ssl
import sys
from pathlib import Path
from typing import Callable

import uvicorn


def launch(
    host: str,
    port: int | str,
    cert_path: Path,
    key_path: Path,
    dev_mode: bool,
    manager_dir: Path,
    get_loopback_port: Callable[[], str],
) -> None:
    """Hand control to uvicorn.

    Production mode runs TWO listeners in the same process via
    ``asyncio.gather``:

    1. Main listener  -- HTTPS on ``<host>:<port>`` serving
       ``sethlans_manager.asgi:application`` (full site).
    2. Loopback  -- plaintext HTTP on ``127.0.0.1:<loopback_port>``
       serving ``sethlans_manager.asgi_loopback:application``
       (only ``/api/status/public/``).

    Dev mode (``--dev``, uvicorn hot reload) keeps the single-server
    path because uvicorn's reloader is not compatible with a
    multi-server ``asyncio.gather`` setup without reimplementing the
    reload supervisor.  The tray helper is not exercised under dev, so
    we print a notice and skip the loopback listener.

    On Windows, force ``SelectorEventLoop`` for both listeners to dodge
    the Proactor socket leak (GitHub #77).
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
    if sys.platform == "win32":
        _install_selector_policy()

    main_config = uvicorn.Config(
        "sethlans_manager.asgi:application",
        host=host,
        port=int(port),
        **ssl_common,
    )
    loopback_config = uvicorn.Config(
        "sethlans_manager.asgi_loopback:application",
        host="127.0.0.1",
        port=loopback_port,
    )
    main_server = uvicorn.Server(main_config)
    loopback_server = uvicorn.Server(loopback_config)
    print(
        f"[INFO] Loopback status listener on "
        f"http://127.0.0.1:{loopback_port}/api/status/public/",
    )

    async def _serve_both() -> None:
        await asyncio.gather(
            main_server.serve(),
            loopback_server.serve(),
        )

    asyncio.run(_serve_both())


def _install_selector_policy() -> None:
    """Install a Windows event-loop policy producing SelectorEventLoops.

    ``uvicorn.Config.loop`` is honoured only when uvicorn itself creates
    the event loop (i.e. ``uvicorn.run``).  When we run both servers via
    ``asyncio.run`` / ``asyncio.gather`` we create the loop ourselves,
    so the policy must be installed up front.
    """
    from sethlans_manager.event_loop_factory import (
        new_selector_event_loop,
    )

    class _SelectorPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            return new_selector_event_loop()

    asyncio.set_event_loop_policy(_SelectorPolicy())
