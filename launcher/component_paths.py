# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Component executable path resolution for the bootstrap launcher.

Extracted from ``launcher/run_launcher.py`` per FR-L12 to keep the
launcher entry point under the 300-line limit while adding the new
``wizard`` branch.

Resolves the executable path for each component (``manager``, ``worker``,
``tray``, ``wizard``) in both PyInstaller-frozen mode (where each
component is bundled separately under ``bin/<component>/``) and source
mode (where each component runs from its top-level ``run_*.py`` script
under the repository root).

Stdlib-only.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from launcher.paths import get_bin_dir


def _frozen_exe(component: str) -> Path:
    bin_dir = get_bin_dir()
    is_windows = platform.system() == "Windows"
    if component == "tray":
        binary = "run_tray_helper.exe" if is_windows else "run_tray_helper"
        return bin_dir / "tray_helper" / binary
    if component in ("manager", "worker", "wizard"):
        binary = f"run_{component}.exe" if is_windows else f"run_{component}"
        return bin_dir / component / binary
    raise ValueError(f"unknown component {component!r}")


_SOURCE_PATHS = {
    "manager": ("manager", "run_manager.py"),
    "worker": ("worker", "run_worker.py"),
    "tray": ("shared", "run_tray.py"),
    "wizard": ("wizard", "run_wizard.py"),
}


def _source_exe(component: str) -> Path:
    if component not in _SOURCE_PATHS:
        raise ValueError(f"unknown component {component!r}")
    pkg_dir, script = _SOURCE_PATHS[component]
    root = Path(__file__).resolve().parent.parent
    return root / pkg_dir / script


def find_component_exe(component: str) -> Path:
    """Return the on-disk path to the component executable / script.

    Frozen mode: ``<bin_dir>/<component>/run_<component>(.exe)``.
        - ``tray`` is a special case (legacy ``tray_helper`` directory
          and ``run_tray_helper`` binary name).
        - ``wizard`` follows the standard layout per FR-L12:
          ``bin/wizard/run_wizard.exe`` on Windows,
          ``bin/wizard/run_wizard`` on POSIX.

    Source mode: ``<repo_root>/<component>/run_<component>.py``.
        - ``tray`` is ``shared/run_tray.py``.
        - ``wizard`` is ``wizard/run_wizard.py`` per FR-L12.

    Raises:
        ValueError: if ``component`` is unknown.
    """
    if getattr(sys, "frozen", False):
        return _frozen_exe(component)
    return _source_exe(component)


def popen_kwargs_for_component() -> dict:
    """Return Popen kwargs that suppress new-console windows on Windows.

    DEVOPS-MED-4 (Phase F3): the launcher itself is built
    ``console=False`` (windowed) but child PyInstaller bundles (manager,
    worker, wizard) are ``console=True`` so their stdout/stderr can be
    piped back to the launcher for diagnostics. Without
    ``CREATE_NO_WINDOW``, spawning a console-mode child from a windowed
    parent allocates a fresh console window, which flashes on screen
    during first-run setup and any normal-mode component restart. The
    flag suppresses the window without breaking ``PIPE`` redirection.

    No-op on POSIX: ``creationflags`` is a Windows-only ``Popen`` kwarg,
    so this returns an empty dict everywhere else. Extracted from
    ``run_launcher.py`` per the Phase G gatekeeper finding to keep the
    launcher entry point under the 300-line limit.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


__all__ = ["find_component_exe", "popen_kwargs_for_component"]
