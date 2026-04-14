# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard download endpoints for FFmpeg and Blender.

FR-A7 through FR-A12: start, progress, and cancel endpoints for
background download tasks.  Each download type uses a tag prefix
(``ffmpeg_`` / ``blender_``) to guard against duplicate starts.
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
from workers.services.sentinel import append_checkpoint, read_sentinel
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

logger = logging.getLogger(__name__)


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _setup_complete() -> bool:
    sentinel = read_sentinel(_get_data_dir())
    return sentinel is not None and sentinel.get("completed_at") is not None


# ---- FFmpeg endpoints (FR-A7, FR-A8, FR-A9) ----

@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_ffmpeg_start_view(request):
    """POST /api/setup/ffmpeg/start/ (FR-A7).

    Starts FFmpeg download in background.  Returns existing task if
    one is already in progress.
    """
    if _setup_complete():
        return Response(status=404)

    data_dir = _get_data_dir()

    # Already installed? (FR-FF5)
    if ffmpeg_already_installed(data_dir):
        append_checkpoint(data_dir, "ffmpeg_installed")
        return Response({
            "status": "already_installed",
            "task_id": None,
        })

    # Duplicate guard (FR-A7 idempotency)
    existing = find_active_task("ffmpeg_")
    if existing:
        tid, prog = existing
        return Response({
            "status": "in_progress",
            "task_id": tid,
        })

    task_id, _ = create_tagged_task("ffmpeg_")
    start_ffmpeg_download(task_id, data_dir)

    return Response({"status": "started", "task_id": task_id})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_ffmpeg_progress_view(request, task_id):
    """GET /api/setup/ffmpeg/progress/<task_id>/ (FR-A8)."""
    progress = get_task(task_id)
    if progress is None:
        return Response({"status": "not_found"}, status=404)

    resp = {
        "status": progress.status,
        "percent": progress.percent,
        "error": progress.error,
    }

    # Record checkpoint on completion
    if progress.status == "complete":
        append_checkpoint(_get_data_dir(), "ffmpeg_installed")

    return Response(resp)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_ffmpeg_cancel_view(request):
    """POST /api/setup/ffmpeg/cancel/ (FR-A9)."""
    if _setup_complete():
        return Response(status=404)

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
@authentication_classes([])
@permission_classes([AllowAny])
def setup_blender_start_view(request):
    """POST /api/setup/blender/start/ (FR-A10).

    Starts Blender pre-download.  Reads the default version from the
    ``SupportedBlenderVersion`` model.
    """
    if _setup_complete():
        return Response(status=404)

    version_info = _get_default_blender_version()
    if not version_info:
        return Response(
            {"error": "No default Blender version configured"},
            status=400,
        )

    series, resolved = version_info
    data_dir = _get_data_dir()

    # Already installed?
    if blender_already_installed(data_dir, resolved):
        append_checkpoint(data_dir, "blender_predownloaded")
        return Response({
            "status": "already_installed",
            "task_id": None,
            "version": resolved,
        })

    # Duplicate guard
    existing = find_active_task("blender_")
    if existing:
        tid, prog = existing
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
@authentication_classes([])
@permission_classes([AllowAny])
def setup_blender_progress_view(request, task_id):
    """GET /api/setup/blender/progress/<task_id>/ (FR-A11)."""
    progress = get_task(task_id)
    if progress is None:
        return Response({"status": "not_found"}, status=404)

    resp = {
        "status": progress.status,
        "percent": progress.percent,
        "error": progress.error,
    }

    if progress.status == "complete":
        append_checkpoint(_get_data_dir(), "blender_predownloaded")

    return Response(resp)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_blender_cancel_view(request):
    """POST /api/setup/blender/cancel/ (FR-A12)."""
    if _setup_complete():
        return Response(status=404)

    active = find_active_task("blender_")
    if not active:
        return Response({"status": "not_found"})

    _, progress = active
    progress.cancel_event.set()
    return Response({"status": "cancelled"})
