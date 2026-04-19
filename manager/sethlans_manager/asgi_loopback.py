# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
ASGI application for the manager's loopback-only status listener.

This app reuses the Django settings of the main listener but pins
``request.urlconf`` to :mod:`sethlans_manager.urls_loopback` so the
resolver only sees ``/api/status/public/``.  Every other path returns
404 from Django's URL resolver — defense in depth if the listener is
ever reachable beyond 127.0.0.1.

The loopback listener is plaintext HTTP (no TLS); network isolation is
provided by the socket bind address, not by any Python-side check.
"""

from __future__ import annotations

import os

from django.core.handlers.asgi import ASGIHandler

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "sethlans_manager.settings",
)


_LOOPBACK_URLCONF = "sethlans_manager.urls_loopback"


class _LoopbackASGIHandler(ASGIHandler):
    """ASGIHandler that pins ``request.urlconf`` to the loopback config.

    ``BaseHandler.resolve_request`` checks for ``request.urlconf`` and,
    when present, resolves against that URL configuration instead of
    ``settings.ROOT_URLCONF`` (Django docs: "How Django processes a
    request" -- urlconf attribute).
    """

    def create_request(self, scope, body_file):
        request, error_response = super().create_request(scope, body_file)
        if request is not None:
            request.urlconf = _LOOPBACK_URLCONF
        return request, error_response


_django_app = _LoopbackASGIHandler()


async def application(scope, receive, send):
    """ASGI entry point for the loopback status listener.

    Lifespan is accepted as a no-op -- the main ASGI app owns
    broadcaster start/stop, and we must not run those hooks a second
    time.
    """
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await _django_app(scope, receive, send)
