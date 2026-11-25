# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``auth.RateLimiter``, ``auth.Sessions`` and ``auth.SetupAuth`` on fake clocks."""
import pytest

from sethlans_manager.setup_mode import auth
from tests.unit.manager.setup_mode._helpers import LAN_ADDRESS, TOKEN, FakeClock

TWO_HOURS = 2 * 60 * 60


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def guard(clock):
    return auth.SetupAuth(TOKEN, limiter=auth.RateLimiter(clock=clock), sessions=auth.Sessions(clock=clock))


class TestRateLimiter:

    def test_defaults_are_10_in_60_seconds(self):
        assert (auth.MAX_FAILURES, auth.WINDOW_SECONDS) == (10, 60)

    def test_nine_failures_do_not_block(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        for _ in range(9):
            limiter.record_failure("a")
        assert limiter.blocked("a") is False

    def test_ten_failures_block(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        for _ in range(10):
            limiter.record_failure("a")
        assert limiter.blocked("a") is True

    def test_block_lifts_when_the_oldest_failure_is_60_seconds_old(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        limiter.record_failure("a")
        clock.advance(30)
        for _ in range(9):
            limiter.record_failure("a")
        clock.advance(29.9)
        assert limiter.blocked("a") is True
        clock.advance(0.1)  # the first failure is now exactly 60 s old
        assert limiter.blocked("a") is False

    def test_clients_are_counted_apart(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        for _ in range(10):
            limiter.record_failure(LAN_ADDRESS)
        assert limiter.blocked(LAN_ADDRESS) is True
        assert limiter.blocked("127.0.0.1") is False


class TestRateLimiterSizeCap:

    def test_default_cap_is_10000_clients(self, clock):
        assert auth.RateLimiter(clock=clock)._max_clients == 10000 == auth.MAX_TRACKED_CLIENTS

    def test_len_counts_tracked_clients(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        assert len(limiter) == 0
        limiter.record_failure("a")
        limiter.record_failure("a")
        limiter.record_failure("b")
        assert len(limiter) == 2

    def test_len_never_exceeds_max_clients(self, clock):
        limiter = auth.RateLimiter(clock=clock, max_clients=5)
        for index in range(50):
            limiter.record_failure(f"10.0.0.{index}")
            assert len(limiter) <= 5
        assert len(limiter) == 5

    def test_oldest_client_is_evicted_and_its_count_forgotten(self, clock):
        limiter = auth.RateLimiter(clock=clock, max_clients=2)
        for _ in range(10):
            limiter.record_failure("old")
        assert limiter.blocked("old") is True
        clock.advance(1)
        limiter.record_failure("b")
        clock.advance(1)
        limiter.record_failure("c")  # full: "old" has the oldest latest failure
        assert limiter.blocked("old") is False
        assert len(limiter) == 2

    def test_a_new_failure_moves_a_client_to_the_back(self, clock):
        limiter = auth.RateLimiter(clock=clock, max_clients=2)
        for _ in range(10):
            limiter.record_failure("a")
        clock.advance(1)
        limiter.record_failure("b")
        clock.advance(1)
        limiter.record_failure("a")  # "a" is now the most recent
        clock.advance(1)
        limiter.record_failure("c")  # evicts "b", not "a"
        assert limiter.blocked("a") is True
        assert "b" not in limiter._failures

    def test_expired_clients_are_dropped_before_evicting(self, clock):
        limiter = auth.RateLimiter(clock=clock, max_clients=2)
        limiter.record_failure("stale")
        clock.advance(30)
        for _ in range(10):
            limiter.record_failure("live")
        clock.advance(31)  # "stale" is out of the window, "live" is not
        limiter.record_failure("new")
        assert limiter.blocked("live") is True
        assert set(limiter._failures) == {"live", "new"}

    def test_each_client_keeps_at_most_max_failures(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        for _ in range(1000):
            limiter.record_failure("a")
        assert len(limiter._failures["a"]) == auth.MAX_FAILURES

    def test_flood_of_addresses_never_blocks_loopback(self, clock):
        guard = auth.SetupAuth(TOKEN, limiter=auth.RateLimiter(clock=clock, max_clients=100))
        for index in range(5000):
            assert guard.attempt("wrong", f"10.{index // 65536}.{index // 256 % 256}.{index % 256}") == auth.BAD
        assert len(guard.limiter) == 100
        assert guard.attempt(TOKEN, "127.0.0.1") == auth.OK

    def test_loopback_at_its_own_limit_stays_limited_during_a_flood(self, clock):
        limiter = auth.RateLimiter(clock=clock, max_clients=100)
        guard = auth.SetupAuth(TOKEN, limiter=limiter)
        for _ in range(10):
            guard.attempt("wrong", "127.0.0.1")
        for index in range(50):
            guard.attempt("wrong", f"10.0.0.{index}")
        assert guard.attempt(TOKEN, "127.0.0.1") == auth.LIMITED


class TestSetupAuthKeepsGivenParts:

    def test_an_empty_limiter_is_kept(self, clock):
        limiter = auth.RateLimiter(clock=clock)
        assert len(limiter) == 0 and not limiter  # empty is falsy
        assert auth.SetupAuth(TOKEN, limiter=limiter).limiter is limiter

    def test_given_sessions_are_kept(self, clock):
        sessions = auth.Sessions(clock=clock)
        assert auth.SetupAuth(TOKEN, sessions=sessions).sessions is sessions

    def test_defaults_are_made_when_none_given(self):
        guard = auth.SetupAuth(TOKEN)
        assert isinstance(guard.limiter, auth.RateLimiter) and isinstance(guard.sessions, auth.Sessions)


class TestSetupAuthAttempt:

    def test_right_token(self, guard):
        assert guard.attempt(TOKEN, "127.0.0.1") == auth.OK

    @pytest.mark.parametrize("provided", ["wrong", TOKEN + "x", TOKEN[:-1], "", None, 42, [TOKEN], {"t": TOKEN}])
    def test_anything_else_is_bad_and_counted(self, guard, provided):
        assert guard.attempt(provided, "c") == auth.BAD
        assert len(guard.limiter._failures["c"]) == 1

    def test_right_token_is_not_counted(self, guard):
        guard.attempt(TOKEN, "c")
        assert guard.limiter.blocked("c") is False
        assert "c" not in guard.limiter._failures

    def test_right_token_at_the_limit_is_still_limited(self, guard):
        for _ in range(10):
            guard.attempt("wrong", "c")
        assert guard.attempt(TOKEN, "c") == auth.LIMITED

    def test_limited_attempts_do_not_extend_the_block(self, guard, clock):
        for _ in range(10):
            guard.attempt("wrong", "c")
        clock.advance(59)
        assert guard.attempt("wrong", "c") == auth.LIMITED
        clock.advance(1)
        assert guard.attempt(TOKEN, "c") == auth.OK

    def test_lan_client_at_the_limit_does_not_block_loopback(self, guard):
        for _ in range(10):
            guard.attempt("wrong", LAN_ADDRESS)
        assert guard.attempt(TOKEN, LAN_ADDRESS) == auth.LIMITED
        assert guard.attempt(TOKEN, "127.0.0.1") == auth.OK

    def test_non_ascii_attempt_does_not_raise(self, guard):
        assert guard.attempt("tøken\udcff", "c") == auth.BAD


class TestSessions:

    def test_issue_returns_a_url_safe_43_char_token(self, clock):
        token = auth.Sessions(clock=clock).issue()
        assert len(token) == 43
        assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

    def test_cookie_and_header_must_both_match(self, clock):
        sessions = auth.Sessions(clock=clock)
        token = sessions.issue()
        assert sessions.check(token, token) is True
        assert sessions.check(token, None) is False
        assert sessions.check(None, token) is False
        assert sessions.check(token, "other") is False
        assert sessions.check("other", token) is False
        assert sessions.check("", "") is False

    def test_no_session_before_issue(self, clock):
        assert auth.Sessions(clock=clock).check("x", "x") is False

    def test_a_new_session_replaces_the_old_one(self, clock):
        sessions = auth.Sessions(clock=clock)
        old = sessions.issue()
        new = sessions.issue()
        assert old != new
        assert sessions.check(old, old) is False
        assert sessions.check(new, new) is True

    def test_expires_after_two_idle_hours(self, clock):
        sessions = auth.Sessions(clock=clock)
        token = sessions.issue()
        clock.advance(TWO_HOURS - 0.001)
        assert sessions.check(token, token) is True
        clock.advance(TWO_HOURS)
        assert sessions.check(token, token) is False
        clock.advance(-TWO_HOURS)  # an expired session stays gone
        assert sessions.check(token, token) is False

    def test_exactly_two_hours_is_expired(self, clock):
        sessions = auth.Sessions(clock=clock)
        token = sessions.issue()
        clock.advance(TWO_HOURS)
        assert sessions.check(token, token) is False

    def test_each_good_request_refreshes_the_expiry(self, clock):
        sessions = auth.Sessions(clock=clock)
        token = sessions.issue()
        for _ in range(3):
            clock.advance(TWO_HOURS - 1)
            assert sessions.check(token, token) is True

    def test_a_failed_check_does_not_refresh(self, clock):
        sessions = auth.Sessions(clock=clock)
        token = sessions.issue()
        clock.advance(TWO_HOURS - 1)
        assert sessions.check(token, "wrong") is False
        clock.advance(1)
        assert sessions.check(token, token) is False
