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
def _summary_data_dir(mocker, tmp_path):
    """Point the summary view's data dir at an empty ``tmp_path``."""
    from workers.views import manager_summary as mod
    mocker.patch.object(mod, "get_manager_data_dir", return_value=tmp_path)
    return tmp_path


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

    def test_manager_url_from_manager_ini_in_data_dir(
        self, admin_client, _summary_data_dir,
    ):
        (_summary_data_dir / "manager.ini").write_text(
            "[server]\nhost = 10.0.0.5\nport = 9443\n",
            encoding="utf-8",
        )
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://10.0.0.5:9443"

    def test_manager_url_defaults_without_manager_ini(self, admin_client):
        payload = admin_client.get("/api/manager/summary/").json()
        assert payload["manager_url"] == "https://localhost:8080"

    def test_data_dir_is_the_shared_manager_data_dir(self, _summary_data_dir):
        """``_data_dir`` delegates to ``get_manager_data_dir``."""
        from workers.views import manager_summary as mod
        assert mod._data_dir() == _summary_data_dir
