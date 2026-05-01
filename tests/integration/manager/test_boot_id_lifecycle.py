# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Boot_id lifecycle integration (FR-14c).

``GET /api/health/`` returns ``manager_boot_id``:
 * same value across two requests in the same process
 * rotates when ``runtime_state`` is reloaded
 * endpoint is reachable on every manager process
"""

from __future__ import annotations

import importlib

import pytest
from django.test import Client

from sethlans_manager import runtime_state


@pytest.mark.django_db
class TestBootIdLifecycle:

    def test_health_returns_current_boot_id(self):
        client = Client()
        resp = client.get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["boot_id"] == runtime_state.manager_boot_id
        assert body["boot_id"]  # non-empty

    def test_two_requests_same_boot_id(self):
        client = Client()
        r1 = client.get("/api/health/")
        r2 = client.get("/api/health/")
        assert r1.json()["boot_id"] == r2.json()["boot_id"]

    def test_reimporting_runtime_state_rotates_boot_id(self):
        client = Client()
        before = client.get("/api/health/").json()["boot_id"]
        prev = runtime_state.manager_boot_id
        try:
            importlib.reload(runtime_state)
            after = client.get("/api/health/").json()["boot_id"]
            assert after != before
            assert after  # non-empty
        finally:
            runtime_state.manager_boot_id = prev

    def test_health_reachable(self):
        client = Client()
        resp = client.get("/api/health/")
        assert resp.status_code == 200
