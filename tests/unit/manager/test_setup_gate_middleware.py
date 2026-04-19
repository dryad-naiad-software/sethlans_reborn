# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/sethlans_manager/middleware/setup_gate.py``.

The token-validation branch is gone: setup authentication is now
session-based (``setup_bootstrap_view`` sets ``session['setup_phase']``).
The gate's job is now just allowlisting + blocking everything else
with the unified envelope.
"""

from __future__ import annotations

import json

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from sethlans_manager.middleware import setup_gate
from sethlans_manager.middleware.setup_gate import SetupGateMiddleware


@pytest.fixture(autouse=True)
def _reset_setup_complete():
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    yield
    setup_gate._setup_complete = prev


@pytest.fixture
def get_response():
    return lambda request: HttpResponse("OK", status=200)


@pytest.fixture
def rf():
    return RequestFactory()


class TestSetupIncomplete:
    """Setup not complete: gate blocks non-allowlisted API paths."""

    def test_api_request_blocked(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        resp = mw(rf.get("/api/projects/"))
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["error"]["code"] == "setup_in_progress"
        assert body["error"]["details"] == {}

    def test_browser_request_redirects_to_setup(
        self, rf, get_response, mocker,
    ):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        resp = mw(rf.get("/dashboard/"))
        assert resp.status_code == 302
        assert resp.url == "/setup/"


class TestAllowedPaths:
    """FR-3: bootstrap / csrf / health / setup SPA / static / media pass."""

    @pytest.mark.parametrize("path", [
        "/api/setup/bootstrap/",
        "/api/setup/topology/",
        "/api/setup/status/",
        "/api/auth/csrf/",
        "/api/health/",
        "/setup/",
        "/setup/bootstrap-error",
        "/static/main.js",
        "/media/img.png",
    ])
    def test_allowlisted_paths_pass_through(
        self, rf, get_response, mocker, path,
    ):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        resp = mw(rf.get(path))
        assert resp.status_code == 200


class TestPostWithoutToken:
    """With the gate no longer reading X-Setup-Token, POSTs to /api/setup/*
    flow through to the view (where IsSetupPhaseUser rules)."""

    def test_post_to_setup_passes_gate(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=False,
        )
        mw = SetupGateMiddleware(get_response)
        setup_gate._setup_complete = False
        resp = mw(rf.post(
            "/api/setup/topology/",
            content_type="application/json",
        ))
        # Gate is a no-op: inner handler returns 200.
        assert resp.status_code == 200


class TestSetupComplete:
    """Once setup completes, /api/setup/* returns setup_complete envelope."""

    def test_setup_paths_return_404(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=True,
        )
        mw = SetupGateMiddleware(get_response)
        resp = mw(rf.get("/api/setup/topology/"))
        assert resp.status_code == 404
        body = json.loads(resp.content)
        assert body["error"]["code"] == "setup_complete"

    def test_other_paths_pass_through(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=True,
        )
        mw = SetupGateMiddleware(get_response)
        for path in ["/api/projects/", "/dashboard/"]:
            resp = mw(rf.get(path))
            assert resp.status_code == 200


class TestDefenseInDepth:
    """Sentinel missing but superuser exists -> treat as complete."""

    def test_superuser_fallback(self, mocker):
        from unittest.mock import MagicMock
        mocker.patch.object(
            setup_gate, "_get_data_dir", return_value="/fake",
        )
        mocker.patch.object(
            setup_gate, "read_sentinel", return_value=None,
        )
        mock_user_model = MagicMock()
        qs = MagicMock()
        qs.exists.return_value = True
        mock_user_model.objects.filter.return_value = qs
        mocker.patch(
            "django.contrib.auth.get_user_model",
            return_value=mock_user_model,
        )
        assert setup_gate._check_sentinel() is True


class TestModuleLevelLatch:

    def test_once_true_stays_true(self, rf, get_response, mocker):
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=True,
        )
        mw = SetupGateMiddleware(get_response)
        assert setup_gate._setup_complete is True
        # Subsequent sentinel-absent check doesn't flip the latch back.
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=False,
        )
        resp = mw(rf.get("/api/projects/"))
        assert resp.status_code == 200
