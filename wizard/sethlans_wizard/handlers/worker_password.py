# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/worker-password/`` — Worker UI password (FR-M2-6).

Hashes the operator-supplied worker UI password using PBKDF2-HMAC-SHA-
256 with parameters identical to
``worker/sethlans_worker_agent/web_ui/auth.py:28-31, 67-74`` (SHA-256,
100 000 iterations, 16-byte salt from ``os.urandom(16)``). The hex
hash + salt are stashed into the in-memory wizard state — the
pending-setup handler (FR-PEND2) reads them out at FR-M2-9.

Validation:
* ``password`` must be a non-empty string with length ≥ 8 (NF-8).
* ``use_admin_password`` is informational metadata; the frontend
  re-sends the admin password from its own in-memory state when this
  flag is True. The wizard does NOT read the admin password back from
  ``pending_setup.json`` — it would not yet exist at this point.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard import progress, wizard_state
from wizard.sethlans_wizard.checkpoints import WORKER_PASSWORD_SET
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)

# Match ``worker/sethlans_worker_agent/web_ui/auth.py`` exactly.
PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 100_000
SALT_LENGTH = 16
MIN_PASSWORD_LENGTH = 8


def hash_worker_password(password: str) -> tuple[str, str]:
    """Hash *password* and return ``(hash_hex, salt_hex)``.

    A fresh 16-byte salt is generated on each call (FR-M2-6 idempotency
    note: re-submission generates a new salt + hash).
    """
    salt = os.urandom(SALT_LENGTH)
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO,
        password.encode("utf-8"),
        salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return derived.hex(), salt.hex()


def make_worker_password_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-6."""
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
    body = _wsgi.read_body(environ)
    if len(body) > _wsgi.BODY_MAX:
        return _wsgi.send_json(
            start_response,
            {"error": "request body too large"},
            status=400,
        )
    payload = _wsgi.parse_json_body(body)
    if payload is None:
        return _wsgi.send_json(
            start_response,
            {"error": "request body must be JSON"},
            status=400,
        )

    password = payload.get("password")
    if not isinstance(password, str) or not password:
        return _wsgi.send_json(
            start_response,
            {"error": "password_required"},
            status=400,
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return _wsgi.send_json(
            start_response,
            {"error": "password_too_short"},
            status=400,
        )

    hash_hex, salt_hex = hash_worker_password(password)
    wizard_state.set_worker_password_hash(hash_hex, salt_hex)
    progress.append_checkpoint(data_dir, WORKER_PASSWORD_SET)
    return _wsgi.send_json(
        start_response,
        {"status": "ok"},
        status=200,
    )


__all__ = ["make_worker_password_handler", "hash_worker_password"]
