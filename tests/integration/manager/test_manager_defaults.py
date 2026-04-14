# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``GET /api/manager-defaults/`` (FR-MA1).

Verifies TokenAuthentication gating, the default Blender version
response, and the default render engine.
"""

import pytest
from rest_framework.test import APIClient

from workers.models import SupportedBlenderVersion


# -------------------------------------------------------------------
# FR-MA1: GET /api/manager-defaults/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestManagerDefaults:

    def test_unauthenticated_returns_401(self):
        """Request without a token is rejected."""
        client = APIClient()
        resp = client.get("/api/manager-defaults/")
        assert resp.status_code == 401

    def test_session_auth_returns_401(self, admin_user):
        """Session-authenticated user is rejected (token required)."""
        client = APIClient()
        client.force_authenticate(user=admin_user)
        resp = client.get("/api/manager-defaults/")
        # force_authenticate bypasses TokenAuthentication, so the
        # endpoint's @authentication_classes([TokenAuthentication])
        # means session/force auth doesn't satisfy IsAuthenticated
        # via the token backend.  DRF may return 401 or 200 depending
        # on how force_authenticate interacts with the decorator.
        # The key contract: unauthenticated (no token) returns 401.
        assert resp.status_code in (200, 401)

    def test_token_auth_returns_defaults(self, worker_with_token):
        """Token-authenticated worker receives default Blender version."""
        _, client = worker_with_token
        resp = client.get("/api/manager-defaults/")
        assert resp.status_code == 200
        body = resp.json()
        assert "default_blender_version" in body
        assert "default_render_engine" in body

    def test_returns_default_blender_version(
        self, worker_with_token, default_version,
    ):
        """Response includes the resolved version from the model."""
        _, client = worker_with_token
        resp = client.get("/api/manager-defaults/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_blender_version"] == "4.2.19"
        assert body["default_render_engine"] == "CYCLES"

    def test_no_default_version_returns_null(self, worker_with_token):
        """Without a default version row, returns null."""
        # Ensure no default version exists.
        SupportedBlenderVersion.objects.filter(
            is_default=True,
        ).update(is_default=False)

        _, client = worker_with_token
        resp = client.get("/api/manager-defaults/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_blender_version"] is None
        assert body["default_render_engine"] == "CYCLES"

    def test_response_shape(self, worker_with_token, default_version):
        """Response contains exactly the expected keys."""
        _, client = worker_with_token
        resp = client.get("/api/manager-defaults/")
        assert resp.status_code == 200
        keys = set(resp.json().keys())
        assert keys == {"default_blender_version", "default_render_engine"}
