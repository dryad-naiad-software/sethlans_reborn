# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Files the earlier setup left behind (FR-SM-7) and ``last_failure`` (FR-LN-6).

``pending_setup.json`` may hold an admin password in plain text, so setup
mode deletes it at start and logs that it did. The other files are no
longer read by anything; they are listed (with their paths) on the form
and the finish page and left alone.
"""
import logging
from pathlib import Path
from typing import List, Optional

from shared.frozen_paths import get_worker_dir
from shared.settings_file import SETUP_SUBDIR, get_settings_dir
from shared.settings_file_io import read_limited

logger = logging.getLogger(__name__)

PENDING_SETUP_NAME = "pending_setup.json"
LAST_FAILURE_NAME = "last_failure"
MAX_FAILURE_BYTES = 4096


def remove_pending_setup(settings_dir: Optional[Path] = None) -> bool:
    """Delete ``<settings>/pending_setup.json``; True when one was deleted."""
    path = (settings_dir or get_settings_dir()) / PENDING_SETUP_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Could not delete the leftover %s: %s", path, exc.strerror)
        return False
    logger.info("Deleted the leftover %s from an earlier setup (it may have held a password)", path)
    return True


def leftover_paths(settings_dir: Optional[Path] = None) -> List[str]:
    """Paths of the earlier setup's files that still exist."""
    root = settings_dir or get_settings_dir()
    candidates = [
        root / "manager" / "manager.ini",
        root / "topology.json",
        root / ".setup_complete",
        root / "wizard",
        root / "worker" / ".setup_complete",
        get_worker_dir() / "config.ini",
    ]
    return [str(path) for path in candidates if path.exists()]


def last_failure(settings_dir: Optional[Path] = None) -> Optional[str]:
    """The first start's failure reason the launcher wrote, or ``None``."""
    path = (settings_dir or get_settings_dir()) / SETUP_SUBDIR / LAST_FAILURE_NAME
    try:
        data = read_limited(path, MAX_FAILURE_BYTES)[:MAX_FAILURE_BYTES]
    except OSError:
        return None
    text = data.decode("utf-8", "replace").strip()
    return text or None
