# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for the error-interceptor routing codes.

The Angular ``authInterceptor`` (``manager/frontend/src/app/core/
interceptors/auth.interceptor.ts``) routes requests based on the
``error.code`` slug in the envelope:

* ``setup_in_progress``      -> router.navigate(['/setup'])
* ``setup_complete``         -> router.navigate(['/login'])
* ``setup_session_conflict`` -> router.navigate(['/login'])
* ``invalid_token``          -> handled INLINE by TokenEntryComponent
                                (interceptor no-op; no navigation)

Per ``development/specs/setup-token-entry.md`` the
``/setup/bootstrap-error`` route was deleted; interceptor routing on
``invalid_token`` is now a no-op. These tests verify that the backend
actually emits each of those codes from the scenarios the interceptor
expects, AND that the 404 ``setup_complete`` envelope the server emits
is exactly the shape the interceptor matches on (envelope.error.code).
"""

from __future__ import annotations

import json

import pytest
from rest_framework.test import APIClient

from .conftest import assert_envelope_shape

pytestmark = pytest.mark.django_db


class TestSetupInProgressCode:
    """Anonymous call to a non-setup API during setup mode emits
    ``setup_in_progress`` so the interceptor routes to /setup."""

    def test_anonymous_projects_during_setup(self, enter_setup_mode):
        client = APIClient()
        client.cookies.clear()
        resp = client.get("/api/projects/")
        # The setup gate blocks /api/* with a 403 envelope carrying
        # the ``setup_in_progress`` code (spec FR-3 / FR-12a).
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_in_progress"


class TestSetupCompleteCode:
    """After sentinel lands, stale wizard clients hit /api/setup/*
    and the gate emits ``setup_complete`` so the interceptor routes
    to /login."""

    def test_setup_topology_post_setup(self, exit_setup_mode):
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            data=json.dumps({"topology": "manager"}),
            content_type="application/json",
        )
        assert resp.status_code == 404
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_complete"

    def test_setup_status_get_post_setup(self, exit_setup_mode):
        resp = APIClient().get("/api/setup/status/")
        assert resp.status_code == 404
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_complete"


class TestInvalidTokenCode:
    """Bad bootstrap token -> ``invalid_token`` envelope.

    Per setup-token-entry.md the interceptor no longer navigates on
    ``invalid_token``; TokenEntryComponent surfaces it inline. The
    contract here is unchanged from the backend side: code must be
    ``invalid_token`` and status 403. Only the frontend handling
    moved.
    """

    def test_bad_token_emits_invalid_token_code(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": "b" * 64}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "invalid_token"


class TestSetupCompleteInterceptorMatchShape:
    """The interceptor reads ``error.code`` from
    ``HttpErrorResponse.error`` — i.e. the parsed JSON body. The 404
    envelope must therefore be valid JSON AND parse into the
    ``SetupErrorEnvelope`` shape with ``code === 'setup_complete'``.
    A plain-text 404 from the URLconf would make the interceptor
    fall through to the ``401/403`` generic branches and break login
    redirection.
    """

    def test_404_body_is_json_parseable(self, exit_setup_mode):
        resp = APIClient().get("/api/setup/status/")
        assert resp.status_code == 404
        ctype = resp.headers.get("Content-Type", "")
        assert "application/json" in ctype, (
            f"404 setup_complete must be JSON so the interceptor can "
            f"parse envelope.error.code; got Content-Type={ctype!r}"
        )

    def test_404_body_envelope_code_is_setup_complete(self, exit_setup_mode):
        body = APIClient().get("/api/setup/status/").json()
        assert_envelope_shape(body)
        # Interceptor does: envelope?.error?.code — anything other
        # than 'setup_complete' here would not redirect to /login.
        assert body["error"]["code"] == "setup_complete"


class TestSetupSessionConflictInterceptorMatch:
    """409 conflict from a mutating setup view must carry the
    ``setup_session_conflict`` code so the interceptor routes to /login.
    """

    def test_409_envelope_emits_session_conflict_code(self, mocker, tmp_path):
        """Two-tab bootstrap race; second tab mutates and gets 409."""
        from django.test import Client
        from tests.integration.manager._setup_helpers import (
            bootstrap, enter_setup_mode, exit_setup_mode,
            patch_data_dir, post_json, reset_rate_limiter,
        )
        enter_setup_mode(mocker)
        reset_rate_limiter(mocker)
        patch_data_dir(mocker, tmp_path)
        try:
            tab_a, tab_b = Client(), Client()
            assert bootstrap(tab_a).status_code == 204
            assert bootstrap(tab_b).status_code == 204
            resp = post_json(
                tab_b, "/api/setup/topology/", {"topology": "manager"},
            )
            assert resp.status_code == 409
            body = resp.json()
            assert_envelope_shape(body)
            assert body["error"]["code"] == "setup_session_conflict"
        finally:
            exit_setup_mode()
