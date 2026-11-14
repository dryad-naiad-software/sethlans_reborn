# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Single owner per Unreal cache directory (Unreal foundation FR-WC-6).

When Unreal is enabled the worker takes an exclusive, non-blocking OS lock
on ``CACHE_DIR/.owner.lock`` before recovery and holds it for the process
lifetime (the ``msvcrt.locking`` / ``fcntl.flock`` pattern of
``config_store/io.py``). If it is unavailable, Unreal is disabled with
``OWNER_LOCK_REASON`` and recovery does not run.

The lock protects the cache, not the manager's view: two worker
processes for one hostname are unsupported regardless (FR-RT-12).

Both lock flavors conflict between two open handles in the same process
too, so ``try_acquire`` on a second ``OwnerLock`` for the same directory
fails while the first is held.
"""
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_FILENAME = '.owner.lock'
OWNER_LOCK_REASON = "cache dir in use by another worker process"


class OwnerLock:
    """Exclusive lock on ``<cache_dir>/.owner.lock``; idempotent ``release``."""

    def __init__(self, cache_dir) -> None:
        self.path = Path(cache_dir) / LOCK_FILENAME
        self._fh = None
        self._mutex = threading.Lock()  # leaf lock; guards _fh

    @property
    def held(self) -> bool:
        with self._mutex:
            return self._fh is not None

    def try_acquire(self) -> bool:
        """Take the lock without blocking. True on success (or if already held)."""
        with self._mutex:
            if self._fh is not None:
                return True
            handle = open(self.path, 'a+b')
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
            self._fh = handle
            return True

    def release(self) -> None:
        with self._mutex:
            handle, self._fh = self._fh, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


_process_lock: Optional[OwnerLock] = None
_process_lock_guard = threading.Lock()  # leaf lock


def acquire_for_process(cache_dir) -> bool:
    """Take (once) and keep the process-lifetime owner lock on ``cache_dir``."""
    global _process_lock
    with _process_lock_guard:
        if _process_lock is not None and _process_lock.held:
            return True
        lock = OwnerLock(cache_dir)
        try:
            ok = lock.try_acquire()
        except OSError as exc:
            logger.error("Could not open the Unreal cache owner lock %s: %s", lock.path, exc)
            return False
        if ok:
            _process_lock = lock
        else:
            logger.error("Unreal cache dir %s is in use by another worker process.", cache_dir)
        return ok


def held_for_process(cache_dir) -> bool:
    """True when this process holds the owner lock on ``cache_dir`` (recovery step 0)."""
    with _process_lock_guard:
        lock = _process_lock
    if lock is None or not lock.held:
        return False
    return os.path.normcase(os.path.abspath(lock.path.parent)) == os.path.normcase(os.path.abspath(cache_dir))


def release_for_process() -> None:
    """Release the process-lifetime lock (tests; the OS releases it at exit)."""
    global _process_lock
    with _process_lock_guard:
        lock, _process_lock = _process_lock, None
    if lock is not None:
        lock.release()
