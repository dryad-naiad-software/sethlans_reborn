# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared WSGI helpers for the static-file route tests.

Phase F4 split ``test_static_file_routes.py`` into three files for
the 300-line limit; the ``_invoke`` / environ helpers and the auth-
state autouse fixture are shared by all three halves.
"""

from __future__ import annotations

import io

import pytest

from wizard.sethlans_wizard import auth_state


SETUP_TOKEN = b"setup-token-xyz-99887766554433221100"
IPC_SECRET = b"ipc-hmac-secret-bytes-zzz-yyy-xxx"


@pytest.fixture(autouse=True)
def _reset_auth_state():
    auth_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()


def invoke(app, environ):
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    return captured.get("status"), captured.get("headers", {}), body


def get_environ(path: str, *, cookie: str | None = None) -> dict:
    """Build a minimal WSGI GET environ dict.

    Issue #175 — page routes are gated by the wizard_session cookie.
    Tests that exercise gated pages must either authenticate via
    :func:`authed_get_environ` or build their own environ with the
    Cookie value set on ``HTTP_COOKIE``.
    """
    env = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }
    if cookie is not None:
        env["HTTP_COOKIE"] = f"wizard_session={cookie}"
    return env


def post_environ(path: str) -> dict:
    env = get_environ(path)
    env["REQUEST_METHOD"] = "POST"
    return env


def authed_get_environ(path: str) -> dict:
    """Build a GET environ pre-authed with a fresh session cookie (#175)."""
    token = auth_state.issue_session_token()
    return get_environ(path, cookie=token)
