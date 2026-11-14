# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Removal of whole cache entries (Unreal foundation FR-PA-10, FR-PA-11,
Conc C1).

An entry directory ``p/<key16>`` is **never deleted in place**: it is
renamed into ``trash/`` first and only the ``trash/`` path is deleted. If
the rename fails (files still open), ``(p_path, size, key16)`` goes on the
*pending moves* list, never on the delete list, so a later retry can never
delete a live entry published at the same path.

* ``retry`` first retries the pending moves **under the index lock** (the
  same lock ``publish`` holds for its own renames): a move whose ``key16``
  is registered again is dropped, the others are renamed into ``trash/``.
  Then the failed ``trash/`` deletes are retried, outside the lock.
* ``take_move_locked(key16)`` lets ``publish`` claim the pending move for
  its key after it moved the directory aside itself.

Bytes stay in ``ledger.pending_trash`` until they are really gone.
"""
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.project_archive.errors import CacheUnavailableError

logger = logging.getLogger(__name__)


class TrashQueue:

    def __init__(self, cache):
        self._cache = cache
        self._moves: List[Tuple[Path, int, str]] = []
        self._deletes: List[Tuple[Path, int]] = []

    def _done(self, size: int) -> None:
        with self._cache.lock:
            self._cache.ledger.pending_trash = max(self._cache.ledger.pending_trash - size, 0)

    def _trash_dir(self) -> Optional[Path]:
        try:
            return self._cache.layout.check_child('trash')
        except CacheUnavailableError as exc:
            logger.error("%s", exc)
            return None

    def dispose_one(self, path: Path, size: int, key: str) -> None:
        """Move one entry directory to ``trash/`` and delete it (or queue the move)."""
        if not fs_safe.lexists(path):
            self._done(size)
            return
        trash = self._trash_dir()
        moved = fs_safe.move_aside(path, trash) if trash is not None else None
        if moved is None:
            logger.warning("Unreal cache entry %s could not be moved to trash; retried later.", key)
            with self._cache.lock:
                self._moves.append((Path(path), size, key))
            return
        self.delete(moved, size)

    def delete(self, path, size: int) -> None:
        """Delete a ``trash/`` path; keep its bytes pending on failure."""
        try:
            gone = fs_safe.remove_tree(path)
        except Exception:  # noqa: BLE001 - never let a delete leak a refcount upstream
            logger.exception("Deleting %s failed.", path)
            gone = False
        if gone:
            self._done(size)
        else:
            with self._cache.lock:
                self._deletes.append((Path(path), size))

    def take_move_locked(self, key: str) -> int:
        """Drop the pending move for ``key`` (caller moved it aside); returns its size."""
        size = 0
        for item in [m for m in self._moves if m[2] == key]:
            self._moves.remove(item)
            size += item[1]
        return size

    def queue_move_locked(self, path, size: int, key: str) -> None:
        self._moves.append((Path(path), size, key))

    def pending_keys_locked(self):
        return {key for _path, _size, key in self._moves}

    def _retry_moves(self) -> List[Tuple[Path, int]]:
        moved_items: List[Tuple[Path, int]] = []
        trash = self._trash_dir()
        with self._cache.lock:
            moves, self._moves = self._moves, []
            for path, size, key in moves:
                if self._cache.entry_for_key_locked(key) is not None:
                    # Re-published under this key: the directory there is live now.
                    self._cache.ledger.pending_trash = max(self._cache.ledger.pending_trash - size, 0)
                    continue
                moved = fs_safe.move_aside(path, trash) if trash is not None and fs_safe.lexists(path) else None
                if moved is not None:
                    moved_items.append((moved, size))
                elif fs_safe.lexists(path):
                    self._moves.append((path, size, key))
                else:
                    self._cache.ledger.pending_trash = max(self._cache.ledger.pending_trash - size, 0)
        return moved_items

    def retry(self) -> None:
        for path, size in self._retry_moves():
            self.delete(path, size)
        with self._cache.lock:
            deletes, self._deletes = self._deletes, []
        for path, size in deletes:
            self.delete(path, size)
