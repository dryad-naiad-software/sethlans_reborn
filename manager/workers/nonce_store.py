# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
In-memory nonce store with lazy expiry and a maximum-entry cap.

Used by the enrollment endpoint to detect replay attacks within a 5-minute
window.  The store is intentionally in-process and non-persistent — nonces
are only meaningful during the short replay window, and a manager restart
clearing the store is acceptable because any replayed request must still
pass HMAC validation against the current enrollment key.

Thread-safety invariant (FR-13):

    ``check_and_record`` performs membership check, eviction of expired
    entries, and insert of the new nonce under a SINGLE lock acquisition.
    Two concurrent requests with the same nonce MUST have exactly one
    return ``True`` and the other ``False``.
"""

import threading
import time

_DEFAULT_TTL = 300
_DEFAULT_MAX_ENTRIES = 10_000


class NonceStore:
    """Track recently-seen nonces under a TTL window."""

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._seen: dict = {}  # nonce -> insert_timestamp
        self._lock = threading.Lock()

    def check_and_record(self, nonce: str) -> bool:
        """Return ``True`` if the nonce is new; ``False`` on replay.

        The check, eviction sweep, and insert all happen under a single
        lock acquisition — see the FR-13 atomicity invariant.
        """
        now = time.monotonic()
        with self._lock:
            if nonce in self._seen:
                # Still within the TTL window?  The lazy eviction below
                # deletes stale entries, so the presence of the nonce in
                # the dict is enough to confirm it's fresh.
                if (now - self._seen[nonce]) < self._ttl:
                    return False
                # Expired entry — fall through to re-insert.
            self._evict_stale(now)
            if len(self._seen) >= self._max_entries:
                self._evict_oldest_to_fit()
            self._seen[nonce] = now
            return True

    def _evict_stale(self, now: float) -> None:
        """Delete entries whose TTL has elapsed (caller holds lock)."""
        ttl = self._ttl
        stale = [
            n for n, ts in self._seen.items()
            if (now - ts) >= ttl
        ]
        for n in stale:
            del self._seen[n]

    def _evict_oldest_to_fit(self) -> None:
        """Evict the oldest entries when the cap is exceeded.

        Called only when the dict is already full; drops roughly 10% of
        the oldest entries so we don't re-enter this path on every insert.
        """
        drop_count = max(1, self._max_entries // 10)
        ordered = sorted(self._seen.items(), key=lambda kv: kv[1])
        for nonce, _ts in ordered[:drop_count]:
            del self._seen[nonce]
