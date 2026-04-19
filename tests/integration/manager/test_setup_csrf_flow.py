# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
CSRF flow integration (FR-1 / FR-12 / F11).

 * Bootstrap endpoint is @csrf_exempt — succeeds with no CSRF cookie.
 * Other setup mutations require CSRF (SessionAuthentication enforces
   Django CSRF on mutating requests).
 * ``/api/auth/csrf/`` is reachable during setup mode (gate allowlist).
"""

from __future__ import annotations

import json

import pytest
from django.test import Client

from tests.integration.manager._setup_helpers import (
    VALID_TOKEN,
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
class TestCsrfFlow:

    def test_bootstrap_succeeds_without_csrf_cookie(self, setup_env):
        strict = Client(enforce_csrf_checks=True)
        resp = strict.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TOKEN}),
            content_type="application/json",
        )
        assert resp.status_code == 204

    def test_post_without_csrf_rejected_after_bootstrap(self, setup_env):
        strict = Client(enforce_csrf_checks=True)
        assert strict.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TOKEN}),
            content_type="application/json",
        ).status_code == 204

        resp = strict.post(
            "/api/setup/topology/",
            data=json.dumps({"topology": "manager"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_csrf_endpoint_reachable_during_setup(self, setup_env):
        client = Client()
        resp = client.get("/api/auth/csrf/")
        assert resp.status_code == 200
        assert "csrftoken" in resp.cookies

    def test_post_with_csrf_succeeds(self, setup_env):
        strict = Client(enforce_csrf_checks=True)
        # Bootstrap first.
        assert strict.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TOKEN}),
            content_type="application/json",
        ).status_code == 204

        # Fetch CSRF cookie explicitly.
        r = strict.get("/api/auth/csrf/")
        token = r.cookies["csrftoken"].value
        resp = strict.post(
            "/api/setup/topology/",
            data=json.dumps({"topology": "manager"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert resp.status_code == 200
