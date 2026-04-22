# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``UrlconfOriginMiddleware`` (waitress-migration-manager
spec, Phase 2).

The middleware reads ``request.META['SERVER_PORT']`` at request time and
pins ``request.urlconf`` accordingly:

* ``SERVER_PORT == WAITRESS_LOOPBACK_PORT_INTERNAL`` -> loopback URLconf.
* ``SERVER_PORT == WAITRESS_LOOPBACK_PORT_PUBLIC`` (or public unset)
  -> leave ROOT_URLCONF in effect.
* Unknown port -> HTTP 500 (fail closed).
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from sethlans_manager.middleware.urlconf_origin import (
    UrlconfOriginMiddleware,
    _LOOPBACK_URLCONF,
)


@pytest.fixture
def rf():
    return RequestFactory()


class _Inner:
    """Captures the request urlconf pinned by the middleware before
    forwarding to a trivial 200 OK response."""

    def __init__(self):
        self.seen_urlconf = object()  # sentinel: not yet called

    def __call__(self, request):
        self.seen_urlconf = getattr(request, "urlconf", None)
        return HttpResponse("ok")


class TestUrlconfOriginMiddlewareEagerInit:
    """``__init__`` must cache settings as strings for fast comparison."""

    def test_internal_port_normalised_to_string(self, settings):
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        mw = UrlconfOriginMiddleware(lambda r: HttpResponse("ok"))
        assert mw._internal_port == "8088"
        assert mw._public_port == "8080"

    def test_public_port_none_allowed(self, settings):
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = None
        mw = UrlconfOriginMiddleware(lambda r: HttpResponse("ok"))
        assert mw._internal_port == "8088"
        assert mw._public_port is None

    def test_missing_internal_port_yields_none(self, settings):
        # Defense: if someone deletes the setting, the middleware
        # caches None and no request will match the internal branch.
        del settings.WAITRESS_LOOPBACK_PORT_INTERNAL
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        mw = UrlconfOriginMiddleware(lambda r: HttpResponse("ok"))
        assert mw._internal_port is None


class TestUrlconfOriginMiddlewareCall:
    """``__call__`` pins urlconf based on SERVER_PORT."""

    def test_loopback_port_pins_loopback_urlconf(self, rf, settings):
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        inner = _Inner()
        mw = UrlconfOriginMiddleware(inner)

        req = rf.get("/api/status/public/")
        req.META["SERVER_PORT"] = "8088"

        resp = mw(req)
        assert resp.status_code == 200
        assert inner.seen_urlconf == _LOOPBACK_URLCONF

    def test_public_port_leaves_urlconf_unset(self, rf, settings):
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        inner = _Inner()
        mw = UrlconfOriginMiddleware(inner)

        req = rf.get("/api/projects/")
        req.META["SERVER_PORT"] = "8080"

        resp = mw(req)
        assert resp.status_code == 200
        # urlconf untouched -> attribute never set on the request.
        assert inner.seen_urlconf is None

    def test_unknown_port_returns_500(self, rf, settings):
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        inner = _Inner()
        mw = UrlconfOriginMiddleware(inner)

        req = rf.get("/api/status/public/")
        req.META["SERVER_PORT"] = "9999"

        # Remember the sentinel object identity so we can prove the
        # inner view was skipped.
        sentinel = inner.seen_urlconf
        resp = mw(req)
        assert resp.status_code == 500
        # Inner view must not have been called — the sentinel remains.
        assert inner.seen_urlconf is sentinel

    def test_public_port_unset_treats_all_non_internal_as_public(
        self, rf, settings,
    ):
        """Phase 2 tolerance: WAITRESS_LOOPBACK_PORT_PUBLIC may be None."""
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = 8088
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = None
        inner = _Inner()
        mw = UrlconfOriginMiddleware(inner)

        req = rf.get("/api/projects/")
        req.META["SERVER_PORT"] = "8080"  # could be anything != 8088
        resp = mw(req)
        assert resp.status_code == 200
        assert inner.seen_urlconf is None

    def test_loopback_port_string_setting_still_matches(
        self, rf, settings,
    ):
        """Settings sourced from INI arrive as strings pre-coercion;
        the middleware must still compare cleanly."""
        settings.WAITRESS_LOOPBACK_PORT_INTERNAL = "8088"
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = "8080"
        inner = _Inner()
        mw = UrlconfOriginMiddleware(inner)

        req = rf.get("/api/status/public/")
        req.META["SERVER_PORT"] = "8088"
        resp = mw(req)
        assert resp.status_code == 200
        assert inner.seen_urlconf == _LOOPBACK_URLCONF


class TestUrlconfOriginMiddlewareInstalledInSettings:
    """Regression: the middleware is wired into ``MIDDLEWARE`` BEFORE
    SetupGateMiddleware so that setup-gate sees the correct urlconf."""

    def test_middleware_present_and_before_setup_gate(self):
        mw = list(settings.MIDDLEWARE)
        origin = (
            "sethlans_manager.middleware.urlconf_origin."
            "UrlconfOriginMiddleware"
        )
        gate = (
            "sethlans_manager.middleware.setup_gate.SetupGateMiddleware"
        )
        assert origin in mw
        assert gate in mw
        assert mw.index(origin) < mw.index(gate)

    def test_x_forwarded_headers_disabled(self):
        """Header-injection defense — these must stay False so the
        middleware's SERVER_PORT check can't be bypassed."""
        assert getattr(settings, "USE_X_FORWARDED_PORT", None) is False
        assert getattr(settings, "USE_X_FORWARDED_HOST", None) is False

    def test_loopback_port_setting_present(self):
        assert hasattr(settings, "WAITRESS_LOOPBACK_PORT_INTERNAL")
        # Must coerce to int.
        assert int(settings.WAITRESS_LOOPBACK_PORT_INTERNAL) > 0
