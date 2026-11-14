# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pristine-entry cache: entry states, refcounts, the byte ledger and
``index.json`` (Unreal foundation FR-PA-5, FR-PA-10, FR-PA-11).

One ``threading.Lock``, the **index lock**, guards the in-memory index,
entry states (``ready`` / ``evicting`` / ``quarantined``), refcounts, the
byte ledger, the set of keys being removed and ``index.json`` writes. No
network I/O and no file-tree walk happens while it is held; deletions of
whole entries happen outside it (``dispose``, via ``TrashQueue``).

* ``acquire(sha)`` atomically checks ``state == ready`` and increments
  the refcount; False means "treat as a miss".
* ``release(sha)`` decrements; a quarantined entry is moved to ``trash/``
  when its refcount reaches 0.
* ``quarantine(sha, reason)`` blocks new acquires and defers the rename
  while the entry is in use; it never removes an entry in use.
* Refcounts live in memory only, so a crash cannot leave one stale.
* Each entry carries ``record_digest``, the SHA-256 of the canonical
  ``.complete.json`` it was published (or loaded at startup) with: the
  in-memory integrity anchor (Sec S1). The on-disk record is checked
  against it on every use.
* A key between ``begin_eviction_locked`` and the end of ``dispose`` is in
  ``removing``; ``publish`` refuses such a key (Conc C9).

``index.json`` is an LRU and size ledger keyed by the **full** SHA-256:
``{"schema": 1, "entries": {sha: {size_bytes, last_used, source_unc}}}``.
It is never used to decide a hit (FR-PA-5).
"""
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from sethlans_worker_agent.project_archive import index_store
from sethlans_worker_agent.project_archive.layout import CacheLayout, key16
from sethlans_worker_agent.project_archive.ledger import ByteLedger
from sethlans_worker_agent.project_archive.populate_lock import PopulateLocks
from sethlans_worker_agent.project_archive.trash import TrashQueue

logger = logging.getLogger(__name__)

READY, EVICTING, QUARANTINED = 'ready', 'evicting', 'quarantined'
INDEX_SCHEMA = index_store.INDEX_SCHEMA


@dataclass
class Entry:
    sha256: str
    size_bytes: int
    last_used: float
    source_unc: str
    record_digest: str = ''
    state: str = READY
    refcount: int = 0
    reason: str = ''


@dataclass(frozen=True)
class CacheLimits:
    cache_max_bytes: int
    min_free_bytes: int


class ArchiveCache:
    """Process-wide pristine cache for one ``CACHE_DIR`` (owner lock held)."""

    def __init__(self, layout: CacheLayout, limits: CacheLimits, *,
                 disk_usage: Callable = shutil.disk_usage, clock: Callable[[], float] = time.time,
                 populate_locks: Optional[PopulateLocks] = None):
        self.layout = layout
        self.limits = limits
        self.lock = threading.Lock()  # the index lock
        self.ledger = ByteLedger()
        self.populate_locks = populate_locks or PopulateLocks()
        self.trash = TrashQueue(self)
        self.removing: Set[str] = set()  # key16s being disposed (guarded by the index lock)
        self._disk_usage = disk_usage
        self._clock = clock
        self._entries: Dict[str, Entry] = {}

    # -- loading (recovery) ------------------------------------------------
    def load(self, entries: List[Entry]) -> None:
        with self.lock:
            self._entries = {e.sha256: e for e in entries}
            self.ledger.pristine_total = sum(e.size_bytes for e in entries)
            self._persist_locked()

    # -- queries -------------------------------------------------------------
    def free_bytes(self) -> int:
        """``disk_usage(CACHE_DIR).free``; eviction reads it under the index lock (Conc C8)."""
        return int(self._disk_usage(os.fspath(self.layout.root)).free)

    def state(self, sha256: str) -> Optional[str]:
        with self.lock:
            entry = self._entries.get(sha256)
            return entry.state if entry else None

    def refcount(self, sha256: str) -> int:
        with self.lock:
            entry = self._entries.get(sha256)
            return entry.refcount if entry else 0

    def record_digest(self, sha256: str) -> str:
        with self.lock:
            entry = self._entries.get(sha256)
            return entry.record_digest if entry else ''

    def ready_shas(self) -> List[str]:
        with self.lock:
            return [sha for sha, e in self._entries.items() if e.state == READY]

    def entry_dir(self, sha256: str) -> Path:
        return self.layout.entry_dir(sha256)

    # -- refcounted use --------------------------------------------------------
    def acquire(self, sha256: str, touch: bool = True) -> bool:
        """Refcount a ``ready`` entry; ``touch`` updates its LRU time (a job's use)."""
        with self.lock:
            entry = self._entries.get(sha256)
            if entry is None or entry.state != READY:
                return False
            entry.refcount += 1
            if touch:
                entry.last_used = self._clock()
                self._persist_locked()
            return True

    def release(self, sha256: str) -> None:
        dispose = None
        with self.lock:
            entry = self._entries.get(sha256)
            if entry is None:
                return
            entry.refcount = max(entry.refcount - 1, 0)
            if entry.state == QUARANTINED and entry.refcount == 0:
                dispose = self._begin_removal_locked(entry)
        if dispose is not None:
            self.dispose([dispose])

    def quarantine(self, sha256: str, reason: str) -> None:
        dispose = None
        with self.lock:
            entry = self._entries.get(sha256)
            if entry is None or entry.state == EVICTING:
                return
            if entry.state != QUARANTINED:
                logger.error("Quarantining Unreal cache entry %s: %s", key16(sha256), reason)
            entry.state, entry.reason = QUARANTINED, reason
            self._persist_locked()
            if entry.refcount == 0:
                dispose = self._begin_removal_locked(entry)
        if dispose is not None:
            self.dispose([dispose])

    # -- index lock helpers (caller holds self.lock) -----------------------------
    def entry_locked(self, sha256: str) -> Optional[Entry]:
        return self._entries.get(sha256)

    def entry_for_key_locked(self, key: str) -> Optional[Entry]:
        for entry in self._entries.values():
            if key16(entry.sha256) == key:
                return entry
        return None

    def register_locked(self, sha256: str, size: int, source_unc: str, acquired: bool, digest: str) -> None:
        self._entries[sha256] = Entry(sha256, size, self._clock(), source_unc, record_digest=digest,
                                      refcount=1 if acquired else 0)
        self.ledger.pristine_total += size
        self._persist_locked()

    def lru_candidates_locked(self) -> List[Entry]:
        idle = [e for e in self._entries.values() if e.state == READY and e.refcount == 0]
        return sorted(idle, key=lambda e: e.last_used)

    def _begin_removal_locked(self, entry: Entry) -> Tuple[Path, int, str]:
        """Mark ``entry`` evicting and drop it from the index; returns what ``dispose`` needs."""
        entry.state = EVICTING
        del self._entries[entry.sha256]
        self.ledger.pristine_total -= entry.size_bytes
        self.ledger.pending_trash += entry.size_bytes
        self.removing.add(key16(entry.sha256))
        self._persist_locked()
        return self.layout.entry_dir(entry.sha256), entry.size_bytes, key16(entry.sha256)

    def begin_eviction_locked(self, victims: List[Entry]) -> List[Tuple[Path, int, str]]:
        return [self._begin_removal_locked(victim) for victim in victims]

    def _persist_locked(self) -> None:
        index_store.write_index(self.layout, [e for e in self._entries.values() if e.state == READY])

    # -- outside the lock ------------------------------------------------------
    def dispose(self, items) -> None:
        """Rename entry directories to ``trash/`` and delete them synchronously (never in place)."""
        for path, size, key in items:
            try:
                self.trash.dispose_one(path, size, key)
            finally:
                with self.lock:
                    self.removing.discard(key)

    def delete_trash(self, path: Path, size: int) -> None:
        """Delete one ``trash/`` path; keep its bytes in ``pending_trash`` on failure."""
        self.trash.delete(path, size)

    def retry_pending_trash(self) -> None:
        self.trash.retry()
