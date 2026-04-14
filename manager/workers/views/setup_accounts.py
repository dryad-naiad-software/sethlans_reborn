# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard account endpoints: admin user and worker UI password.

FR-A5: ``POST /api/setup/admin-user/``
FR-A6: ``POST /api/setup/worker-password/``
"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.services.sentinel import append_checkpoint, read_sentinel
from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
    set_worker_ui_password,
)

logger = logging.getLogger(__name__)

_MIN_WORKER_PASSWORD_LEN = 8


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _get_ini_path() -> Path:
    return _get_data_dir() / "manager.ini"


def _setup_complete() -> bool:
    sentinel = read_sentinel(_get_data_dir())
    return sentinel is not None and sentinel.get("completed_at") is not None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_admin_user_view(request):
    """POST /api/setup/admin-user/ (FR-A5).

    Creates a Django superuser.  Returns 409 if the username already
    exists.
    """
    if _setup_complete():
        return Response(status=404)

    username = request.data.get("username", "").strip()
    email = request.data.get("email", "").strip()
    password = request.data.get("password", "")
    password_confirm = request.data.get("password_confirm", "")

    if not username:
        return Response(
            {"errors": ["Username is required."]}, status=400,
        )
    if not password:
        return Response(
            {"errors": ["Password is required."]}, status=400,
        )
    if password != password_confirm:
        return Response(
            {"errors": ["Passwords do not match."]}, status=400,
        )

    try:
        user = create_admin_user(username, email, password)
    except ValidationError as exc:
        messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
        # Check for "already taken" to return 409
        if any("already taken" in m.lower() for m in messages):
            return Response(
                {"error": "admin_exists", "username": username},
                status=409,
            )
        return Response({"errors": messages}, status=400)

    # Also generate enrollment key when admin is created
    try:
        generate_enrollment_key()
    except Exception:
        logger.exception("Failed to generate enrollment key")

    append_checkpoint(_get_data_dir(), "admin_created")
    return Response({"status": "ok", "username": user.username})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_worker_password_view(request):
    """POST /api/setup/worker-password/ (FR-A6).

    Accepts ``{"password": str, "use_admin_password": bool}``.
    """
    if _setup_complete():
        return Response(status=404)

    password = request.data.get("password", "")
    if not password or len(password) < _MIN_WORKER_PASSWORD_LEN:
        return Response(
            {"error": f"Password must be at least "
                      f"{_MIN_WORKER_PASSWORD_LEN} characters."},
            status=400,
        )

    # Worker config lives alongside manager.ini in dev mode
    config_path = _get_data_dir() / "worker.ini"

    try:
        set_worker_ui_password(config_path, password)
    except Exception as exc:
        logger.exception("Failed to set worker UI password")
        return Response(
            {"error": f"Failed to set password: {exc}"}, status=500,
        )

    append_checkpoint(_get_data_dir(), "worker_password_set")
    return Response({"status": "ok"})
