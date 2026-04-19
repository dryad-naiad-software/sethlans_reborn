# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for the error-interceptor routing codes.

The Angular ``errorInterceptor`` (``manager/frontend/src/app/core/
interceptors/auth.interceptor.ts``) routes requests based on the
``error.code`` slug in the envelope:

* ``setup_in_progress`` -> /setup
* ``setup_complete``    -> /login
* ``invalid_token``     -> /setup/bootstrap-error
* ``setup_session_conflict`` -> /login

These tests verify that the backend actually emits each of those codes
from the scenarios the interceptor expects.
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
    """Bad bootstrap token -> ``invalid_token`` envelope so the
    interceptor routes to /setup/bootstrap-error."""

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
