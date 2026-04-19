# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the setup session probe endpoint.

Covers ``GET /api/setup/session/`` (FR-BE-1 in
``development/specs/setup-token-entry.md``):

* 204 when a bound setup-phase session exists.
* 403 ``setup_in_progress`` envelope when no setup_phase on session.
* 409 ``setup_session_conflict`` envelope when the request's
  ``setup_session_id`` disagrees with ``manager.ini [setup] session_id``.
* OpenAPI schema at ``/api/schema/`` advertises 204 + 403 under the
  Setup tag.
* After the sentinel is written, the ``SetupGateMiddleware`` post-setup
  branch emits the 404 ``setup_complete`` envelope.
"""

from __future__ import annotations

import configparser

import pytest
from django.test import Client

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    reset_rate_limiter,
    write_sentinel_complete,
)


def _is_envelope(body: dict) -> bool:
    err = body.get("error")
    if not isinstance(err, dict):
        return False
    return (
        isinstance(err.get("code"), str)
        and isinstance(err.get("message"), str)
        and isinstance(err.get("details"), dict)
    )


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestSetupSessionProbe:

    def test_probe_returns_204_after_bootstrap(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 204
        # 204 responses have no body.
        assert resp.content == b""

    def test_probe_returns_403_envelope_when_no_setup_phase(
        self, setup_env,
    ):
        fresh = Client()
        resp = fresh.get("/api/setup/session/")
        assert resp.status_code == 403
        body = resp.json()
        assert _is_envelope(body)
        assert body["error"]["code"] == "setup_in_progress"

    def test_probe_returns_409_when_session_id_conflicts(
        self, setup_env, client,
    ):
        # Bootstrap binds manager.ini [setup] session_id to this session's
        # setup_session_id.  Mutate the stored ID to simulate a second tab
        # having raced in and grabbed the binding.
        assert bootstrap(client).status_code == 204
        session = client.session
        session["setup_session_id"] = "bogus-conflicting-id-xxx"
        session.save()

        resp = client.get("/api/setup/session/")
        assert resp.status_code == 409
        body = resp.json()
        assert _is_envelope(body)
        assert body["error"]["code"] == "setup_session_conflict"

    def test_probe_403_envelope_shape_is_exact(self, setup_env):
        # Reinforces the error envelope contract for the unguarded call.
        fresh = Client()
        resp = fresh.get("/api/setup/session/")
        assert resp.status_code == 403
        body = resp.json()
        # Exact top-level key set: just "error".
        assert set(body.keys()) == {"error"}
        # Exact error sub-keys.
        assert set(body["error"].keys()) == {"code", "message", "details"}
        assert body["error"]["code"] == "setup_in_progress"
        assert body["error"]["details"] == {}

    def test_openapi_schema_documents_session_probe(self, setup_env):
        # The schema endpoint is not allowlisted by the gate in setup
        # mode, so use a sentinel-complete state for this lookup.  We
        # flip the gate to complete then read the schema — the probe URL
        # must be documented on the route under the Setup tag with both
        # 204 and 403 responses.
        from sethlans_manager.middleware import setup_gate
        setup_gate._setup_complete = True
        try:
            resp = Client().get(
                "/api/schema/?format=json", HTTP_ACCEPT="application/json",
            )
            assert resp.status_code == 200
            doc = resp.json()
        finally:
            setup_gate._setup_complete = False

        paths = doc.get("paths", {})
        probe = paths.get("/api/setup/session/")
        assert probe is not None, (
            "OpenAPI schema missing /api/setup/session/"
        )
        get_op = probe.get("get")
        assert get_op is not None
        assert "Setup" in get_op.get("tags", [])
        responses = get_op.get("responses", {})
        assert "204" in responses
        assert "403" in responses

    def test_probe_404s_after_sentinel_written(
        self, setup_env, client, mocker,
    ):
        # After the verify step writes the sentinel, SetupGateMiddleware
        # flips into post-setup mode and 404s every /api/setup/* path.
        assert bootstrap(client).status_code == 204
        write_sentinel_complete(setup_env)
        # Force gate to re-evaluate; _check_sentinel is patched to False
        # by enter_setup_mode, so swap it back to return True.
        from sethlans_manager.middleware import setup_gate
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=True,
        )

        resp = client.get("/api/setup/session/")
        assert resp.status_code == 404
        body = resp.json()
        assert _is_envelope(body)
        assert body["error"]["code"] == "setup_complete"

    def test_bootstrap_binds_session_id_to_ini(self, setup_env, client):
        # The probe's 204 path relies on the session_id binding written
        # by setup_bootstrap_view; sanity-check that linkage here.
        assert bootstrap(client).status_code == 204
        sid = client.session.get("setup_session_id")
        assert sid
        cfg = configparser.ConfigParser()
        cfg.read(setup_env / "manager.ini")
        assert cfg.get("setup", "session_id") == sid
