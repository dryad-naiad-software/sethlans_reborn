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


SENTINEL_COMPLETE = {
    "version": 1,
    "completed_at": "2025-01-15T12:00:00Z",
    "topology": "manager",
    "checkpoints": [],
}


@pytest.fixture(autouse=True)
def _setup_complete_gate(mocker, tmp_path):
    """Simulate a post-setup manager (gate lets /api/manager/* through)."""
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = True
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=True,
    )
    # Also stub the summary view's data dir + sentinel.
    from workers.views import manager_summary as mod
    mocker.patch.object(mod, "_data_dir", return_value=tmp_path)
    mocker.patch.object(
        mod, "read_sentinel", return_value=SENTINEL_COMPLETE,
    )


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
        assert payload["topology"] == "manager"

    def test_admin_token_auth_allowed(self, admin_user):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=admin_user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        resp = client.get("/api/manager/summary/")
        assert resp.status_code == 200
