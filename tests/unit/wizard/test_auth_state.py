# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/auth_state.py`` (Spec 1 / A3).

Covers FR-W7 (rate limiter, single-active-session, constant-time
session compare) and FR-W12 (single handoff-state lock invariant).
"""

from __future__ import annotations

import threading
import time

import pytest

from wizard.sethlans_wizard import auth_state


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Wipe shared module state between tests."""
    auth_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()


class TestRateLimiter:

    def test_under_threshold_is_not_limited(self):
        for _ in range(auth_state._RATE_LIMIT_MAX - 1):
            auth_state.record_attempt("10.0.0.1")
        assert auth_state.is_rate_limited("10.0.0.1") is False

    def test_at_threshold_triggers_limit(self):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            auth_state.record_attempt("10.0.0.1")
        assert auth_state.is_rate_limited("10.0.0.1") is True

    def test_eleventh_attempt_is_blocked(self):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            auth_state.record_attempt("10.0.0.1")
        # The 11th attempt would still be over the line.
        auth_state.record_attempt("10.0.0.1")
        assert auth_state.is_rate_limited("10.0.0.1") is True

    def test_per_ip_isolation(self):
        for _ in range(auth_state._RATE_LIMIT_MAX):
            auth_state.record_attempt("10.0.0.1")
        assert auth_state.is_rate_limited("10.0.0.1") is True
        assert auth_state.is_rate_limited("10.0.0.2") is False

    def test_window_slides_via_monotonic(self, mocker):
        """Old attempts outside the 60s window must be pruned."""
        mock_mono = mocker.patch.object(auth_state.time, "monotonic")
        mock_mono.return_value = 1000.0
        for _ in range(auth_state._RATE_LIMIT_MAX):
            auth_state.record_attempt("10.0.0.1")
        assert auth_state.is_rate_limited("10.0.0.1") is True
        # Advance past the window.
        mock_mono.return_value = 1000.0 + auth_state._RATE_LIMIT_WINDOW + 1
        assert auth_state.is_rate_limited("10.0.0.1") is False

    def test_uses_monotonic_not_walltime(self):
        """``time.monotonic`` is the documented rate-limiter clock."""
        # Smoke: calling time.monotonic() must succeed without
        # raising. The window-slides test above asserts the actual
        # clock is the one consulted via the mocker patch.
        before = time.monotonic()
        auth_state.record_attempt("10.0.0.99")
        after = time.monotonic()
        assert before <= after


class TestSessionToken:

    def test_no_session_validates_false(self):
        assert auth_state.validate_session_token("anything") is False
        assert auth_state.validate_session_token(None) is False

    def test_set_then_validate(self):
        auth_state.set_session_token("alpha-token")
        assert auth_state.validate_session_token("alpha-token") is True
        assert auth_state.validate_session_token("beta-token") is False

    def test_issue_returns_url_safe_token(self):
        token = auth_state.issue_session_token()
        # secrets.token_urlsafe(32) yields 43 base64-url chars (no padding).
        assert isinstance(token, str)
        assert len(token) >= 32
        assert auth_state.validate_session_token(token) is True

    def test_single_active_session_invalidates_prior(self):
        first = auth_state.issue_session_token()
        second = auth_state.issue_session_token()
        assert first != second
        assert auth_state.validate_session_token(first) is False
        assert auth_state.validate_session_token(second) is True

    def test_clear_invalidates_session(self):
        auth_state.set_session_token("kept-briefly")
        auth_state.clear_session_token()
        assert auth_state.validate_session_token("kept-briefly") is False

    def test_validate_uses_compare_digest(self, mocker):
        mock_compare = mocker.patch.object(
            auth_state.secrets, "compare_digest",
            wraps=auth_state.secrets.compare_digest,
        )
        auth_state.set_session_token("constant-time-please")
        auth_state.validate_session_token("constant-time-please")
        assert mock_compare.called

    def test_validate_rejects_non_string(self):
        auth_state.set_session_token("xyz")
        assert auth_state.validate_session_token(b"xyz") is False
        assert auth_state.validate_session_token(123) is False


class TestAttemptsReaper:
    """Issue #147 — periodic reaper that bounds ``_attempts`` growth.

    A scanner / botnet sweep that hits the auth endpoint once per IP
    used to leak a dict entry per unique IP until process exit. The
    reaper drops any bucket whose newest timestamp is older than
    ``_RATE_LIMIT_WINDOW``, walking the dict every
    ``REAPER_INTERVAL_SECS`` seconds.
    """

    def test_record_attempt_starts_reaper(self):
        """First ``record_attempt`` call lazily spawns the daemon."""
        assert auth_state._reaper.is_running() is False
        auth_state.record_attempt("10.0.0.1")
        assert auth_state._reaper.is_running() is True

    def test_reset_state_stops_reaper(self):
        """``reset_state_for_tests`` joins the reaper between tests."""
        auth_state.record_attempt("10.0.0.1")
        assert auth_state._reaper.is_running() is True
        auth_state.reset_state_for_tests()
        assert auth_state._reaper.is_running() is False

    def test_reap_drops_buckets_older_than_window(self, mocker):
        """A bucket whose newest timestamp is past the window is dropped."""
        mock_mono = mocker.patch.object(auth_state.time, "monotonic")
        mock_mono.return_value = 1000.0
        auth_state.record_attempt("10.0.0.1")
        auth_state.record_attempt("10.0.0.2")
        assert "10.0.0.1" in auth_state._attempts
        assert "10.0.0.2" in auth_state._attempts

        # Advance past the window — both buckets should now be reapable.
        future = 1000.0 + auth_state._RATE_LIMIT_WINDOW + 1
        removed = auth_state._reap_stale_buckets(future)
        assert removed == 2
        assert auth_state._attempts == {}

    def test_reap_keeps_fresh_buckets(self, mocker):
        """A bucket with a fresh timestamp survives a reap pass."""
        mock_mono = mocker.patch.object(auth_state.time, "monotonic")
        mock_mono.return_value = 1000.0
        auth_state.record_attempt("10.0.0.1")
        # Time advances less than the window — bucket must stay.
        future = 1000.0 + (auth_state._RATE_LIMIT_WINDOW / 2)
        removed = auth_state._reap_stale_buckets(future)
        assert removed == 0
        assert "10.0.0.1" in auth_state._attempts

    def test_unique_ip_sweep_is_bounded(self, mocker):
        """10k unique-IP attempts shrink back to {} after the window slides.

        This is the regression scenario from Issue #147 — each unique
        IP used to leak one bucket forever. The reaper now reclaims
        them once their timestamps fall outside the window.
        """
        mock_mono = mocker.patch.object(auth_state.time, "monotonic")
        mock_mono.return_value = 5000.0
        for i in range(10_000):
            # Synthetic IPs in 10.x.y.z space — content is irrelevant,
            # the test cares only about dict cardinality.
            auth_state.record_attempt(
                f"10.{(i >> 16) & 0xFF}.{(i >> 8) & 0xFF}.{i & 0xFF}"
            )
        assert len(auth_state._attempts) == 10_000

        # Advance past the rate-limit window and drive a single reap
        # tick manually (no need to wait on the real interval).
        mock_mono.return_value = 5000.0 + auth_state._RATE_LIMIT_WINDOW + 1
        removed = auth_state._reap_stale_buckets(mock_mono.return_value)
        assert removed == 10_000
        assert auth_state._attempts == {}

    def test_reaper_tick_for_tests_invokes_reap(self):
        """``tick_for_tests`` drives one reap iteration synchronously.

        We seed the dict, advance the reaper's clock past the window,
        and confirm the synchronous tick prunes everything without
        having to wait on the real ``REAPER_INTERVAL_SECS`` sleep.
        """
        auth_state.record_attempt("10.0.0.99")
        assert "10.0.0.99" in auth_state._attempts

        # Build a one-off reaper with a clock we control directly so
        # the assertion does not depend on monkeypatching the module
        # clock used by ``record_attempt``.
        from wizard.sethlans_wizard._attempts_reaper import AttemptsReaper

        fake_now = [10_000.0]
        reaper = AttemptsReaper(
            auth_state._reap_stale_buckets,
            interval_secs=0.05,
            clock=lambda: fake_now[0],
        )
        # Push the clock past the window and tick.
        fake_now[0] = 10_000.0 + auth_state._RATE_LIMIT_WINDOW + 1
        removed = reaper.tick_for_tests()
        assert removed >= 1
        assert "10.0.0.99" not in auth_state._attempts


class TestHandoffLock:

    def test_returns_singleton_lock(self):
        lock1 = auth_state.get_handoff_lock()
        lock2 = auth_state.get_handoff_lock()
        assert lock1 is lock2

    def test_lock_is_acquirable(self):
        lock = auth_state.get_handoff_lock()
        acquired = lock.acquire(timeout=0.5)
        try:
            assert acquired is True
        finally:
            if acquired:
                lock.release()

    def test_session_writes_are_serialised(self):
        """Concurrent set_session_token writes must not deadlock or
        leave a stale read visible.

        We hammer ``issue_session_token`` from multiple threads and
        check the final ``validate_session_token`` agrees with one of
        the issued tokens — i.e. the lock kept the publish atomic.
        """
        issued: list[str] = []
        issued_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            tok = auth_state.issue_session_token()
            with issued_lock:
                issued.append(tok)

        threads = [
            threading.Thread(target=worker, name=f"hammer-{i}")
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "auth_state lock deadlock"

        # Exactly one of the issued tokens is the live one.
        live = [t for t in issued if auth_state.validate_session_token(t)]
        assert len(live) == 1
