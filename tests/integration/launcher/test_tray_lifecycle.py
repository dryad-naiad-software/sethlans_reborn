# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration test for the launcher -> tray helper lifecycle (FR-19c).

Spawns the tray helper subprocess (``shared/run_tray.py``) with
``SETHLANS_LAUNCHER_PID`` pointing at a real parent stub process,
then kills the parent and asserts the tray detects the orphaned
state via :mod:`shared.tray.launcher_watch` (psutil parent-pid +
``create_time`` snapshot) and self-exits within the documented
window.

This is the FIRST end-to-end exercise of the parent-pid watchdog.
The polling cadence is 2 s (``_POLL_INTERVAL_SECONDS`` in
``shared/tray/poller.py``); we allow up to 15 s of wall-clock for
the tray to observe the dead parent and exit cleanly.

Mocking is forbidden here on purpose -- the whole point of the test
is to verify the cross-process behavior between two real OS
processes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# psutil is the actual mechanism the tray uses to check parent
# liveness; if it is missing the launcher_watch becomes a no-op
# (returns True forever) and this test would deadlock.
psutil = pytest.importorskip("psutil")

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_TRAY = REPO_ROOT / "shared" / "run_tray.py"

# Wall-clock budget for the tray to notice the parent is gone and
# exit.  Two poll intervals (4 s) plus generous slack for process
# start-up + Qt teardown.
TRAY_EXIT_TIMEOUT = 15.0


def _spawn_parent_stub() -> subprocess.Popen:
    """Spawn an inert parent process the tray will adopt as 'launcher'.

    Just sleeps long enough that the tray has time to observe it as
    alive on its first tick.  The test kills it explicitly to
    trigger the orphan branch.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
    )


def _spawn_tray(parent_pid: int) -> subprocess.Popen:
    """Spawn the tray helper with the given pid as its 'launcher'.

    Inherits PYTHONPATH including the repo root so the subprocess
    can import ``shared.tray`` when running from source (frozen
    bundles get their import path baked in by PyInstaller; the
    bare ``python shared/run_tray.py`` invocation does not).
    """
    env = os.environ.copy()
    env["SETHLANS_LAUNCHER_PID"] = str(parent_pid)
    # Force an offscreen Qt platform so no display is required.
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Mirror pytest.ini's ``pythonpath`` block so the subprocess
    # can resolve ``shared.tray`` and ``manager.workers`` imports.
    extra = os.pathsep.join([
        str(REPO_ROOT),
        str(REPO_ROOT / "manager"),
        str(REPO_ROOT / "worker"),
    ])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{extra}{os.pathsep}{existing}" if existing else extra
    )
    # Point the poller at a closed loopback port so HTTP fetches
    # fail fast -- the launcher-watch check still runs at the top
    # of every tick regardless of HTTP outcome.
    return subprocess.Popen(
        [sys.executable, str(RUN_TRAY)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _kill(proc: subprocess.Popen) -> None:
    """Best-effort terminate then kill a subprocess; never raises.

    Timeouts are generous (5 s after ``terminate``, 10 s after the
    fallback ``kill``) because a heavily loaded CI runner can take
    several seconds to finish Qt teardown on ``SIGTERM``.  The final
    unconditional ``wait`` ensures the OS has reaped the process
    before the test returns so nothing leaks into a sibling test.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def _wait_for_exit(
    proc: subprocess.Popen, timeout: float,
) -> int | None:
    """Poll ``proc`` for exit; return returncode or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            return rc
        time.sleep(0.25)
    return None


@pytest.mark.timeout(60)
class TestTrayLifecycleAgainstParent:
    """End-to-end parent-launcher -> tray child lifecycle."""

    def test_tray_stays_alive_while_parent_is_alive(self):
        """The tray child must NOT exit on its own while the launcher
        process is still alive.  We give the tray ~6 s (3 poll
        intervals) to disprove the contract before passing.
        """
        parent = _spawn_parent_stub()
        tray = None
        try:
            tray = _spawn_tray(parent.pid)
            # Give the tray enough time to start Qt + the poller and
            # run several ticks.  Two intervals = 4 s; we wait ~6 s
            # for safety.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if tray.poll() is not None:
                    pytest.fail(
                        "Tray exited while parent was still alive "
                        f"(rc={tray.returncode})",
                    )
                time.sleep(0.5)
            # Final assertion: still alive.
            assert tray.poll() is None
        finally:
            _kill(tray)
            _kill(parent)

    def test_tray_exits_when_parent_dies(self):
        """The tray child must self-exit within the documented window
        once its launcher parent disappears (FR-19c).
        """
        parent = _spawn_parent_stub()
        tray = None
        try:
            tray = _spawn_tray(parent.pid)
            # Give the tray a generous observation window to see the
            # parent as alive at least once and snapshot create_time
            # for PID-reuse defense.  Poll rather than `sleep(3)` so
            # a congested CI runner that takes >3 s to finish Qt
            # bootstrap + first poll doesn't flake.  If the tray
            # exits during the window, that's a real bug (tray
            # self-terminated despite a live parent).
            observe_deadline = time.monotonic() + 8.0
            while time.monotonic() < observe_deadline:
                if tray.poll() is not None:
                    pytest.fail(
                        "Tray died before parent could be killed "
                        f"(rc={tray.returncode}); cannot exercise "
                        "the orphan branch.",
                    )
                time.sleep(0.5)
            # Kill the parent.  The tray should observe this on its
            # next tick and self-exit.
            _kill(parent)
            rc = _wait_for_exit(tray, timeout=TRAY_EXIT_TIMEOUT)
            assert rc is not None, (
                "Tray did not exit within "
                f"{TRAY_EXIT_TIMEOUT}s of parent death."
            )
            # Acceptable exit codes: 0 (clean SIGTERM-style shutdown
            # via app.quit()) or any small non-negative code.  What we
            # rule out is a crash dump (very large abs value, e.g.
            # negative signal codes on POSIX).  On Windows abs > 1000
            # is typically a structured-exception code.
            assert -1 <= rc <= 1000, (
                f"Tray exited with suspicious code rc={rc}"
            )
        finally:
            _kill(tray)
            _kill(parent)
