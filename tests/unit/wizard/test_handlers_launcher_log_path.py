# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/launcher_log_path.py``.

Spec 1 / B4. The launcher-log-path endpoint surfaces
``<data_dir>/wizard/.launcher_log_path`` to the redirecting page so it
can show the user where to look when boot stalls or the runtime fails.

Covers:

* Session-gated access (``X-Wizard-Session`` header required).
* Forbidden query-string keys → 400 (SEC-MED-12 defense in depth).
* Method other than GET → 405.
* Returns ``{"path": "..."}`` when the marker file exists.
* Returns ``{"path": ""}`` when the marker file is missing (graceful
  fallback so the page can render generic guidance).
* Marker file with mixed line endings / trailing whitespace is stripped.
"""

from __future__ import annotations

import io
import json

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers.launcher_log_path import (
    make_launcher_log_path_handler,
)


_VALID_SESSION = "valid-session-token-for-llp"


def _build_environ(*, method="GET", path="/api/wizard/launcher-log-path/",
                   remote_addr="127.0.0.1", query_string="",
                   headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
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
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(_VALID_SESSION)
    yield
    auth_state.reset_state_for_tests()


def _auth_env(**kw):
    headers = kw.pop("headers", {}) or {}
    headers["X-Wizard-Session"] = _VALID_SESSION
    return _build_environ(headers=headers, **kw)


def _write_log_path(data_dir, value: str) -> None:
    """Write ``.launcher_log_path`` marker under <data_dir>/wizard/."""
    target_dir = data_dir / "wizard"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / ".launcher_log_path").write_text(value, encoding="utf-8")


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------

class TestHappyPath:

    def test_returns_path_when_marker_exists(self, tmp_path):
        _write_log_path(tmp_path, "/var/log/sethlans/launcher.log")
        handler = make_launcher_log_path_handler(tmp_path)
        status, headers, body = _call(handler, _auth_env())
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("application/json")
        assert body == {"path": "/var/log/sethlans/launcher.log"}

    def test_returns_empty_string_when_marker_missing(self, tmp_path):
        # No file written — endpoint MUST NOT 404 / 500. Return empty
        # string so the page can fall back to generic guidance.
        handler = make_launcher_log_path_handler(tmp_path)
        status, _, body = _call(handler, _auth_env())
        assert status.startswith("200"), status
        assert body == {"path": ""}

    def test_strips_trailing_whitespace_and_newlines(self, tmp_path):
        _write_log_path(tmp_path, "/tmp/launcher.log\n\n  ")
        handler = make_launcher_log_path_handler(tmp_path)
        _, _, body = _call(handler, _auth_env())
        assert body == {"path": "/tmp/launcher.log"}

    def test_handles_windows_path_with_crlf(self, tmp_path):
        _write_log_path(tmp_path, "C:\\Users\\me\\launcher.log\r\n")
        handler = make_launcher_log_path_handler(tmp_path)
        _, _, body = _call(handler, _auth_env())
        assert body == {"path": "C:\\Users\\me\\launcher.log"}


# ---------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------

class TestAuthGate:

    def test_missing_session_header_returns_401(self, tmp_path):
        _write_log_path(tmp_path, "/whatever.log")
        handler = make_launcher_log_path_handler(tmp_path)
        # No X-Wizard-Session header.
        status, _, body = _call(handler, _build_environ())
        assert status.startswith("401"), status
        assert body and "error" in body
        # Path MUST NOT leak in the unauthenticated error envelope.
        assert "/whatever.log" not in json.dumps(body)

    def test_wrong_session_token_returns_401(self, tmp_path):
        _write_log_path(tmp_path, "/whatever.log")
        handler = make_launcher_log_path_handler(tmp_path)
        env = _build_environ(headers={"X-Wizard-Session": "wrong"})
        status, _, _ = _call(handler, env)
        assert status.startswith("401"), status


# ---------------------------------------------------------------------
# Method + query-string defense
# ---------------------------------------------------------------------

class TestMethodAndQueryStringDefense:

    def test_post_returns_405(self, tmp_path):
        handler = make_launcher_log_path_handler(tmp_path)
        env = _auth_env(method="POST")
        status, headers, _ = _call(handler, env)
        assert status.startswith("405"), status
        assert headers.get("Allow") == "GET"

    def test_put_returns_405(self, tmp_path):
        handler = make_launcher_log_path_handler(tmp_path)
        env = _auth_env(method="PUT")
        status, _, _ = _call(handler, env)
        assert status.startswith("405"), status

    def test_query_string_session_token_returns_400(self, tmp_path):
        """SEC-MED-12 defense in depth: token-shaped QS keys MUST 400."""
        handler = make_launcher_log_path_handler(tmp_path)
        env = _auth_env(query_string="session_token=foo")
        status, _, body = _call(handler, env)
        assert status.startswith("400"), status
        assert body and "error" in body

    def test_query_string_url_returns_400(self, tmp_path):
        handler = make_launcher_log_path_handler(tmp_path)
        env = _auth_env(query_string="url=https://evil.example/")
        status, _, _ = _call(handler, env)
        assert status.startswith("400"), status


# ---------------------------------------------------------------------
# Constructor argument validation
# ---------------------------------------------------------------------

class TestConstructor:

    def test_accepts_str_data_dir(self, tmp_path):
        # Real launcher passes a Path, but the factory must accept str
        # too (mirrors other handlers).
        _write_log_path(tmp_path, "/x.log")
        handler = make_launcher_log_path_handler(str(tmp_path))
        status, _, body = _call(handler, _auth_env())
        assert status.startswith("200")
        assert body == {"path": "/x.log"}
