# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Process-tree tear-down for ``tools/install_smoke.py`` (issue #198).

Lives in its own module so neither ``install_smoke.py`` nor
``_wizard_smoke_helpers.py`` blow past the 300-line ceiling.
"""

from __future__ import annotations

import subprocess

import psutil


def terminate_process_tree(
    proc: subprocess.Popen, timeout: float = 5.0,
) -> None:
    """SIGTERM->SIGKILL the launcher AND every descendant.

    The frozen launcher spawns ``run_manager.exe``, ``run_wizard.exe``,
    and ``caddy.exe``. Plain ``proc.terminate()`` only signals the
    launcher PID -- Windows does not propagate to descendants -- so an
    orphan ``run_manager.exe`` keeps file handles open on
    ``dist/manager/_internal/*.pyd`` and blocks the next PyInstaller
    ``--clean`` build with ``PermissionError: [WinError 5]``.

    Snapshot descendants BEFORE signalling: a reaped/reparented launcher
    would otherwise race the enumeration. Works cross-platform because
    psutil hides the Windows ``OpenProcess``/POSIX ``/proc`` divergence.
    """
    if proc.poll() is not None:
        return
    _errors = (psutil.NoSuchProcess, psutil.AccessDenied, OSError)
    try:
        parent = psutil.Process(proc.pid)
        targets = parent.children(recursive=True)
        targets.append(parent)
    except psutil.NoSuchProcess:
        return
    for t in targets:
        try:
            t.terminate()
        except _errors:
            pass
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for t in alive:
        try:
            t.kill()
        except _errors:
            pass
    psutil.wait_procs(alive, timeout=timeout)


__all__ = ["terminate_process_tree"]
