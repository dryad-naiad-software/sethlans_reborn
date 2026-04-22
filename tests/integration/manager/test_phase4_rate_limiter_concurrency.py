# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 integration-style concurrency test for the rate limiter's
``check_and_record``.

Kept separate from ``test_phase4_thumbnail_signals.py`` so each file
stays under the project's 300-line test limit.  The test is
Django-free (pure in-memory limiter) but lives in the integration tree
because it exercises the threaded contention model that unit tests
would normally avoid.
"""

import threading

from workers.rate_limiter import InMemoryRateLimiter


def test_check_and_record_limit_strict_under_load():
    """N threads hammering ``check_and_record`` on the same key — the
    limit is enforced exactly once; no thread slips past.

    Legacy two-call API (``is_rate_limited`` then ``record_attempt``)
    would let two threads both pass the check and then both record,
    bumping the effective limit by ``n_threads``.  The Phase 4
    single-lock ``check_and_record`` closes that window.
    """
    max_attempts = 10
    n_threads = 100
    limiter = InMemoryRateLimiter(
        max_attempts=max_attempts, window_seconds=300,
    )
    barrier = threading.Barrier(n_threads)
    allowed = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        limited = limiter.check_and_record("contested")
        if not limited:
            with lock:
                allowed.append(1)

    threads = [
        threading.Thread(target=worker) for _ in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(allowed) == max_attempts, (
        f"Expected exactly {max_attempts} allowed, "
        f"got {len(allowed)}"
    )
