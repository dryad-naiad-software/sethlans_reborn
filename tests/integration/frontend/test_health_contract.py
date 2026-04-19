# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``GET /api/health/``.

Expected response shape (FR-14c):

    {"boot_id": "<uuid-hex>", "version": "<semver-string>"}

The endpoint is anonymous and MUST be reachable during setup mode
AND after setup completes, because the Angular restart-poll depends
on observing a ``boot_id`` change across a manager restart.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


class TestHealthShape:

    def test_keys_are_exactly_boot_id_and_version(self, enter_setup_mode):
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"boot_id", "version"}, (
            f"Health payload shape drifted: {body!r}"
        )

    def test_boot_id_is_nonempty_string(self, enter_setup_mode):
        body = APIClient().get("/api/health/").json()
        assert isinstance(body["boot_id"], str)
        assert body["boot_id"], "boot_id must be non-empty"

    def test_version_is_string(self, enter_setup_mode):
        body = APIClient().get("/api/health/").json()
        assert isinstance(body["version"], str)

    def test_boot_id_is_stable_within_process(self, enter_setup_mode):
        """Two consecutive GETs observe the same boot_id (same process)."""
        c = APIClient()
        a = c.get("/api/health/").json()["boot_id"]
        b = c.get("/api/health/").json()["boot_id"]
        assert a == b


class TestHealthAccessibility:

    def test_accessible_during_setup_mode(self, enter_setup_mode):
        """Setup gate must allowlist /api/health/ during setup (FR-3)."""
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200

    def test_accessible_after_setup_complete(self, exit_setup_mode):
        """Post-setup health endpoint still responds (regression guard)."""
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert "boot_id" in body and "version" in body

    def test_anonymous_allowed(self, enter_setup_mode):
        """No session, no auth header -- health still returns 200."""
        client = APIClient()
        # Clear any session cookies the test client might carry.
        client.cookies.clear()
        resp = client.get("/api/health/")
        assert resp.status_code == 200
