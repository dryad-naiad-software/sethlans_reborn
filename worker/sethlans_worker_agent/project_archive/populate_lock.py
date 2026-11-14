# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Populate locks keyed by the pin (Unreal foundation FR-PA-10, Conc R2-L5).

One ``threading.Lock`` per ``archive_sha256``, held in a dict guarded by
the **global dict lock**. It serializes lookup -> copy -> extract ->
publish for one archive identity: two jobs needing the same archive cause
exactly one copy and one extraction, even under two different paths.

* Waiting is cancellable: ``acquire(timeout=POLL_SECONDS)`` in a loop that
  checks cancel, shutdown and the deadline (``Abort.check``).
* One populate lock per thread: a thread that already holds one gets
  ``RuntimeError`` (prevents an A<->B deadlock between two populates that
  both evict).
* Lock order: global dict lock < populate lock < index lock < marker lock.
  The dict lock is only ever held for dict bookkeeping.
* Unused locks are dropped from the dict (reference-counted).
"""
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, List

from sethlans_worker_agent.project_archive.abort import NEVER, Abort

POLL_SECONDS = 1.0


class PopulateLocks:

    def __init__(self, poll_seconds: float = POLL_SECONDS):
        self._dict_lock = threading.Lock()  # the global dict lock
        self._locks: Dict[str, List] = {}  # pin -> [lock, users]
        self._held = threading.local()
        self._poll = poll_seconds

    def _checkout(self, pin: str) -> threading.Lock:
        with self._dict_lock:
            slot = self._locks.setdefault(pin, [threading.Lock(), 0])
            slot[1] += 1
            return slot[0]

    def _checkin(self, pin: str) -> None:
        with self._dict_lock:
            slot = self._locks.get(pin)
            if slot is not None:
                slot[1] -= 1
                if slot[1] <= 0:
                    del self._locks[pin]

    def holds_any(self) -> bool:
        return getattr(self._held, 'pin', None) is not None

    def known(self) -> int:
        with self._dict_lock:
            return len(self._locks)

    @contextmanager
    def hold(self, pin: str, abort: Abort = NEVER) -> Iterator[None]:
        """Hold the populate lock for ``pin``; waiting checks ``abort`` every poll."""
        if self.holds_any():
            raise RuntimeError("a thread may hold only one populate lock")
        lock = self._checkout(pin)
        acquired = False
        try:
            while not acquired:
                abort.check()
                acquired = lock.acquire(timeout=self._poll)
            self._held.pin = pin
            yield
        finally:
            if acquired:
                self._held.pin = None
                lock.release()
            self._checkin(pin)
