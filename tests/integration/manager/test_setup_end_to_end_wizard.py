# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end wizard integration: bootstrap → phase progression → sentinel
→ restart-request → simulated restart → post-setup endpoints.

No real process respawn — we simulate the launcher's restart by:
 * flipping the gate's ``_setup_complete`` to True
 * rotating ``runtime_state.manager_boot_id``
The test then verifies the setup endpoints go 404 and the new
``/api/manager/summary/`` admin endpoint works.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from sethlans_manager import runtime_state
from sethlans_manager.middleware import setup_gate
from workers.services.sentinel import create_sentinel

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    post_json,
    reset_rate_limiter,
)

User = get_user_model()


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestEndToEndWizard:

    def test_bootstrap_then_topology_then_restart_request(
        self, setup_env, client,
    ):
        assert bootstrap(client).status_code == 204
        # Topology step (the only wizard step we exercise here — other
        # steps need real ffmpeg/blender assets and are covered by E2E).
        resp = post_json(
            client, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp.status_code == 200

        # Simulate remaining phases landing by writing sentinel with
        # completed_at set (equivalent to verify step succeeding).
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )

        # Restart: marker created via O_EXCL.
        resp_restart = client.post("/api/setup/restart/")
        assert resp_restart.status_code == 202
        marker = setup_env / ".restart_requested"
        assert marker.exists()
        payload = json.loads(marker.read_text())
        assert "requested_at" in payload

    def test_second_restart_returns_409(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )
        assert client.post("/api/setup/restart/").status_code == 202
        second = client.post("/api/setup/restart/")
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "precondition_unmet"

    def test_post_restart_setup_endpoints_return_404(
        self, setup_env, client, mocker,
    ):
        # Simulate manager restart: flip gate + rotate boot_id.
        assert bootstrap(client).status_code == 204
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )

        # "Restart": gate flips to complete, boot_id rotates.
        prev_boot = runtime_state.manager_boot_id
        setup_gate._setup_complete = True
        runtime_state.manager_boot_id = uuid.uuid4().hex

        try:
            # Setup GET now returns 404 via gate.
            resp = client.get("/api/setup/topology/")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "setup_complete"
        finally:
            runtime_state.manager_boot_id = prev_boot

    def test_manager_summary_reachable_post_setup(self, setup_env):
        # Create a superuser for the admin-only endpoint.
        admin = User.objects.create_superuser(
            username="wizadmin", email="a@b.com",
            password="Str0ngP@ssw0rd!",
        )
        # Post-setup: sentinel complete, gate flipped.
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )
        setup_gate._setup_complete = True

        client = APIClient()
        client.force_authenticate(user=admin)
        resp = client.get("/api/manager/summary/")
        assert resp.status_code == 200
        body = resp.json()
        assert "manager_url" in body
        assert body["admin_username"] == "wizadmin"
