# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard account endpoints: admin user and worker UI password.

FR-A5: ``POST /api/setup/admin-user/``
FR-A6: ``POST /api/setup/worker-password/``
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.authentication import SetupPhaseAuthentication
from workers.permissions import IsSetupPhaseUser
from workers.services.setup_lock import (
    setup_conflict_response,
    setup_mutation_lock,
)
from workers.services.setup_session import enforce_setup_session_binding
from workers.services.auto_enroll import auto_enroll_local_worker
from workers.services.filesystem_trust import (
    get_worker_config_path,
    write_worker_config,
)
from workers.services import checkpoints
from workers.services.sentinel import append_checkpoint, read_sentinel
from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
    set_worker_ui_password,
)
from workers.utils.errors import setup_error

logger = logging.getLogger(__name__)

_MIN_WORKER_PASSWORD_LEN = 8


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _get_ini_path() -> Path:
    return _get_data_dir() / "manager.ini"


def _try_auto_enroll_local_worker() -> bool:
    """FR-FT2: auto-enroll the co-located worker if manager_worker."""
    sentinel = read_sentinel(_get_data_dir())
    if not sentinel or sentinel.get("topology") != "manager_worker":
        return False

    try:
        enrollment = auto_enroll_local_worker()
        config_path = get_worker_config_path()
        write_worker_config(
            config_path,
            enrollment["api_token"],
            enrollment["cert_fingerprint"],
            enrollment["manager_url"],
            enrollment["manager_id"],
        )
        logger.info("Local worker auto-enrolled at %s", config_path)
        return True
    except Exception:
        logger.exception("Failed to auto-enroll local worker")
        return False


def _validate_admin_fields(data):
    """Return ``(username, email, password)`` or an error Response."""
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    password_confirm = data.get("password_confirm", "")

    if not username:
        return setup_error(
            "invalid_input", "Username is required.", 400,
        )
    if not password:
        return setup_error(
            "invalid_input", "Password is required.", 400,
        )
    if password != password_confirm:
        return setup_error(
            "invalid_input", "Passwords do not match.", 400,
        )
    return username, email, password


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_admin_user_view(request):
    """POST /api/setup/admin-user/ (FR-A5)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        return _setup_admin_user_locked(request)


def _setup_admin_user_locked(request):
    result = _validate_admin_fields(request.data)
    if isinstance(result, Response):
        return result
    username, email, password = result

    try:
        user = create_admin_user(username, email, password)
    except ValidationError as exc:
        messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
        if any("already taken" in m.lower() for m in messages):
            return setup_error(
                "precondition_unmet",
                "Admin user already exists.",
                409,
                details={"username": username},
            )
        return setup_error(
            "invalid_input",
            "; ".join(messages),
            400,
            details={"errors": messages},
        )

    # Also generate enrollment key when admin is created
    try:
        generate_enrollment_key()
    except Exception:
        logger.exception("Failed to generate enrollment key")

    # FR-FT2: Auto-enroll local worker for manager_worker topology
    local_worker_enrolled = _try_auto_enroll_local_worker()

    append_checkpoint(_get_data_dir(), checkpoints.ADMIN_CREATED)
    response_data = {"status": "ok", "username": user.username}
    if local_worker_enrolled:
        response_data["local_worker_enrolled"] = True
    return Response(response_data)


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_worker_password_view(request):
    """POST /api/setup/worker-password/ (FR-A6)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        return _setup_worker_password_locked(request)


def _setup_worker_password_locked(request):
    password = request.data.get("password", "")
    if not password or len(password) < _MIN_WORKER_PASSWORD_LEN:
        return setup_error(
            "invalid_input",
            f"Password must be at least "
            f"{_MIN_WORKER_PASSWORD_LEN} characters.",
            400,
        )

    # Worker config lives alongside manager.ini in dev mode
    config_path = _get_data_dir() / "worker.ini"

    try:
        set_worker_ui_password(config_path, password)
    except Exception as exc:
        logger.exception("Failed to set worker UI password")
        return setup_error(
            "internal_error",
            f"Failed to set password: {exc}",
            500,
        )

    append_checkpoint(_get_data_dir(), checkpoints.WORKER_PASSWORD_SET)
    return Response({"status": "ok"})
