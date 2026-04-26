# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tiny HTTPS client helpers for the wizard E2E tests.

Mirrors :mod:`tests.integration.wizard._http` — kept as a separate module
so the e2e test package stays self-contained and doesn't depend on the
integration test package's import surface.

Returns ``(status, headers, body)`` tuples so 4xx/5xx are inspected as
data, not exceptions.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


def _no_verify_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def request(
    method: str, url: str, *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Execute *method* *url*; return ``(status, headers, body_bytes)``."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    ctx = _no_verify_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return (
                resp.status,
                {k: v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            {k: v for k, v in exc.headers.items()} if exc.headers else {},
            exc.read() if hasattr(exc, "read") else b"",
        )


def post_json(
    url: str, payload: dict, *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], dict | None]:
    """POST JSON *payload* to *url*; return ``(status, headers, parsed)``."""
    body = json.dumps(payload).encode("utf-8")
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Type", "application/json")
    status, resp_headers, raw = request(
        "POST", url, body=body, headers=hdrs, timeout=timeout,
    )
    return status, resp_headers, _safe_json(raw)


def get_json(
    url: str, *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], dict | None]:
    """GET *url* and parse the JSON response."""
    status, resp_headers, raw = request(
        "GET", url, headers=headers, timeout=timeout,
    )
    return status, resp_headers, _safe_json(raw)


def _safe_json(raw: bytes) -> dict | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded
