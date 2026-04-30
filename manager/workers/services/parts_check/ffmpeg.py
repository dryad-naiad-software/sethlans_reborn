# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FFmpeg part: PATH-first detection + version gate + bundled fallback.

Detection priority (per spec FR §34-39):

    1. ``manager.ini [ffmpeg] path =`` override (single argv element,
       resolved via ``os.path.realpath`` + ``os.path.isfile``, gated
       by ``verify_runs`` and major version >= 8).
    2. ``shutil.which("ffmpeg")`` PATH lookup gated by major version
       >= 8.
    3. ``<data_dir>/bin/ffmpeg/8.1/`` re-boot fast path (presence
       check + verify_runs).
    4. Atomic download + verify + extract pipeline.

This module registers the ``"ffmpeg"`` part on import.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from shared.frozen_paths import get_data_dir

from . import registry
from .ffmpeg_download import (
    FFMPEG_VERSION,
    cleanup_stale_partials,
    download_and_install_bundled,
    get_ffmpeg_binary,
    get_ffmpeg_dir,
    parse_major_version,
    verify_runs,
)

logger = logging.getLogger(__name__)

MIN_MAJOR_VERSION = 8


def _read_manager_ini_override() -> Optional[str]:
    """Return ``manager.ini [ffmpeg] path =`` value or ``None``.

    Lazy-imports the config-loader so this module remains importable
    in test contexts that have not booted Django settings yet.
    """
    try:
        from sethlans_manager.config_loader import _config
    except Exception:  # pragma: no cover
        return None
    if _config.has_option("ffmpeg", "path"):
        value = _config.get("ffmpeg", "path").strip()
        return value or None
    return None


def _check_override(override: str) -> Optional[registry.Status]:
    """Validate the manager.ini override path; return Status or None.

    Returns:
        - ``Status(status="ready", source="system", ...)`` on success.
        - ``Status(status="failed", error="override_path_invalid")``
          if the path is not a regular file.
        - ``Status(status="failed", error="override_path_unverifiable")``
          if verify_runs fails or the major version is < 8.
        - ``None`` is never returned from this branch — every code
          path resolves to a definitive Status.
    """
    resolved = os.path.realpath(override)
    if not os.path.isfile(resolved):
        logger.error(
            "manager.ini [ffmpeg] path = %r is not a regular file "
            "(resolved=%r)", override, resolved,
        )
        return registry.Status(
            status="failed", error="override_path_invalid",
        )
    if not verify_runs(resolved):
        logger.error(
            "manager.ini override %r failed verify_runs", resolved,
        )
        return registry.Status(
            status="failed", error="override_path_unverifiable",
        )
    major = parse_major_version(resolved)
    if major is None or major < MIN_MAJOR_VERSION:
        logger.error(
            "manager.ini override %r reports major version %r "
            "(< %d)", resolved, major, MIN_MAJOR_VERSION,
        )
        return registry.Status(
            status="failed", error="override_path_unverifiable",
        )
    return registry.Status(
        status="ready",
        source="system",
        version=str(major),
        path=resolved,
    )


def _check_path_lookup() -> Optional[registry.Status]:
    """Check ``shutil.which('ffmpeg')`` with the >= 8 version gate.

    Returns ``Status(status="ready", source="system", ...)`` on
    success.  Returns ``None`` (not a failure!) if no usable PATH
    binary is found — the caller continues to the bundled-presence
    check.  A PATH binary with major < 8 is treated the same as
    "no PATH binary" because the bundled fallback is the right
    upgrade path; we don't fail closed here.
    """
    path = shutil.which("ffmpeg")
    if not path:
        return None
    if not verify_runs(path):
        logger.info(
            "PATH ffmpeg %r failed verify_runs; falling through to "
            "bundled.", path,
        )
        return None
    major = parse_major_version(path)
    if major is None or major < MIN_MAJOR_VERSION:
        logger.info(
            "PATH ffmpeg %r reports major %r (< %d); falling through "
            "to bundled.", path, major, MIN_MAJOR_VERSION,
        )
        return None
    return registry.Status(
        status="ready",
        source="system",
        version=str(major),
        path=path,
    )


def _check_bundled_present(data_dir: Path) -> Optional[registry.Status]:
    """Re-boot fast path: bundled binary already present at <data_dir>.

    Returns a ready Status when the binary exists and verify_runs
    passes; ``None`` otherwise (caller proceeds to the download path).
    """
    bundled_dir = get_ffmpeg_dir(data_dir)
    binary = get_ffmpeg_binary(bundled_dir)
    if binary is None:
        return None
    if not verify_runs(binary):
        logger.warning(
            "Bundled ffmpeg at %s failed verify_runs; will redownload.",
            binary,
        )
        return None
    return registry.Status(
        status="ready",
        source="bundled",
        version=FFMPEG_VERSION,
        path=str(binary),
    )


def check_ffmpeg() -> registry.Status:
    """Resolve FFmpeg per spec FR §34-39.  See module docstring."""
    data_dir = get_data_dir("manager")

    # Step 0: stale-partial sweep (pre-extraction, single-thread guard).
    cleanup_stale_partials(data_dir / "bin" / "ffmpeg")

    # Step 1: manager.ini override.
    override = _read_manager_ini_override()
    if override:
        return _check_override(override)

    # Step 2: PATH lookup with version gate.
    path_status = _check_path_lookup()
    if path_status is not None:
        return path_status

    # Step 3: re-boot fast path (bundled already present).
    bundled_status = _check_bundled_present(data_dir)
    if bundled_status is not None:
        return bundled_status

    # Step 4: download + verify + extract + verify-runs.
    install_dir = get_ffmpeg_dir(data_dir)
    return download_and_install_bundled(install_dir)


# Register at import time.  Repeat imports are no-ops because dict
# assignment is idempotent.
registry.register_part("ffmpeg", check_ffmpeg)
