# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Pure helpers for ``menu_manager``.

Split out of the main module to keep each file under the 300-line cap
(CLAUDE.md).  No Qt imports here — these helpers have no UI affinity.
"""

from __future__ import annotations

import configparser
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{30,60}$")
_MAX_INI_BYTES = 1024

SENTINEL_NAME = "setup_complete.json"


def sentinel_exists(manager_data_dir: Path) -> bool:
    """True when either setup-sentinel file exists (legacy or current)."""
    return (
        (manager_data_dir / SENTINEL_NAME).exists()
        or (manager_data_dir.parent / ".setup_complete").exists()
    )


def read_token(manager_data_dir: Path) -> str:
    """Return the setup token from ``manager.ini`` or empty string."""
    ini_path = manager_data_dir / "manager.ini"
    # Python 3.13 changed Path.exists() to propagate OSError (e.g.
    # PermissionError) rather than silently returning False. Guard
    # explicitly so read_token remains fail-closed across versions.
    try:
        if not ini_path.exists():
            return ""
    except OSError:
        return ""
    try:
        size = ini_path.stat().st_size
    except OSError:
        return ""
    if size > _MAX_INI_BYTES:
        logger.warning(
            "manager.ini exceeds %d bytes; ignoring for token read",
            _MAX_INI_BYTES,
        )
        return ""
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        logger.warning("Could not parse manager.ini: %s", exc)
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


def open_logs(data_dir: Path) -> None:
    log_path = data_dir / "logs" / "manager.log"
    if not log_path.exists():
        logger.warning("Manager log not found at %s", log_path)
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
