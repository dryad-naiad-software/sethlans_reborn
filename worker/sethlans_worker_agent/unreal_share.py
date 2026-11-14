# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's own Sethlans share (Unreal foundation FR-WC-7, D3b/Q3).

The root comes **only** from this worker's config
(``config_unreal.SHARE`` / ``SETHLANS_UNREAL_SHARE``); nothing the
manager sends can set or change it, and it is fixed for the process
lifetime (changing it needs a worker restart).

State is one frozen ``ShareState`` replaced under the leaf lock of the
``ShareMonitor``. ``state()`` is the single read for ``snapshot()``,
``poll_params()`` and ``try_reserve``, so a report can never pair one
root with another's reachability. A positive result older than twice the
current recheck interval reads as unreachable (a check that never
finishes cannot keep reporting a stale positive).

Reachability runs on its own daemon thread ``unreal-share-check``:
``os.stat(root)`` in a helper thread ``unreal-share-stat`` with a
``SHARE_TIMEOUT_SECONDS`` limit; reachable means it returned in time and
is a directory. At most one check is in flight: a due check is skipped
while a previous ``stat`` is still blocked. After a failure the interval
doubles up to ``SHARE_RECHECK_MAX_SECONDS``; a success resets it.
``mark_unreachable`` forces ``reachable=False`` at once; a result is
applied only if its check started after the latest ``mark_unreachable``.

NF-6: with Unreal disabled nothing here is configured or started.
"""
import logging
import os
import stat
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional, Tuple

from shared.unreal.unc import UncPath, is_single_label_host, validate_unc_dir

logger = logging.getLogger(__name__)

SINGLE_LABEL_WARNING = (
    "Use a DNS-resolvable fully qualified host name or an address; "
    "single-label names may be resolved by broadcast."
)
_IDLE_WAIT_CAP_SECONDS = 60.0
_BLOCKED_RECHECK_SECONDS = 1.0


@dataclass(frozen=True)
class ShareState:
    root: Optional[UncPath]
    reachable: bool
    checked_at: Optional[float]  # time.monotonic() of the last applied check
    last_error: str


NOT_CONFIGURED = ShareState(root=None, reachable=False, checked_at=None, last_error='not configured')


class ShareMonitor:
    """Reachability of one fixed share root."""

    def __init__(self, root: UncPath, base_seconds: float, max_seconds: float,
                 timeout_seconds: float, clock: Callable[[], float] = time.monotonic,
                 stat_fn: Callable = os.stat):
        self._root = root
        self._base = base_seconds
        self._max = max(max_seconds, base_seconds)
        self._timeout = timeout_seconds
        self._clock = clock
        self._stat = stat_fn
        self._lock = threading.Lock()  # leaf lock
        self._state = ShareState(root, False, None, 'not checked yet')
        self._interval = base_seconds
        self._next_due = clock()
        self._mark_seq = 0
        self._in_flight = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def interval(self) -> float:
        with self._lock:
            return self._interval

    def state(self) -> ShareState:
        with self._lock:
            current, interval = self._state, self._interval
        if current.reachable and (current.checked_at is None
                                  or self._clock() - current.checked_at > 2 * interval):
            return replace(current, reachable=False, last_error='last check result is stale')
        return current

    def begin_check(self) -> Optional[int]:
        """Start a due check; returns its token, or None (not due / one in flight)."""
        with self._lock:
            if self._in_flight or self._clock() < self._next_due:
                return None
            self._in_flight = True
            return self._mark_seq

    def finish_check(self, token: int, ok: bool, error: str) -> bool:
        """Apply a check result; False when it was discarded (started before a mark)."""
        with self._lock:
            if token != self._mark_seq:
                return False
            now = self._clock()
            self._interval = self._base if ok else min(self._interval * 2, self._max)
            self._next_due = now + self._interval
            before = self._state
            self._state = ShareState(self._root, ok, now, '' if ok else error)
        self._log_transition(before, ok, error)
        return True

    def stat_returned(self) -> None:
        """The ``stat`` call of the in-flight check has returned (or never started)."""
        with self._lock:
            self._in_flight = False

    def mark_unreachable(self, reason: str) -> None:
        """Force unreachable now; recheck at the base interval (Conc R2-M3)."""
        with self._lock:
            self._mark_seq += 1
            self._interval = self._base
            self._next_due = self._clock() + self._base
            before = self._state
            self._state = ShareState(self._root, False, before.checked_at, reason)
        self._wake.set()
        self._log_transition(before, False, reason)

    def _log_transition(self, before: ShareState, ok: bool, error: str) -> None:
        if ok and not before.reachable:
            logger.info("Sethlans share %s reachable.", self._root)
        elif not ok and (before.reachable or before.checked_at is None):
            logger.info("Sethlans share %s unreachable: %s.", self._root, error)

    def check_now(self) -> Tuple[bool, str]:
        """``stat`` the root in a helper thread, waiting at most the timeout."""
        done = threading.Event()
        box = {}

        def work():
            try:
                st = self._stat(self._root.canonical)
                box['result'] = (True, '') if stat.S_ISDIR(st.st_mode) else (False, 'not a directory')
            except Exception as exc:  # noqa: BLE001 - helper thread must not raise
                box['result'] = (False, f'{type(exc).__name__}: {exc}')
            finally:
                done.set()
                self.stat_returned()

        try:
            threading.Thread(target=work, name='unreal-share-stat', daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            self.stat_returned()
            return False, f'could not start the check: {exc}'
        if done.wait(self._timeout):
            return box['result']
        return False, f'no answer within {self._timeout:g} s'

    def _seconds_until_due(self) -> float:
        with self._lock:
            delay = self._next_due - self._clock()
            blocked = self._in_flight
        if delay <= 0:
            return _BLOCKED_RECHECK_SECONDS if blocked else 0.0
        return min(delay, _IDLE_WAIT_CAP_SECONDS)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                token = self.begin_check()
                if token is not None:
                    ok, error = self.check_now()
                    self.finish_check(token, ok, error)
            except Exception:  # noqa: BLE001 - thread must survive (NF-5)
                logger.exception("Sethlans share check failed unexpectedly.")
            self._wake.clear()
            if self._stop.is_set():
                break
            self._wake.wait(self._seconds_until_due())

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='unreal-share-check', daemon=True)
            self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


_monitor: Optional[ShareMonitor] = None
_monitor_lock = threading.Lock()  # leaf lock; guards _monitor only


def configure(raw: str, base_seconds: float, max_seconds: float, timeout_seconds: float) -> None:
    """Validate and install the configured root (FR-WC-7). Starts nothing.

    ``''`` means not configured (no monitor). An invalid value raises
    ``ValueError`` with the rule message; the caller disables Unreal.
    """
    global _monitor
    monitor = None
    if raw:
        root = validate_unc_dir(raw)
        if is_single_label_host(root):
            logger.warning("Sethlans share %s: %s", root, SINGLE_LABEL_WARNING)
        monitor = ShareMonitor(root, base_seconds, max_seconds, timeout_seconds)
    with _monitor_lock:
        _monitor = monitor


def _current() -> Optional[ShareMonitor]:
    with _monitor_lock:
        return _monitor


def start() -> None:
    """Start the reachability thread, if a root is configured."""
    monitor = _current()
    if monitor is not None:
        monitor.start()


def state() -> ShareState:
    """The single read of root + reachability."""
    monitor = _current()
    return monitor.state() if monitor is not None else NOT_CONFIGURED


def mark_unreachable(reason: str) -> None:
    """Force unreachable (share failure during a job, FR-UE-17)."""
    monitor = _current()
    if monitor is not None:
        monitor.mark_unreachable(reason)


def reset_for_tests() -> None:
    """Stop the thread and forget the root (test isolation only)."""
    global _monitor
    with _monitor_lock:
        monitor, _monitor = _monitor, None
    if monitor is not None:
        monitor.stop(timeout=5)
