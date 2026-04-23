# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Project version resolution for both source and frozen modes.

Single source of truth for the Sethlans version string. Reads the
repo-root ``VERSION`` file in source mode, and ``sys._MEIPASS/VERSION``
in frozen (PyInstaller one-dir) mode. The result is cached so repeat
calls do not re-read the file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from shared.frozen_paths import get_app_dir, is_frozen

logger = logging.getLogger(__name__)

_FALLBACK_VERSION = "0.0.0+unknown"
_VERSION_FILENAME = "VERSION"

_cached_version: Optional[str] = None
_warned_missing: bool = False


def _resolve_version_path() -> Path:
    """Return the path to the bundled ``VERSION`` file.

    Frozen mode uses ``sys._MEIPASS`` (the PyInstaller contents dir
    where ``datas=[(..., '.')]`` places files). Source mode resolves
    to ``<project_root>/VERSION``.
    """
    if is_frozen():
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        return meipass / _VERSION_FILENAME
    return get_app_dir() / _VERSION_FILENAME


def get_version() -> str:
    """Return the Sethlans version string.

    Reads the bundled ``VERSION`` file on first call and caches the
    stripped contents for subsequent calls. Returns a safe fallback
    (``0.0.0+unknown``) and logs a single warning if the file is
    missing or unreadable.
    """
    global _cached_version, _warned_missing
    if _cached_version is not None:
        return _cached_version

    path = _resolve_version_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        if not _warned_missing:
            logger.warning(
                "VERSION file unreadable at %s (%s); using fallback %r",
                path, exc, _FALLBACK_VERSION,
            )
            _warned_missing = True
        _cached_version = _FALLBACK_VERSION
        return _cached_version

    _cached_version = raw.strip() or _FALLBACK_VERSION
    return _cached_version


__all__ = ["get_version"]
