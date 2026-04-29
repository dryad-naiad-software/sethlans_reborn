# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/auth/`` — setup-token validation (FR-W7, Spec 1 / A3).

Accepts ``{"token": "..."}`` JSON, ``secrets.compare_digest``s against the
launcher-delivered setup token; on match issues a fresh single-active-
session token. Mismatch → 403 + rate-limited per source IP (10/60s → 429).

Guard rails: setup token in-memory only (SEC-MED-11); session token
never in URL/query/fragment (FR-W-FE3a/b — 400 if present); tokens
never logged (FR-W7 / NF-6).
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import auth_state

logger = logging.getLogger(__name__)

# Match the worker WSGI helpers' cap (defense-in-depth against oversized
# auth bodies). 4 KiB is far above any plausible token-bearing payload.
_AUTH_BODY_MAX = 4096

_SESSION_HEADER = "X-Wizard-Session"
_SESSION_HEADER_ENVIRON = "HTTP_X_WIZARD_SESSION"

# Issue #175 — cookie name for the server-side page-level auth gate.
# Page GETs cannot use the X-Wizard-Session header (address-bar
# navigations do not send it); we mirror the session token to a cookie.
_SESSION_COOKIE_NAME = "wizard_session"

# Query-string keys we forbid for the session token (FR-W-FE3b).
_FORBIDDEN_QS_KEYS = frozenset({"session_token", "session", "token"})


# ---------------------------------------------------------------------
# WSGI helpers (kept private to this module so the auth surface has no
# implicit dependency on worker/web_ui.http_helpers_wsgi).
# ---------------------------------------------------------------------

def _status_line(code: int) -> str:
    reasons = {
        200: "OK",
        400: "Bad Request",
        403: "Forbidden",
        405: "Method Not Allowed",
        429: "Too Many Requests",
    }
    return f"{code} {reasons.get(code, 'OK')}"


def _send_json(
    start_response: Callable,
    payload: dict,
    status: int = 200,
    extra_headers: Optional[list[tuple[str, str]]] = None,
) -> Iterable[bytes]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(_status_line(status), headers)
    return [body]


def _read_body(environ: dict) -> bytes:
    """Read at most ``_AUTH_BODY_MAX + 1`` bytes from ``wsgi.input``."""
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
    if declared is not None and declared > _AUTH_BODY_MAX:
        return b"\x00" * (_AUTH_BODY_MAX + 1)
    read_limit = _AUTH_BODY_MAX + 1
    if declared is not None:
        read_limit = min(declared, _AUTH_BODY_MAX + 1)
    try:
        return stream.read(read_limit) or b""
    except (OSError, ValueError):
        return b""


def _client_ip(environ: dict) -> str:
    """Stable per-client identifier for rate-limiting (REMOTE_ADDR only)."""
    return environ.get("REMOTE_ADDR") or "unknown"


def _is_browser_https(environ: dict) -> bool:
    """True if the browser-side connection is HTTPS (issue #175 cookie Secure)."""
    if (environ.get("HTTP_X_FORWARDED_PROTO") or "").lower() == "https":
        return True
    return (environ.get("wsgi.url_scheme") or "").lower() == "https"


def _query_string_has_session(environ: dict) -> bool:
    """Return True if QUERY_STRING contains a forbidden token key (FR-W-FE3a/b)."""
    qs = environ.get("QUERY_STRING", "") or ""
    if not qs:
        return False
    # Cheap parse — we only need to look at the keys.
    for chunk in qs.split("&"):
        if "=" not in chunk:
            key = chunk
        else:
            key, _, _ = chunk.partition("=")
        if key.lower() in _FORBIDDEN_QS_KEYS:
            return True
    return False


# ---------------------------------------------------------------------
# Public handler factory
# ---------------------------------------------------------------------

def make_auth_handler(setup_token: bytes) -> Callable:
    """Build the WSGI auth handler bound to *setup_token* (closure-held)."""
    if not isinstance(setup_token, (bytes, bytearray)) or not setup_token:
        raise ValueError("setup_token must be non-empty bytes")
    try:
        expected_token = setup_token.decode("ascii").strip()
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("setup_token must be ASCII-encodable") from exc
    if not expected_token:
        raise ValueError("setup_token must be non-empty after strip")

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle_auth(environ, start_response, expected_token)

    return handler


def _handle_auth(
    environ: dict,
    start_response: Callable,
    expected_token: str,
) -> Iterable[bytes]:
    """Dispatch a request against the auth route."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )

    if _query_string_has_session(environ):
        logger.warning(
            "Refused auth request with token-shaped query string from %s",
            _client_ip(environ),
        )
        return _send_json(
            start_response,
            {"error": "session token must not appear in URL"},
            status=400,
        )

    ip = _client_ip(environ)
    if auth_state.is_rate_limited(ip):
        logger.warning("Rate-limited auth attempt from %s", ip)
        return _send_json(
            start_response,
            {"error": "too many attempts; retry in 60 seconds"},
            status=429,
            extra_headers=[("Retry-After", "60")],
        )

    body = _read_body(environ)
    if len(body) > _AUTH_BODY_MAX:
        logger.warning("Oversized auth body from %s", ip)
        return _send_json(
            start_response,
            {"error": "request body too large"},
            status=400,
        )

    provided = _extract_token(body)
    if provided is None:
        # Don't record a rate-limit attempt for a malformed body — the
        # cost of the parse is the only deterrent we need; recording
        # would let a buggy client lock itself out without ever
        # trying a real token.
        return _send_json(
            start_response,
            {"error": "request body must be JSON {\"token\": \"...\"}"},
            status=400,
        )

    # Constant-time compare (FR-W7 / SEC-MED-1).
    if secrets.compare_digest(provided, expected_token):
        # Single-active-session: issuing a new token invalidates any
        # prior session in one atomic write under the handoff lock.
        session_token = auth_state.issue_session_token()
        logger.info("Auth success from %s; new session issued", ip)
        # Issue #175 — mirror session token into a cookie so address-
        # bar GETs to gated pages can be validated server-side.
        # Secure is conditional on browser-side HTTPS (Caddy forwards
        # X-Forwarded-Proto in prod; test hits HTTP loopback directly).
        cookie_value = (
            f"{_SESSION_COOKIE_NAME}={session_token}; "
            "Path=/; SameSite=Strict"
        )
        if _is_browser_https(environ):
            cookie_value += "; Secure"
        return _send_json(
            start_response,
            {"status": "ok", "session_token": session_token},
            status=200,
            extra_headers=[("Set-Cookie", cookie_value)],
        )

    auth_state.record_attempt(ip)
    logger.warning("Auth failure from %s", ip)
    return _send_json(
        start_response,
        {"error": "invalid setup token"},
        status=403,
    )


def _extract_token(body: bytes) -> Optional[str]:
    """Return the ``token`` field from *body*, or None if malformed."""
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        return None
    return token


# ---------------------------------------------------------------------
# Helpers for other handlers (A4 will import these)
# ---------------------------------------------------------------------

def extract_session_header(environ: dict) -> Optional[str]:
    """Return the value of ``X-Wizard-Session`` from *environ*."""
    return environ.get(_SESSION_HEADER_ENVIRON)


def session_header_valid(environ: dict) -> bool:
    """Constant-time validate the ``X-Wizard-Session`` header.

    Convenience wrapper around
    :func:`wizard.sethlans_wizard.auth_state.validate_session_token`.
    A4's topology / done / runtime-ready handlers MUST gate access via
    this helper.
    """
    return auth_state.validate_session_token(extract_session_header(environ))


def extract_session_cookie(environ: dict) -> Optional[str]:
    """Return the ``wizard_session`` cookie value from ``HTTP_COOKIE`` (#175)."""
    raw = environ.get("HTTP_COOKIE")
    if not raw:
        return None
    needle = _SESSION_COOKIE_NAME + "="
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if chunk.startswith(needle):
            return chunk[len(needle):]
    return None


def session_cookie_valid(environ: dict) -> bool:
    """Constant-time validate the ``wizard_session`` cookie (#175 page gate)."""
    return auth_state.validate_session_token(extract_session_cookie(environ))


__all__ = [
    "make_auth_handler",
    "extract_session_header",
    "session_header_valid",
    "extract_session_cookie",
    "session_cookie_valid",
]
