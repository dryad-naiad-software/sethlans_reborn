# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for launcher tray-spawn ordering and env-var passing.

Exercises the spawn wiring in ``launcher/run_launcher.py::_spawn_tray``
and the orchestration loop's quit/restart dispatch in
``launcher/orchestration.py``.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from launcher import orchestration, run_launcher


# ------------------------------------------------------------------
# Tray spawn: env vars SETHLANS_TRAY_IPC_SECRET + SETHLANS_LAUNCHER_PID
# ------------------------------------------------------------------

class TestSpawnTrayEnv:
    """Contract per commit f379bc5 / GitHub #89:

    * Healthy spawn (still alive after the 3s self-abort window)
      returns the Popen handle.
    * `_start_component` raising -> sys.exit(1) (no orphan processes).
    * Spawn succeeds but tray exits within 3s -> sys.exit(1) (likely a
      missing PySide6 backend; user gets a visible error rather than a
      silent install).
    """

    def test_env_vars_passed_to_tray_subprocess(self, mocker, tmp_path):
        # Make wait() raise TimeoutExpired so the function treats the
        # tray as "still alive after 3s = healthy" and returns the proc
        # instead of sys.exit'ing on a phantom early exit.
        proc = mocker.MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="tray", timeout=3)
        spy = mocker.patch.object(
            run_launcher, "_start_component", return_value=proc,
        )
        result = run_launcher._spawn_tray(tmp_path, secret="the-secret")
        spy.assert_called_once()
        _args, kwargs = spy.call_args
        env = kwargs["env"]
        assert env["SETHLANS_TRAY_IPC_SECRET"] == "the-secret"
        assert env["SETHLANS_LAUNCHER_PID"] == str(os.getpid())
        # Healthy spawn returns the live Popen handle.
        assert result is proc

    def test_exception_in_spawn_aborts_with_exit_1(
        self, mocker, tmp_path, capsys,
    ):
        # _start_component raising -> sys.exit(1) with a banner on stderr.
        # The launcher MUST fail hard rather than continue: the tray is
        # the only UX surface for the install, so silent absence would
        # leave orphan manager/worker processes with no user feedback.
        mocker.patch.object(
            run_launcher, "_start_component",
            side_effect=RuntimeError("no exe"),
        )
        with pytest.raises(SystemExit) as exc_info:
            run_launcher._spawn_tray(tmp_path, "x")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failed to spawn tray helper" in err
        assert "Aborting startup" in err

    def test_tray_exiting_within_3s_aborts_with_exit_1(
        self, mocker, tmp_path, capsys,
    ):
        # Tray exits within the 3s self-abort window -> hard fail with
        # the "exited immediately" banner. Mirrors the missing-backend
        # path that motivated f379bc5.
        proc = mocker.MagicMock()
        proc.wait.return_value = 2  # simulated exit code
        mocker.patch.object(
            run_launcher, "_start_component", return_value=proc,
        )
        with pytest.raises(SystemExit) as exc_info:
            run_launcher._spawn_tray(tmp_path, "x")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Tray helper exited immediately with code 2" in err


# ------------------------------------------------------------------
# orchestration._handle_quit cascade dispatch
# ------------------------------------------------------------------

class TestHandleQuit:

    def test_quit_all_triggers_cascade_and_returns_zero(self, mocker):
        cascade_spy = mocker.patch.object(
            orchestration.cascade, "cascade_quit",
        )
        state = {"manager": object(), "worker": object(), "cascade": False}
        rc = orchestration._handle_quit("all", state, tray=None)
        cascade_spy.assert_called_once()
        assert rc == 0
        assert state["cascade"] is True

    def test_quit_manager_only(self, mocker):
        manager_only = mocker.patch.object(
            orchestration.cascade, "quit_manager_only",
        )
        manager_proc = object()
        state = {"manager": manager_proc, "worker": object(),
                 "cascade": False}
        rc = orchestration._handle_quit("manager", state, tray=None)
        manager_only.assert_called_once_with(manager_proc)
        # Worker was alive: rc is None so the main loop keeps running.
        assert rc is None
        assert state["manager"] is None

    def test_quit_manager_when_no_worker_returns_zero(self, mocker):
        mocker.patch.object(
            orchestration.cascade, "quit_manager_only",
        )
        state = {"manager": object(), "worker": None, "cascade": False}
        rc = orchestration._handle_quit("manager", state, tray=None)
        assert rc == 0

    def test_second_quit_during_cascade_triggers_emergency_kill(self, mocker):
        emergency = mocker.patch.object(
            orchestration.cascade, "emergency_kill_all",
        )
        state = {
            "manager": object(),
            "worker": object(),
            "cascade": True,
        }
        rc = orchestration._handle_quit("all", state, tray=None)
        emergency.assert_called_once()
        assert rc == 0

    def test_none_quit_is_noop(self):
        state = {"manager": object(), "worker": None, "cascade": False}
        rc = orchestration._handle_quit(None, state, tray=None)
        assert rc is None


# ------------------------------------------------------------------
# orchestration._handle_restart
# ------------------------------------------------------------------

class TestHandleRestart:

    def test_manager_restart_replaces_process(self, mocker, tmp_path):
        new_proc = object()
        mocker.patch.object(
            orchestration,
            "handle_restart_request",
            return_value=new_proc,
        )
        state = {"manager": object(), "worker": None, "cascade": False}

        def _start_component(name):
            return object()

        orchestration._handle_restart(
            "manager", state, tmp_path, _start_component,
        )
        assert state["manager"] is new_proc

    def test_non_manager_restart_is_ignored(self, mocker, tmp_path):
        mocker.patch.object(
            orchestration,
            "handle_restart_request",
            return_value=object(),
        )
        original = object()
        state = {"manager": original, "worker": None, "cascade": False}
        orchestration._handle_restart(
            "worker", state, tmp_path, lambda n: object(),
        )
        assert state["manager"] is original

    def test_no_current_manager_is_ignored(self, tmp_path):
        state = {"manager": None, "worker": None, "cascade": False}
        # Must not raise.
        orchestration._handle_restart(
            "manager", state, tmp_path, lambda n: object(),
        )
