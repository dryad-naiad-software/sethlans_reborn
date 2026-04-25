# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard download endpoints for FFmpeg and Blender.

FR-A7 through FR-A12: start, progress, and cancel endpoints for
background download tasks.
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
from workers.authentication import SetupPhaseAuthentication
from workers.permissions import IsSetupPhaseUser
from workers.services.setup_lock import (
    setup_conflict_response,
    setup_mutation_lock,
)
from workers.services.setup_session import enforce_setup_session_binding
from workers.services import checkpoints
from workers.services.sentinel import append_checkpoint
from workers.services.download_progress import (
    create_tagged_task,
    find_active_task,
    get_task,
)
from workers.services.ffmpeg_download import (
    ffmpeg_already_installed,
    start_ffmpeg_download,
)
from workers.services.blender_download import (
    blender_already_installed,
    start_blender_download,
)
from workers.utils.errors import setup_error

logger = logging.getLogger(__name__)


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


# ---- FFmpeg endpoints (FR-A7, FR-A8, FR-A9) ----

@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_ffmpeg_start_view(request):
    """POST /api/setup/ffmpeg/start/ (FR-A7)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        return _setup_ffmpeg_start_locked()


def _setup_ffmpeg_start_locked():
    data_dir = _get_data_dir()

    # Already installed? (FR-FF5)
    if ffmpeg_already_installed(data_dir):
        append_checkpoint(data_dir, checkpoints.FFMPEG_INSTALLED)
        return Response({
            "status": "already_installed",
            "task_id": None,
        })

    # Duplicate guard (FR-A7 idempotency)
    existing = find_active_task("ffmpeg_")
    if existing:
        tid, _prog = existing
        return Response({
            "status": "in_progress",
            "task_id": tid,
        })

    task_id, _ = create_tagged_task("ffmpeg_")
    start_ffmpeg_download(task_id, data_dir)

    return Response({"status": "started", "task_id": task_id})


@api_view(["GET"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_ffmpeg_progress_view(request, task_id):
    """GET /api/setup/ffmpeg/progress/<task_id>/ (FR-A8)."""
    progress = get_task(task_id)
    if progress is None:
        return setup_error(
            "precondition_unmet", "Task not found.", 404,
        )

    resp = {
        "status": progress.status,
        "percent": progress.percent,
        "error": progress.error,
    }

    if progress.status == "complete":
        append_checkpoint(_get_data_dir(), checkpoints.FFMPEG_INSTALLED)

    return Response(resp)


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_ffmpeg_cancel_view(request):
    """POST /api/setup/ffmpeg/cancel/ (FR-A9)."""
    enforce_setup_session_binding(request)
    active = find_active_task("ffmpeg_")
    if not active:
        return Response({"status": "not_found"})

    _, progress = active
    progress.cancel_event.set()
    return Response({"status": "cancelled"})


# ---- Blender endpoints (FR-A10, FR-A11, FR-A12) ----

def _get_default_blender_version() -> tuple[str, str] | None:
    """Return (series, resolved_version) for the default Blender."""
    from workers.models import SupportedBlenderVersion
    try:
        default = SupportedBlenderVersion.objects.filter(
            is_default=True,
        ).first()
        if default:
            return default.series, default.resolved_version
    except Exception:
        pass
    return None


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_blender_start_view(request):
    """POST /api/setup/blender/start/ (FR-A10)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        return _setup_blender_start_locked()


def _setup_blender_start_locked():
    version_info = _get_default_blender_version()
    if not version_info:
        return setup_error(
            "invalid_input",
            "No default Blender version configured",
            400,
        )

    series, resolved = version_info
    data_dir = _get_data_dir()

    if blender_already_installed(data_dir, resolved):
        append_checkpoint(data_dir, checkpoints.BLENDER_PREDOWNLOADED)
        return Response({
            "status": "already_installed",
            "task_id": None,
            "version": resolved,
        })

    existing = find_active_task("blender_")
    if existing:
        tid, _prog = existing
        return Response({
            "status": "in_progress",
            "task_id": tid,
            "version": resolved,
        })

    task_id, _ = create_tagged_task("blender_")
    start_blender_download(task_id, data_dir, resolved)

    return Response({
        "status": "started",
        "task_id": task_id,
        "version": resolved,
    })


@api_view(["GET"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_blender_progress_view(request, task_id):
    """GET /api/setup/blender/progress/<task_id>/ (FR-A11)."""
    progress = get_task(task_id)
    if progress is None:
        return setup_error(
            "precondition_unmet", "Task not found.", 404,
        )

    resp = {
        "status": progress.status,
        "percent": progress.percent,
        "error": progress.error,
    }

    if progress.status == "complete":
        append_checkpoint(_get_data_dir(), checkpoints.BLENDER_PREDOWNLOADED)

    return Response(resp)


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_blender_cancel_view(request):
    """POST /api/setup/blender/cancel/ (FR-A12)."""
    enforce_setup_session_binding(request)
    active = find_active_task("blender_")
    if not active:
        return Response({"status": "not_found"})

    _, progress = active
    progress.cancel_event.set()
    return Response({"status": "cancelled"})
