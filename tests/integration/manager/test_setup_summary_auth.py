# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

pytestmark = pytest.skip(
    "Obsoleted by setup-auth-unification; replaced in follow-up test phase",
    allow_module_level=True,
)

"""
Integration tests for ``GET /api/setup/summary/`` authorization
(bug #71 / Bug 3).

The wizard calls ``summary`` right after ``verify`` while the browser
is still anonymous, so the endpoint must accept EITHER a valid
``X-Setup-Token`` header (matching ``manager.ini [setup] token``) OR
an authenticated Django session.  If the ``[setup]`` section has been
pruned post-setup, only the session path is available.
"""

import configparser

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from workers.services.sentinel import create_sentinel

User = get_user_model()
_SETUP_TOKEN = "integration-summary-token-abc"


def _write_manager_ini(tmp_path, include_setup_section: bool):
    """Write a minimal manager.ini — optionally with [setup] token."""
    ini = tmp_path / "manager.ini"
    config = configparser.ConfigParser()
    config.add_section("server")
    config.set("server", "host", "0.0.0.0")
    config.set("server", "port", "8080")
    if include_setup_section:
        config.add_section("setup")
        config.set("setup", "token", _SETUP_TOKEN)
    with open(ini, "w") as fh:
        config.write(fh)
    return ini


@pytest.fixture
def completed_setup(tmp_path, settings):
    """Point BASE_DIR at tmp_path and write a completed sentinel."""
    settings.BASE_DIR = tmp_path
    create_sentinel(tmp_path, "manager", ["topology_chosen"])
    _write_manager_ini(tmp_path, include_setup_section=True)
    return tmp_path


@pytest.fixture
def completed_setup_no_section(tmp_path, settings):
    """Sentinel completed but ``[setup]`` section pruned from INI."""
    settings.BASE_DIR = tmp_path
    create_sentinel(tmp_path, "manager", ["topology_chosen"])
    _write_manager_ini(tmp_path, include_setup_section=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_setup_gate():
    """Force the module-level _setup_complete flag true for each test.

    These tests write the sentinel before sending the request, so the
    gate should passthrough.  Pin it explicitly so prior test state
    cannot interfere.
    """
    from sethlans_manager.middleware import setup_gate
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = True
    yield
    setup_gate._setup_complete = prev


@pytest.fixture(autouse=True)
def _runtime_state():
    prev = runtime_state.cert_fingerprint
    runtime_state.cert_fingerprint = "ab" * 32
    yield
    runtime_state.cert_fingerprint = prev


@pytest.mark.django_db
class TestSetupSummaryAuthorization:

    def test_valid_setup_token_returns_summary(self, completed_setup):
        """X-Setup-Token matching manager.ini → 200."""
        client = APIClient()
        resp = client.get(
            "/api/setup/summary/",
            HTTP_X_SETUP_TOKEN=_SETUP_TOKEN,
        )
        assert resp.status_code == 200
        assert "enrollment_key" in resp.data

    def test_authenticated_session_returns_summary(
        self, completed_setup,
    ):
        """Logged-in admin session → 200 (session auth path)."""
        admin = User.objects.create_superuser(
            username="sumadmin",
            password="sumpass12345",
            email="sum@test.com",
        )
        client = APIClient()
        client.force_login(admin)
        resp = client.get("/api/setup/summary/")
        assert resp.status_code == 200
        assert resp.data["admin_username"] == "sumadmin"

    def test_no_token_no_session_returns_403(self, completed_setup):
        """Anonymous client without token → 403."""
        client = APIClient()
        resp = client.get("/api/setup/summary/")
        assert resp.status_code == 403

    def test_wrong_token_no_session_returns_403(self, completed_setup):
        """Invalid X-Setup-Token, no session → 403."""
        client = APIClient()
        resp = client.get(
            "/api/setup/summary/",
            HTTP_X_SETUP_TOKEN="not-the-real-token",
        )
        assert resp.status_code == 403

    def test_no_setup_section_unauthenticated_returns_403(
        self, completed_setup_no_section,
    ):
        """[setup] section absent + no session → 403 fallback."""
        client = APIClient()
        # Even a header cannot satisfy the missing expected token.
        resp = client.get(
            "/api/setup/summary/",
            HTTP_X_SETUP_TOKEN="anything",
        )
        assert resp.status_code == 403
