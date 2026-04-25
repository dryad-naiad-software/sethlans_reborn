# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Topology-aware verification checks for ``POST /api/setup/verify/``.

Extracted from ``setup_verify.py`` to keep that module under the
project's 300-line ceiling.  Each ``_check_*`` returns a
``{"name", "passed", "error"}`` dict.
"""

from pathlib import Path

from workers.services import checkpoints
from workers.services.setup import verify_blender_runs, verify_ffmpeg_runs
from workers.services.ffmpeg_download import get_ffmpeg_binary
from workers.services.auto_enroll import check_local_worker_enrolled
from workers.services.blender_download import (
    get_blender_dir, blender_already_installed,
)
from workers.services.sentinel import read_sentinel

# ``_check_ffmpeg`` MUST NOT subprocess-run the ffmpeg binary until the
# ``checkpoints.FFMPEG_INSTALLED`` sentinel checkpoint is present:
# attempting to verify a half-extracted binary can wedge the Waitress
# worker thread (issue #125).  ``_check_blender`` carries the symmetric
# guard against ``checkpoints.BLENDER_PREDOWNLOADED`` (issue #129) — a
# half-extracted blender binary would wedge the same way, and relying on
# ``blender_already_installed`` (which only checks ``is_file()``) is not
# enough on its own.


def run_verification_checks(
    data_dir: Path, topology: str | None,
) -> list[dict]:
    """Run the topology-aware verification checklist."""
    checks = [
        _check_db_reachable(),
        _check_admin_exists(),
        _check_enrollment_key(),
    ]
    if topology != "worker_only":
        checks.append(_check_ffmpeg(data_dir))
    if topology == "manager_worker":
        checks.append(_check_blender(data_dir))
        checks.append(check_local_worker_enrolled())
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
    """Verify FFmpeg binary is present and runs.

    Short-circuits BEFORE touching the binary if the
    ``ffmpeg_installed`` sentinel checkpoint is absent.  This
    guarantees we never subprocess-run a half-extracted binary
    (issue #125), which would wedge the Waitress worker thread.
    """
    sentinel = read_sentinel(data_dir)
    checkpoint_list = (
        sentinel.get("checkpoints", []) if sentinel else []
    )
    if checkpoints.FFMPEG_INSTALLED not in checkpoint_list:
        return {
            "name": "ffmpeg", "passed": False,
            "error": "FFmpeg not yet installed",
        }
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


def _find_blender_binary(
    blender_dir: Path, version: str,
) -> Path | None:
    """Locate the Blender binary for the current platform."""
    import platform as plat
    from workers.services.blender_download import get_platform_id
    pid = get_platform_id()
    if not pid:
        return None
    install = blender_dir / f"blender-{version}-{pid}"
    if not install.is_dir():
        return None
    system = plat.system()
    if system == "Windows":
        binary = install / "blender.exe"
    elif system == "Darwin":
        binary = (
            install / "Blender.app" / "Contents" / "MacOS" / "Blender"
        )
    else:
        binary = install / "blender"
    return binary if binary.is_file() else None


def _check_blender(data_dir: Path) -> dict:
    """Verify Blender binary runs (manager_worker only, optional).

    Short-circuits BEFORE touching the binary if the
    ``blender_predownloaded`` sentinel checkpoint is absent.  This
    guarantees we never subprocess-run a half-extracted binary
    (issue #129), which would wedge the Waitress worker thread the
    same way #125 documented for ffmpeg.  ``blender_already_installed``
    alone is not sufficient here: it checks only ``is_file()`` and
    will return ``True`` mid-extraction.
    """
    try:
        sentinel = read_sentinel(data_dir)
        checkpoint_list = (
            sentinel.get("checkpoints", []) if sentinel else []
        )
        if checkpoints.BLENDER_PREDOWNLOADED not in checkpoint_list:
            return {
                "name": "blender", "passed": True,
                "error": None,
                "detail": "Blender not pre-downloaded (optional)",
            }
        from workers.models import SupportedBlenderVersion
        default_version = SupportedBlenderVersion.objects.filter(
            is_default=True,
        ).first()
        if not default_version:
            return {
                "name": "blender", "passed": True,
                "error": None,
                "detail": "No default Blender version configured",
            }
        version = default_version.version
        if not blender_already_installed(data_dir, version):
            return {
                "name": "blender", "passed": True,
                "error": None,
                "detail": "Blender not pre-downloaded (optional)",
            }
        binary = _find_blender_binary(
            get_blender_dir(data_dir), version,
        )
        if binary is None:
            return {
                "name": "blender", "passed": False,
                "error": "Blender directory exists but binary not found",
            }
        verify_blender_runs(binary)
        return {"name": "blender", "passed": True, "error": None}
    except Exception as exc:
        return {"name": "blender", "passed": False, "error": str(exc)}
