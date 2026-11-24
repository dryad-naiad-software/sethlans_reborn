# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Path / directory helpers for the bootstrap launcher.

Stdlib only — no Django, no third-party imports.  Kept separate from
``run_launcher.py`` so the launcher entry point stays under the
300-line ceiling.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from shared.settings_file import get_settings_dir


def get_data_dir() -> Path:
    """Return the Sethlans settings folder (lock, topology, IPC markers).

    Delegates to :func:`shared.settings_file.get_settings_dir` so the
    launcher resolves the same folder as the wizard, manager, worker and
    tray: ``SETHLANS_SETTINGS_DIR`` (absolute; a relative value raises
    ``ValueError``), else ``%LOCALAPPDATA%\\Sethlans`` on Windows,
    ``~/Library/Application Support/Sethlans`` on macOS,
    ``$XDG_DATA_HOME/sethlans`` (default ``~/.local/share/sethlans``)
    on Linux.
    """
    return get_settings_dir()


def get_install_dir() -> Path:
    """Return the installation directory."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent.parent.parent
    return Path(__file__).resolve().parent.parent


def get_bin_dir() -> Path:
    """Return the bin/ directory containing component bundles."""
    if getattr(sys, 'frozen', False):
        exe_parent_parent = Path(sys.executable).resolve().parent.parent
        if platform.system() == "Darwin":
            # Inside .app/Contents/MacOS/<exe>; component bundles live
            # under Contents/Resources/bin/<component>/ per Apple's
            # convention (and per packaging/macos/build_dmg.sh). The
            # Windows layout is flat (bin/<component>/ next to
            # bin/launcher/), so the parent.parent arithmetic lands on
            # the install root directly.
            return exe_parent_parent / "Resources" / "bin"
        return exe_parent_parent
    return get_install_dir()


def set_file_permissions(path: Path) -> None:
    """Set restrictive permissions: POSIX 0600, Windows icacls."""
    import stat

    if platform.system() == "Windows":
        username = os.environ.get("USERNAME", "")
        if username:
            subprocess.run(
                [
                    "icacls", str(path),
                    "/inheritance:r",
                    "/grant:r", f"{username}:(R,W)",
                ],
                capture_output=True,
            )
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
