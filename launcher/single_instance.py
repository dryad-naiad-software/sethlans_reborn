# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Single-instance lock for the launcher (FR-14b; owner decision Q4).

One launcher per settings folder. The lock is an OS lock on
``<settings folder>/.launcher.lock``, held for the launcher's life and
let go by the OS when the process ends, however it ends:

* Windows: ``msvcrt.locking`` (a byte-range lock) on that file;
* POSIX: ``fcntl.flock`` on it.

The file lives in the owner-only settings folder, so another account
cannot create or hold it, and every spelling of the folder (an 8.3
short name, a junction, a symlink, a ``subst`` drive) reaches the same
file and so the same lock. The folder is named by ``os.path.realpath``.
``--reset-setup`` uses the lock to tell whether Sethlans runs.

No Django / third-party deps — stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOCKFILE_NAME = ".launcher.lock"


@dataclass
class LockHandle:
    """Keeps the lock: the open lock file. Keep it for the launcher's life."""

    fd: int
    path: Path


def lock_path(settings_dir: Path) -> Path:
    """``<realpath of settings_dir>/.launcher.lock``."""
    return Path(os.path.realpath(settings_dir)) / LOCKFILE_NAME


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def acquire_single_instance_lock(settings_dir: Path) -> Optional[LockHandle]:
    """Take the lock of ``settings_dir``; ``None`` when another launcher holds it.

    The folder is created when missing (the launcher makes it owner-only
    before it gets here).
    """
    path = lock_path(settings_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    if _try_lock(fd):
        return LockHandle(fd=fd, path=path)
    os.close(fd)
    return None


def release_lock(lock: Optional[LockHandle]) -> None:
    """Let the lock go (closing the file is enough on both systems); best effort."""
    if lock is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(lock.fd, 0, os.SEEK_SET)
            msvcrt.locking(lock.fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        os.close(lock.fd)
    except OSError:
        pass
