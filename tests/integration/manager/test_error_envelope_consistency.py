# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Error envelope consistency integration (FR-8 / FR-8a / D2).

Confirms that:
 * every ``/api/setup/*`` error body matches the unified envelope
 * non-setup API errors keep the stock DRF shape
 * ``SetupPhaseError`` from inside a view hits the custom exception
   handler and renders as the envelope
 * rate-limit ``details`` is always ``{}``
 * invalid-token and missing-token collapse to the same code
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from workers.services.setup_phase import SetupPhaseError

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    post_json,
    reset_rate_limiter,
)


def _is_envelope(body):
    if "error" not in body:
        return False
    err = body["error"]
    return (
        isinstance(err, dict)
        and isinstance(err.get("code"), str)
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
class TestSetupErrorEnvelope:

    def test_bootstrap_invalid_token_matches_envelope(
        self, setup_env, client,
    ):
        resp = bootstrap(client, token="short")
        assert resp.status_code == 403
        assert _is_envelope(resp.json())
        assert resp.json()["error"]["code"] == "invalid_token"

    def test_missing_token_collapses_to_invalid_token(
        self, setup_env, client,
    ):
        resp = post_json(client, "/api/setup/bootstrap/", {})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "invalid_token"

    def test_rate_limited_details_empty(self, setup_env, client):
        for _ in range(10):
            bootstrap(client, token="wrong")
        resp = bootstrap(client)
        assert resp.status_code == 429
        body = resp.json()
        assert _is_envelope(body)
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"] == {}

    def test_invalid_input_on_topology_matches_envelope(
        self, setup_env, client,
    ):
        assert bootstrap(client).status_code == 204
        resp = post_json(
            client, "/api/setup/topology/", {"topology": "bogus"},
        )
        assert resp.status_code == 400
        assert _is_envelope(resp.json())
        assert resp.json()["error"]["code"] == "invalid_input"

    def test_setup_phase_error_rendered_as_envelope(
        self, setup_env, client, mocker,
    ):
        """Raise SetupPhaseError from within bootstrap → envelope 409."""
        assert bootstrap(client).status_code == 204

        # Patch the topology view to raise.
        from workers.views import setup_status as status_mod

        def _raise(*a, **kw):
            raise SetupPhaseError(
                code="precondition_unmet",
                message="wrong phase",
                status=409,
                details={"expected": "admin"},
            )

        mocker.patch.object(status_mod, "read_sentinel", side_effect=_raise)
        resp = post_json(
            client, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert _is_envelope(body)
        assert body["error"]["code"] == "precondition_unmet"
        assert body["error"]["details"]["expected"] == "admin"


@pytest.mark.django_db
class TestNonSetupUnchanged:

    def test_non_setup_error_keeps_drf_shape(self):
        """``/api/projects/`` without auth keeps stock DRF error shape."""
        # Autouse fixture keeps gate in setup-complete mode here so we
        # hit the DRF auth path, not the gate.
        client = APIClient()
        resp = client.get("/api/projects/")
        # DRF default returns {"detail": "..."} — NOT our envelope.
        body = resp.json()
        assert "error" not in body or not _is_envelope(body)
        assert "detail" in body
