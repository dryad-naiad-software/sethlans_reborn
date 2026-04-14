# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the SetupGateMiddleware.

Exercises the real Django middleware stack via the test client.
The ``conftest._bypass_setup_gate`` autouse fixture sets the module-level
``_setup_complete`` boolean to ``True`` for all tests.  Tests in this
file that need setup mode must explicitly set it to ``False``.
"""

import configparser

import pytest
from rest_framework.test import APIClient

from sethlans_manager.middleware import setup_gate


@pytest.fixture()
def _enter_setup_mode():
    """Set the middleware to setup-mode (sentinel absent).

    Overrides the global ``_bypass_setup_gate`` autouse fixture for
    tests that need to exercise the gate.
    """
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    yield
    setup_gate._setup_complete = prev


@pytest.fixture()
def setup_token(tmp_path, settings):
    """Write a manager.ini with a known setup token.

    Patches ``settings.BASE_DIR`` to ``tmp_path`` so the middleware's
    ``_read_setup_token()`` finds the INI file there.
    """
    token_value = "test-setup-token-abc123"
    ini_path = tmp_path / "manager.ini"
    config = configparser.ConfigParser()
    config.add_section("setup")
    config.set("setup", "token", token_value)
    with open(ini_path, "w") as f:
        config.write(f)
    settings.BASE_DIR = tmp_path
    return token_value


# -------------------------------------------------------------------
# FR-G2: API calls return 503 when sentinel is absent
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupModeBlocking:

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_api_returns_503_during_setup(self):
        """Non-setup API endpoints return 503 in setup mode."""
        client = APIClient()
        resp = client.get("/api/projects/")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Setup not complete."

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_browser_redirects_to_setup(self):
        """Browser requests redirect to /setup/ in setup mode."""
        client = APIClient()
        resp = client.get("/", HTTP_ACCEPT="text/html")
        assert resp.status_code == 302
        assert resp["Location"] == "/setup/"

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_nested_url_redirects_to_setup(self):
        """Non-API, non-allowed paths also redirect to /setup/."""
        client = APIClient()
        resp = client.get("/dashboard/projects/")
        assert resp.status_code == 302
        assert resp["Location"] == "/setup/"


# -------------------------------------------------------------------
# FR-G1/FR-G2: Allowed paths during setup mode
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestAllowedPathsDuringSetup:

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_static_accessible_during_setup(self):
        """Requests to /static/ are not blocked by the gate."""
        client = APIClient()
        # Static will 404 (no actual file) but NOT 503.
        resp = client.get("/static/test.css")
        assert resp.status_code != 503

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_setup_api_accessible_during_setup(self):
        """Requests to /api/setup/ pass through the gate."""
        client = APIClient()
        # The endpoint may 404 (no view registered yet) but NOT 503.
        resp = client.get("/api/setup/status/")
        assert resp.status_code != 503

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_setup_html_accessible_during_setup(self):
        """Requests to /setup/ pass through the gate."""
        client = APIClient()
        resp = client.get("/setup/")
        assert resp.status_code != 503
        assert resp.status_code != 302  # No redirect loop


# -------------------------------------------------------------------
# FR-L6: Setup token validation on POST to /api/setup/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupTokenValidation:

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_post_without_token_returns_403(self, setup_token):
        """POST to /api/setup/ without X-Setup-Token returns 403."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            data={"topology": "manager"},
            format="json",
        )
        assert resp.status_code == 403
        assert "token" in resp.json()["detail"].lower()

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_post_with_wrong_token_returns_403(self, setup_token):
        """POST with an incorrect token returns 403."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            data={"topology": "manager"},
            format="json",
            HTTP_X_SETUP_TOKEN="wrong-token",
        )
        assert resp.status_code == 403

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_post_with_correct_token_passes_gate(self, setup_token):
        """POST with the correct token passes the middleware gate.

        The request may still 404 (no view) but must NOT be 403.
        """
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            data={"topology": "manager"},
            format="json",
            HTTP_X_SETUP_TOKEN=setup_token,
        )
        assert resp.status_code != 403

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_get_does_not_require_token(self, setup_token):
        """GET requests to /api/setup/ do not need the token."""
        client = APIClient()
        resp = client.get("/api/setup/status/")
        assert resp.status_code != 403


# -------------------------------------------------------------------
# FR-G3: Middleware detects sentinel creation mid-process
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSentinelTransition:

    @pytest.mark.usefixtures("_enter_setup_mode")
    def test_api_works_after_sentinel_written(self, tmp_path, settings):
        """Once the sentinel is written, non-setup API calls work."""
        from workers.services.sentinel import create_sentinel

        settings.BASE_DIR = tmp_path
        client = APIClient()

        # Confirm 503 before sentinel.
        resp = client.get("/api/projects/")
        assert resp.status_code == 503

        # Write sentinel.
        create_sentinel(tmp_path, "manager", ["topology_chosen"])

        # Now the middleware should re-check and pass through.
        resp = client.get("/api/projects/")
        assert resp.status_code != 503


# -------------------------------------------------------------------
# FR-G6: Defense-in-depth (superuser exists, sentinel missing)
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestDefenseInDepth:

    def test_superuser_without_sentinel_blocks_setup_mode(
        self, admin_user,
    ):
        """If a superuser exists but sentinel is missing, refuse setup.

        The middleware should treat setup as complete (defense-in-depth).
        """
        # Force the middleware to re-evaluate.
        setup_gate._setup_complete = False
        try:
            client = APIClient()
            resp = client.get("/api/projects/")
            # Should NOT be 503 — defense-in-depth kicks in.
            assert resp.status_code != 503
        finally:
            setup_gate._setup_complete = True


# -------------------------------------------------------------------
# Normal mode: middleware is a passthrough
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestNormalModePassthrough:

    def test_api_accessible_when_setup_complete(self, admin_client):
        """When _setup_complete is True, API requests pass through."""
        resp = admin_client.get("/api/projects/")
        assert resp.status_code == 200
