# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Polite-shutdown machinery for the wizard (FR-W17).

Three exit conditions evaluated continuously after ``.wizard_done``:
(a) browser-acked success armed via :func:`schedule_grace_timer`
(close()-hook in runtime_ready), (b) ``.wizard_reject`` marker
observed by the polling thread, (c) 5-minute monotonic failsafe in
the same thread. ``os._exit`` is used deliberately — atexit handlers
already-performed FR-W10 cleanup MUST NOT re-fire.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from wizard.sethlans_wizard import ipc

logger = logging.getLogger(__name__)


def _shutdown_server() -> None:
    """Lazy proxy for ``server.shutdown_server`` (avoids import cycle)."""
    from wizard.sethlans_wizard import server  # noqa: PLC0415 — cycle

    server.shutdown_server()


# FR-W17(a) — 3-second grace from WSGI close()-hook to server shutdown.
GRACE_TIMER_SECONDS = 3.0

# FR-W17(c) — 5-minute hard failsafe from .wizard_done write.
FAILSAFE_SECONDS = 300.0

# FR-W17(b) — .wizard_reject polling cadence.
REJECT_POLL_INTERVAL = 0.5

# Issue #208: the former FR-W17(c) ``.runtime_failed`` short-circuit was
# removed. Since #203 the launcher terminates the wizard during hand-off,
# before the runtime is spawned, and never writes that marker; runtime
# start-up failures surface through the launcher's splash
# (``on_startup_failed``) instead.

# Set True once .wizard_done has been written. The post-done threads
# wait on this event; they MUST NOT start polling before it is set, or
# they could race with the wizard's own startup (e.g., observing a
# stale .wizard_reject from a prior crashed run that the launcher
# sweeps after spawn).
done_written: threading.Event = threading.Event()

# Test-only — cleared in production, set by ``reset_state_for_tests``
# to ask the polling thread to exit between tests instead of
# accumulating zombie daemon threads. Production code MUST NOT set
# this event.
_test_exit_signal: threading.Event = threading.Event()

# Guards against double-arming the grace timer (the close() hook can
# fire more than once if a transport quirk re-iterates the response).
_grace_timer_lock = threading.Lock()
_grace_timer: Optional[threading.Timer] = None
_grace_armed: bool = False

# Set once the polling thread has been started (idempotent guard so
# tests that call start_post_done_threads twice don't spawn duplicates).
_threads_started_lock = threading.Lock()
_threads_started: bool = False


def reset_state_for_tests() -> None:
    """Clear module state + signal in-flight pollers to exit. Test-only."""
    global _grace_timer, _grace_armed, _threads_started
    with _grace_timer_lock:
        if _grace_timer is not None:
            try:
                _grace_timer.cancel()
            except Exception:  # pragma: no cover — defensive
                pass
        _grace_timer = None
        _grace_armed = False
    # Wake any thread parked on done_written.wait() and tell it to
    # exit on its next loop iteration.
    _test_exit_signal.set()
    done_written.set()
    # Give the thread a brief moment to observe the exit signal and
    # return from _run.
    time.sleep(0.05)
    # Now reset the signals back to their production-default state so
    # the next test starts clean.
    _test_exit_signal.clear()
    done_written.clear()
    with _threads_started_lock:
        _threads_started = False


def mark_done_written() -> None:
    """Signal that ``.wizard_done`` has been written (FR-W17 trigger).

    Idempotent. Safe to call from the done handler before
    :func:`start_post_done_threads` is invoked — the threads will
    observe ``done_written`` is set and start polling immediately.
    """
    done_written.set()


def schedule_grace_timer(
    on_fire: Optional[Callable[[], None]] = None,
) -> bool:
    """Arm the 3-second FR-W17(a) grace timer. Returns True iff THIS
    call armed it (idempotent; first-time only per FR-W14)."""
    global _grace_timer, _grace_armed
    callback = on_fire if on_fire is not None else _default_grace_action
    with _grace_timer_lock:
        if _grace_armed:
            return False
        _grace_armed = True
        _grace_timer = threading.Timer(GRACE_TIMER_SECONDS, callback)
        _grace_timer.daemon = True
        _grace_timer.start()
    logger.info(
        "FR-W17(a) grace timer started (%.1fs to server shutdown)",
        GRACE_TIMER_SECONDS,
    )
    return True


def _default_grace_action() -> None:
    """Default grace-timer callback: stop waitress, exit 0."""
    try:
        logger.info("FR-W17(a) grace elapsed; closing wizard server")
        _shutdown_server()
    finally:
        request_exit(0)


def request_exit(code: int) -> None:
    """End the wizard process with *code* via ``os._exit`` (FR-W17)."""
    logger.info("FR-W17 requesting wizard process exit with code=%d", code)
    os._exit(code)


def start_post_done_threads(
    data_dir: Path,
    ipc_secret: bytes,
    *,
    failsafe_seconds: float = FAILSAFE_SECONDS,
    poll_interval: float = REJECT_POLL_INTERVAL,
    on_reject: Optional[Callable[[dict], None]] = None,
    on_failsafe: Optional[Callable[[], None]] = None,
) -> threading.Thread:
    """Spawn the FR-W17 (b)/(c) polling thread. Idempotent.

    The thread blocks on :data:`done_written`, then polls
    ``.wizard_reject`` (calls *on_reject*) and tracks a monotonic
    failsafe deadline (calls *on_failsafe*).
    """
    global _threads_started
    with _threads_started_lock:
        if _threads_started:
            # Already running — return a no-op handle. Tests that need
            # the live thread must call reset_state_for_tests() first.
            placeholder = threading.Thread(target=lambda: None)
            placeholder.daemon = True
            return placeholder
        _threads_started = True

    reject_action = on_reject if on_reject is not None else _default_reject_action
    failsafe_action = (
        on_failsafe if on_failsafe is not None else _default_failsafe_action
    )

    secret = bytes(ipc_secret)
    canonical_dir = Path(data_dir)

    def _run() -> None:
        try:
            _poll_loop(
                canonical_dir, secret, failsafe_seconds, poll_interval,
                reject_action, failsafe_action,
            )
        except Exception:  # noqa: BLE001 — daemon must not crash unhandled
            logger.exception("FR-W17 post-done poller crashed; exiting")

    thread = threading.Thread(
        target=_run, name="wizard-post-done-poller", daemon=True,
    )
    thread.start()
    return thread


def _poll_loop(
    canonical_dir: Path,
    secret: bytes,
    failsafe_seconds: float,
    poll_interval: float,
    reject_action: Callable[[dict], None],
    failsafe_action: Callable[[], None],
) -> None:
    """The polling loop body, factored out so :func:`_run` stays small.

    Honours :data:`_test_exit_signal` so the test fixture can drain
    in-flight pollers between tests rather than leaking daemon threads.
    """
    done_written.wait()
    if _test_exit_signal.is_set():
        return
    deadline_monotonic = time.monotonic() + failsafe_seconds
    logger.info(
        "FR-W17 post-done poller started; failsafe in %.0fs",
        failsafe_seconds,
    )
    reject_path = canonical_dir / "wizard" / ipc.MARKER_WIZARD_REJECT
    while True:
        if _test_exit_signal.is_set():
            return
        payload = ipc.read_marker(
            reject_path, secret, "wizard_reject", canonical_dir,
        )
        if payload is not None:
            logger.info("FR-W17(b) .wizard_reject observed; exiting")
            reject_action(payload)
            return
        if time.monotonic() >= deadline_monotonic:
            logger.info("FR-W17(c) failsafe deadline reached; exiting")
            failsafe_action()
            return
        time.sleep(poll_interval)


def _default_reject_action(_payload: dict) -> None:
    """Default reject-marker callback: stop waitress, exit non-zero."""
    try:
        _shutdown_server()
    finally:
        request_exit(3)


def _default_failsafe_action() -> None:
    """Default failsafe-trip callback: stop waitress, exit 0."""
    try:
        _shutdown_server()
    finally:
        request_exit(0)


__all__ = [
    "FAILSAFE_SECONDS",
    "GRACE_TIMER_SECONDS",
    "REJECT_POLL_INTERVAL",
    "done_written",
    "mark_done_written",
    "request_exit",
    "reset_state_for_tests",
    "schedule_grace_timer",
    "start_post_done_threads",
]
