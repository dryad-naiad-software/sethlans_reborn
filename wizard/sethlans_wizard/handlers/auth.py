# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/auth/`` — setup-token validation handler (FR-W7).

Spec 1 / A3.

Behaviour:

* Accepts ``{"token": "<setup-token>"}`` as a JSON request body.
* Compares against the launcher-delivered setup token using
  ``secrets.compare_digest`` (FR-W7 constant-time compare).
* On success, generates a fresh single-active-session token via
  ``secrets.token_urlsafe(32)`` (any prior session is invalidated)
  and returns ``{"status": "ok", "session_token": "<...>"}``.
* On wrong token, returns HTTP 403 + records a failed attempt against
  the source IP (FR-W7 rate limiter).
* When a source IP exceeds 10 failed attempts in 60 seconds, returns
  HTTP 429 with a ``Retry-After: 60`` header.

Security guard rails:

* The setup token is held in process memory only — passed in as a
  closure argument by ``server.create_app`` after the wizard has read
  it from the launcher's chmod-600 ``.setup_token`` file (and unlinked
  the file per SEC-MED-11).
* The session token MUST NOT appear in any URL, query string, or
  fragment (FR-W-FE3a parallel rule for the session token, FR-W-FE3b).
  This handler refuses any request that puts a token-shaped key in the
  query string — a defense-in-depth check; the frontend never does
  that under FR-W-FE3a, but a hostile or buggy client might.
* Per FR-W7 / NF-6: tokens MUST NOT be logged. This module logs
  attempts and rate-limit hits without emitting the token value.
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
    """Extract a stable per-client identifier for rate-limiting.

    Prefers ``REMOTE_ADDR`` (the socket peer). Forwarded headers are
    NOT consulted — the wizard binds ``0.0.0.0`` directly with no
    reverse proxy, so ``REMOTE_ADDR`` is always the real peer. Falls
    back to ``"unknown"`` so a missing key cannot disable the limiter.
    """
    return environ.get("REMOTE_ADDR") or "unknown"


def _query_string_has_session(environ: dict) -> bool:
    """Return True if QUERY_STRING contains a forbidden token key.

    FR-W-FE3a / FR-W-FE3b: the session token (and the setup token)
    MUST NOT appear in any URL, query string, or fragment. Defense in
    depth: refuse the request rather than honour a token that arrived
    out of band.
    """
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
    """Build the WSGI auth handler bound to *setup_token*.

    *setup_token* is the bytes returned by
    ``ipc.read_secret_file(<data_dir>/wizard/.setup_token)``. Stored in
    a closure (not a module-level), held only for the lifetime of the
    wizard process. The bytes are decoded to ASCII at handler-build
    time — setup tokens are URL-safe base64 per FR-L3 / FR-L4
    (``secrets.token_urlsafe(32)``).
    """
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
        return _send_json(
            start_response,
            {"status": "ok", "session_token": session_token},
            status=200,
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


__all__ = [
    "make_auth_handler",
    "extract_session_header",
    "session_header_valid",
]
