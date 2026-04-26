# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/auth.py`` (Spec 1 / A3).

Covers FR-W7 (auth + rate limit + single-active-session +
constant-time compare), FR-W-FE3a/FR-W-FE3b (no token in URL), and
NF-6 (no token in logs).
"""

from __future__ import annotations

import io
import json

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import auth as auth_handler


_SETUP_TOKEN = "setup-token-abc-1234567890"
_SETUP_TOKEN_BYTES = _SETUP_TOKEN.encode("ascii")


def _build_environ(*, method="POST", path="/api/wizard/auth/", body=b"",
                   remote_addr="127.0.0.1", query_string="", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    for hk, hv in (headers or {}).items():
        env["HTTP_" + hk.upper().replace("-", "_")] = hv
    return env


def _call(handler, environ):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(handler(environ, start_response))
    parsed = json.loads(body.decode("utf-8")) if body else None
    return captured["status"], dict(captured["headers"]), parsed


@pytest.fixture(autouse=True)
def _reset_auth_state():
    auth_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()


@pytest.fixture
def handler():
    return auth_handler.make_auth_handler(_SETUP_TOKEN_BYTES)


class TestFactory:

    def test_rejects_empty_setup_token(self):
        with pytest.raises(ValueError):
            auth_handler.make_auth_handler(b"")

    def test_rejects_non_bytes_setup_token(self):
        with pytest.raises(ValueError):
            auth_handler.make_auth_handler("a-string")  # type: ignore[arg-type]

    def test_strips_whitespace_from_token(self):
        h = auth_handler.make_auth_handler(b"   trimmed-token\n")
        env = _build_environ(
            body=json.dumps({"token": "trimmed-token"}).encode("utf-8"),
        )
        status, _, body = _call(h, env)
        assert status.startswith("200"), body


class TestAuthHappyPath:

    def test_correct_token_returns_session(self, handler):
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        status, _, body = _call(handler, env)
        assert status.startswith("200")
        assert body["status"] == "ok"
        assert isinstance(body["session_token"], str)
        assert len(body["session_token"]) >= 32
        # Session is now live in auth_state.
        assert auth_state.validate_session_token(body["session_token"])

    def test_wrong_token_returns_403(self, handler):
        env = _build_environ(
            body=json.dumps({"token": "WRONG"}).encode("utf-8"),
        )
        status, _, body = _call(handler, env)
        assert status.startswith("403")
        assert "invalid" in body["error"].lower()

    def test_uses_compare_digest(self, handler, mocker):
        mock_cmp = mocker.patch(
            "wizard.sethlans_wizard.handlers.auth.secrets.compare_digest",
            wraps=auth_handler.secrets.compare_digest,
        )
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        _call(handler, env)
        assert mock_cmp.called

    def test_single_active_session_invalidates_prior(self, handler):
        env_a = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        _, _, body_a = _call(handler, env_a)
        env_b = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        _, _, body_b = _call(handler, env_b)
        assert body_a["session_token"] != body_b["session_token"]
        assert not auth_state.validate_session_token(body_a["session_token"])
        assert auth_state.validate_session_token(body_b["session_token"])


class TestRateLimit:

    def test_eleventh_attempt_returns_429(self, handler):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            env = _build_environ(
                body=json.dumps({"token": "WRONG"}).encode("utf-8"),
            )
            status, _, _ = _call(handler, env)
            assert status.startswith("403")
        env = _build_environ(
            body=json.dumps({"token": "WRONG"}).encode("utf-8"),
        )
        status, headers, body = _call(handler, env)
        assert status.startswith("429"), status
        assert headers.get("Retry-After") == "60"
        assert "too many" in body["error"].lower()

    def test_429_blocks_correct_token_during_window(self, handler):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            env = _build_environ(
                body=json.dumps({"token": "WRONG"}).encode("utf-8"),
            )
            _call(handler, env)
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        status, _, _ = _call(handler, env)
        assert status.startswith("429")

    def test_per_ip_isolation_in_handler(self, handler):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            env = _build_environ(
                body=json.dumps({"token": "WRONG"}).encode("utf-8"),
                remote_addr="10.0.0.1",
            )
            _call(handler, env)
        env_other = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
            remote_addr="10.0.0.2",
        )
        status, _, _ = _call(handler, env_other)
        assert status.startswith("200")


class TestNoTokenInUrl:

    @pytest.mark.parametrize("qs_key", ["session_token", "session", "token"])
    def test_qs_token_rejected(self, handler, qs_key):
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
            query_string=f"{qs_key}=v",
        )
        status, _, body = _call(handler, env)
        assert status.startswith("400")
        assert "url" in body["error"].lower()


class TestEdgeCases:

    def test_get_returns_405(self, handler):
        env = _build_environ(method="GET")
        status, headers, body = _call(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"
        assert "method" in body["error"].lower()

    def test_malformed_json_returns_400(self, handler):
        env = _build_environ(body=b"not-json{{{")
        status, _, body = _call(handler, env)
        assert status.startswith("400")
        assert "json" in body["error"].lower()

    def test_missing_token_field_returns_400(self, handler):
        env = _build_environ(body=json.dumps({"other": "x"}).encode("utf-8"))
        status, _, _ = _call(handler, env)
        assert status.startswith("400")

    def test_empty_token_returns_400(self, handler):
        env = _build_environ(body=json.dumps({"token": ""}).encode("utf-8"))
        status, _, _ = _call(handler, env)
        assert status.startswith("400")

    def test_oversized_body_returns_400(self, handler):
        # CONTENT_LENGTH > _AUTH_BODY_MAX (4096) short-circuits.
        env = _build_environ(body=b"\x00" * 100)
        env["CONTENT_LENGTH"] = "999999"
        status, _, body = _call(handler, env)
        assert status.startswith("400")
        assert "large" in body["error"].lower()

    def test_malformed_body_does_not_record_attempt(self, handler):
        for _ in range(auth_state._RATE_LIMIT_MAX + 5):
            env = _build_environ(body=b"not-json")
            _call(handler, env)
        # Now the correct token must still be accepted (no rate-limit
        # lockout on parse failures).
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        status, _, _ = _call(handler, env)
        assert status.startswith("200")


class TestSessionHeaderHelpers:

    def test_extract_session_header_present(self):
        env = _build_environ(headers={"X-Wizard-Session": "abc"})
        assert auth_handler.extract_session_header(env) == "abc"

    def test_extract_session_header_absent(self):
        env = _build_environ()
        assert auth_handler.extract_session_header(env) is None

    def test_session_header_valid_round_trips(self):
        token = auth_state.issue_session_token()
        env = _build_environ(headers={"X-Wizard-Session": token})
        assert auth_handler.session_header_valid(env) is True

    def test_session_header_valid_rejects_wrong_token(self):
        auth_state.issue_session_token()
        env = _build_environ(headers={"X-Wizard-Session": "wrong"})
        assert auth_handler.session_header_valid(env) is False


class TestNoTokenInLogs:

    def test_token_value_never_logged(self, handler, caplog):
        wrong = "WRONG-token-value-9876543210"
        logger_name = "wizard.sethlans_wizard.handlers.auth"
        with caplog.at_level("INFO", logger=logger_name):
            ok = _build_environ(
                body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
            )
            _call(handler, ok)
            bad = _build_environ(
                body=json.dumps({"token": wrong}).encode("utf-8"),
            )
            _call(handler, bad)
        for rec in caplog.records:
            msg = rec.getMessage()
            assert _SETUP_TOKEN not in msg
            assert wrong not in msg
