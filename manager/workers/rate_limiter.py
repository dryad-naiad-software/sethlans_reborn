# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
In-memory rate limiter for protecting endpoints against brute force.

Thread-safe implementation with lazy eviction and entry cap.
Suitable for a LAN-only single-process deployment.

Phase 4 (Waitress migration) note
---------------------------------
The legacy API exposed ``is_rate_limited(ip)`` + ``record_attempt(ip)``
as two separate calls — under threaded Waitress two workers could both
observe ``is_rate_limited == False`` and then each call
``record_attempt``, slipping one extra attempt past the limit on every
contended window boundary (classic TOCTOU).  The canonical API is now
``check_and_record(ip)`` which performs both steps under a single lock
acquisition.  The legacy methods remain as thin delegates for a few
read-only call sites (``enroll_view`` pre-validation, test fixtures);
they internally reuse the same lock, and callers that need the
combined check-then-record semantics MUST use ``check_and_record``.
"""

import threading
import time


class InMemoryRateLimiter:
    """
    Tracks failed attempts per IP within a sliding time window.

    Parameters
    ----------
    max_attempts : int
        Number of attempts allowed within the window.
    window_seconds : int
        Duration of the sliding window in seconds.
    max_entries : int
        Maximum number of tracked IPs before forced eviction.
    """

    def __init__(self, max_attempts, window_seconds, max_entries=10000):
        self._attempts = {}  # ip -> (count, window_start)
        self._lock = threading.Lock()
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._max_entries = max_entries

    # ------------------------------------------------------------------
    # Canonical Phase 4 API — single lock acquisition closes TOCTOU.
    # ------------------------------------------------------------------

    def check_and_record(self, ip):
        """Atomically check the limit for ``ip`` and record an attempt.

        Returns
        -------
        bool
            ``True`` if the caller should be treated as rate-limited
            (i.e., the attempt count was already at/over the limit
            BEFORE this call); the attempt is still recorded so that
            repeated probes continue to extend the window.  ``False``
            if the attempt is within the limit and the caller should
            be allowed to proceed.

        Notes
        -----
        Both the limit read and the attempt increment happen under a
        single ``self._lock`` acquisition.  This closes the read /
        write gap that the legacy two-call API exposed when multiple
        threads serviced requests in parallel (Phase 5+ Waitress).
        """
        now = time.time()
        with self._lock:
            self._evict_stale(now)
            entry = self._attempts.get(ip)
            limited = False
            if entry and (now - entry[1]) < self._window:
                if entry[0] >= self._max_attempts:
                    limited = True
                self._attempts[ip] = (entry[0] + 1, entry[1])
            else:
                self._attempts[ip] = (1, now)
            return limited

    # ------------------------------------------------------------------
    # Legacy two-call API.
    #
    # Retained because a handful of call sites (and the existing test
    # suite) only need to *read* the limit without recording an
    # attempt — e.g. ``enroll_view`` checks ``is_rate_limited`` at the
    # top of the handler and only records on *failed* downstream
    # validation.  Both methods still hold ``self._lock`` internally,
    # so they are individually thread-safe; it is only the *combined*
    # read-then-write sequence that had the race, which is now exposed
    # as ``check_and_record``.
    # ------------------------------------------------------------------

    def is_rate_limited(self, ip):
        """Return ``True`` if the IP has exceeded the attempt limit.

        Read-only check.  Does NOT record an attempt.  Use
        ``check_and_record`` when the callsite also records the
        attempt on the same code path.
        """
        now = time.time()
        with self._lock:
            self._evict_stale(now)
            entry = self._attempts.get(ip)
            if entry and (now - entry[1]) < self._window:
                if entry[0] >= self._max_attempts:
                    return True
        return False

    def record_attempt(self, ip):
        """Record a failed attempt for the given IP."""
        now = time.time()
        with self._lock:
            entry = self._attempts.get(ip)
            if entry and (now - entry[1]) < self._window:
                self._attempts[ip] = (entry[0] + 1, entry[1])
            else:
                self._attempts[ip] = (1, now)

    # ------------------------------------------------------------------
    # Internal helpers — callers already hold ``self._lock``.
    # ------------------------------------------------------------------

    def _evict_stale(self, now):
        """Remove expired entries; enforce max_entries cap."""
        stale = [
            ip for ip, (_, start) in self._attempts.items()
            if (now - start) >= self._window
        ]
        for ip in stale:
            del self._attempts[ip]
        if len(self._attempts) > self._max_entries:
            sorted_ips = sorted(
                self._attempts,
                key=lambda ip: self._attempts[ip][1],
            )
            excess = len(self._attempts) - self._max_entries
            for ip in sorted_ips[:excess]:
                del self._attempts[ip]
