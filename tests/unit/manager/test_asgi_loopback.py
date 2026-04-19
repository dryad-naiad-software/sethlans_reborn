# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.asgi_loopback`` (FR-22 / FR-22a).

The loopback ASGI app pins ``request.urlconf`` to the loopback URLconf
so every request dispatched through it can only resolve paths defined
in ``sethlans_manager.urls_loopback``.
"""

from __future__ import annotations

import asyncio


class TestAsgiLoopbackModule:

    def test_importable(self):
        import sethlans_manager.asgi_loopback as mod
        assert mod is not None
        assert hasattr(mod, "application")

    def test_urlconf_attribute_pins_loopback(self, mocker):
        """``create_request`` sets ``request.urlconf`` on every request."""
        from sethlans_manager.asgi_loopback import _LoopbackASGIHandler

        handler = _LoopbackASGIHandler()

        # Fake request and error_response.
        fake_request = mocker.MagicMock()
        fake_request.urlconf = None
        # Patch the super().create_request to return our fake.
        mocker.patch(
            "django.core.handlers.asgi.ASGIHandler.create_request",
            return_value=(fake_request, None),
        )

        scope = {"type": "http", "method": "GET", "path": "/x"}
        body_file = object()
        req, err = handler.create_request(scope, body_file)

        assert req is fake_request
        assert err is None
        assert req.urlconf == "sethlans_manager.urls_loopback"

    def test_urlconf_not_set_when_super_returns_none(self, mocker):
        from sethlans_manager.asgi_loopback import _LoopbackASGIHandler
        handler = _LoopbackASGIHandler()
        # super returns (None, error_response) — don't touch urlconf.
        err = object()
        mocker.patch(
            "django.core.handlers.asgi.ASGIHandler.create_request",
            return_value=(None, err),
        )
        req, error_response = handler.create_request({}, None)
        assert req is None
        assert error_response is err


class TestAsgiLifespan:

    def test_lifespan_startup_and_shutdown_no_op(self):
        from sethlans_manager.asgi_loopback import application

        messages_sent = []
        events = iter([
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ])

        async def receive():
            return next(events)

        async def send(msg):
            messages_sent.append(msg)

        scope = {"type": "lifespan"}
        asyncio.run(application(scope, receive, send))

        assert {"type": "lifespan.startup.complete"} in messages_sent
        assert {"type": "lifespan.shutdown.complete"} in messages_sent
