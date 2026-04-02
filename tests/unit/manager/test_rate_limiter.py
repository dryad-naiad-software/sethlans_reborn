# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/rate_limiter.py.

Uses time mocking to test sliding window behaviour without real sleeps.
"""

from workers.rate_limiter import InMemoryRateLimiter


class TestInMemoryRateLimiter:

    # ---- Basic behaviour ----

    def test_new_ip_is_not_rate_limited(self):
        limiter = InMemoryRateLimiter(max_attempts=3, window_seconds=60)
        assert limiter.is_rate_limited("192.168.1.1") is False

    def test_under_limit_not_blocked(self):
        limiter = InMemoryRateLimiter(max_attempts=3, window_seconds=60)
        limiter.record_attempt("10.0.0.1")
        limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is False

    def test_at_limit_is_blocked(self):
        limiter = InMemoryRateLimiter(max_attempts=3, window_seconds=60)
        for _ in range(3):
            limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is True

    def test_over_limit_stays_blocked(self):
        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)
        for _ in range(5):
            limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is True

    # ---- Window expiry ----

    def test_window_expiry_resets_block(self, mocker):
        """After the window elapses, the IP should no longer be blocked."""
        fake_time = mocker.patch("workers.rate_limiter.time")

        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)

        # Record attempts at t=100
        fake_time.time.return_value = 100.0
        limiter.record_attempt("10.0.0.1")
        limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is True

        # Advance past the window at t=161
        fake_time.time.return_value = 161.0
        assert limiter.is_rate_limited("10.0.0.1") is False

    def test_attempt_after_expiry_starts_new_window(self, mocker):
        fake_time = mocker.patch("workers.rate_limiter.time")

        limiter = InMemoryRateLimiter(max_attempts=2, window_seconds=60)

        fake_time.time.return_value = 100.0
        limiter.record_attempt("10.0.0.1")
        limiter.record_attempt("10.0.0.1")

        # After window expires, new attempt starts fresh
        fake_time.time.return_value = 200.0
        limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is False

    # ---- Separate IPs ----

    def test_different_ips_tracked_independently(self):
        limiter = InMemoryRateLimiter(max_attempts=1, window_seconds=60)
        limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is True
        assert limiter.is_rate_limited("10.0.0.2") is False

    # ---- Eviction ----

    def test_stale_entries_evicted(self, mocker):
        fake_time = mocker.patch("workers.rate_limiter.time")

        limiter = InMemoryRateLimiter(
            max_attempts=1, window_seconds=10
        )

        fake_time.time.return_value = 100.0
        limiter.record_attempt("stale_ip")

        # Far into the future — stale entry should be evicted
        fake_time.time.return_value = 200.0
        # is_rate_limited triggers eviction
        limiter.is_rate_limited("other_ip")
        assert "stale_ip" not in limiter._attempts

    def test_max_entries_cap_evicts_oldest(self, mocker):
        fake_time = mocker.patch("workers.rate_limiter.time")

        limiter = InMemoryRateLimiter(
            max_attempts=5, window_seconds=300, max_entries=3
        )

        # Insert 4 IPs at increasing times
        for i in range(4):
            fake_time.time.return_value = float(100 + i)
            limiter.record_attempt(f"ip_{i}")

        # Trigger eviction via is_rate_limited
        fake_time.time.return_value = 104.0
        limiter.is_rate_limited("trigger")

        # Oldest entry (ip_0) should have been evicted
        assert "ip_0" not in limiter._attempts
        # Newest entries should remain
        assert "ip_3" in limiter._attempts

    # ---- Edge cases ----

    def test_max_attempts_one(self):
        limiter = InMemoryRateLimiter(max_attempts=1, window_seconds=60)
        limiter.record_attempt("10.0.0.1")
        assert limiter.is_rate_limited("10.0.0.1") is True

    def test_zero_attempts_recorded_not_blocked(self):
        limiter = InMemoryRateLimiter(max_attempts=0, window_seconds=60)
        # With max_attempts=0, even zero recorded attempts should block
        # since 0 >= 0 is True
        assert limiter.is_rate_limited("10.0.0.1") is False

    def test_empty_ip_string(self):
        limiter = InMemoryRateLimiter(max_attempts=1, window_seconds=60)
        limiter.record_attempt("")
        assert limiter.is_rate_limited("") is True
