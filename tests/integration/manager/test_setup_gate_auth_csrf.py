# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for GitHub issue #71 (Bug 1).

The Angular setup wizard must be able to prime its CSRF cookie during
setup mode by calling ``GET /api/auth/csrf/``.  The endpoint itself is
anonymous-safe (``@permission_classes([AllowAny])``), but it was
previously blocked with 503 by ``SetupGateMiddleware`` because the path
did not appear in ``_ALLOWED_PREFIXES``.

Regression tests verify:

1. ``GET /api/auth/csrf/`` returns 200 during setup mode.
2. ``GET /api/auth/login/`` still returns 503 (we did NOT whitelist
   ``/api/auth/`` broadly).
3. ``POST /api/auth/csrf/`` is not 503 — middleware must not gate it
   (the view itself returns 405 since it is GET-only).
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from sethlans_manager.middleware import setup_gate

pytestmark = pytest.skip(
    "Obsoleted by setup-auth-unification; replaced in follow-up test phase",
    allow_module_level=True,
)


@pytest.fixture()
def _enter_setup_mode(monkeypatch):
    """Force the gate middleware into setup-mode for a single test."""
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    monkeypatch.setattr(
        "sethlans_manager.middleware.setup_gate._check_sentinel",
        lambda: False,
    )
    yield
    setup_gate._setup_complete = prev


@pytest.mark.django_db
class TestAuthCsrfBypassesSetupGate:
    """``/api/auth/csrf/`` must pass through the gate during setup."""

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_get_auth_csrf_returns_200_during_setup(self):
        """GET /api/auth/csrf/ is allowed during setup mode."""
        client = APIClient()
        resp = client.get("/api/auth/csrf/")
        assert resp.status_code == 200, (
            f"Expected 200 for /api/auth/csrf/ during setup, got "
            f"{resp.status_code}."
        )

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_get_auth_login_still_returns_503_during_setup(self):
        """Other /api/auth/ endpoints must remain gated.

        Regression guard: ensure we did NOT over-whitelist ``/api/auth/``
        — only the exact CSRF-priming endpoint is allowed through.
        """
        client = APIClient()
        resp = client.get("/api/auth/login/")
        assert resp.status_code == 503, (
            f"Expected 503 for /api/auth/login/ during setup, got "
            f"{resp.status_code}.  The gate must not let other auth "
            f"endpoints through."
        )
        assert resp.json()["detail"] == "Setup not complete."

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_post_auth_csrf_is_not_gated_during_setup(self):
        """POST to /api/auth/csrf/ is not gate-blocked.

        The middleware does not care about HTTP method for this path.
        The view is GET-only, so POST returns 405 Method Not Allowed —
        we just assert it is NOT 503 (middleware-blocked).
        """
        client = APIClient()
        resp = client.post("/api/auth/csrf/")
        assert resp.status_code != 503, (
            f"Expected /api/auth/csrf/ to bypass the gate regardless "
            f"of method, got {resp.status_code}."
        )
