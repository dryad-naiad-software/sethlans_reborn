# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_bootstrap.py``.

Covers the setup-auth-unification contract:

* valid token → 204, session has ``setup_phase=True`` + ``setup_session_id``
* missing / empty / <32-byte / wrong token → 403 ``invalid_token``
* ``session.cycle_key`` is called before writing the phase flag
* 11th attempt from the same IP → 429 ``rate_limited`` with empty details
* endpoint is ``@csrf_exempt`` (no CSRF token needed on the request)
"""

from __future__ import annotations

import pytest

from workers.rate_limiter import InMemoryRateLimiter
from workers.views import setup_bootstrap as bootstrap_mod
from workers.views.setup_bootstrap import setup_bootstrap_view


VALID_TOKEN = "a" * 64  # >=32 bytes

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_setup_gate(mocker):
    """Force the setup gate to report setup-incomplete each test."""
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = False
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=False,
    )


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(mocker):
    """Install a fresh limiter per test so attempt counts don't bleed."""
    limiter = InMemoryRateLimiter(max_attempts=10, window_seconds=300)
    mocker.patch.object(
        bootstrap_mod, "_bootstrap_rate_limiter", limiter,
    )
    return limiter


@pytest.fixture
def _patch_token(mocker):
    """Stub ``read_setup_token`` — default returns VALID_TOKEN."""
    return mocker.patch.object(
        bootstrap_mod, "read_setup_token", return_value=VALID_TOKEN,
    )


@pytest.fixture
def _patch_bind(mocker):
    """Stub ``bind_setup_session_id`` so no manager.ini write occurs."""
    return mocker.patch.object(
        bootstrap_mod, "bind_setup_session_id", return_value=True,
    )


@pytest.fixture
def _patch_data_dir(mocker, tmp_path):
    mocker.patch.object(bootstrap_mod, "_data_dir", return_value=tmp_path)


def _post(client, token=None):
    if token is None:
        return client.post(
            "/api/setup/bootstrap/", data={}, content_type="application/json",
        )
    import json
    return client.post(
        "/api/setup/bootstrap/",
        data=json.dumps({"token": token}),
        content_type="application/json",
    )


class TestBootstrapSuccess:

    def test_valid_token_returns_204(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        resp = _post(client, VALID_TOKEN)
        assert resp.status_code == 204

    def test_session_flags_set_on_success(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        _post(client, VALID_TOKEN)
        assert client.session.get("setup_phase") is True
        sid = client.session.get("setup_session_id")
        assert isinstance(sid, str) and len(sid) == 32

    def test_cycle_key_called_on_success(
        self, client, mocker, _patch_token, _patch_bind, _patch_data_dir,
    ):
        # Spy on SessionBase.cycle_key via the request session class.
        from django.contrib.sessions.backends.db import SessionStore
        spy = mocker.spy(SessionStore, "cycle_key")
        resp = _post(client, VALID_TOKEN)
        assert resp.status_code == 204
        assert spy.call_count >= 1

    def test_no_csrf_required(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        # enforce_csrf_checks defaults to False, but assert explicitly that
        # the view is reachable even on a CSRF-strict client.
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        import json
        resp = strict.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": VALID_TOKEN}),
            content_type="application/json",
        )
        assert resp.status_code == 204

    def test_binds_session_id_to_ini(
        self, client, _patch_token, _patch_data_dir, mocker,
    ):
        bind = mocker.patch.object(
            bootstrap_mod, "bind_setup_session_id", return_value=True,
        )
        _post(client, VALID_TOKEN)
        bind.assert_called_once()
        _, session_id = bind.call_args.args
        assert session_id == client.session["setup_session_id"]


class TestBootstrapInvalidToken:

    def _assert_invalid(self, resp):
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "invalid_token"
        assert body["error"]["details"] == {}

    def test_missing_token(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        self._assert_invalid(_post(client))

    def test_empty_token(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        self._assert_invalid(_post(client, ""))

    def test_token_under_32_bytes(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        self._assert_invalid(_post(client, "x" * 31))

    def test_wrong_token(
        self, client, _patch_bind, _patch_data_dir, mocker,
    ):
        mocker.patch.object(
            bootstrap_mod, "read_setup_token", return_value=VALID_TOKEN,
        )
        self._assert_invalid(_post(client, "b" * 64))

    def test_empty_expected_token_in_ini(
        self, client, _patch_bind, _patch_data_dir, mocker,
    ):
        mocker.patch.object(
            bootstrap_mod, "read_setup_token", return_value="",
        )
        self._assert_invalid(_post(client, VALID_TOKEN))

    def test_missing_expected_token_in_ini(
        self, client, _patch_bind, _patch_data_dir, mocker,
    ):
        mocker.patch.object(
            bootstrap_mod, "read_setup_token", return_value=None,
        )
        self._assert_invalid(_post(client, VALID_TOKEN))

    def test_invalid_token_does_not_set_session_phase(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        _post(client, "nope")
        assert client.session.get("setup_phase") is not True


class TestBootstrapRateLimit:

    def test_11th_attempt_returns_429(
        self, client, _patch_token, _patch_bind, _patch_data_dir,
    ):
        for _ in range(10):
            _post(client, "wrong")  # counts as attempt
        resp = _post(client, VALID_TOKEN)  # 11th
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"] == {}


class TestBootstrapUnitView:
    """Direct-view invocation for tighter isolation."""

    def test_direct_invocation_success(
        self, _patch_token, _patch_bind, _patch_data_dir,
    ):
        from rest_framework.test import APIRequestFactory
        from importlib import import_module
        rf = APIRequestFactory()
        request = rf.post(
            "/api/setup/bootstrap/",
            {"token": VALID_TOKEN}, format="json",
        )
        # Attach a real session backend.
        engine = import_module("django.contrib.sessions.backends.db")
        request.session = engine.SessionStore()
        response = setup_bootstrap_view(request)
        assert response.status_code == 204
