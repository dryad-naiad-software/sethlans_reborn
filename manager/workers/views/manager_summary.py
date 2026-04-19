# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Post-setup manager summary endpoint (``GET /api/manager/summary/``).

Returns the same payload that the old ``setup_summary_view`` served
during the wizard, but from the admin side of the house so enrolled
admins can re-fetch the enrollment key / fingerprint after setup
completes.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import (
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from sethlans_manager import runtime_state
from shared.frozen_paths import get_data_dir, is_frozen
from workers.services.sentinel import read_sentinel

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _build_summary_payload(data_dir: Path, sentinel: dict) -> dict:
    ini_path = data_dir / "manager.ini"
    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)
    host = config.get("server", "host", fallback="0.0.0.0")
    port = config.get("server", "port", fallback="8080")
    if host == "0.0.0.0":
        host = "localhost"
    manager_url = f"https://{host}:{port}"

    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    admin_username = admin.username if admin else ""

    from workers.models import ManagerSettings
    try:
        ms = ManagerSettings.objects.get(pk=1)
        enrollment_key = ms.enrollment_key
    except ManagerSettings.DoesNotExist:
        enrollment_key = ""

    return {
        "manager_url": manager_url,
        "admin_username": admin_username,
        "enrollment_key": enrollment_key,
        "cert_fingerprint": runtime_state.cert_fingerprint or "",
        "topology": sentinel.get("topology", "") if sentinel else "",
    }


@extend_schema(tags=["Manager"])
@api_view(["GET"])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, IsAdminUser])
def manager_summary_view(request):
    """GET /api/manager/summary/ — admin-only post-setup summary."""
    data_dir = _data_dir()
    sentinel = read_sentinel(data_dir)
    return Response(_build_summary_payload(data_dir, sentinel or {}))
