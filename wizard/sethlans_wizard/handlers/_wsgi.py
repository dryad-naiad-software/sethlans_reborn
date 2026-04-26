# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Internal WSGI helpers shared by the wizard handler modules (Spec 1).

A3's ``handlers/auth.py`` predates this module and keeps its own copies
of these helpers (intentional — its surface is closed and we did not
want to introduce a cross-module import for the auth path). A4's
topology / done / runtime-ready handlers all reuse the helpers below to
avoid four near-identical copies of the same WSGI plumbing.

Hard rules:

* NO worker / manager imports — the wizard bundle is its own PyInstaller
  artifact and cannot pull worker WSGI helpers (NF-9 bundle isolation).
* NO logging of request bodies — bodies may contain the setup token or
  the session token (FR-W-FE3a / FR-W-FE3b / NF-6).
* Body cap mirrors A3's ``_AUTH_BODY_MAX = 4096`` — defensive cap; no
  wizard endpoint accepts a payload anywhere near that size.
"""

from __future__ import annotations

import json
from typing import Callable, Iterable, Optional

# Defensive body cap; mirrors handlers/auth.py.
BODY_MAX = 4096

_FORBIDDEN_QS_KEYS = frozenset({"session_token", "session", "token", "url"})


def status_line(code: int) -> str:
    """Return ``"<code> <reason>"`` for a small known set of statuses."""
    reasons = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        405: "Method Not Allowed",
    }
    return f"{code} {reasons.get(code, 'OK')}"


def send_json(
    start_response: Callable,
    payload: dict,
    status: int = 200,
    extra_headers: Optional[list[tuple[str, str]]] = None,
) -> Iterable[bytes]:
    """Render *payload* as a JSON WSGI response."""
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status_line(status), headers)
    return [body]


def read_body(environ: dict, max_bytes: int = BODY_MAX) -> bytes:
    """Read at most ``max_bytes + 1`` bytes from ``wsgi.input``.

    Returns ``max_bytes + 1`` worth of zeros when the declared
    ``CONTENT_LENGTH`` exceeds the cap — caller is expected to detect
    the oversized condition via ``len(body) > max_bytes`` and respond
    with a 400.
    """
    stream = environ.get("wsgi.input")
    if stream is None:
        return b""
    raw_len = environ.get("CONTENT_LENGTH", "")
    try:
        declared = int(raw_len) if raw_len else None
        if declared is not None and declared < 0:
            declared = None
    except (TypeError, ValueError):
        declared = None
    if declared is not None and declared > max_bytes:
        return b"\x00" * (max_bytes + 1)
    read_limit = max_bytes + 1
    if declared is not None:
        read_limit = min(declared, max_bytes + 1)
    try:
        return stream.read(read_limit) or b""
    except (OSError, ValueError):
        return b""


def query_string_has_forbidden_key(environ: dict) -> bool:
    """Refuse any request whose query string carries a token-shaped key.

    FR-W-FE3a / FR-W-FE3b / SEC-MED-12: neither setup tokens, session
    tokens, nor probe URLs (SEC-MED-12) may be operator-controlled via
    URL parameters. Defense in depth.
    """
    qs = environ.get("QUERY_STRING", "") or ""
    if not qs:
        return False
    for chunk in qs.split("&"):
        key = chunk if "=" not in chunk else chunk.split("=", 1)[0]
        if key.lower() in _FORBIDDEN_QS_KEYS:
            return True
    return False


def client_ip(environ: dict) -> str:
    """Return ``REMOTE_ADDR`` or ``"unknown"`` (for log breadcrumbs)."""
    return environ.get("REMOTE_ADDR") or "unknown"


def parse_json_body(body: bytes) -> Optional[dict]:
    """Parse *body* as JSON; return the dict on success or ``None``."""
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


__all__ = [
    "BODY_MAX",
    "status_line",
    "send_json",
    "read_body",
    "query_string_has_forbidden_key",
    "client_ip",
    "parse_json_body",
]
