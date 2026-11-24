# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers for the settings-file and path-resolution tests."""
import sys
from pathlib import Path

PATH_ENV_VARS = (
    "SETHLANS_SETTINGS_FILE",
    "SETHLANS_SETTINGS_DIR",
    "SETHLANS_DATA_ROOT",
    "SETHLANS_MANAGER_DATA_DIR",
    "SETHLANS_WORKER_DATA_DIR",
    "SETHLANS_TLS_DATA_DIR",
    "SETHLANS_WORKER_TLS_DATA_DIR",
)

# An absolute path on the running OS that never exists.
ABS_ROOT = Path("C:\\sethlans-test") if sys.platform == "win32" else Path("/sethlans-test")


def clear_path_env(monkeypatch) -> None:
    """Remove every env var that moves a Sethlans path."""
    for name in PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def write_settings(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path
