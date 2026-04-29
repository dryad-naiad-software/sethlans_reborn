# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the wizard_session cookie path of the auth handler.

Issue #175 — page-level auth gate. The auth handler MUST emit a
``Set-Cookie: wizard_session=...`` response on a successful POST so
that subsequent address-bar GETs to gated wizard pages can be
validated server-side. The cookie value mirrors the JSON-returned
``session_token`` and is invalidated whenever a fresh auth POST
rotates the session (single-active-session invariant).

Kept in its own file so :mod:`test_handlers_auth` stays under the
300-line ceiling.
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


class TestSetCookieHeader:
    """The 200 response carries Set-Cookie with the right attributes."""

    def test_auth_success_sets_cookie_with_https_attributes(self, handler):
        # Production-shape request: Caddy forwards X-Forwarded-Proto: https.
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
            headers={"X-Forwarded-Proto": "https"},
        )
        status, headers, body = _call(handler, env)
        assert status.startswith("200"), body
        cookie = headers.get("Set-Cookie", "")
        # Must carry the issued session_token verbatim.
        assert cookie.startswith("wizard_session="), cookie
        assert body["session_token"] in cookie, (cookie, body)
        # Required attributes for the page-auth gate.
        assert "Path=/" in cookie
        assert "SameSite=Strict" in cookie
        # Browser-side HTTPS → Secure flag set.
        assert "Secure" in cookie
        # Session-cookie semantics — dies with the browser tab.
        assert "Max-Age" not in cookie
        assert "Expires=" not in cookie
        # JS must be able to clear the cookie on expireAndRedirect, so
        # HttpOnly is intentionally NOT set.
        assert "HttpOnly" not in cookie

    def test_auth_success_omits_secure_on_plain_http(self, handler):
        # Test-shape request: no X-Forwarded-Proto, wsgi.url_scheme=http.
        # Chromium under Playwright refuses to store Secure cookies on
        # plain HTTP, so the gate would never engage in the test
        # harness. Omitting Secure is the correct behaviour here.
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        env["wsgi.url_scheme"] = "http"
        status, headers, _ = _call(handler, env)
        assert status.startswith("200")
        cookie = headers.get("Set-Cookie", "")
        assert cookie.startswith("wizard_session=")
        assert "Secure" not in cookie, cookie

    def test_auth_success_sets_secure_when_wsgi_scheme_https(self, handler):
        # If something speaks WSGI directly with url_scheme=https (no
        # forwarding), the gate still authorises Secure.
        env = _build_environ(
            body=json.dumps({"token": _SETUP_TOKEN}).encode("utf-8"),
        )
        env["wsgi.url_scheme"] = "https"
        status, headers, _ = _call(handler, env)
        assert status.startswith("200")
        cookie = headers.get("Set-Cookie", "")
        assert "Secure" in cookie, cookie

    def test_failed_auth_does_not_set_cookie(self, handler):
        env = _build_environ(
            body=json.dumps({"token": "WRONG"}).encode("utf-8"),
        )
        status, headers, _ = _call(handler, env)
        assert status.startswith("403")
        assert "Set-Cookie" not in headers, headers


class TestExtractSessionCookie:

    def test_extract_session_cookie_present(self):
        env = _build_environ(headers={"Cookie": "wizard_session=abc"})
        assert auth_handler.extract_session_cookie(env) == "abc"

    def test_extract_session_cookie_among_others(self):
        env = _build_environ(
            headers={"Cookie": "foo=1; wizard_session=abc; bar=2"},
        )
        assert auth_handler.extract_session_cookie(env) == "abc"

    def test_extract_session_cookie_absent(self):
        env = _build_environ()
        assert auth_handler.extract_session_cookie(env) is None

    def test_extract_session_cookie_empty_header(self):
        env = _build_environ(headers={"Cookie": ""})
        assert auth_handler.extract_session_cookie(env) is None


class TestSessionCookieValid:

    def test_session_cookie_valid_round_trips(self):
        token = auth_state.issue_session_token()
        env = _build_environ(headers={"Cookie": f"wizard_session={token}"})
        assert auth_handler.session_cookie_valid(env) is True

    def test_session_cookie_valid_rejects_wrong_token(self):
        auth_state.issue_session_token()
        env = _build_environ(headers={"Cookie": "wizard_session=wrong"})
        assert auth_handler.session_cookie_valid(env) is False

    def test_session_cookie_valid_rejects_missing_cookie(self):
        auth_state.issue_session_token()
        env = _build_environ()
        assert auth_handler.session_cookie_valid(env) is False

    def test_session_cookie_valid_rejects_when_no_session_issued(self):
        env = _build_environ(headers={"Cookie": "wizard_session=anything"})
        assert auth_handler.session_cookie_valid(env) is False
