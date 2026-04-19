# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``POST /api/setup/bootstrap/``.

Frontend expectation (see ``setup-bootstrap.service.ts`` +
``app.config.ts`` APP_INITIALIZER flow):

* Happy path -> 204 No Content with an empty body.
* Session cookie is set on the response so subsequent setup calls
  authenticate via ``SessionAuthentication``.
* All error responses conform to ``SetupErrorEnvelope``.
"""

from __future__ import annotations

import json

import pytest
from rest_framework.test import APIClient

from .conftest import VALID_TEST_TOKEN, assert_envelope_shape

pytestmark = pytest.mark.django_db


class TestBootstrapSuccessContract:

    def test_returns_204_no_content_with_empty_body(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TEST_TOKEN}),
            content_type="application/json",
        )
        assert resp.status_code == 204
        assert resp.content in (b"", b"null", b"{}"), (
            f"Bootstrap 204 must be empty body; got {resp.content!r}"
        )

    def test_session_cookie_set_on_success(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """After bootstrap the client session has setup_phase=True."""
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TEST_TOKEN}),
            content_type="application/json",
        )
        assert resp.status_code == 204
        # Django test client exposes the session dict directly.
        assert client.session.get("setup_phase") is True
        assert isinstance(client.session.get("setup_session_id"), str)


class TestBootstrapErrorContract:

    def test_invalid_token_envelope_matches_ts(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": "b" * 64}),  # wrong but long enough
            content_type="application/json",
        )
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "invalid_token"
        # FR-2a / S4: details is empty -- no attempt counter leaked.
        assert body["error"]["details"] == {}

    def test_rate_limit_envelope_matches_ts(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """11th attempt from same IP -> 429 rate_limited with empty details."""
        client = APIClient()
        # Exhaust the 10-attempt window with wrong tokens.
        for _ in range(10):
            client.post(
                "/api/setup/bootstrap/",
                data=json.dumps({"token": "b" * 64}),
                content_type="application/json",
            )
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": "b" * 64}),
            content_type="application/json",
        )
        assert resp.status_code == 429
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"] == {}
