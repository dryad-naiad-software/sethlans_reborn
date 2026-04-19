# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``GET /api/manager/summary/``.

Frontend expectation: payload matches the TS ``SetupSummary`` interface
at ``manager/frontend/src/app/features/setup/models/setup.models.ts``::

    interface SetupSummary {
      manager_url: string;
      admin_username: string;
      enrollment_key: string;
      cert_fingerprint: string;
      topology: string;
    }

Auth: admin session or admin TokenAuthentication required.  Anonymous
-> 401, non-admin authenticated user -> 403.
"""

from __future__ import annotations

import re

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
    "topology",
}


@pytest.fixture
def post_setup_state(mocker, tmp_path):
    """Drive the summary view into a post-setup, sentinel-present state."""
    from workers.views import manager_summary as mod
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = True
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=True,
    )
    mocker.patch.object(mod, "_data_dir", return_value=tmp_path)
    mocker.patch.object(
        mod,
        "read_sentinel",
        return_value={
            "version": 1,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "manager",
            "checkpoints": [],
        },
    )
    return tmp_path


class TestSummaryInterfaceFields:
    """The response must contain every field in TS SetupSummary."""

    def test_ts_interface_fields_match_expected(self, setup_models_ts_source):
        """Catch drift if someone edits the TS interface without updating API."""
        m = re.search(
            r"export\s+interface\s+SetupSummary\s*\{([^}]+)\}",
            setup_models_ts_source,
        )
        assert m, "SetupSummary interface not found in TS source"
        body = m.group(1)
        ts_fields = set(re.findall(r"(\w+)\s*:\s*\w", body))
        assert ts_fields == EXPECTED_FIELDS, (
            f"TS SetupSummary fields drifted from expected: "
            f"ts={sorted(ts_fields)} expected={sorted(EXPECTED_FIELDS)}"
        )

    def test_admin_session_returns_all_fields(
        self, admin_client, post_setup_state,
    ):
        resp = admin_client.get(SUMMARY_PATH)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert set(body.keys()) >= EXPECTED_FIELDS, (
            f"Response missing fields: {EXPECTED_FIELDS - set(body.keys())}"
        )
        # Each field must be a string (TS interface is all-string).
        for field in EXPECTED_FIELDS:
            assert isinstance(body[field], str), (
                f"Field {field} is {type(body[field])}, expected str"
            )
        assert body["topology"] == "manager"


class TestSummaryAuthContract:

    def test_anonymous_returns_401_or_403(self, post_setup_state):
        """Unauthenticated callers are rejected by DRF."""
        client = APIClient()
        resp = client.get(SUMMARY_PATH)
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for anonymous, got {resp.status_code}"
        )

    def test_non_admin_user_returns_403(self, post_setup_state):
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

    def test_token_auth_admin_allowed(self, post_setup_state):
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
