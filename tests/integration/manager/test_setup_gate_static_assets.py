# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for GitHub issue #70.

Regression tests verifying the SetupGateMiddleware does NOT intercept
root-served Angular static assets (``/main-*.js``, ``/polyfills-*.js``,
``/styles-*.css``, etc.) during setup mode.  WhiteNoiseMiddleware must
run BEFORE SetupGateMiddleware in the MIDDLEWARE list so that files it
owns (via ``STATIC_ROOT`` or ``WHITENOISE_ROOT``) are served directly.

Unknown paths still fall through to the gate, which preserves the
redirect-to-/setup/ behavior for browser routes and the 503 behavior
for /api/ routes.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings
from rest_framework.test import APIClient

from sethlans_manager.middleware import setup_gate

pytestmark = pytest.skip(
    "Obsoleted by setup-auth-unification; replaced in follow-up test phase",
    allow_module_level=True,
)


@pytest.fixture()
def _enter_setup_mode(monkeypatch):
    """Force the gate middleware into setup-mode for a single test.

    Sets the module-level boolean to ``False`` AND stubs
    ``_check_sentinel`` to always return ``False`` so the per-request
    re-check inside ``__call__`` does not flip it back to ``True``
    (e.g., when a stale ``.setup_complete`` file exists in the dev
    workspace or when a superuser is present in the test DB).
    """
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    monkeypatch.setattr(
        "sethlans_manager.middleware.setup_gate._check_sentinel",
        lambda: False,
    )
    yield
    setup_gate._setup_complete = prev


@pytest.fixture()
def whitenoise_root(tmp_path):
    """Create a fake Angular dist dir containing a JS asset.

    Returns a ``(root_path, asset_name, asset_body)`` tuple.  WhiteNoise
    serves files from this directory at the URL root (``/main-abc.js``)
    when ``WHITENOISE_ROOT`` is set to it.
    """
    root = tmp_path / "angular-dist"
    root.mkdir()
    asset_name = "main-ABC123.js"
    asset_body = b"// angular bundle\nconsole.log('hi');\n"
    (root / asset_name).write_bytes(asset_body)
    return root, asset_name, asset_body


@pytest.mark.django_db
class TestMiddlewareOrderRegression:
    """Verify WhiteNoise precedes SetupGate in MIDDLEWARE (issue #70)."""

    def test_whitenoise_runs_before_setup_gate(self, settings):
        """WhiteNoise must be ordered BEFORE the setup gate.

        Otherwise SetupGateMiddleware redirects Angular's root-served
        JS/CSS bundles to /setup/, breaking the wizard (browser fetches
        HTML where it expects JavaScript).
        """
        middleware = list(settings.MIDDLEWARE)
        wn = "whitenoise.middleware.WhiteNoiseMiddleware"
        gate = (
            "sethlans_manager.middleware.setup_gate.SetupGateMiddleware"
        )
        assert wn in middleware, "WhiteNoise middleware missing"
        assert gate in middleware, "SetupGate middleware missing"
        assert middleware.index(wn) < middleware.index(gate), (
            "WhiteNoiseMiddleware must come BEFORE "
            "SetupGateMiddleware so static assets are served before "
            "the gate can redirect them to /setup/."
        )


@pytest.mark.django_db
class TestStaticAssetsBypassGate:
    """Root-served static assets must not be redirected to /setup/."""

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_root_level_js_asset_is_served_during_setup(
        self, whitenoise_root, settings,
    ):
        """A root-served JS asset returns 200, not 302 to /setup/."""
        root, asset_name, asset_body = whitenoise_root

        with override_settings(
            WHITENOISE_ROOT=str(root),
            WHITENOISE_AUTOREFRESH=True,
        ):
            client = Client()
            resp = client.get(f"/{asset_name}")

        assert resp.status_code == 200, (
            f"Expected 200 for root-served JS asset, got "
            f"{resp.status_code}.  If this is 302 to /setup/, the "
            f"SetupGateMiddleware is running before WhiteNoise."
        )
        assert resp["Content-Type"].startswith(
            ("application/javascript", "text/javascript"),
        ), f"Wrong Content-Type: {resp['Content-Type']}"
        body = b"".join(resp.streaming_content) if getattr(
            resp, "streaming_content", None,
        ) else resp.content
        assert body == asset_body

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_unknown_root_path_still_redirects_to_setup(
        self, whitenoise_root,
    ):
        """Non-static root paths still redirect to /setup/.

        Verifies the fix did not break the gate's redirect behavior —
        WhiteNoise only serves files it owns; everything else falls
        through to the setup gate.
        """
        root, _, _ = whitenoise_root
        with override_settings(
            WHITENOISE_ROOT=str(root),
            WHITENOISE_AUTOREFRESH=True,
        ):
            client = Client()
            resp = client.get("/")

        assert resp.status_code == 302
        assert resp["Location"] == "/setup/"

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_unknown_api_path_still_returns_503(
        self, whitenoise_root,
    ):
        """Non-setup /api/ paths still return 503 during setup."""
        root, _, _ = whitenoise_root
        with override_settings(
            WHITENOISE_ROOT=str(root),
            WHITENOISE_AUTOREFRESH=True,
        ):
            client = APIClient()
            resp = client.get("/api/jobs/")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Setup not complete."

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_setup_status_api_still_accessible(
        self, whitenoise_root,
    ):
        """/api/setup/status/ remains reachable during setup mode."""
        root, _, _ = whitenoise_root
        with override_settings(
            WHITENOISE_ROOT=str(root),
            WHITENOISE_AUTOREFRESH=True,
        ):
            client = APIClient()
            resp = client.get("/api/setup/status/")

        # The gate must let it through — not 503 (gate-blocked) and
        # not 302 (redirect-to-/setup/).  The view itself may 404 in
        # some environments (e.g., setup URL module conditionally
        # loaded); that is out of scope for this middleware test.
        assert resp.status_code not in (302, 503), (
            f"Expected gate to pass /api/setup/status/ through, got "
            f"{resp.status_code}."
        )
