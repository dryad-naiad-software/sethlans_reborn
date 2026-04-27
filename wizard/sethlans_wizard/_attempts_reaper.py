# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Background reaper for the wizard auth-state ``_attempts`` dict.

Issue #147 — the rate-limiter dict in :mod:`auth_state` is keyed by
client IP. Pruning of empty buckets only happens for IPs that re-query;
a scanner that hits the auth endpoint once per IP and never returns
leaves stale entries until the wizard process exits.

This module owns a daemon thread that periodically walks
``auth_state._attempts`` and drops any IP bucket whose newest timestamp
is older than the rate-limit window. It is started lazily by
``auth_state.record_attempt`` (so import has zero side-effects, which
keeps tests trivial to isolate) and stopped by
``auth_state.reset_state_for_tests`` between tests.

The thread is a ``daemon=True`` so it never blocks process shutdown
and the FR-W17 polite-shutdown machinery in
:mod:`shutdown` keeps working unchanged. It honours a
``threading.Event`` exit signal so tests can drain it cleanly without
waiting on a 30-second sleep.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Reaper cadence. Comfortably shorter than the auth-state rate-limit
# window so a scanned-then-abandoned IP is reclaimed within roughly two
# windows of its last attempt at worst, keeping the dict bounded under
# an adversarial unique-IP sweep.
REAPER_INTERVAL_SECS: float = 30.0


class AttemptsReaper:
    """Lifecycle wrapper around the reaper daemon thread.

    A single instance is owned by :mod:`auth_state`. Methods are
    safe to call from any thread; internal state is guarded by
    :attr:`_lock`.
    """

    def __init__(
        self,
        reap_callable: Callable[[float], int],
        *,
        interval_secs: float = REAPER_INTERVAL_SECS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a reaper that calls *reap_callable* every *interval_secs*.

        Args:
            reap_callable: One-shot prune. Receives ``clock()`` as its
                only argument and returns the number of buckets it
                dropped (return value is not used by the reaper itself,
                only by the tests).
            interval_secs: Sleep between iterations. Production passes
                the module default; tests pass small values (e.g.
                ``0.05``) so the reaper ticks promptly.
            clock: Time source. Production uses :func:`time.monotonic`;
                tests inject a mock to deterministically advance the
                rate-limit window without sleeping.
        """
        self._reap_callable = reap_callable
        self._interval_secs = float(interval_secs)
        self._clock = clock
        self._exit: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def ensure_started(self) -> None:
        """Start the daemon thread once. Idempotent."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._exit.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="wizard-auth-state-reaper",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, join_timeout: float = 1.0) -> None:
        """Signal the reaper to exit and wait briefly for it.

        Used by :func:`auth_state.reset_state_for_tests` between tests.
        Production code does not call this — the daemon thread dies
        with the process.
        """
        with self._lock:
            thread = self._thread
            self._exit.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
        with self._lock:
            self._thread = None
            self._exit.clear()

    def is_running(self) -> bool:
        """Return True if the daemon thread is currently alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def tick_for_tests(self) -> int:
        """Manually drive a single reap iteration (test-only).

        Bypasses the sleep so tests don't have to wait on a real
        interval. Returns the number of buckets reaped, propagating
        the underlying callable's return value.
        """
        return int(self._reap_callable(self._clock()))

    def _loop(self) -> None:
        """Background loop body. Honours the exit event on every tick."""
        try:
            while not self._exit.wait(self._interval_secs):
                try:
                    self._reap_callable(self._clock())
                except Exception:  # noqa: BLE001 — daemon must not crash
                    logger.exception(
                        "auth-state reaper iteration crashed; continuing",
                    )
        finally:
            logger.debug("auth-state reaper exiting")


__all__ = ["AttemptsReaper", "REAPER_INTERVAL_SECS"]
