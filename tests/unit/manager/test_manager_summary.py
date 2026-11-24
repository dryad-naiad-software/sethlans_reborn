# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/manager_summary.py``.

The view is admin-only and post-setup.  Permission classes are
``[IsAuthenticated, IsAdminUser]``.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


#: The summary keys since issue #304 dropped ``topology``.
SUMMARY_KEYS = {
    "manager_url", "admin_username", "enrollment_key", "cert_fingerprint",
}


@pytest.fixture(autouse=True)
def _no_bind_env(monkeypatch):
    """No inherited manager host/port env; tests set them explicitly."""
    monkeypatch.delenv("SETHLANS_MANAGER_PORT", raising=False)
    monkeypatch.delenv("SETHLANS_MANAGER_HOST", raising=False)


@pytest.fixture(autouse=True)
def _summary_settings_file():
    """The settings file the view reads: the unit conftest's per-test path.

    It does not exist until a test writes it.
    """
    from shared.settings_file import settings_path
    path = settings_path()
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class TestManagerSummaryAuth:

    def test_anonymous_denied(self):
        resp = APIClient().get("/api/manager/summary/")
        assert resp.status_code in (401, 403)

    def test_non_admin_denied(self, django_user_model):
        user = django_user_model.objects.create_user(
            username="plain", password="x",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/api/manager/summary/")
        assert resp.status_code == 403

    def test_admin_session_auth_allowed(self, admin_client):
        resp = admin_client.get("/api/manager/summary/")
        assert resp.status_code == 200
        payload = resp.json()
        assert "manager_url" in payload
        assert "admin_username" in payload
        assert "enrollment_key" in payload
        assert "cert_fingerprint" in payload

    def test_admin_token_auth_allowed(self, admin_user):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=admin_user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        resp = client.get("/api/manager/summary/")
        assert resp.status_code == 200


class TestManagerSummaryPayload:

    def test_keys_have_no_topology(self, admin_client):
        """Issue #304: the summary no longer reads the setup marker."""
        payload = admin_client.get("/api/manager/summary/").json()
        assert set(payload) == SUMMARY_KEYS

    def test_view_does_not_read_the_setup_marker(self):
        from workers.views import manager_summary as mod
        assert not hasattr(mod, "read_sentinel")

    def test_manager_url_from_the_settings_file(
        self, admin_client, _summary_settings_file,
    ):
        _summary_settings_file.write_text(
            "[server]\nhost = 10.0.0.5\nport = 9443\n",
            encoding="utf-8",
        )
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://10.0.0.5:9443"

    def test_manager_url_defaults_without_settings_file(self, admin_client):
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://localhost:8080"

    def test_manager_ini_in_base_dir_is_ignored(self, admin_client, settings, tmp_path):
        """A leftover ``manager.ini`` is never read; only ``sethlans.ini`` counts."""
        settings.BASE_DIR = tmp_path
        (tmp_path / "manager.ini").write_text(
            "[server]\nhost = 10.0.0.9\nport = 7000\n", encoding="utf-8",
        )
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://localhost:8080"

    def test_env_beats_the_settings_file(self, admin_client, _summary_settings_file, monkeypatch):
        """#313: host and port resolve env > file > default, as at the start."""
        _summary_settings_file.write_text(
            "[server]\nhost = 10.0.0.5\nport = 9443\n", encoding="utf-8",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")
        monkeypatch.setenv("SETHLANS_MANAGER_HOST", "10.0.0.7")
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://10.0.0.7:8443"

    def test_env_port_with_file_host(self, admin_client, _summary_settings_file, monkeypatch):
        _summary_settings_file.write_text(
            "[server]\nhost = 10.0.0.5\nport = 9443\n", encoding="utf-8",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://10.0.0.5:8443"
