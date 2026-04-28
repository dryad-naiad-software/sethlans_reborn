# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tiny HTTP client helpers shared by the wizard integration tests.

Wraps :mod:`urllib.request` with sensible defaults. Returns
``(status, headers, body_bytes)`` tuples regardless of HTTP status —
even 4xx/5xx responses are returned (not raised) so test assertions
can inspect the JSON error envelope.

Issue #170: the wizard subprocess speaks plain HTTP on loopback now
(Caddy in front terminates TLS); the integration suite skips Caddy and
connects directly to the loopback listener, so no TLS context is
needed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Execute *method* *url*; return ``(status, headers, body_bytes)``."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (
                resp.status,
                {k: v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx land here — return the response normally so callers
        # can assert against the status code without try/except dancing.
        return (
            exc.code,
            {k: v for k, v in exc.headers.items()} if exc.headers else {},
            exc.read() if hasattr(exc, "read") else b"",
        )


def post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], dict | None]:
    """POST JSON *payload* to *url*; return ``(status, headers, parsed)``.

    If the response body is not valid JSON the third element is None.
    """
    body = json.dumps(payload).encode("utf-8")
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Type", "application/json")
    status, resp_headers, raw = request(
        "POST", url, body=body, headers=hdrs, timeout=timeout,
    )
    parsed = _safe_json(raw)
    return status, resp_headers, parsed


def get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """GET *url*; return ``(status, headers, body_bytes)``."""
    return request("GET", url, headers=headers, timeout=timeout)


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], dict | None]:
    """GET *url* and parse the JSON response."""
    status, resp_headers, raw = get(url, headers=headers, timeout=timeout)
    parsed = _safe_json(raw)
    return status, resp_headers, parsed


def _safe_json(raw: bytes) -> dict | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded
