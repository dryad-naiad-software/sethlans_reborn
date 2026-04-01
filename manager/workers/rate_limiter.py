# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
In-memory rate limiter for protecting endpoints against brute force.

Thread-safe implementation with lazy eviction and entry cap.
Suitable for a LAN-only single-process deployment.
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

    def is_rate_limited(self, ip):
        """Return True if the IP has exceeded the attempt limit."""
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
