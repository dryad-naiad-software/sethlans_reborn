# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 unit tests for locked access to ``_last_stuck_check``.

``requeue_stuck_jobs`` is debounced by a module-level float.  Under
threaded Waitress, two heartbeats processed concurrently could both
observe a stale timestamp and each run the stuck-scan in parallel.  A
``threading.Lock`` around the check-and-swap collapses that window.
"""

import threading
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from workers.views import stuck_jobs as stuck_jobs_module
from workers.views.stuck_jobs import requeue_stuck_jobs


@pytest.fixture(autouse=True)
def reset_debounce():
    stuck_jobs_module._last_stuck_check = 0.0
    yield
    stuck_jobs_module._last_stuck_check = 0.0


_tx_patch = patch.object(
    stuck_jobs_module, "transaction",
    **{"atomic.return_value": nullcontext()},
)


class TestLockExists:

    def test_lock_attribute_is_a_lock(self):
        lock = stuck_jobs_module._last_stuck_check_lock
        # Python's threading.Lock is a built-in whose type check is
        # most reliable via duck typing on the methods present.
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")

    def test_lock_is_reentrant_neutral_for_simple_use(self):
        """We expect a plain Lock (not RLock); either is acceptable
        so long as the debounce path acquires exactly once."""
        lock = stuck_jobs_module._last_stuck_check_lock
        acquired = lock.acquire(timeout=1.0)
        try:
            assert acquired is True
        finally:
            if acquired:
                lock.release()


class TestDebounceLockedAccess:

    @_tx_patch
    @patch.object(stuck_jobs_module, "Job")
    def test_concurrent_callers_only_one_runs(self, mock_job_cls, _tx):
        """Two concurrent requeue_stuck_jobs calls under the lock
        produce exactly one scan pass (the second hits debounce)."""
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []

        results = []

        def runner():
            results.append(requeue_stuck_jobs())

        threads = [threading.Thread(target=runner) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2)

        # All five concurrent calls ran, but the scan itself only
        # fired once; the other four short-circuited via debounce.
        # ``filter`` is the first DB access after the debounce gate.
        assert mock_job_cls.objects.filter.call_count == 1
        # All five returns are 0 (empty list).
        assert all(r == 0 for r in results)

    @_tx_patch
    @patch.object(stuck_jobs_module, "Job")
    def test_lock_released_even_on_exception(self, mock_job_cls, _tx):
        """Exception inside the critical section must release the lock."""
        mock_job_cls.objects.filter.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            requeue_stuck_jobs()

        # Lock must be acquirable again; otherwise the next request
        # deadlocks the server.
        acquired = stuck_jobs_module._last_stuck_check_lock.acquire(
            timeout=1.0,
        )
        try:
            assert acquired is True, (
                "Lock was not released after exception"
            )
        finally:
            if acquired:
                stuck_jobs_module._last_stuck_check_lock.release()

    @_tx_patch
    @patch.object(stuck_jobs_module, "Job")
    def test_locked_timestamp_update_is_atomic(
        self, mock_job_cls, _tx,
    ):
        """The check-and-swap on ``_last_stuck_check`` must run under
        the module lock so two observers can't both pass the gate."""
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []

        # Run once to set the timestamp.
        requeue_stuck_jobs()
        first_ts = stuck_jobs_module._last_stuck_check
        assert first_ts > 0.0

        # Immediate second call — debounce skips, timestamp unchanged.
        requeue_stuck_jobs()
        assert stuck_jobs_module._last_stuck_check == first_ts


class TestLockDoesNotBlockUnrelatedThreads:

    def test_lock_held_briefly(self):
        """Sanity: acquiring the lock from another thread should not
        hang indefinitely under normal use."""
        lock = stuck_jobs_module._last_stuck_check_lock
        result = {"ok": False}

        def other():
            got = lock.acquire(timeout=1.0)
            try:
                result["ok"] = got
            finally:
                if got:
                    lock.release()

        t = threading.Thread(target=other)
        t.start()
        t.join(timeout=2.0)
        assert result["ok"] is True
