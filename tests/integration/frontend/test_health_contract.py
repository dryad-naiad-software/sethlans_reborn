# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``GET /api/health/``.

Expected response shape (FR-14c):

    {"boot_id": "<uuid-hex>", "version": "<semver-string>"}

The endpoint is anonymous and reachable on every manager process,
because the Angular restart-poll depends on observing a ``boot_id``
change across a manager restart.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


class TestHealthShape:

    def test_keys_are_exactly_boot_id_and_version(self):
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"boot_id", "version"}, (
            f"Health payload shape drifted: {body!r}"
        )

    def test_boot_id_is_nonempty_string(self):
        body = APIClient().get("/api/health/").json()
        assert isinstance(body["boot_id"], str)
        assert body["boot_id"], "boot_id must be non-empty"

    def test_version_is_string(self):
        body = APIClient().get("/api/health/").json()
        assert isinstance(body["version"], str)

    def test_boot_id_is_stable_within_process(self):
        """Two consecutive GETs observe the same boot_id (same process)."""
        c = APIClient()
        a = c.get("/api/health/").json()["boot_id"]
        b = c.get("/api/health/").json()["boot_id"]
        assert a == b


class TestHealthAccessibility:

    def test_accessible(self):
        """Health endpoint responds (regression guard)."""
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert "boot_id" in body and "version" in body

    def test_anonymous_allowed(self):
        """No session, no auth header -- health still returns 200."""
        client = APIClient()
        # Clear any session cookies the test client might carry.
        client.cookies.clear()
        resp = client.get("/api/health/")
        assert resp.status_code == 200
