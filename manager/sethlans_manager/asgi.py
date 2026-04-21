# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
ASGI config for sethlans_manager project.

Exposes the ASGI callable as a module-level variable named ``application``.
The application is a lifespan-aware wrapper around the Django ASGI app.

Phase 3 status (manager spec): ``MulticastBroadcaster.start()/.stop()``
has moved to ``launcher/run_launcher.py``; the lifespan hooks below
are reduced to log-only no-ops. The lifespan coroutine inside
``application(...)`` is preserved so uvicorn's lifespan protocol is
still satisfied during the hybrid Phase 3 serving path (uvicorn main
listener + Waitress loopback + Caddy front door). This file is only
**deleted in Phase 7** — keeping it as a no-op here prevents a
double-broadcaster boot (one from launcher + one from asgi.py) and
avoids a no-broadcaster boot if the launcher's start fails.
"""

import logging
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings')

# Initialise Django first so runtime_state and the broadcaster module
# can be imported safely.
_django_app = get_asgi_application()

# Apply our logging config now that Django settings are loaded.  Django's
# built-in bootstrap is disabled (``LOGGING_CONFIG = None`` in settings)
# to avoid referencing ``AdminEmailHandler`` in frozen builds — see
# ``sethlans_manager.logging_config`` for details.
from sethlans_manager.logging_config import configure as _configure_logging  # noqa: E402
_configure_logging()

if os.environ.get('SETHLANS_DEV_MODE') == '1':
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
    _django_app = ASGIStaticFilesHandler(_django_app)

logger = logging.getLogger(__name__)


async def _on_startup() -> None:
    """Lifespan startup hook: broadcaster now owned by launcher.

    Reduced to a log-only no-op in manager spec Phase 3; the
    launcher owns ``MulticastBroadcaster`` lifecycle via its own
    signal-handler wiring. Kept here so uvicorn's lifespan protocol
    receives a ``lifespan.startup.complete`` message as it always
    has. File is deleted in Phase 7.
    """
    logger.debug(
        "asgi lifespan startup: broadcaster now owned by launcher "
        "(manager spec Phase 3; no-op here)."
    )


async def _on_shutdown() -> None:
    """Lifespan shutdown hook: broadcaster now owned by launcher.

    See :func:`_on_startup`. No-op; retained so the lifespan
    protocol round-trips cleanly.
    """
    logger.debug(
        "asgi lifespan shutdown: broadcaster teardown owned by "
        "launcher (manager spec Phase 3; no-op here)."
    )


async def application(scope, receive, send):
    """Lifespan-aware ASGI entry point.

    For ``lifespan`` scope, runs the ``on_startup``/``on_shutdown``
    hooks inline.  For ``http``/``websocket`` scopes, delegates to the
    underlying Django ASGI application.
    """
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await _on_startup()
                except Exception as exc:  # pragma: no cover
                    await send({
                        "type": "lifespan.startup.failed",
                        "message": str(exc),
                    })
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await _on_shutdown()
                finally:
                    await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await _django_app(scope, receive, send)
