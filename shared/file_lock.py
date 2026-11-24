# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-process advisory lock on a sidecar file, stdlib only.

Same pattern as the worker's config store lock and the Unreal cache owner
lock: ``msvcrt.locking`` on one byte (Windows) or ``fcntl.flock`` (POSIX),
taken non-blocking and polled until a deadline. Both lock flavors conflict
between two open handles in the same process too, so one ``file_lock`` per
call also serialises threads.

The worker keeps its own copy (``config_store.io.CrossProcessLock``): the
worker package is not importable from the manager or launcher bundles, and
this module is.
"""
import contextlib
import os
import time
from pathlib import Path
from typing import Iterator

from shared import owner_only_acl

DEFAULT_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL = 0.05


class FileLockTimeoutError(TimeoutError):
    """The lock was not acquired before the deadline.

    A ``TimeoutError`` is an ``OSError``, so callers' ``except OSError``
    handlers catch it.
    """


def _try_lock(fd: int) -> bool:
    if os.name == 'nt':
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
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # closing the descriptor releases it anyway


@contextlib.contextmanager
def file_lock(lock_path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path`` (created owner-only if missing).

    The parent folder must exist. Raises ``FileLockTimeoutError`` when the
    lock is still held elsewhere after ``timeout`` seconds.
    """
    fd = owner_only_acl.create_owner_only_file(Path(lock_path), shared=True)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise FileLockTimeoutError(f"Timed out waiting for the lock {lock_path}")
            time.sleep(_POLL_INTERVAL)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
