# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for Phase 2 of the waitress-migration-manager spec.

These tests exercise the split-listener behaviour via Django's test
client combined with ``UrlconfOriginMiddleware``: requests whose
``SERVER_PORT`` matches the loopback port resolve against the loopback
URLconf, every other port resolves against the main URLconf, and any
unknown port fails closed with HTTP 500.

The tests also enforce two invariants that must hold for the Phase 2
test gate:

1. Static grep audit — the files in the Waitress request path must not
   introduce any ``asyncio.get_event_loop`` / ``new_event_loop`` /
   ``set_event_loop`` calls that would crash on a Waitress worker
   thread.
2. Fresh-thread dynamic audit — a request driven through a spawned
   thread with ``asyncio.get_event_loop`` monkey-patched to raise must
   still succeed against the loopback URLconf.  If any code in the
   Waitress-served request path tried to resolve the current thread's
   event loop, the patched function would raise and the test would
   fail.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from django.test import Client

# Repo root, derived from the manager directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Header-injection / port-branching regressions --------------------

@pytest.mark.django_db
class TestHeaderInjectionRegressions:
    """The middleware must not trust X-Forwarded-* headers."""

    def test_x_forwarded_port_does_not_switch_urlconf(self):
        """Main-listener request with X-Forwarded-Port=<loopback> must
        still get a 404 on /api/status/public/.  Django's SERVER_PORT
        comes from the socket, not the header, because we pinned
        ``USE_X_FORWARDED_PORT = False``."""
        client = Client()
        resp = client.get(
            "/api/status/public/",
            HTTP_X_FORWARDED_PORT=str(
                __import__("django.conf", fromlist=["settings"])
                .settings.WAITRESS_LOOPBACK_PORT_INTERNAL
            ),
        )
        assert resp.status_code == 404

    def test_x_forwarded_host_does_not_switch_urlconf(self):
        client = Client()
        resp = client.get(
            "/api/status/public/",
            HTTP_X_FORWARDED_HOST="loopback.example.com",
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestMiddlewareFailsClosedOnUnknownPort:
    """Unknown SERVER_PORT -> HTTP 500 via UrlconfOriginMiddleware.

    Requires ``WAITRESS_LOOPBACK_PORT_PUBLIC`` to be set — otherwise the
    middleware tolerates any non-internal port as "public" (Phase 2
    tolerance mode).  We force a concrete public port here via
    ``@pytest.mark.urls``-adjacent settings override.
    """

    def test_unknown_server_port_returns_500(self, settings):
        # Populate both ports so the middleware has a strict set to
        # validate against. Re-instantiate the middleware so the eager
        # init picks up the overridden values.
        settings.WAITRESS_LOOPBACK_PORT_PUBLIC = 8080
        from sethlans_manager.middleware.urlconf_origin import (
            UrlconfOriginMiddleware,
        )
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get("/api/status/public/", SERVER_PORT="65535")

        called = []

        def _inner(r):
            called.append(r)
            from django.http import HttpResponse
            return HttpResponse("should not be called")

        mw = UrlconfOriginMiddleware(_inner)
        resp = mw(req)
        assert resp.status_code == 500
        assert called == []


@pytest.mark.django_db
class TestLoopbackPortSwitchesUrlconf:
    """Requests to the loopback port are served via urls_loopback."""

    def test_loopback_server_port_resolves_status_public(self, settings):
        loopback_port = str(settings.WAITRESS_LOOPBACK_PORT_INTERNAL)
        client = Client()
        resp = client.get(
            "/api/status/public/",
            SERVER_PORT=loopback_port,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {
            "boot_id",
            "version",
            "setup_mode",
            "workers_online",
            "jobs_queued",
            "jobs_rendering",
        }

    def test_loopback_server_port_other_paths_return_404(
        self, settings,
    ):
        """A path valid under the main URLconf but not the loopback
        URLconf must 404 when the loopback port is used."""
        loopback_port = str(settings.WAITRESS_LOOPBACK_PORT_INTERNAL)
        client = Client()
        # /api/projects/ would be valid on the public listener but is
        # not registered in urls_loopback; the middleware pins the
        # loopback URLconf so this path must 404 here.
        resp = client.get(
            "/api/projects/",
            SERVER_PORT=loopback_port,
        )
        assert resp.status_code == 404


# --- Static grep audit -----------------------------------------------

class TestNoAsyncioEventLoopCallsInWaitressPath:
    """The Waitress loopback handler executes on a non-main thread; any
    call to ``asyncio.get_event_loop``/``new_event_loop``/
    ``set_event_loop`` in the request path would crash on Python 3.14+
    (no implicit per-thread loop creation).

    Files audited are exactly those listed in
    ``waitress-migration-manager.md`` Phase 2 Coexistence Rules.
    """

    _AUDITED_FILES = (
        "manager/workers/views/status_public.py",
        "manager/sethlans_manager/middleware/setup_gate.py",
        "manager/workers/services/sentinel.py",
        "shared/frozen_paths.py",
    )
    _FORBIDDEN = re.compile(
        r"asyncio\.(get_event_loop|new_event_loop|set_event_loop)\("
    )

    def test_audited_files_have_no_event_loop_calls(self):
        hits: list[tuple[str, int, str]] = []
        for rel in self._AUDITED_FILES:
            path = _REPO_ROOT / rel
            assert path.exists(), f"audited file missing: {path}"
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1,
            ):
                if self._FORBIDDEN.search(line):
                    hits.append((rel, lineno, line.strip()))
        assert hits == [], (
            "asyncio event-loop calls found in Waitress request path: "
            f"{hits}"
        )


# --- Fresh-thread dynamic audit --------------------------------------

@pytest.mark.django_db
@pytest.mark.urls("sethlans_manager.urls_loopback")
class TestFreshThreadAsyncioAudit:
    """Spawn a Python thread, monkey-patch ``asyncio.get_event_loop`` to
    raise, then issue a request to the loopback URLconf.  If any code
    in the request path tries to resolve the current thread's event
    loop, the patched function will raise and the test will fail."""

    def test_request_succeeds_with_asyncio_get_event_loop_patched(self):
        import asyncio as _asyncio

        original = _asyncio.get_event_loop
        errors: list[Exception] = []
        results: list[int] = []

        def _boom(*_a, **_kw):
            raise RuntimeError(
                "asyncio.get_event_loop called from Waitress request "
                "path — Phase 2 coexistence rule violated."
            )

        def _worker():
            try:
                _asyncio.get_event_loop = _boom
                try:
                    client = Client()
                    resp = client.get("/api/status/public/")
                    results.append(resp.status_code)
                finally:
                    _asyncio.get_event_loop = original
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        t = threading.Thread(
            target=_worker,
            name="phase2-fresh-thread-audit",
            daemon=True,
        )
        t.start()
        t.join(timeout=10.0)
        assert not t.is_alive(), "audit thread did not exit"
        assert errors == [], f"audit thread raised: {errors}"
        assert results == [200]
