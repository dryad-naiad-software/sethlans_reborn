# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for :mod:`worker.web_ui.setup.lock`.

Covers the Phase 4a foundation module that later sub-commits
(4b-4g) will wire into the rewritten worker setup handlers.  These
tests establish the contract now so handler-wiring commits can
focus on call-site correctness, not lock semantics.

Mirrors the concurrency pattern established in
``test_setup_gate_wsgi_concurrency.py::test_concurrent_reads_during_mark_setup_complete``
(FR-18 locking invariant).
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import pytest

from sethlans_worker_agent.web_ui.setup.lock import (
    is_setup_mutation_in_progress,
    setup_mutation_lock,
)
from sethlans_worker_agent.web_ui.setup import lock as lock_module


@pytest.fixture(autouse=True)
def _ensure_lock_released():
    """Belt-and-braces: guarantee the module-level lock is free
    between tests.

    Every test in this module acquires the module-level lock -- if
    a test asserts wrongly and raises mid-``with``, the context
    manager releases on its own, but if a test leaks the lock in
    some other way (e.g., a thread that never completes) the
    remaining tests would wedge.  This fixture fails loudly instead.
    """
    assert not lock_module._setup_lock.locked(), (
        'lock leaked from a previous test -- investigate'
    )
    yield
    # Defensive cleanup: if a test left it locked, release so
    # subsequent tests don't pile up failures.  We still fail the
    # offending test via the post-yield assertion below.
    if lock_module._setup_lock.locked():
        try:
            lock_module._setup_lock.release()
        except RuntimeError:
            # Released from a thread we don't own -- best-effort;
            # the test that leaked should already be failing.
            pass
        pytest.fail('test leaked the setup mutation lock')


class TestHappyPath:
    def test_acquire_yields_true_and_releases_on_exit(self):
        """First acquirer yields ``True``; lock is released on exit."""
        assert not is_setup_mutation_in_progress()
        with setup_mutation_lock() as acquired:
            assert acquired is True
            assert is_setup_mutation_in_progress() is True
        assert is_setup_mutation_in_progress() is False

    def test_sequential_acquire_release_reacquire(self):
        """Releasing allows the next acquirer to succeed."""
        with setup_mutation_lock() as first:
            assert first is True
        # After release, a fresh acquire succeeds.
        with setup_mutation_lock() as second:
            assert second is True
        assert not is_setup_mutation_in_progress()


class TestContention:
    def test_concurrent_second_acquirer_gets_false(self):
        """While one thread holds the lock, another gets ``False``.

        Uses an event pair so the test is deterministic: the holder
        signals "I have the lock", the other thread attempts
        acquisition, then the holder is released.  No sleeps.
        """
        holder_has_lock = threading.Event()
        release_holder = threading.Event()
        second_result: List[bool] = []

        def holder():
            with setup_mutation_lock() as acquired:
                assert acquired is True
                holder_has_lock.set()
                # Block until the contender has had its chance.
                assert release_holder.wait(timeout=5)

        def contender():
            # Wait until the holder is definitely inside the with.
            assert holder_has_lock.wait(timeout=5)
            with setup_mutation_lock() as acquired:
                second_result.append(acquired)

        t_holder = threading.Thread(target=holder)
        t_contender = threading.Thread(target=contender)
        t_holder.start()
        t_contender.start()
        # Wait until the contender has finished attempting
        # acquisition, then release the holder.
        t_contender.join(timeout=5)
        release_holder.set()
        t_holder.join(timeout=5)

        assert not t_holder.is_alive()
        assert not t_contender.is_alive()
        # Contender must have seen False (non-blocking failure),
        # not queued behind the holder.
        assert second_result == [False]
        # Lock fully released after holder returns.
        assert not is_setup_mutation_in_progress()

    def test_same_thread_reentry_yields_false(self):
        """Re-entry from the same thread yields ``False``.

        ``threading.Lock`` (not ``RLock``) is non-reentrant.  The
        docstring declares this explicitly; this test pins it.
        """
        with setup_mutation_lock() as outer:
            assert outer is True
            with setup_mutation_lock() as inner:
                assert inner is False
            # Lock is still held by the outer frame -- re-entry
            # did NOT release it on exit.
            assert is_setup_mutation_in_progress() is True
        assert not is_setup_mutation_in_progress()


class TestReleaseOnException:
    def test_lock_released_when_block_raises(self):
        """An exception inside the ``with`` block still releases."""
        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with setup_mutation_lock() as acquired:
                assert acquired is True
                raise Boom('mutation handler raised')

        # Lock must be free for subsequent acquirers despite the
        # exception.
        assert not is_setup_mutation_in_progress()
        with setup_mutation_lock() as acquired:
            assert acquired is True

    def test_unacquired_exit_does_not_error(self):
        """If acquisition failed, the ``__exit__`` path must not
        attempt to release a lock it doesn't own.

        Reproduces the "acquired=False, then ``finally: release()``"
        bug the implementation explicitly guards against.  If the
        guard is removed, ``threading.Lock.release()`` raises
        ``RuntimeError`` and this test fails.
        """
        # Hold the lock from a helper thread so the test thread's
        # acquisition attempt cleanly fails.
        started = threading.Event()
        finish = threading.Event()

        def holder():
            with setup_mutation_lock() as acquired:
                assert acquired is True
                started.set()
                assert finish.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert started.wait(timeout=5)
            # Now the test thread attempts acquisition and lets
            # the context manager exit normally with acquired=False.
            with setup_mutation_lock() as acquired:
                assert acquired is False
            # If we got here without RuntimeError, the guard works.
        finally:
            finish.set()
            t.join(timeout=5)
            assert not t.is_alive()

        assert not is_setup_mutation_in_progress()


class TestIsSetupMutationInProgress:
    def test_reports_true_while_held(self):
        assert is_setup_mutation_in_progress() is False
        with setup_mutation_lock() as acquired:
            assert acquired is True
            assert is_setup_mutation_in_progress() is True
        assert is_setup_mutation_in_progress() is False

    def test_reports_false_when_free(self):
        assert is_setup_mutation_in_progress() is False
        # Double-check across back-to-back acquires.
        with setup_mutation_lock():
            pass
        assert is_setup_mutation_in_progress() is False


class TestStressConcurrency:
    def test_n_threads_exactly_one_acquires(self):
        """N threads race for the lock at the same barrier point;
        exactly one gets ``True``, the rest get ``False``.  No
        deadlock or corruption.

        Mirrors the Phase-3 concurrency test pattern
        (``test_concurrent_reads_during_mark_setup_complete``).
        The holder holds long enough that every other contender
        returns before the holder releases -- guaranteeing the
        "exactly one True" invariant.  Without this hold the race
        is still safe, but the test would occasionally allow
        multiple sequential True results and the "exactly one"
        check would be wrong for the wrong reason.
        """
        n = 16
        barrier = threading.Barrier(n)
        release_winner = threading.Event()
        results: List[bool] = []
        winners: List[int] = []
        # Condition variable guards ``results``/``winners`` and
        # signals the coordinator when n-1 losers have recorded.
        results_cond = threading.Condition()

        def worker(tid: int) -> None:
            barrier.wait()
            with setup_mutation_lock() as acquired:
                with results_cond:
                    results.append(acquired)
                    if acquired:
                        winners.append(tid)
                    results_cond.notify_all()
                if acquired:
                    # Hold long enough that every loser has had
                    # its chance to attempt acquisition and
                    # record False.  Using an event (not sleep)
                    # keeps the test hermetic.
                    assert release_winner.wait(timeout=10)

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            # Wait until n-1 losers have recorded False, then
            # release the winner.  Condition-variable wait has
            # none of the GIL-contention hazards of a busy-spin
            # loop against 16 active workers.
            with results_cond:
                losers_done = results_cond.wait_for(
                    lambda: sum(
                        1 for r in results if r is False
                    ) == n - 1,
                    timeout=10,
                )
            release_winner.set()
            for f in as_completed(futures, timeout=10):
                f.result()

        assert losers_done, (
            'timed out waiting for n-1 losers to record False'
        )
        assert len(results) == n
        true_count = sum(1 for r in results if r is True)
        false_count = sum(1 for r in results if r is False)
        assert true_count == 1, (
            f'expected exactly one winner, got {true_count}'
        )
        assert false_count == n - 1
        assert len(winners) == 1
        # Lock fully released after all workers finish.
        assert not is_setup_mutation_in_progress()

    def test_repeated_acquire_release_cycles_no_leak(self):
        """Many sequential acquire/release cycles don't leak or
        wedge the lock.  Cheap sanity check that the context
        manager's ``finally`` path runs every iteration.
        """
        for _ in range(200):
            with setup_mutation_lock() as acquired:
                assert acquired is True
        assert not is_setup_mutation_in_progress()
