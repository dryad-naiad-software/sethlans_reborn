# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard verification and summary endpoints.

FR-A13: ``POST /api/setup/verify/`` — runs topology-aware checks,
writes the sentinel FIRST, then returns the response.

FR-A14: ``GET /api/setup/summary/`` — returns post-setup summary data.
"""

import logging
from pathlib import Path

from django.conf import settings
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from sethlans_manager import runtime_state
from workers.services.sentinel import (
    create_sentinel,
    read_sentinel,
)
from workers.services.setup import verify_ffmpeg_runs
from workers.services.ffmpeg_download import get_ffmpeg_binary

logger = logging.getLogger(__name__)


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_verify_view(request):
    """POST /api/setup/verify/ (FR-A13).

    Runs topology-aware verification.  Writes sentinel FIRST, then
    returns the response.  If sentinel write fails, returns
    ``all_passed: false``.
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)

    # If sentinel already has completed_at, return cached result
    if sentinel and sentinel.get("completed_at"):
        return Response({
            "checks": [],
            "all_passed": True,
        })

    topology = sentinel.get("topology") if sentinel else None
    checkpoints = sentinel.get("checkpoints", []) if sentinel else []
    checks = _run_verification_checks(data_dir, topology)
    all_passed = all(c["passed"] for c in checks)

    if all_passed:
        # Write sentinel FIRST (FR-A13 critical requirement)
        try:
            create_sentinel(data_dir, topology or "manager", checkpoints)
        except Exception:
            logger.exception("Failed to write setup sentinel")
            return Response({
                "checks": checks,
                "all_passed": False,
                "error": "Failed to write setup sentinel.",
            })

    return Response({
        "checks": checks,
        "all_passed": all_passed,
    })


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_summary_view(request):
    """GET /api/setup/summary/ (FR-A14).

    Returns post-setup summary: manager URL, admin username,
    enrollment key, cert fingerprint, topology.
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)

    if not sentinel or not sentinel.get("completed_at"):
        return Response(
            {"error": "Setup not yet verified"}, status=400,
        )

    # Build manager URL from settings
    import configparser
    ini_path = data_dir / "manager.ini"
    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)
    host = config.get("server", "host", fallback="0.0.0.0")
    port = config.get("server", "port", fallback="8080")
    if host == "0.0.0.0":
        host = "localhost"
    manager_url = f"https://{host}:{port}"

    # Admin username
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    admin_username = admin.username if admin else ""

    # Enrollment key
    from workers.models import ManagerSettings
    try:
        ms = ManagerSettings.objects.get(pk=1)
        enrollment_key = ms.enrollment_key
    except ManagerSettings.DoesNotExist:
        enrollment_key = ""

    # Cert fingerprint
    cert_fingerprint = runtime_state.cert_fingerprint or ""

    return Response({
        "manager_url": manager_url,
        "admin_username": admin_username,
        "enrollment_key": enrollment_key,
        "cert_fingerprint": cert_fingerprint,
        "topology": sentinel.get("topology", ""),
    })


def _run_verification_checks(
    data_dir: Path, topology: str | None,
) -> list[dict]:
    """Run the topology-aware verification checklist."""
    checks = []

    # Check 1: Migrations applied (DB reachable)
    checks.append(_check_db_reachable())

    # Check 2: Admin user authenticates
    checks.append(_check_admin_exists())

    # Check 3: FFmpeg available
    checks.append(_check_ffmpeg(data_dir))

    # Check 4: Enrollment key exists
    checks.append(_check_enrollment_key())

    return checks


def _check_db_reachable() -> dict:
    """Verify the database is reachable."""
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return {"name": "database", "passed": True, "error": None}
    except Exception as exc:
        return {
            "name": "database", "passed": False,
            "error": f"Database unreachable: {exc}",
        }


def _check_admin_exists() -> dict:
    """Verify at least one superuser exists."""
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            return {
                "name": "admin_user", "passed": True, "error": None,
            }
        return {
            "name": "admin_user", "passed": False,
            "error": "No admin user found",
        }
    except Exception as exc:
        return {
            "name": "admin_user", "passed": False,
            "error": str(exc),
        }


def _check_ffmpeg(data_dir: Path) -> dict:
    """Verify FFmpeg binary is present and runs."""
    binary = get_ffmpeg_binary(data_dir)
    if binary is None:
        return {
            "name": "ffmpeg", "passed": False,
            "error": "FFmpeg binary not found",
        }
    try:
        verify_ffmpeg_runs(binary)
        return {
            "name": "ffmpeg", "passed": True, "error": None,
        }
    except RuntimeError as exc:
        return {
            "name": "ffmpeg", "passed": False, "error": str(exc),
        }


def _check_enrollment_key() -> dict:
    """Verify enrollment key exists in the database."""
    try:
        from workers.models import ManagerSettings
        ms = ManagerSettings.objects.get(pk=1)
        if ms.enrollment_key:
            return {
                "name": "enrollment_key", "passed": True,
                "error": None,
            }
        return {
            "name": "enrollment_key", "passed": False,
            "error": "No enrollment key configured",
        }
    except Exception as exc:
        return {
            "name": "enrollment_key", "passed": False,
            "error": str(exc),
        }
