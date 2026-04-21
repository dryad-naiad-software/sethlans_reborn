# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 unit tests for ``rate_limiter.check_and_record``.

``check_and_record`` replaces the legacy ``is_rate_limited`` +
``record_attempt`` pair with a single lock acquisition, closing the
TOCTOU window that threaded Waitress (Phase 5+) would otherwise expose
by letting two threads both observe ``False`` and then each record an
attempt, pushing the count past the limit.
"""

import threading

from workers.rate_limiter import InMemoryRateLimiter


class TestCheckAndRecordBasic:

    def test_first_call_not_limited_records_one(self):
        limiter = InMemoryRateLimiter(max_attempts=3, window_seconds=60)
        assert limiter.check_and_record("10.0.0.1") is False
        # one attempt recorded
        assert limiter._attempts["10.0.0.1"][0] == 1

    def test_calls_under_limit_return_false(self):
        limiter = InMemoryRateLimiter(max_attempts=3, window_seconds=60)
        assert limiter.check_and_record("10.0.0.1") is False
        assert limiter.check_and_record("10.0.0.1") is False
        assert limiter.check_and_record("10.0.0.1") is False
        # 3rd attempt still "not limited" — count was 2 before, now 3;
        # next call should be the one to trip the limit.
        assert limiter.check_and_record("10.0.0.1") is True

    def test_limit_enforced_exactly_at_max(self):
        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)
        assert limiter.check_and_record("x") is False  # count 0 -> 1
        assert limiter.check_and_record("x") is False  # count 1 -> 2
        # Count now == max_attempts, next call must return True.
        assert limiter.check_and_record("x") is True
        assert limiter.check_and_record("x") is True

    def test_different_ips_tracked_independently(self):
        limiter = InMemoryRateLimiter(max_attempts=1, window_seconds=60)
        assert limiter.check_and_record("a") is False
        assert limiter.check_and_record("a") is True
        # Different IP unaffected
        assert limiter.check_and_record("b") is False


class TestCheckAndRecordWindowExpiry:

    def test_expired_window_resets(self, mocker):
        fake_time = mocker.patch("workers.rate_limiter.time")

        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)

        fake_time.time.return_value = 100.0
        limiter.check_and_record("ip")
        limiter.check_and_record("ip")
        assert limiter.check_and_record("ip") is True

        # Window expires
        fake_time.time.return_value = 200.0
        # Old entry evicted, new window starts, not limited.
        assert limiter.check_and_record("ip") is False


class TestCheckAndRecordConcurrency:
    """The headline Phase 4 concurrency guarantee."""

    def test_exactly_max_attempts_allowed_across_threads(self):
        """N threads hammer the same key; at most max_attempts
        calls return False.

        Under the legacy API two threads could both observe
        ``is_rate_limited == False``, each record, and slip an extra
        attempt past the cap.  Under ``check_and_record`` the cap is
        strict.
        """
        max_attempts = 5
        n_threads = 50
        limiter = InMemoryRateLimiter(
            max_attempts=max_attempts, window_seconds=300,
        )
        allowed_counter = []
        barrier = threading.Barrier(n_threads)
        lock = threading.Lock()

        def worker():
            barrier.wait()  # maximise contention
            was_limited = limiter.check_and_record("contested_ip")
            if was_limited is False:
                with lock:
                    allowed_counter.append(1)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Exactly ``max_attempts`` calls should have returned False;
        # the rest should have been limited.
        assert len(allowed_counter) == max_attempts, (
            f"Expected exactly {max_attempts} allowed calls, "
            f"got {len(allowed_counter)}"
        )

    def test_stress_no_count_exceeded_silently(self):
        """Even under heavy contention the stored count equals the
        total number of calls — no lost writes under the lock."""
        limiter = InMemoryRateLimiter(
            max_attempts=999_999, window_seconds=300,
        )
        n_threads = 20
        calls_per_thread = 50
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for _ in range(calls_per_thread):
                limiter.check_and_record("shared")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert limiter._attempts["shared"][0] == n_threads * calls_per_thread


class TestLegacyApiPreserved:
    """Backward compat: the existing two-call API still works."""

    def test_legacy_is_rate_limited_still_works(self):
        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)
        limiter.record_attempt("x")
        limiter.record_attempt("x")
        assert limiter.is_rate_limited("x") is True

    def test_legacy_record_does_not_inflate_check_and_record(self):
        """Mixing legacy and new API share the same underlying state."""
        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)
        limiter.record_attempt("x")
        # 1 attempt recorded via legacy; new API sees it.
        assert limiter.check_and_record("x") is False  # now 2
        assert limiter.check_and_record("x") is True   # still at limit
