# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Quit Sethlans cascade sequencing (tray spec FR-21).

Order on ``target="all"``:

1. Worker SIGTERM + wait up to 30s.
2. Manager SIGTERM + wait 3s.
3. Tray SIGTERM + wait 2s.
4. Any survivors escalated to SIGKILL.
5. Launcher returns.

A second quit click arriving during cascade escalates immediately to
SIGKILL-all.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

WORKER_GRACE_SECONDS = 30.0
MANAGER_GRACE_SECONDS = 3.0
TRAY_GRACE_SECONDS = 2.0
# FR-L10 — wizard polite-shutdown grace for SIGINT during the wizard
# hand-off window. Kept short because the wizard owns no rendering
# state and the launcher's idle-timeout logic also calls into here.
WIZARD_GRACE_SECONDS = 5.0

# Polling cadence inside _terminate_with_grace.  Small enough that a
# second quit click is honored within ~1s, short enough that we do
# not burn CPU.
_POLL_INTERVAL_SECONDS = 1.0


class _SecondQuitRequested(Exception):
    """Raised internally to abort the cascade when a 2nd quit arrives."""


def _wait_within_grace(
    proc: subprocess.Popen, grace: float,
    second_quit_check: Optional[Callable[[], bool]],
) -> bool:
    """Poll ``proc.wait`` until grace expires or second quit lands.

    Returns True if the process exited within grace, False otherwise.
    Raises ``_SecondQuitRequested`` if ``second_quit_check`` reports a
    second quit marker during the wait.
    """
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        step = min(_POLL_INTERVAL_SECONDS, max(0.0, remaining))
        try:
            proc.wait(timeout=step)
            return True
        except subprocess.TimeoutExpired:
            pass
        if second_quit_check is not None and second_quit_check():
            raise _SecondQuitRequested()
    return False


def _force_kill(proc: subprocess.Popen, label: str) -> None:
    try:
        proc.kill()
    except OSError as exc:
        logger.warning("kill() on %s failed: %s", label, exc)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        logger.error("%s failed to die after SIGKILL", label)


def _terminate_with_grace(
    proc: Optional[subprocess.Popen], label: str, grace: float,
    second_quit_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Send SIGTERM then poll until exit or grace expiry.

    FR-21: while waiting, periodically invoke ``second_quit_check``.
    If it returns True, raise ``_SecondQuitRequested`` so the caller
    can escalate to ``emergency_kill_all`` immediately instead of
    finishing the graceful sequence.
    """
    if proc is None or proc.poll() is not None:
        return
    logger.info("Sending SIGTERM to %s (pid=%s)", label, proc.pid)
    try:
        proc.terminate()
    except OSError as exc:
        logger.warning("terminate() on %s failed: %s", label, exc)
    if _wait_within_grace(proc, grace, second_quit_check):
        return
    logger.warning(
        "%s did not exit within %.1fs; escalating to SIGKILL",
        label, grace,
    )
    _force_kill(proc, label)


def _hard_kill(procs: Iterable[Optional[subprocess.Popen]]) -> None:
    for proc in procs:
        if proc is None or proc.poll() is not None:
            continue
        try:
            proc.kill()
        except OSError as exc:
            logger.warning("kill() failed on pid=%s: %s",
                           proc.pid, exc)


def cascade_quit(
    worker: Optional[subprocess.Popen],
    manager: Optional[subprocess.Popen],
    tray: Optional[subprocess.Popen],
    second_quit_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Run the FR-21 cascade in order.

    If ``second_quit_check`` is provided and returns True mid-cascade,
    the remaining survivors are SIGKILLed immediately.
    """
    try:
        _terminate_with_grace(
            worker, "worker", WORKER_GRACE_SECONDS, second_quit_check,
        )
        _terminate_with_grace(
            manager, "manager", MANAGER_GRACE_SECONDS, second_quit_check,
        )
        _terminate_with_grace(
            tray, "tray", TRAY_GRACE_SECONDS, second_quit_check,
        )
    except _SecondQuitRequested:
        logger.warning(
            "Second quit received during cascade; SIGKILLing survivors",
        )
        _hard_kill([worker, manager, tray])


def quit_manager_only(manager: Optional[subprocess.Popen]) -> None:
    """Handle ``target="manager"`` Quit Manager marker."""
    try:
        _terminate_with_grace(
            manager, "manager", MANAGER_GRACE_SECONDS,
        )
    except _SecondQuitRequested:  # pragma: no cover - no check supplied
        _hard_kill([manager])


def emergency_kill_all(
    worker: Optional[subprocess.Popen],
    manager: Optional[subprocess.Popen],
    tray: Optional[subprocess.Popen],
) -> None:
    """Second-click escalation: SIGKILL everything immediately."""
    logger.warning(
        "Emergency kill: second Quit received during cascade",
    )
    _hard_kill([worker, manager, tray])
