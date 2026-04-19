# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``POST /api/setup/restart/``.

Frontend expectation (done.component + restart-poll.service):

* Setup-phase session + sentinel present -> 202 Accepted (empty body).
* Second call while ``.restart_requested`` still exists -> 409 with
  envelope code ``precondition_unmet``.
* Anonymous session (no bootstrap yet) -> 403 with envelope code
  ``setup_in_progress`` so the interceptor routes the user to /setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rest_framework.test import APIClient

from workers.services.sentinel import create_sentinel

from .conftest import assert_envelope_shape

pytestmark = pytest.mark.django_db

SENTINEL_TOPOLOGY = "manager"
SENTINEL_CHECKPOINTS = [
    "topology_chosen",
    "network_configured",
    "database_configured",
    "admin_user_created",
    "verified",
]


@pytest.fixture
def restart_data_dir(mocker, tmp_path) -> Path:
    """Patch restart-view data_dir to an isolated tmp dir."""
    from workers.views import setup_restart as restart_mod
    mocker.patch.object(restart_mod, "_data_dir", return_value=tmp_path)
    return tmp_path


@pytest.fixture
def sentinel_present(restart_data_dir) -> Path:
    """Create a setup-complete sentinel in the restart data_dir."""
    create_sentinel(
        restart_data_dir, SENTINEL_TOPOLOGY, SENTINEL_CHECKPOINTS,
    )
    return restart_data_dir


@pytest.fixture
def setup_phase_client(enter_setup_mode) -> APIClient:
    """An APIClient with a setup-phase session flag set."""
    client = APIClient()
    # Force a real session cookie + setup_phase=True by driving
    # Django's session store directly.
    session = client.session
    session["setup_phase"] = True
    session["setup_session_id"] = "deadbeef" * 4
    session.save()
    # Attach the session cookie for subsequent requests.
    from django.conf import settings as django_settings
    client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key
    return client


class TestRestartHappyPath:

    def test_returns_202_with_setup_phase_and_sentinel(
        self, setup_phase_client, sentinel_present,
    ):
        resp = setup_phase_client.post("/api/setup/restart/")
        assert resp.status_code == 202, (
            f"Expected 202, got {resp.status_code}: {resp.content!r}"
        )

    def test_second_call_returns_409_envelope(
        self, setup_phase_client, sentinel_present,
    ):
        first = setup_phase_client.post("/api/setup/restart/")
        assert first.status_code == 202
        second = setup_phase_client.post("/api/setup/restart/")
        assert second.status_code == 409
        body = second.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "precondition_unmet"


class TestRestartAuthContract:

    def test_anonymous_returns_envelope(
        self, enter_setup_mode, sentinel_present,
    ):
        """Anonymous POST during setup mode -> envelope with setup_in_progress
        code so the Angular interceptor routes to /setup.

        DRF will emit 403 here because the IsSetupPhaseUser permission
        denies anonymous sessions; the unified exception handler scopes
        /api/setup/* error bodies to the envelope (FR-8a).
        """
        client = APIClient()
        client.cookies.clear()
        resp = client.post("/api/setup/restart/")
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        # Either setup_in_progress (middleware-level) or invalid_token
        # (DRF permission-level mapped via _infer_code). Both are in the
        # TS union and both trigger envelope routing in the interceptor.
        assert body["error"]["code"] in {
            "setup_in_progress", "invalid_token",
        }

    def test_post_setup_gate_returns_setup_complete_envelope(
        self, exit_setup_mode, sentinel_present,
    ):
        """Post-completion, gate returns 404 setup_complete (FR-13a)."""
        resp = APIClient().post("/api/setup/restart/")
        assert resp.status_code == 404
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_complete"
