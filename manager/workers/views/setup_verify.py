# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard verification and summary endpoints.

FR-A13: ``POST /api/setup/verify/`` — runs topology-aware checks,
writes the sentinel FIRST, then returns the response.

FR-A14: ``GET /api/setup/summary/`` — returns post-setup summary data
to the wizard (during setup mode only).  The post-completion admin
counterpart is ``GET /api/manager/summary/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from sethlans_manager import runtime_state
from workers.authentication import SetupPhaseAuthentication
from workers.permissions import IsSetupPhaseUser
from workers.services.setup_lock import (
    setup_conflict_response,
    setup_mutation_lock,
)
from workers.services.setup_session import enforce_setup_session_binding
from workers.services.sentinel import (
    create_sentinel,
    read_sentinel,
)
# Check helpers are re-exported here so ``_run_verification_checks`` and
# existing unit tests can patch them on this module.
from workers.views.setup_verify_checks import (  # noqa: F401
    _check_admin_exists,
    _check_blender,
    _check_db_reachable,
    _check_enrollment_key,
    _check_ffmpeg,
)
from workers.services.auto_enroll import check_local_worker_enrolled
from workers.utils.errors import setup_error

logger = logging.getLogger(__name__)


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_verify_view(request):
    """POST /api/setup/verify/ (FR-A13)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        return _setup_verify_locked()


def _setup_verify_locked():
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)

    # If sentinel already has completed_at, return cached result
    if sentinel and sentinel.get("completed_at"):
        return Response({
            "checks": [],
            "all_passed": True,
        })

    topology = sentinel.get("topology") if sentinel else None
    checkpoint_list = sentinel.get("checkpoints", []) if sentinel else []
    checks = _run_verification_checks(data_dir, topology)
    all_passed = all(c["passed"] for c in checks)

    if all_passed:
        try:
            create_sentinel(data_dir, topology or "manager", checkpoint_list)
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
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_summary_view(request):
    """GET /api/setup/summary/ (FR-A14).

    Served only during setup mode.  Post-setup access moves to
    ``GET /api/manager/summary/`` (admin-only).
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)

    if not sentinel or not sentinel.get("completed_at"):
        return setup_error(
            "precondition_unmet", "Setup not yet verified", 409,
        )

    return Response(_build_summary_payload(data_dir, sentinel))


def _build_summary_payload(data_dir: Path, sentinel: dict) -> dict:
    """Assemble the post-setup summary payload."""
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
        "topology": sentinel.get("topology", ""),
    }


def _run_verification_checks(
    data_dir: Path, topology: str | None,
) -> list[dict]:
    """Run the topology-aware verification checklist.

    The ``worker_only`` topology omits the ffmpeg check: a worker_only
    manager never renders, so there is no reason to require an ffmpeg
    binary to satisfy verify (issue #127).  Mirrors the topology gate
    in ``setup_verify_checks.run_verification_checks``.
    """
    checks = []
    checks.append(_check_db_reachable())
    checks.append(_check_admin_exists())
    if topology != "worker_only":
        checks.append(_check_ffmpeg(data_dir))
    checks.append(_check_enrollment_key())

    if topology == "manager_worker":
        checks.append(_check_blender(data_dir))
        checks.append(check_local_worker_enrolled())

    return checks
