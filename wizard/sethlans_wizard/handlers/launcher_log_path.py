# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/wizard/launcher-log-path/`` — surface the launcher log path.

Spec 1 / B4. The redirecting page (``/redirecting``) needs to display the
launcher's log path in two places:

* The 60-second booting fallback message ("we've been booting for a while
  — open this file with your text editor to see what's going on").
* The ``status: "failed"`` error surface (the runtime-ready handler
  embeds ``log_path`` in its own response, but the redirecting page also
  uses this endpoint at load time so it can show the path immediately if
  ``fetch()`` to ``/api/wizard/runtime-ready/`` rejects before any poll
  succeeds — see FE-v2.2-MED-1 post-failsafe recovery).

The launcher writes its log path to ``<data_dir>/wizard/.launcher_log_path``
(per FR-W-FE6). If that file is missing — A6 may not yet have wired the
write — this handler returns an empty string so the page can fall back
to a generic "log path not available" caption rather than crashing.

Security:

* Session-gated like every other wizard data endpoint
  (``X-Wizard-Session`` header validated via
  :func:`session_header_valid`).
* Forbidden query-string keys → 400 (defense in depth, mirrors the other
  handlers — SEC-MED-12).
* Path is a LOCAL FILESYSTEM PATH, never a URL. The wizard server does
  NOT serve log content (SEC-MED-6 / SEC-v2.3-LOW-1) — the user opens
  the file with their text editor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)


def _read_launcher_log_path(data_dir: Path) -> str:
    """Return the launcher log path; empty string if missing/unreadable.

    Mirrors :func:`wizard.sethlans_wizard.handlers.runtime_ready
    ._read_launcher_log_path` so the redirecting page sees the same value
    the runtime-ready handler embeds in its ``failed`` envelope.
    """
    candidate = data_dir / "wizard" / ".launcher_log_path"
    try:
        raw = candidate.read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def make_launcher_log_path_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir*."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "GET")],
        )

    if _wsgi.query_string_has_forbidden_key(environ):
        logger.warning(
            "Refused launcher-log-path request with forbidden query key from %s",
            _wsgi.client_ip(environ),
        )
        return _wsgi.send_json(
            start_response,
            {"error": "session token / url must not appear in URL"},
            status=400,
        )

    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    return _wsgi.send_json(
        start_response,
        {"path": _read_launcher_log_path(data_dir)},
        status=200,
    )


__all__ = ["make_launcher_log_path_handler"]
