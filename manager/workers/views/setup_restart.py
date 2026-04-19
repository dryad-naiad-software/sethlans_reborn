# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode restart endpoint (``POST /api/setup/restart/``).

Writes ``<data_dir>/.restart_requested`` atomically with mode 0o600
via ``O_CREAT|O_EXCL|O_WRONLY`` (FR-13 / S7).  A second request while
the marker exists returns ``409``.  The file's location is
containment-checked against the resolved data_dir to block path
traversal via symlinks.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.authentication import SetupPhaseAuthentication
from workers.permissions import IsSetupPhaseUser
from workers.services.setup_session import enforce_setup_session_binding
from workers.services.sentinel import read_sentinel
from workers.utils.errors import setup_error

logger = logging.getLogger(__name__)

RESTART_MARKER_NAME = ".restart_requested"


def _data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _containment_ok(data_dir: Path, marker: Path) -> bool:
    """Return True if ``marker`` resolves to a path inside ``data_dir``."""
    try:
        resolved_dir = Path(data_dir).resolve()
        resolved_marker = marker.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        return resolved_marker.is_relative_to(resolved_dir)
    except AttributeError:
        # Python <3.9 fallback; project is 3.12+ so this is belt-only.
        try:
            resolved_marker.relative_to(resolved_dir)
            return True
        except ValueError:
            return False


def _write_marker_payload(path: Path, payload: dict) -> None:
    """Write ``payload`` to ``path`` atomically (tempfile + os.replace).

    Caller is responsible for having opened the target with O_EXCL to
    claim ownership; this helper replaces the contents so the final
    file is fully written.
    """
    parent = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".marker")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _claim_restart_marker(marker: Path):
    """O_EXCL-claim the marker file. Returns a Response on failure."""
    try:
        fd = os.open(
            str(marker),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return setup_error(
            "precondition_unmet",
            "Restart has already been requested.",
            409,
        )
    except OSError as exc:
        logger.exception("Failed to create restart marker")
        return setup_error(
            "internal_error",
            f"Failed to create restart marker: {exc}",
            500,
        )
    try:
        os.close(fd)
    except OSError:
        pass
    return None


@extend_schema(tags=["Setup"])
@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_restart_view(request):
    """Create ``.restart_requested`` atomically in the manager data_dir."""
    enforce_setup_session_binding(request)
    data_dir = _data_dir()

    sentinel = read_sentinel(data_dir)
    if not sentinel or not sentinel.get("completed_at"):
        return setup_error(
            "precondition_unmet",
            "Cannot restart before the setup sentinel is written.",
            409,
        )

    marker = data_dir / RESTART_MARKER_NAME
    if not _containment_ok(data_dir, marker):
        return setup_error(
            "internal_error",
            "Restart marker path containment check failed.",
            500,
        )

    claim_err = _claim_restart_marker(marker)
    if claim_err is not None:
        return claim_err

    try:
        _write_marker_payload(
            marker,
            {"requested_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.exception("Failed to write restart marker payload")
        try:
            os.unlink(marker)
        except OSError:
            pass
        return setup_error(
            "internal_error",
            "Failed to write restart marker.",
            500,
        )

    return Response(status=202)
