# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Backend contract tests for ``GET /api/manager/summary/``.

Response shape: the payload holds exactly the fields in
``EXPECTED_FIELDS`` below (manager_url, admin_username, enrollment_key,
cert_fingerprint), each as a string.  ``topology`` was dropped with the
manager setup-complete gate (issue #304).

Auth: admin session or admin TokenAuthentication required.  Anonymous
-> 401, non-admin authenticated user -> 403.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

SUMMARY_PATH = "/api/manager/summary/"

EXPECTED_FIELDS = {
    "manager_url",
    "admin_username",
    "enrollment_key",
    "cert_fingerprint",
}


@pytest.fixture
def summary_data_dir(mocker, tmp_path):
    """Point the summary view's data dir (``manager.ini``) at tmp."""
    from workers.views import manager_summary as mod
    mocker.patch.object(mod, "_data_dir", return_value=tmp_path)
    return tmp_path


class TestSummaryInterfaceFields:
    """The response must contain exactly the fields of the API contract."""

    def test_admin_session_returns_all_fields(
        self, admin_client, summary_data_dir,
    ):
        resp = admin_client.get(SUMMARY_PATH)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert set(body.keys()) == EXPECTED_FIELDS, (
            f"Missing: {EXPECTED_FIELDS - set(body.keys())}; "
            f"Extra: {set(body.keys()) - EXPECTED_FIELDS}"
        )
        # Each field must be a string (TS interface is all-string).
        for field in EXPECTED_FIELDS:
            assert isinstance(body[field], str), (
                f"Field {field} is {type(body[field])}, expected str"
            )

    def test_summary_reads_manager_ini_from_data_dir(
        self, admin_client, summary_data_dir,
    ):
        """The URL is built from ``manager.ini`` in the data dir."""
        (summary_data_dir / "manager.ini").write_text(
            "[server]\nhost = 10.0.0.5\nport = 9443\n", encoding="utf-8",
        )
        body = admin_client.get(SUMMARY_PATH).json()
        assert body["manager_url"] == "https://10.0.0.5:9443"


class TestSummaryAuthContract:

    def test_anonymous_returns_401_or_403(self, summary_data_dir):
        """Unauthenticated callers are rejected by DRF."""
        client = APIClient()
        resp = client.get(SUMMARY_PATH)
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for anonymous, got {resp.status_code}"
        )

    def test_non_admin_user_returns_403(self, summary_data_dir):
        """An authenticated but non-admin user gets 403."""
        User = get_user_model()
        user = User.objects.create_user(
            username="regular",
            password="testpass123",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get(SUMMARY_PATH)
        assert resp.status_code == 403

    def test_token_auth_admin_allowed(self, summary_data_dir):
        """Admin TokenAuthentication also permits summary access (FR-6a)."""
        from rest_framework.authtoken.models import Token
        User = get_user_model()
        admin = User.objects.create_superuser(
            username="tok_admin",
            email="tok@example.com",
            password="testpass123",
        )
        token = Token.objects.create(user=admin)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        resp = client.get(SUMMARY_PATH)
        assert resp.status_code == 200
