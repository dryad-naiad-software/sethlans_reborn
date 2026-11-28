# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Log locations and the OS viewer, for the tray's View Logs items.

* View Logs (every menu, FR-LN-4) opens the launcher's log folder,
  ``<settings>/logs``: ``launcher.log``, ``tray.log`` and the start logs
  the failure reasons come from (``manager-start.log``,
  ``setup-start.log``).
* View Manager Logs (the manager section) opens the manager's own
  ``manager.log`` in the manager's data folder
  (``frozen_paths.get_data_dir("manager")/logs``).

No Qt imports here.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from shared.frozen_paths import get_data_dir

logger = logging.getLogger(__name__)

LOGS_SUBDIR = "logs"
MANAGER_LOG_NAME = "manager.log"


def launcher_logs_dir(settings_dir: Path) -> Path:
    """``<settings>/logs``."""
    return Path(settings_dir) / LOGS_SUBDIR


def manager_log_path() -> Path:
    """``manager.log`` in the manager's data folder."""
    return get_data_dir("manager") / LOGS_SUBDIR / MANAGER_LOG_NAME


def open_path(path: Path) -> bool:
    """Open a file or folder in the platform's default viewer.

    Returns False when it is not there or the viewer could not start;
    never raises (a click handler must not).
    """
    try:
        if not path.exists():
            logger.warning("Not found: %s", path)
            return False
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to open %s: %s", path, exc)
        return False


def open_launcher_logs(settings_dir: Path) -> bool:
    """View Logs: the launcher's log folder."""
    return open_path(launcher_logs_dir(settings_dir))


def open_manager_log() -> bool:
    """View Manager Logs: ``manager.log``; never raises."""
    try:
        path = manager_log_path()
    except Exception as exc:  # noqa: BLE001 - an unreadable settings file
        logger.warning("Could not find the manager's log: %s", exc)
        return False
    return open_path(path)
