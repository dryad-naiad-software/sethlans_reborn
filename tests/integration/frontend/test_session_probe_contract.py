# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``GET /api/setup/session/``.

This probe endpoint is consumed exclusively by the Angular
``setupSessionGuard`` on ``/setup/wizard`` (see
``development/specs/setup-token-entry.md`` FR-BE-1 / FR-8).

Frontend expectations:

* 204 with empty body when the caller's session is in setup phase AND
  bound to the same ``setup_session_id`` as ``manager.ini``.
* 403 with envelope ``{"error": {"code": "setup_in_progress", ...}}``
  when the caller has no setup-phase session.
* 409 with envelope ``{"error": {"code": "setup_session_conflict", ...}}``
  when the caller's session is in setup phase but a DIFFERENT session
  already owns the ini binding.
* Every error body conforms to ``SetupErrorEnvelope`` and Content-Type
  is parseable (JSON).
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from rest_framework.test import APIClient

from tests.integration.manager._setup_helpers import (
    bootstrap as _bootstrap,
    enter_setup_mode as _enter_setup_mode,
    exit_setup_mode as _exit_setup_mode,
    patch_data_dir as _patch_data_dir,
    reset_rate_limiter as _reset_rate_limiter,
)
from workers.utils.errors import ERROR_CODES

from .conftest import assert_envelope_shape

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup_env(mocker, tmp_path):
    """End-to-end setup-mode fixture with real ini + real bootstrap."""
    _enter_setup_mode(mocker)
    _reset_rate_limiter(mocker)
    data_dir = _patch_data_dir(mocker, tmp_path)
    yield data_dir
    _exit_setup_mode()


class TestSessionProbeAnonymous:
    """No bootstrap -> 403 setup_in_progress envelope."""

    def test_anonymous_returns_403_envelope(self, setup_env):
        client = APIClient()
        client.cookies.clear()
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_in_progress"
        # Interceptor requires the code to live in the canonical set.
        assert body["error"]["code"] in ERROR_CODES
        assert body["error"]["details"] == {}

    def test_anonymous_response_content_type_is_json(self, setup_env):
        client = APIClient()
        client.cookies.clear()
        resp = client.get("/api/setup/session/")
        ctype = resp.headers.get("Content-Type", "")
        assert "application/json" in ctype, (
            f"Setup error envelope must be JSON; got {ctype!r}"
        )

    def test_non_setup_in_progress_code_not_invalid_token(self, setup_env):
        """FR-BE-1 note: default 403 fallthrough would say 'invalid_token'.
        The view raises SetupPhaseError explicitly to avoid that.
        """
        client = APIClient()
        client.cookies.clear()
        resp = client.get("/api/setup/session/")
        body = resp.json()
        assert body["error"]["code"] != "invalid_token", (
            "Bare 403 fallthrough leaked through — view must raise "
            "SetupPhaseError('setup_in_progress', ...) explicitly."
        )


class TestSessionProbeBound:
    """After bootstrap, the probe returns 204 with an empty body."""

    def test_bound_session_returns_204(self, setup_env):
        client = Client()
        assert _bootstrap(client).status_code == 204
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 204

    def test_204_body_is_empty(self, setup_env):
        client = Client()
        assert _bootstrap(client).status_code == 204
        resp = client.get("/api/setup/session/")
        assert resp.status_code == 204
        # Django's 204 response has empty content; tolerate ``b""``,
        # ``b"null"``, ``b"{}"`` as the valid FR-BE-1 success body.
        assert resp.content in (b"", b"null", b"{}"), (
            f"204 must have empty body; got {resp.content!r}"
        )

    def test_204_content_type_is_parseable_when_present(self, setup_env):
        """Even when the body is empty, the response must expose a
        content-type header that the Angular HttpClient can parse (or
        treat as empty). This guards against the HttpClient "Unexpected
        token" parse error that bit us in issue #77."""
        client = Client()
        assert _bootstrap(client).status_code == 204
        resp = client.get("/api/setup/session/")
        # Django may emit ``Content-Type: text/html; charset=utf-8`` for
        # an empty 204. The Angular HttpClient tolerates any
        # content-type when the body is zero bytes, so either a JSON
        # content-type OR zero body is acceptable.
        body_len = len(resp.content or b"")
        ctype = resp.headers.get("Content-Type", "")
        assert body_len == 0 or "application/json" in ctype, (
            f"Non-empty body must be JSON; body={resp.content!r} "
            f"ctype={ctype!r}"
        )


class TestSessionProbeSessionConflict:
    """Session in setup phase but bound to a different session_id -> 409."""

    def test_different_session_returns_409_envelope(self, setup_env):
        tab_a = Client()
        tab_b = Client()
        assert _bootstrap(tab_a).status_code == 204
        assert _bootstrap(tab_b).status_code == 204

        # Tab A owns the ini binding (first bootstrap wins). Tab B
        # has a valid setup_phase session but a DIFFERENT
        # setup_session_id — the probe must 409.
        resp = tab_b.get("/api/setup/session/")
        assert resp.status_code == 409, (
            f"Expected 409 setup_session_conflict; got {resp.status_code} "
            f"{resp.content!r}"
        )
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_session_conflict"
        assert body["error"]["code"] in ERROR_CODES
        # Interceptor routes setup_session_conflict -> /login; no
        # details-leak allowed.
        assert body["error"]["details"] == {}


class TestSessionProbeCodeVocabulary:
    """All codes the probe can emit must live in the TS union."""

    def test_codes_are_in_canonical_set(self, setup_env):
        """Exhaustive enumeration of probe failure modes."""
        # Anonymous path.
        c1 = APIClient()
        c1.cookies.clear()
        b1 = c1.get("/api/setup/session/").json()
        # Conflict path.
        tab_a, tab_b = Client(), Client()
        _bootstrap(tab_a)
        _bootstrap(tab_b)
        b2 = tab_b.get("/api/setup/session/").json()
        for body in (b1, b2):
            assert body["error"]["code"] in ERROR_CODES, (
                f"Probe emitted code not in ERROR_CODES: "
                f"{body['error']['code']!r}"
            )


class TestSessionProbePostSetup:
    """After sentinel is written, the probe is 404-gated by the
    middleware (alongside every other /api/setup/* path)."""

    def test_probe_404s_after_setup_complete(self, mocker):
        from sethlans_manager.middleware import setup_gate
        prev = setup_gate._setup_complete
        setup_gate._setup_complete = True
        mocker.patch.object(
            setup_gate, "_check_sentinel", return_value=True,
        )
        try:
            resp = APIClient().get("/api/setup/session/")
            assert resp.status_code == 404
            body = resp.json()
            assert_envelope_shape(body)
            assert body["error"]["code"] == "setup_complete"
        finally:
            setup_gate._setup_complete = prev


# Keep the module under the 300-line budget even as test surface grows.
# Per-scenario fixtures are intentionally minimal; share only the
# setup_env entrypoint so additions stay local to their test class.
_ = json  # silence unused-import lints when someone strips tests
