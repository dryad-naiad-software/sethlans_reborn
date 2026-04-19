# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Single-instance lock for the launcher.

Prevents multiple concurrent launcher processes from stomping on the
same data directory.  Cross-platform: Windows uses a named mutex
(``CreateMutexW``); POSIX uses ``fcntl.flock`` on a lockfile in the
data directory.

See setup-auth-unification.md FR-14b (C5).

No Django / third-party deps — stdlib only.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

MUTEX_NAME = "Global\\Sethlans.Launcher"
LOCKFILE_NAME = ".launcher.lock"
ERROR_ALREADY_EXISTS = 183


@dataclass
class LockHandle:
    """Opaque handle keeping the single-instance lock alive.

    Must be kept alive (not garbage-collected) for the full launcher
    lifetime.  ``backend`` is "windows" (mutex HANDLE in ``handle``)
    or "posix" (file descriptor via ``fh``).
    """

    backend: str
    handle: Any = None  # Windows mutex HANDLE
    fh: Any = None      # POSIX file handle
    path: Optional[Path] = None


def _acquire_windows() -> Optional[LockHandle]:
    """Acquire a Windows named-mutex lock. Returns None if held."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    if not handle:
        return None
    if last_error == ERROR_ALREADY_EXISTS:
        # Another launcher owns the mutex; release our handle.
        kernel32.CloseHandle(handle)
        return None
    return LockHandle(backend="windows", handle=handle)


def _acquire_posix(data_dir: Path) -> Optional[LockHandle]:
    """Acquire a POSIX flock on ``<data_dir>/.launcher.lock``."""
    import fcntl

    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / LOCKFILE_NAME

    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        fh.close()
        return None
    return LockHandle(backend="posix", fh=fh, path=lock_path)


def acquire_single_instance_lock(data_dir: Path) -> Optional[LockHandle]:
    """Acquire a single-instance lock.

    Returns a ``LockHandle`` on success or ``None`` if another launcher
    already holds the lock.  The handle must be retained for the
    process lifetime.
    """
    if platform.system() == "Windows":
        return _acquire_windows()
    return _acquire_posix(data_dir)


def _release_windows(lock: LockHandle) -> None:
    try:
        import ctypes
        if lock.handle:
            ctypes.windll.kernel32.CloseHandle(lock.handle)
    except Exception:
        pass


def _release_posix(lock: LockHandle) -> None:
    if lock.fh is None:
        return
    try:
        import fcntl
        try:
            fcntl.flock(lock.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    except Exception:
        pass
    try:
        lock.fh.close()
    except Exception:
        pass


def release_lock(lock: Optional[LockHandle]) -> None:
    """Best-effort release of a lock handle."""
    if lock is None:
        return
    if lock.backend == "windows":
        _release_windows(lock)
    else:
        _release_posix(lock)
