# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared test helpers for Phase 1 (Spec 2) wizard handler tests.

Mirrors the request/response plumbing already in
``test_handlers_topology.py`` so each new handler test file does not
re-define the same WSGI environ builder.
"""

from __future__ import annotations

import io
import json
from typing import Any


VALID_SESSION = "valid-session-token-phase1"


def build_environ(
    *,
    method: str = "POST",
    path: str = "/api/wizard/x/",
    body: bytes = b"",
    remote_addr: str = "127.0.0.1",
    query_string: str = "",
    headers: dict | None = None,
) -> dict:
    env: dict = {
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


def auth_env(body: bytes, **kwargs) -> dict:
    """Build a POST environ carrying the canonical session header."""
    headers = kwargs.pop("headers", None) or {}
    headers["X-Wizard-Session"] = VALID_SESSION
    return build_environ(body=body, headers=headers, **kwargs)


def call_handler(handler, environ: dict) -> tuple[str, dict, Any]:
    """Invoke *handler* against *environ* and parse the JSON body."""
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(handler(environ, start_response))
    parsed = json.loads(body.decode("utf-8")) if body else None
    return captured["status"], dict(captured["headers"]), parsed


__all__ = ["VALID_SESSION", "build_environ", "auth_env", "call_handler"]
