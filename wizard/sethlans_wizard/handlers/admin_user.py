# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/admin-user/`` — Admin user step (FR-M2-5).

Validates the candidate admin tuple via the stdlib password validators
in ``wizard.sethlans_wizard.password_validators`` and stashes the
validated tuple into the in-memory wizard state. The plaintext
password lives in process memory only — it is NEVER written to
``manager.ini``, the wizard log, the launcher log, or any sentinel
until FR-M2-9's pending-setup serialization (FR-PEND4 / NF-6).

Failure responses return HTTP 400 with the union of failure codes —
the caller surfaces them via the page UI. Resource-integrity failure
(common-passwords file missing or hash mismatch) is HTTP 500
``common_passwords_resource_invalid`` per FR-M2-5 fail-closed rule.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard import progress, wizard_state
from wizard.sethlans_wizard.checkpoints import ADMIN_VALIDATED
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.password_validators import (
    validate_password,
    verify_resource,
)

logger = logging.getLogger(__name__)


def _basic_field_check(payload: dict) -> tuple[str, str, str, str] | str:
    """Pull username/email/password/confirm from *payload*.

    Returns a 4-tuple on success or an error code string on shape
    failure.
    """
    username = payload.get("username")
    email = payload.get("email")
    password = payload.get("password")
    confirm = payload.get("password_confirm")
    for name, value in (
        ("username", username),
        ("email", email),
        ("password", password),
        ("password_confirm", confirm),
    ):
        if not isinstance(value, str) or not value:
            return f"{name}_required"
    if password != confirm:
        return "password_mismatch"
    return username, email, password, confirm  # type: ignore[return-value]


def make_admin_user_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-5."""
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

    # Resource fail-closed (FR-M2-5).
    resource_error = verify_resource()
    if resource_error is not None:
        logger.critical("admin-user handler refused: %s", resource_error)
        return _wsgi.send_json(
            start_response,
            {"error": resource_error},
            status=500,
        )

    field_check = _basic_field_check(payload)
    if isinstance(field_check, str):
        return _wsgi.send_json(
            start_response,
            {"error": field_check},
            status=400,
        )
    username, email, password, _confirm = field_check

    failures = validate_password(password, user_attrs=[username, email])
    if failures:
        # Resource-integrity failures bubble through the validator as
        # ``common_passwords_resource_invalid`` — promote to 500.
        if "common_passwords_resource_invalid" in failures:
            return _wsgi.send_json(
                start_response,
                {"error": "common_passwords_resource_invalid"},
                status=500,
            )
        return _wsgi.send_json(
            start_response,
            {"error": "password_invalid", "failures": failures},
            status=400,
        )

    # Stash the validated tuple. NEVER log the password.
    wizard_state.set_admin(username, email, password)
    progress.append_checkpoint(data_dir, ADMIN_VALIDATED)
    return _wsgi.send_json(
        start_response,
        {"status": "ok", "username": username},
        status=200,
    )


__all__ = ["make_admin_user_handler"]
