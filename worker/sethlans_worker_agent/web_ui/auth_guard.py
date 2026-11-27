# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The failure limit and the result cache of the worker page password check (setup-rebuild FR-SEC-8).

``FailureLimit``: at most ``limit`` failed attempts per client address in a
sliding ``window`` (10 in 60 s). ``begin`` counts an attempt as a failure up
front, before any hashing, so parallel guesses are all counted; ``forgive``
takes back one counted attempt when the password turned out right (or when
no check ran). One lock; stale addresses are dropped as they are met and in
a sweep when many addresses are held.

``ResultCache``: one entry ``(hash_str, digest, expires)``: ``hash_str`` is
the stored ``ui_password_hash`` the entry was made against, ``digest`` a
BLAKE2b of the password keyed with a random key made once per process,
``expires`` the creation time plus ``ttl`` (fixed; a hit does not extend
it). Only a correct password is ever stored, so one entry is enough. A hit
needs the same ``hash_str``, an equal digest (``hmac.compare_digest``) and
no expiry. In memory only.

Both take an injectable monotonic clock for tests. Thread-safe.
"""
import collections
import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable, Deque, Dict, Optional, Tuple

SWEEP_ABOVE = 1024


class FailureLimit:
    """Failed attempts per client address in a sliding window."""

    def __init__(self, limit: int = 10, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: Dict[str, Deque[float]] = {}

    def _prune(self, client: str, now: float) -> Deque[float]:
        """The live attempts of ``client``; caller holds ``_lock``."""
        stamps = self._attempts.get(client)
        if stamps is None:
            return collections.deque()
        while stamps and stamps[0] <= now - self._window:
            stamps.popleft()
        if not stamps:
            del self._attempts[client]
        return stamps

    def _sweep(self, now: float) -> None:
        for client in list(self._attempts):
            self._prune(client, now)

    def begin(self, client: str) -> Optional[float]:
        """Count one attempt as a failure and return its stamp; ``None`` (count nothing) when at the limit."""
        with self._lock:
            now = self._clock()
            if len(self._attempts) > SWEEP_ABOVE:
                self._sweep(now)
            stamps = self._prune(client, now)
            if len(stamps) >= self._limit:
                return None
            self._attempts.setdefault(client, stamps).append(now)
            return now

    def forgive(self, client: str, stamp: float) -> None:
        """Take back the attempt counted at ``stamp`` (the password was right, or nothing was checked)."""
        with self._lock:
            stamps = self._attempts.get(client)
            if stamps is None:
                return
            try:
                stamps.remove(stamp)
            except ValueError:
                pass
            if not stamps:
                del self._attempts[client]

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


class ResultCache:
    """The last correct password, as a keyed BLAKE2b digest tied to the hash it was checked against."""

    def __init__(self, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._entry: Optional[Tuple[str, bytes, float]] = None

    def _digest(self, password: str) -> bytes:
        return hashlib.blake2b(password.encode('utf-8'), key=self._key, digest_size=32).digest()

    def hit(self, hash_str: str, password: str) -> bool:
        digest = self._digest(password)
        with self._lock:
            entry = self._entry
        if entry is None:
            return False
        saved_hash, saved_digest, expires = entry
        if self._clock() >= expires or saved_hash != hash_str:
            return False
        return hmac.compare_digest(saved_digest, digest)

    def store(self, hash_str: str, password: str) -> None:
        """Remember a correct password unless a live entry for the same hash is held (the expiry stays fixed)."""
        digest = self._digest(password)
        with self._lock:
            now = self._clock()
            entry = self._entry
            if entry is not None and entry[0] == hash_str and now < entry[2]:
                return
            self._entry = (hash_str, digest, now + self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._entry = None
