# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Pure helpers for ``menu_manager``.

Split out of the main module to keep each file under the 300-line cap
(CLAUDE.md).  No Qt imports here — these helpers have no UI affinity.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from shared.settings_file import read_settings, settings_path

logger = logging.getLogger(__name__)

_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{30,60}$")
# The whole settings file (server, database, ...), not just [setup].
_MAX_INI_BYTES = 64 * 1024

SENTINEL_NAME = "setup_complete.json"


def sentinel_exists(manager_data_dir: Path) -> bool:
    """True when either setup-sentinel file exists (legacy or current)."""
    return (
        (manager_data_dir / SENTINEL_NAME).exists()
        or (manager_data_dir.parent / ".setup_complete").exists()
    )


def read_token(ini_path: Optional[Path] = None) -> str:
    """Return ``[setup] token`` from the settings file or empty string.

    *ini_path* defaults to :func:`shared.settings_file.settings_path`.
    Fail-closed: a bad path, a missing, oversized or unparsable file all
    give an empty string.
    """
    try:
        ini_path = settings_path() if ini_path is None else Path(ini_path)
        # Path.stat() raises (FileNotFoundError, PermissionError) rather
        # than returning; any OSError means "no token".
        size = ini_path.stat().st_size
    except (OSError, ValueError):
        return ""
    if size > _MAX_INI_BYTES:
        logger.warning(
            "%s exceeds %d bytes; ignoring for token read", ini_path.name,
            _MAX_INI_BYTES,
        )
        return ""
    try:
        cfg = read_settings(ini_path)
    except OSError as exc:
        logger.warning("Could not parse %s: %s", ini_path.name, exc)
        return ""
    return cfg.get("setup", "token", fallback="") or ""


def validate_token(token: str) -> bool:
    if not token:
        return False
    if not _TOKEN_SHAPE.match(token):
        logger.warning(
            "Setup token has invalid shape; token_len=%d", len(token),
        )
        return False
    return True


def open_logs(data_dir: Path, name: str = "manager.log") -> None:
    """Open ``<data_dir>/logs/<name>`` in the platform's default viewer.

    The ``name`` argument was added so wizard-phase callers can target
    ``wizard.log`` / ``launcher.log`` without inlining the cross-
    platform launcher switch (FR-9). The default ``"manager.log"``
    preserves backwards compatibility with existing manager-phase
    callers.
    """
    log_path = data_dir / "logs" / name
    if not log_path.exists():
        logger.warning("Log not found at %s", log_path)
        return
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(log_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(log_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(log_path)], check=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to open log viewer: %s", exc)
