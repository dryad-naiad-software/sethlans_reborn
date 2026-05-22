# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Resolve the path to the bundled ``run_manager`` executable (#191).

Lives in its own module so :mod:`launcher.apply_pending_setup` stays
under the project-wide 300-line cap. The hardening checks are spec
FR-LAUNCHER2 — ``.resolve(strict=True)`` + ``.is_file()`` + a bounds
check against ``get_install_root()`` (issue #192) so a symlink-swap
attack against the install tree cannot redirect the launcher to an
arbitrary binary outside the install root.

Issue #192: the bounds check was originally against ``get_app_dir()``,
but in frozen mode ``get_app_dir()`` returns the *caller's* component
directory (here: ``bin/launcher/``). The launcher resolves the manager
exe at ``bin/manager/run_manager.exe``, which is NOT under
``bin/launcher/``, so every cross-component call raised. The fix
widens the bounds check to ``get_install_root()`` (``bin/``), which
is the correct cross-component install boundary.
"""

from __future__ import annotations

import platform
from pathlib import Path


def is_frozen() -> bool:
    """Return ``True`` when running inside the PyInstaller bundle.

    Delegates to :func:`shared.frozen_paths.is_frozen` so the launcher,
    manager, and worker all use the same source-of-truth.
    """
    try:
        from shared.frozen_paths import is_frozen as _is_frozen
    except ImportError:  # pragma: no cover - defensive
        from sethlans_manager.frozen_paths import (  # type: ignore[no-redef]
            is_frozen as _is_frozen,
        )
    return _is_frozen()


def manager_exe() -> Path:
    """Return the path to the bundled ``run_manager`` executable.

    Frozen mode: looks up the bundled manager directory via
    :func:`shared.frozen_paths.get_manager_dir`, appends
    ``run_manager.exe`` (Windows) or ``run_manager`` (POSIX), then
    applies the FR-LAUNCHER2 hardening checks:

    1. ``.resolve(strict=True)`` — dangling symlinks / missing files
       raise ``FileNotFoundError`` rather than launching a bogus
       subprocess.
    2. ``.is_file()`` — guard against directories or pipes.
    3. ``is_relative_to(get_install_root())`` — a symlink that escapes
       the install tree raises rather than executing. (Issue #192:
       was previously ``get_app_dir()``, but in frozen mode that
       returns the caller's own component dir — wrong for
       cross-component lookup.)

    Source mode: raises ``RuntimeError`` so callers cannot accidentally
    use the wrong argv path. Source-mode callers must build their argv
    with ``[sys.executable, manage.py, ...]`` instead.
    """
    if not is_frozen():
        raise RuntimeError(
            "manager_exe() called in source mode; use sys.executable "
            "+ manage.py path instead"
        )
    try:
        from shared.frozen_paths import get_install_root, get_manager_dir
    except ImportError:  # pragma: no cover - defensive
        from sethlans_manager.frozen_paths import (  # type: ignore[no-redef]
            get_install_root,
            get_manager_dir,
        )
    exe_name = (
        "run_manager.exe" if platform.system() == "Windows" else "run_manager"
    )
    candidate = get_manager_dir() / exe_name
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError(
            f"manager_exe(): resolved path is not a regular file: {resolved}"
        )
    install_root = get_install_root().resolve()
    if not resolved.is_relative_to(install_root):
        raise RuntimeError(
            f"manager_exe(): resolved path {resolved} escapes install "
            f"root {install_root}"
        )
    return resolved


__all__ = ["is_frozen", "manager_exe"]
