# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``GET /api/health/`` (``manager/workers/views/health.py``).

Health returns ``{boot_id, version}``.  ``boot_id`` matches
``runtime_state.manager_boot_id`` and is stable across requests in the
same process.  The endpoint is anonymous (FR-14c / FR-15).
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


class TestHealthResponse:

    def test_returns_200(self):
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200

    def test_body_shape(self):
        resp = APIClient().get("/api/health/")
        body = resp.json()
        assert set(body.keys()) == {"boot_id", "version"}

    def test_boot_id_matches_runtime_state(self):
        from sethlans_manager import runtime_state
        resp = APIClient().get("/api/health/")
        assert resp.json()["boot_id"] == runtime_state.manager_boot_id

    def test_anonymous_access_allowed(self):
        # No credentials — endpoint allowed.
        resp = APIClient().get("/api/health/")
        assert resp.status_code == 200

    def test_boot_id_stable_across_requests(self):
        client = APIClient()
        first = client.get("/api/health/").json()["boot_id"]
        second = client.get("/api/health/").json()["boot_id"]
        assert first == second
