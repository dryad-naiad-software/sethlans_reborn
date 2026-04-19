# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/cascade.py`` (tray spec FR-21)."""

from __future__ import annotations

import subprocess

from launcher import cascade
from launcher.cascade import (
    cascade_quit,
    emergency_kill_all,
    quit_manager_only,
)


def _mock_proc(mocker, alive=True, raise_on_wait=False, wait_side=None):
    proc = mocker.MagicMock(spec=subprocess.Popen)
    # poll() returns None while alive, exit code when dead.
    proc.poll.return_value = None if alive else 0
    if wait_side is not None:
        proc.wait.side_effect = wait_side
    elif raise_on_wait:
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
    else:
        proc.wait.return_value = 0
    proc.pid = 100
    return proc


# ------------------------------------------------------------------
# cascade_quit (target="all")
# ------------------------------------------------------------------

class TestCascadeQuitAll:

    def test_terminates_in_order_worker_manager_tray(self, mocker):
        calls = []

        def _term_track(name):
            def _side():
                calls.append(name)
            return _side

        worker = _mock_proc(mocker)
        manager = _mock_proc(mocker)
        tray = _mock_proc(mocker)
        worker.terminate.side_effect = _term_track("worker")
        manager.terminate.side_effect = _term_track("manager")
        tray.terminate.side_effect = _term_track("tray")

        cascade_quit(worker, manager, tray)

        assert calls == ["worker", "manager", "tray"]

    def test_dead_subprocess_is_skipped(self, mocker):
        worker = _mock_proc(mocker, alive=False)
        cascade._terminate_with_grace(worker, "worker", 1.0)
        worker.terminate.assert_not_called()

    def test_none_subprocess_is_skipped(self):
        # Should not raise.
        cascade._terminate_with_grace(None, "nobody", 1.0)

    def test_timeout_escalates_to_sigkill(self, mocker):
        # Every grace-period wait times out; the post-kill wait
        # returns.  (The grace loop may iterate multiple times given
        # the small grace value and coarse monotonic clock, so use a
        # TimeoutExpired-returning function rather than a fixed list.)
        proc = mocker.MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.pid = 1
        calls = {"n": 0}

        def _wait(timeout=None):
            calls["n"] += 1
            # After kill(), return 0 immediately.  The 5s post-kill
            # wait uses timeout=5.0; everything in-grace uses <= 1s.
            if proc.kill.called:
                return 0
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

        proc.wait.side_effect = _wait
        cascade._terminate_with_grace(proc, "manager", 0.01)
        proc.kill.assert_called_once()


# ------------------------------------------------------------------
# quit_manager_only (target="manager")
# ------------------------------------------------------------------

class TestQuitManagerOnly:

    def test_only_manager_affected(self, mocker):
        worker = _mock_proc(mocker)
        manager = _mock_proc(mocker)
        tray = _mock_proc(mocker)
        quit_manager_only(manager)
        manager.terminate.assert_called_once()
        worker.terminate.assert_not_called()
        tray.terminate.assert_not_called()

    def test_none_is_safe(self):
        quit_manager_only(None)  # must not raise


# ------------------------------------------------------------------
# emergency_kill_all (second-click escalation)
# ------------------------------------------------------------------

class TestEmergencyKillAll:

    def test_sigkills_all_alive_procs(self, mocker):
        worker = _mock_proc(mocker)
        manager = _mock_proc(mocker)
        tray = _mock_proc(mocker)
        emergency_kill_all(worker, manager, tray)
        worker.kill.assert_called_once()
        manager.kill.assert_called_once()
        tray.kill.assert_called_once()

    def test_dead_procs_skipped(self, mocker):
        worker = _mock_proc(mocker, alive=False)
        manager = _mock_proc(mocker)
        emergency_kill_all(worker, manager, None)
        worker.kill.assert_not_called()
        manager.kill.assert_called_once()

    def test_kill_oserror_does_not_raise(self, mocker):
        proc = _mock_proc(mocker)
        proc.kill.side_effect = OSError("boom")
        # Must not propagate.
        emergency_kill_all(proc, None, None)


# ------------------------------------------------------------------
# Grace constants match spec
# ------------------------------------------------------------------

class TestSpecConstants:

    def test_worker_grace_30s(self):
        assert cascade.WORKER_GRACE_SECONDS == 30.0

    def test_manager_grace_3s(self):
        assert cascade.MANAGER_GRACE_SECONDS == 3.0

    def test_tray_grace_2s(self):
        assert cascade.TRAY_GRACE_SECONDS == 2.0


# ------------------------------------------------------------------
# Second-quit-during-cascade (Fix C / issue #79)
# ------------------------------------------------------------------

class TestSecondQuitDuringCascade:

    def test_no_second_quit_normal_cascade_exits_cleanly(self, mocker):
        # Process exits immediately on wait -> no SIGKILL.
        worker = _mock_proc(mocker)
        manager = _mock_proc(mocker)
        tray = _mock_proc(mocker)
        check = mocker.MagicMock(return_value=False)
        cascade.cascade_quit(
            worker, manager, tray, second_quit_check=check,
        )
        worker.terminate.assert_called_once()
        manager.terminate.assert_called_once()
        tray.terminate.assert_called_once()
        worker.kill.assert_not_called()
        manager.kill.assert_not_called()
        tray.kill.assert_not_called()

    def test_grace_expiry_still_sigkills(self, mocker):
        # No second quit; wait always times out -> normal SIGKILL
        # escalation path (pre-Fix-C behavior).
        proc = mocker.MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.pid = 7

        def _wait(timeout=None):
            if proc.kill.called:
                return 0
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

        proc.wait.side_effect = _wait
        cascade._terminate_with_grace(
            proc, "x", 0.05,
            second_quit_check=lambda: False,
        )
        proc.kill.assert_called_once()

    def test_second_quit_mid_grace_triggers_emergency_kill(self, mocker):
        # Worker hangs during grace; second_quit_check returns True
        # on the first poll -> cascade_quit escalates to SIGKILL-all
        # without waiting out manager/tray grace.
        worker = _mock_proc(mocker, raise_on_wait=True)
        manager = _mock_proc(mocker)
        tray = _mock_proc(mocker)

        calls = {"n": 0}

        def _check():
            calls["n"] += 1
            return True  # second quit present on first poll

        cascade.cascade_quit(
            worker, manager, tray, second_quit_check=_check,
        )
        # Emergency path kills everyone alive.
        worker.kill.assert_called_once()
        manager.kill.assert_called_once()
        tray.kill.assert_called_once()
        # Manager/tray should NOT have been SIGTERMed because cascade
        # aborted before reaching them.
        manager.terminate.assert_not_called()
        tray.terminate.assert_not_called()

    def test_second_quit_after_grace_expiry_kills_survivors(self, mocker):
        # Worker hangs past grace, then dies under SIGKILL.  No
        # second_quit marker appears during the worker grace (check
        # returns False).  Once we transition to the manager grace,
        # the second-quit marker arrives, so emergency_kill escalates
        # survivors immediately.
        worker = mocker.MagicMock(spec=subprocess.Popen)
        worker.poll.return_value = None
        worker.pid = 1

        def _worker_wait(timeout=None):
            if worker.kill.called:
                return 0
            raise subprocess.TimeoutExpired(cmd="w", timeout=timeout)

        worker.wait.side_effect = _worker_wait

        manager = mocker.MagicMock(spec=subprocess.Popen)
        manager.poll.return_value = None
        manager.pid = 2

        def _manager_wait(timeout=None):
            raise subprocess.TimeoutExpired(cmd="m", timeout=timeout)

        manager.wait.side_effect = _manager_wait
        tray = _mock_proc(mocker)

        phase = {"which": "worker"}

        def _check():
            # During worker grace: no 2nd quit.
            # During manager grace: 2nd quit arrives.
            return phase["which"] == "manager"

        orig_kill = worker.kill.side_effect

        def _flip(*a, **kw):
            phase["which"] = "manager"
            if orig_kill is not None:
                return orig_kill(*a, **kw)
            return None

        worker.kill.side_effect = _flip

        cascade.cascade_quit(
            worker, manager, tray, second_quit_check=_check,
        )
        # Worker was SIGKILLed via normal grace-expiry path; the
        # subsequent _hard_kill sweep may call kill() again on
        # survivors (poll() still returns None in this mock), which
        # is harmless in production since the process is already
        # dead.
        assert worker.kill.call_count >= 1
        # Manager + tray SIGKILLed via emergency path (second quit).
        manager.kill.assert_called_once()
        tray.kill.assert_called_once()
