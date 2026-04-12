# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Per-OS path selection for the worker config store (FR-24, FR-24a).

Selects:
  * ``get_data_dir()``   — per-user data directory for config, tools,
                           assets, output, temp, logs, failed_uploads.
  * ``SYSTEM_CONFIG_PATH`` — ``/etc/sethlans/worker.json`` on Linux;
                             ``None`` on Windows/macOS.
"""

import os
import platform
from pathlib import Path
from typing import Optional


def get_data_dir() -> Path:
    """Return the per-OS per-user data directory for worker state.

    When SETHLANS_WORKER_DATA_DIR is set (e.g., in Docker), use it
    directly instead of the OS-specific path.

    Also used by ``config.py`` for ``MANAGED_TOOLS_DIR``,
    ``MANAGED_ASSETS_DIR``, ``WORKER_OUTPUT_DIR``, ``WORKER_TEMP_DIR``,
    ``WORKER_LOG_DIR``, and ``FAILED_UPLOADS_DIR`` per FR-24a.
    """
    env_override = os.environ.get("SETHLANS_WORKER_DATA_DIR")
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"SETHLANS_WORKER_DATA_DIR must be an absolute path, "
                f"got: {env_override}"
            )
        return p
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                base = os.path.join(userprofile, "AppData", "Local")
            else:
                base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sethlans" / "worker"
    if system == "Darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "Sethlans" / "worker"
        )
    # Linux / other POSIX
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sethlans" / "worker"
    return Path.home() / ".local" / "share" / "sethlans" / "worker"


def _system_config_path() -> Optional[Path]:
    if platform.system() == "Linux":
        return Path("/etc/sethlans/worker.json")
    return None


SYSTEM_CONFIG_PATH: Optional[Path] = _system_config_path()


def user_config_path() -> Path:
    return get_data_dir() / "config.json"


def lockfile_path() -> Path:
    return get_data_dir() / "config.json.lock"
