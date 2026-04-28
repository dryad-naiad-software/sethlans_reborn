# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Static file handlers for the wizard frontend (Spec 1 / B2 + FR-W-FE2).

Serves the three wizard pages and their vendored assets out of
``wizard/frontend/static/``:

* ``GET /``                          → ``index.html`` (token entry page)
* ``GET /static/vendor/<path>``      → vendored JS/CSS/etc.
* ``GET /static/css/<path>``         → page-specific CSS overrides

All HTML responses carry the FR-W-FE2 security headers
(Content-Security-Policy, X-Content-Type-Options, Referrer-Policy,
Permissions-Policy).

Path traversal protection is the critical security control here: the
incoming PATH_INFO is resolved against the static root and rejected
with 404 unless the resolved path stays inside the root.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

# FR-W-FE2 security headers applied to every HTML response.
#
# Issue #146 — `script-src` no longer carries `'unsafe-inline'`. After
# Phase F2 extracted page scripts to ES modules under `static/js/` and
# the legacy-browser fallback was lifted out of inline `<script
# nomodule>` blocks into `static/js/legacy-fallback.js`, every `<script>`
# tag on the wizard pages has a `src=` — there are no inline script
# bodies to authorise.
#
# Issue #171 — `'unsafe-eval'` IS required in `script-src`. Petite-vue
# compiles every reactive expression (v-model, :class, :disabled,
# @submit, etc.) with `new Function(...)`, which CSP categorises under
# `'unsafe-eval'`. Without it the bindings silently fail and the wizard
# is unusable from the very first page. The wizard runs only on
# loopback, never accepts external scripts (`script-src 'self'` still
# blocks third-party injection), and renders no untrusted content, so
# the marginal XSS-eval risk is acceptable.
#
# `style-src` keeps `'unsafe-inline'` because Bootstrap 5 injects style
# attributes via JS (e.g. for tooltips, collapses, modal backdrops);
# tightening that without breaking the UI would require nonces or
# hashes and is out of scope here.
_HTML_SECURITY_HEADERS: list[tuple[str, str]] = [
    (
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "form-action 'self'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "interest-cohort=()"),
]

# Map file suffix → response Content-Type. Anything else is 404.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _send_404(start_response: Callable) -> Iterable[bytes]:
    body = b'{"error": "Not Found"}'
    start_response(
        "404 Not Found",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _send_405(start_response: Callable) -> Iterable[bytes]:
    body = b'{"error": "Method Not Allowed"}'
    start_response(
        "405 Method Not Allowed",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Allow", "GET, HEAD"),
        ],
    )
    return [body]


def _resolve_safe(static_root: Path, rel: str) -> Path | None:
    """Return *rel* resolved under *static_root*, or None if traversal.

    ``rel`` is the slash-prefixed-or-bare PATH_INFO segment beneath the
    URL prefix (e.g., for ``GET /static/vendor/foo.js`` the caller
    passes ``"vendor/foo.js"``). The path is rejected if it contains a
    NUL byte, references parent directories that escape *static_root*,
    or otherwise resolves outside the root.
    """
    if rel is None:
        return None
    rel = rel.lstrip("/")
    if not rel:
        return None
    if "\x00" in rel:
        return None
    candidate = (static_root / rel).resolve()
    try:
        candidate.relative_to(static_root.resolve())
    except ValueError:
        return None
    return candidate


def _serve_file(
    path: Path,
    start_response: Callable,
    method: str,
    extra_headers: list[tuple[str, str]] | None = None,
) -> Iterable[bytes]:
    """Stream *path* as a 200 response with the right Content-Type."""
    if not path.is_file():
        return _send_404(start_response)
    suffix = path.suffix.lower()
    content_type = _CONTENT_TYPES.get(suffix)
    if content_type is None:
        # Refuse to serve unknown types — defense in depth against a
        # static drop accidentally exposing a sensitive file.
        return _send_404(start_response)

    body = path.read_bytes()
    headers: list[tuple[str, str]] = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response("200 OK", headers)
    if method == "HEAD":
        return [b""]
    return [body]


def make_index_handler(
    static_root: Path,
    filename: str = "index.html",
) -> Callable:
    """Build a WSGI handler that serves a single HTML page at a fixed route.

    Defaults to ``index.html`` (B2 / token entry) but takes *filename*
    so other wizard pages (B3 ``topology.html``, B4 ``redirecting.html``)
    can reuse the same security-headers / 405 / serve-file plumbing
    without duplicating the factory.
    """
    static_root = Path(static_root)
    page_path = static_root / filename

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method not in ("GET", "HEAD"):
            return _send_405(start_response)
        return _serve_file(
            page_path,
            start_response,
            method,
            extra_headers=_HTML_SECURITY_HEADERS,
        )

    return handler


def make_static_handler(static_root: Path, url_prefix: str) -> Callable:
    """Build a WSGI handler that serves ``static_root`` under *url_prefix*.

    ``url_prefix`` MUST end with a slash (e.g., ``"/static/vendor/"``).
    Requests whose ``PATH_INFO`` does not start with the prefix get
    404; requests that pass path-traversal validation are streamed.
    """
    if not url_prefix.endswith("/"):
        raise ValueError("url_prefix must end with '/'")
    static_root = Path(static_root)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method not in ("GET", "HEAD"):
            return _send_405(start_response)
        path = environ.get("PATH_INFO", "") or ""
        if not path.startswith(url_prefix):
            return _send_404(start_response)
        rel = path[len(url_prefix):]
        target = _resolve_safe(static_root, rel)
        if target is None:
            logger.warning(
                "Rejected static path traversal attempt: %r",
                path,
            )
            return _send_404(start_response)
        # FR-W-FE2 security headers apply to HTML responses; vendored
        # JS/CSS get nosniff + Referrer-Policy as a baseline so a
        # browser cannot mime-sniff a JS file into HTML, but no CSP
        # because CSP is enforced via the document, not the asset.
        baseline_headers = [
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ]
        return _serve_file(
            target,
            start_response,
            method,
            extra_headers=baseline_headers,
        )

    return handler


__all__ = [
    "make_index_handler",
    "make_static_handler",
]
