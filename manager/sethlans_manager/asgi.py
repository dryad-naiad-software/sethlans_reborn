# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
ASGI config for sethlans_manager project.

Exposes the ASGI callable as a module-level variable named ``application``.
The application is a lifespan-aware wrapper around the Django ASGI app
so uvicorn's lifespan protocol can drive broadcaster start/stop — see
spec FR-7 for the full rationale.

For the standard Django ASGI entry point documentation see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
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

# Imported after Django is initialised.
from sethlans_manager import runtime_state  # noqa: E402
from workers.multicast_broadcaster import (  # noqa: E402
    MulticastBroadcaster,
)

logger = logging.getLogger(__name__)

_broadcaster: MulticastBroadcaster | None = None


async def _on_startup() -> None:
    """Lifespan startup hook: start the multicast broadcaster.

    Skipped in the uvicorn ``--dev`` reloader parent so only the reload
    child runs a broadcaster.  Also a no-op when ``runtime_state`` has
    not been populated yet (e.g., an incomplete startup) — the enroll
    view will return 503 until ``run_manager.py`` seeds the values.
    """
    global _broadcaster
    if (
        os.environ.get("SETHLANS_DEV_MODE") == "1"
        and os.environ.get("RUN_MAIN") != "true"
    ):
        return
    if runtime_state.manager_id is None:
        logger.warning(
            "Broadcaster not started: runtime_state.manager_id is None"
        )
        return
    _broadcaster = MulticastBroadcaster(
        manager_id=runtime_state.manager_id,
        name=runtime_state.broadcaster_name or "Sethlans Manager",
        host=runtime_state.broadcaster_host or "",
        ip=runtime_state.broadcaster_ip or "0.0.0.0",
        port=runtime_state.broadcaster_port or 8080,
        version=runtime_state.broadcaster_version or "0.0.0",
    )
    _broadcaster.start()


async def _on_shutdown() -> None:
    """Lifespan shutdown hook: stop and join the broadcaster thread."""
    global _broadcaster
    if _broadcaster is not None:
        _broadcaster.stop()
        _broadcaster.join(timeout=5.0)
        _broadcaster = None


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
