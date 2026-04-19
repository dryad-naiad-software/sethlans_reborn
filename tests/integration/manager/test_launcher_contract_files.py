# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Launcher contract file integration (FR-13 / FR-13a / S7).

 * ``/api/setup/restart/`` uses O_EXCL and containment-checks the
   marker path — a symlink pointing outside data_dir is rejected with
   the unified error envelope.
 * Post-setup, ``/api/setup/restart/`` is unreachable (gate 404).
"""

from __future__ import annotations

import os
import platform

import pytest

from sethlans_manager.middleware import setup_gate
from workers.services.sentinel import create_sentinel

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    reset_rate_limiter,
)


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestLauncherContractFiles:

    def test_happy_path_creates_marker_o_excl(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )
        resp = client.post("/api/setup/restart/")
        assert resp.status_code == 202
        marker = setup_env / ".restart_requested"
        assert marker.exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink containment check is POSIX-only; Windows CI lacks "
               "symlink privilege by default.",
    )
    def test_symlink_outside_data_dir_rejected(
        self, setup_env, client, tmp_path_factory,
    ):
        assert bootstrap(client).status_code == 204
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )
        outside = tmp_path_factory.mktemp("outside")
        marker = setup_env / ".restart_requested"
        # Place a symlink at the marker path pointing outside data_dir.
        target = outside / "evil"
        os.symlink(target, marker)

        resp = client.post("/api/setup/restart/")
        # Containment check OR O_EXCL failure (the symlink dangles) —
        # either way we get a structured envelope error, not a 2xx.
        assert resp.status_code >= 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] in (
            "internal_error", "precondition_unmet",
        )

    def test_post_setup_restart_404(self, setup_env, client):
        # Flip gate → setup complete.
        assert bootstrap(client).status_code == 204
        create_sentinel(
            setup_env, "manager",
            ["topology_chosen", "verified"],
        )
        setup_gate._setup_complete = True

        resp = client.post("/api/setup/restart/")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "setup_complete"
