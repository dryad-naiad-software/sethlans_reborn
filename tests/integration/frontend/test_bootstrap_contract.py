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


class TestBootstrapCookies:
    """Cookie-side contract the frontend relies on after a 204.

    The Angular token-entry flow assumes:

    * ``sessionid`` is rotated by ``request.session.cycle_key()`` so
      session fixation attacks fail.
    * CSRF cookie is primed so the downstream wizard mutations
      (non-bootstrap setup views) carry ``X-CSRFToken`` via the
      interceptor's ``withXsrfConfiguration``.
    """

    def test_sessionid_cookie_rotated_on_success(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        client = APIClient()
        # Seed a session cookie BEFORE bootstrap by hitting an
        # allowlisted endpoint that touches the session.
        client.get("/api/setup/status/")
        pre_sessionid = client.cookies.get("sessionid")
        pre_value = pre_sessionid.value if pre_sessionid else None

        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TEST_TOKEN}),
            content_type="application/json",
        )
        assert resp.status_code == 204
        post_sessionid = client.cookies.get("sessionid")
        assert post_sessionid is not None, (
            "Bootstrap must set a sessionid cookie"
        )
        post_value = post_sessionid.value
        if pre_value is not None:
            assert post_value != pre_value, (
                "sessionid must rotate (cycle_key) on bootstrap; "
                f"pre={pre_value!r} post={post_value!r}"
            )

    def test_csrf_priming_endpoint_sets_csrftoken_cookie(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """Per FR-10 of setup-token-entry.md, CSRF priming is handled
        by the ``initializeSetupCheck`` APP_INITIALIZER, which hits
        ``GET /api/auth/csrf/``. The bootstrap POST itself is
        ``@csrf_exempt`` and need NOT set the cookie. We therefore
        verify the priming endpoint — which the Angular app relies on
        to feed ``withXsrfConfiguration`` — actually sets csrftoken.
        """
        client = APIClient()
        resp = client.get("/api/auth/csrf/")
        # The priming endpoint may be 200 or 204 depending on the
        # auth-service implementation; what matters is csrftoken.
        assert resp.status_code in (200, 204), (
            f"CSRF priming endpoint returned {resp.status_code}; "
            f"the frontend depends on this endpoint to populate "
            f"csrftoken before any wizard mutation fires."
        )
        assert client.cookies.get("csrftoken") is not None, (
            "GET /api/auth/csrf/ must set a csrftoken cookie; the "
            "Angular interceptor reads this cookie and sends it back "
            "in X-CSRFToken on every mutating request."
        )


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

    def test_invalid_token_is_not_400_401_or_409(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """Spec v2 revision: the frontend DROPPED 400/401/409 branches
        for bootstrap. Any return of those would be a silent contract
        regression that the TokenEntryComponent cannot surface.
        """
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": "b" * 64}),
            content_type="application/json",
        )
        assert resp.status_code not in (400, 401, 409), (
            f"Bootstrap must not emit 400/401/409 for a bad token; "
            f"frontend has no branch for those. Got {resp.status_code}."
        )
        assert resp.status_code == 403

    def test_rate_limited_code_not_anything_else(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """429 body's code must be exactly ``rate_limited``."""
        client = APIClient()
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
        assert body["error"]["code"] == "rate_limited"
        # Absolutely not any other plausible slug.
        assert body["error"]["code"] != "invalid_token"
        assert body["error"]["code"] != "internal_error"

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
