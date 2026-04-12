# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/nonce_store.py``.

Covers the TTL expiration, lazy eviction, max-entries cap, generic
thread-safety, and — most importantly — the FR-13 / AC-9 concurrent-
replay regression: N threads calling ``check_and_record`` on the SAME
nonce must have exactly one return ``True``.
"""

import threading

import pytest

from workers.nonce_store import NonceStore


class TestBasicBehavior:
    def test_new_nonce_returns_true(self):
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        assert store.check_and_record("abc") is True

    def test_immediate_replay_returns_false(self):
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        assert store.check_and_record("abc") is True
        assert store.check_and_record("abc") is False

    def test_distinct_nonces_both_succeed(self):
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        assert store.check_and_record("nonce-a") is True
        assert store.check_and_record("nonce-b") is True


class TestTTLExpiration:
    def test_expired_nonce_can_be_re_inserted(self, mocker):
        """After the TTL elapses, the same nonce is considered fresh."""
        fake_clock = [1000.0]
        mocker.patch(
            "workers.nonce_store.time.monotonic",
            side_effect=lambda: fake_clock[0],
        )
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        assert store.check_and_record("abc") is True
        # Still within the window → replay rejected.
        fake_clock[0] = 1100.0
        assert store.check_and_record("abc") is False
        # Past the window → accepted as new.
        fake_clock[0] = 1400.0
        assert store.check_and_record("abc") is True

    def test_stale_entries_evicted_lazily(self, mocker):
        """Inserting a fresh nonce evicts stale entries on the same call."""
        fake_clock = [1000.0]
        mocker.patch(
            "workers.nonce_store.time.monotonic",
            side_effect=lambda: fake_clock[0],
        )
        store = NonceStore(ttl_seconds=60, max_entries=1000)
        for i in range(5):
            assert store.check_and_record(f"stale-{i}") is True
        assert len(store._seen) == 5
        # Advance past the TTL; a new insert should evict all stale
        # entries AND drop the old ones from the dict.
        fake_clock[0] = 2000.0
        assert store.check_and_record("fresh") is True
        assert set(store._seen.keys()) == {"fresh"}


class TestMaxEntriesCap:
    def test_cap_drops_oldest_entries_on_insert(self, mocker):
        """Once the cap is hit, the oldest ~10% are evicted."""
        # Use a fake clock so we can give each insert an unambiguously
        # increasing timestamp — the eviction order depends on it.
        fake_clock = [1000.0]

        def next_ts():
            fake_clock[0] += 0.01
            return fake_clock[0]

        mocker.patch(
            "workers.nonce_store.time.monotonic",
            side_effect=next_ts,
        )
        # Small cap so the test is fast and deterministic.
        store = NonceStore(ttl_seconds=3600, max_entries=10)
        # Fill the store to the cap.
        for i in range(10):
            assert store.check_and_record(f"n-{i}") is True
        assert len(store._seen) == 10
        # Insert one more — should evict ~10% (min 1) of the oldest.
        assert store.check_and_record("overflow") is True
        assert len(store._seen) <= 10
        assert "overflow" in store._seen
        # The newest of the original entries should still be present.
        assert "n-9" in store._seen
        # The oldest should have been dropped.
        assert "n-0" not in store._seen


class TestThreadSafety:
    def test_concurrent_unique_inserts_all_succeed(self):
        """50 threads each inserting a unique nonce — all return True."""
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        n_threads = 50
        barrier = threading.Barrier(n_threads)
        results = [None] * n_threads

        def worker(idx):
            barrier.wait()
            results[idx] = store.check_and_record(f"unique-{idx}")

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is True for r in results)
        assert len(store._seen) == n_threads

    def test_concurrent_same_nonce_exactly_one_winner(self):
        """FR-13 / AC-9 regression.

        100 threads calling ``check_and_record`` on the SAME nonce
        simultaneously — exactly 1 returns True, the other 99 return
        False.  Uses ``threading.Barrier`` so every thread hits the
        critical section at the same moment.
        """
        store = NonceStore(ttl_seconds=300, max_entries=1000)
        n_threads = 100
        barrier = threading.Barrier(n_threads)
        results = [None] * n_threads

        def worker(idx):
            barrier.wait()
            results[idx] = store.check_and_record("shared-nonce")

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r is True]
        losers = [r for r in results if r is False]
        assert len(winners) == 1
        assert len(losers) == n_threads - 1
        assert len(store._seen) == 1


@pytest.mark.parametrize("nonce", ["", "a", "x" * 256])
def test_various_nonce_strings_accepted(nonce):
    """The store treats the nonce as an opaque string, not a structured value."""
    store = NonceStore(ttl_seconds=300, max_entries=1000)
    assert store.check_and_record(nonce) is True
    assert store.check_and_record(nonce) is False
