# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 unit tests for the thread-local skip-guard in
``workers/signal_helpers.py``.

The guard replaces the pre-Phase-4 ``post_save.disconnect`` / ``connect``
dance.  Under threaded Waitress (Phase 5+), the old pattern suppressed
signals process-wide for the duration of one thread's save loop — these
tests cover the TLS-based replacement:

* ``depth`` starts at 0 on a fresh thread.
* ``with _skip_thumbnail_signals():`` increments on enter, decrements
  on exit.
* ``_skip_thumbnails()`` returns ``True`` while depth > 0.
* Nested ``with`` blocks stack (reentrancy).
* An exception inside the block still restores depth (``finally``).
* One thread's guard does NOT affect another thread's view of the flag.
"""

import threading

import pytest

from workers import signal_helpers
from workers.signal_helpers import (
    _skip_thumbnail_signals,
    _skip_thumbnails,
)


@pytest.fixture(autouse=True)
def reset_tls_depth():
    """Each test sees a fresh TLS depth on the running thread."""
    # threading.local() is per-thread; we only need to reset the one
    # the test runs on.
    if hasattr(signal_helpers._tls, "depth"):
        signal_helpers._tls.depth = 0
    yield
    if hasattr(signal_helpers._tls, "depth"):
        signal_helpers._tls.depth = 0


class TestSkipDepthCounter:

    def test_initial_depth_is_zero(self):
        assert _skip_thumbnails() is False

    def test_enter_increments_exit_decrements(self):
        assert _skip_thumbnails() is False
        with _skip_thumbnail_signals():
            assert _skip_thumbnails() is True
        assert _skip_thumbnails() is False

    def test_nested_contexts_stack(self):
        """Reentrancy: inner exit must NOT prematurely re-enable."""
        with _skip_thumbnail_signals():
            assert signal_helpers._tls.depth == 1
            with _skip_thumbnail_signals():
                assert signal_helpers._tls.depth == 2
                assert _skip_thumbnails() is True
            # Outer context still active.
            assert signal_helpers._tls.depth == 1
            assert _skip_thumbnails() is True
        assert signal_helpers._tls.depth == 0
        assert _skip_thumbnails() is False

    def test_exception_inside_block_still_clears_depth(self):
        """``finally`` restores the counter even if the block raises.

        This is the exception-path regression guard: a handler that
        raises inside the save loop must not poison subsequent calls
        on the same thread.
        """
        assert _skip_thumbnails() is False
        with pytest.raises(RuntimeError):
            with _skip_thumbnail_signals():
                assert _skip_thumbnails() is True
                raise RuntimeError("inner save failed")
        # Depth restored — next call sees a clean slate.
        assert _skip_thumbnails() is False

    def test_exception_inside_nested_block_preserves_outer_depth(self):
        with _skip_thumbnail_signals():
            with pytest.raises(ValueError):
                with _skip_thumbnail_signals():
                    raise ValueError("inner failed")
            # Outer still active.
            assert _skip_thumbnails() is True
        assert _skip_thumbnails() is False

    def test_multiple_sequential_calls_each_clean(self):
        for _ in range(5):
            with _skip_thumbnail_signals():
                assert _skip_thumbnails() is True
            assert _skip_thumbnails() is False


class TestThreadIsolation:
    """Guard on thread A must NOT be visible from thread B."""

    def test_concurrent_threads_see_independent_depth(self):
        # Two barriers sequence the test:
        #   gate1: A enters its skip context, then both threads meet
        #          → B observes the flag on its own thread.
        #   gate2: B has observed and A's ``_skip_thumbnails()`` sample
        #          is taken while still inside the context.
        gate1 = threading.Barrier(2)
        gate2 = threading.Barrier(2)
        observations = {"a_inside": None, "b_outside": None}

        def thread_a():
            with _skip_thumbnail_signals():
                gate1.wait(timeout=5)   # signal B that A is inside
                observations["a_inside"] = _skip_thumbnails()
                gate2.wait(timeout=5)   # hold until B has sampled

        def thread_b():
            gate1.wait(timeout=5)       # A is inside its context
            observations["b_outside"] = _skip_thumbnails()
            gate2.wait(timeout=5)

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)

        assert observations["a_inside"] is True
        assert observations["b_outside"] is False


class TestUnderflowClamp:
    """Defensive clamp: an underflow must not poison the thread."""

    def test_manual_underflow_clamped_to_zero(self, caplog):
        # Simulate a bug where decrement runs without a matching
        # increment — the context manager's ``finally`` guards against
        # going negative.
        signal_helpers._tls.depth = 0
        # Directly force the underflow path via the context manager.
        # Prior depth 0 → enter brings to 1 → exit brings to 0.
        with _skip_thumbnail_signals():
            # Simulate external corruption: set depth to a value that,
            # after decrement in ``finally``, would go negative.
            signal_helpers._tls.depth = 0
        # After clamp:
        assert signal_helpers._tls.depth == 0
        assert _skip_thumbnails() is False
