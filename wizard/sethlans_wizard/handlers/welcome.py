# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/welcome/`` — welcome-seen checkpoint (FR-M2-1).

Records the ``welcome_seen`` checkpoint to ``.setup_progress.json``
when the user clicks Next on the welcome page. Recording the checkpoint
makes resume logic uniform — re-opening the wizard skips Welcome and
lands on the first incomplete step (FR-CHK3).

Idempotent under FR-CHK1a's per-process progress-file lock.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard import progress
from wizard.sethlans_wizard.checkpoints import WELCOME_SEEN
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)


def make_welcome_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-1."""
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
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )

    if _wsgi.query_string_has_forbidden_key(environ):
        logger.warning(
            "Refused welcome request with token-shaped query string from %s",
            _wsgi.client_ip(environ),
        )
        return _wsgi.send_json(
            start_response,
            {"error": "session token must not appear in URL"},
            status=400,
        )

    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    try:
        progress.append_checkpoint(data_dir, WELCOME_SEEN)
    except OSError as exc:
        logger.error(
            "Could not write welcome checkpoint under %s: %s", data_dir, exc,
        )
        return _wsgi.send_json(
            start_response,
            {"error": "could not record welcome checkpoint"},
            status=500,
        )

    return _wsgi.send_json(start_response, {"status": "ok"}, status=200)


__all__ = ["make_welcome_handler"]
