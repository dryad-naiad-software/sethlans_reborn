# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``GET /api/setup/session/`` (FR-BE-1/2/3).

Covers the setup-token-entry spec's route-guard probe endpoint:

* Valid bound setup-phase session  -> 204.
* Missing/absent setup-phase flag  -> 403 ``setup_in_progress`` envelope.
* Session bound to a DIFFERENT ``setup_session_id`` (another tab owns
  the binding) -> 409 ``setup_session_conflict`` envelope.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _setup_incomplete(mocker):
    """Force setup-gate to report setup-incomplete."""
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = False
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=False,
    )


def _bind_session(client, session_id="sid-A"):
    """Attach ``setup_phase=True`` + session_id to the Django test client."""
    session = client.session
    session["setup_phase"] = True
    session["setup_session_id"] = session_id
    session.save()


def _patch_snapshot(mocker, session_id):
    """Patch the snapshot helper imported into ``setup_session``."""
    from workers.services import setup_session as sess_mod
    mocker.patch.object(
        sess_mod, "setup_state_snapshot",
        return_value={
            "complete": False, "phase": None, "session_id": session_id,
        },
    )


class TestSetupSessionView204:

    def test_returns_204_when_session_bound(self, client, mocker):
        # No binding in ini — enforce_setup_session_binding should no-op.
        _patch_snapshot(mocker, None)
        _bind_session(client, "sid-A")
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 204
        # 204 has empty body.
        assert resp.content in (b"", b"null")

    def test_returns_204_when_session_id_matches_binding(
        self, client, mocker,
    ):
        _patch_snapshot(mocker, "sid-A")
        _bind_session(client, "sid-A")
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 204


class TestSetupSessionView403:

    def test_returns_403_when_no_session_phase(self, client):
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "setup_in_progress"
        assert "message" in body["error"]
        assert body["error"]["details"] == {}

    def test_envelope_shape_matches_spec(self, client):
        resp = client.get("/api/setup/session/")
        body = resp.json()
        assert set(body["error"].keys()) >= {"code", "message", "details"}

    def test_returns_403_when_setup_phase_is_falsy(self, client):
        # Session present but no setup_phase flag.
        session = client.session
        session["other_key"] = "x"
        session.save()
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "setup_in_progress"


class TestSetupSessionViewConflict:

    def test_returns_409_when_bound_to_different_session_id(
        self, client, mocker,
    ):
        # Another tab owns the binding with a different session id.
        _patch_snapshot(mocker, "sid-OTHER-TAB")
        _bind_session(client, "sid-THIS-TAB")
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "setup_session_conflict"
