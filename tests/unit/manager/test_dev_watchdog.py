# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the Phase 6 dev hot-reload watchdog wrapper.

Covers:
* Debounce: a burst of events collapses to exactly one restart.
* Extended burst: events spanning multiple debounce windows still
  yield at most one restart per window (per spec FR line 668).
* Parent/child env wiring: parent sets ``SETHLANS_DEV_MODE`` and
  ``SETHLANS_DEV_IS_PARENT``; child env drops ``IS_PARENT``, keeps
  ``DEV_MODE``.
* ``--dev`` stripped from child argv.
* Path filter: ``.py`` triggers a restart; ``.txt`` and
  ``__pycache__/*.pyc`` do not.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from sethlans_manager import dev_watchdog
from sethlans_manager import dev_watchdog_handler as dwh


class TestShouldTriggerRestart:

    def test_python_file_triggers(self):
        assert dwh._should_trigger_restart("/repo/manager/views.py") is True

    def test_txt_file_does_not_trigger(self):
        assert dwh._should_trigger_restart("/repo/manager/readme.txt") is False

    def test_pycache_pyc_does_not_trigger(self):
        path = "/repo/manager/__pycache__/foo.cpython-314.pyc"
        assert dwh._should_trigger_restart(path) is False

    def test_ignored_dirs_do_not_trigger(self):
        for path in (
            "/repo/.venv/lib/site-packages/foo.py",
            "/repo/.git/hooks/post-commit.py",
            "/repo/node_modules/pkg/run.py",
        ):
            assert dwh._should_trigger_restart(path) is False

    def test_empty_path_does_not_trigger(self):
        assert dwh._should_trigger_restart("") is False


class TestDebouncedRestarter:

    def test_burst_coalesces_to_single_restart(self):
        """10 events within 100 ms → exactly 1 restart fires after
        the 500 ms debounce window elapses."""
        fired: list[int] = []
        restarter = dwh._DebouncedRestarter(
            callback=lambda: fired.append(1),
            debounce_seconds=0.2,
        )
        start = time.monotonic()
        for _ in range(10):
            restarter.schedule()
            # 10 ms between events; total ~100 ms
            time.sleep(0.01)
        # Wait for debounce window + slack.
        deadline = start + 1.0
        while time.monotonic() < deadline and not fired:
            time.sleep(0.01)
        assert len(fired) == 1

    def test_long_burst_spanning_multiple_windows(self):
        """A 5-second-equivalent burst spanning multiple debounce
        windows fires at most one restart per window. We simulate
        two windows with a short debounce for test speed."""
        fired: list[float] = []
        restarter = dwh._DebouncedRestarter(
            callback=lambda: fired.append(time.monotonic()),
            debounce_seconds=0.2,
        )
        # Window 1: burst, then idle long enough for the timer to fire.
        for _ in range(5):
            restarter.schedule()
            time.sleep(0.02)
        time.sleep(0.5)
        # Window 2: second burst → second fire.
        for _ in range(5):
            restarter.schedule()
            time.sleep(0.02)
        time.sleep(0.5)
        assert len(fired) == 2

    def test_cancel_drops_pending_restart(self):
        fired: list[int] = []
        restarter = dwh._DebouncedRestarter(
            callback=lambda: fired.append(1),
            debounce_seconds=0.2,
        )
        restarter.schedule()
        restarter.cancel()
        time.sleep(0.4)
        assert fired == []


class TestBuildChildEnv:

    def test_strips_is_parent_keeps_dev_mode(self):
        parent = {
            "PATH": "/usr/bin",
            "SETHLANS_DEV_MODE": "1",
            "SETHLANS_DEV_IS_PARENT": "1",
        }
        env = dev_watchdog._build_child_env(parent_env=parent)
        assert env["PATH"] == "/usr/bin"
        assert env["SETHLANS_DEV_MODE"] == "1"
        assert "SETHLANS_DEV_IS_PARENT" not in env

    def test_adds_dev_mode_if_missing(self):
        env = dev_watchdog._build_child_env(parent_env={"PATH": "/usr/bin"})
        assert env["SETHLANS_DEV_MODE"] == "1"
        assert "SETHLANS_DEV_IS_PARENT" not in env


class TestStripDevFlag:

    def test_removes_first_dev_occurrence(self):
        assert dev_watchdog._strip_dev_flag(
            ["--dev", "--verbose"],
        ) == ["--verbose"]

    def test_no_op_when_absent(self):
        assert dev_watchdog._strip_dev_flag(
            ["--verbose", "--port", "8080"],
        ) == ["--verbose", "--port", "8080"]

    def test_removes_only_first(self):
        # Defensive — should never happen, but keep the contract clear.
        assert dev_watchdog._strip_dev_flag(
            ["--dev", "--dev"],
        ) == ["--dev"]


class TestRunDevWatchdogParentEnv:

    def test_parent_sets_dev_mode_and_is_parent(self, mocker, monkeypatch):
        """The parent process sets BOTH markers in its own ``os.environ``
        before spawning. Child env construction is tested separately."""
        monkeypatch.delenv("SETHLANS_DEV_MODE", raising=False)
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)

        # Stub everything that would start real threads / subprocesses.
        fake_obs = mocker.MagicMock()
        mocker.patch.object(
            dev_watchdog, "_start_observer", return_value=fake_obs,
        )
        mocker.patch.object(
            dev_watchdog, "_install_parent_signal_handlers",
        )
        supervisor_cls = mocker.patch.object(
            dev_watchdog, "_ChildSupervisor",
        )
        fake_supervisor = supervisor_cls.return_value
        fake_supervisor.wait_for_unexpected_exit.return_value = 0

        rc = dev_watchdog.run_dev_watchdog(
            manage_script=Path(__file__),
            argv=["--dev"],
        )

        assert rc == 0
        assert os.environ["SETHLANS_DEV_MODE"] == "1"
        assert os.environ["SETHLANS_DEV_IS_PARENT"] == "1"
        fake_supervisor.spawn.assert_called_once()


class TestChildSupervisorSpawn:

    def test_spawn_passes_scrubbed_env_and_strips_dev(
        self, mocker, monkeypatch, tmp_path,
    ):
        """``spawn`` must call Popen with an env dict that contains
        ``SETHLANS_DEV_MODE=1`` and omits ``SETHLANS_DEV_IS_PARENT``."""
        monkeypatch.setenv("SETHLANS_DEV_MODE", "1")
        monkeypatch.setenv("SETHLANS_DEV_IS_PARENT", "1")

        popen_mock = mocker.patch.object(
            dev_watchdog.subprocess, "Popen",
        )
        import threading
        supervisor = dev_watchdog._ChildSupervisor(
            manage_script=tmp_path / "manage.py",
            child_argv=["--extra"],  # parent already stripped --dev
            shutdown_event=threading.Event(),
        )
        supervisor.spawn()
        popen_mock.assert_called_once()
        kwargs = popen_mock.call_args.kwargs
        env = kwargs["env"]
        assert env["SETHLANS_DEV_MODE"] == "1"
        assert "SETHLANS_DEV_IS_PARENT" not in env
        cmd = popen_mock.call_args.args[0]
        assert "--dev" not in cmd
        assert "--extra" in cmd

    def test_stop_graceful_then_kill_on_timeout(
        self, mocker, tmp_path,
    ):
        """``stop`` signals first, waits up to ``timeout`` seconds,
        and only kills if the child does not exit."""
        import threading
        import subprocess as real_subprocess
        proc = mocker.MagicMock()
        proc.wait.side_effect = [
            real_subprocess.TimeoutExpired(cmd="x", timeout=5.0),
            -9,
        ]
        supervisor = dev_watchdog._ChildSupervisor(
            manage_script=tmp_path / "manage.py",
            child_argv=[],
            shutdown_event=threading.Event(),
        )
        supervisor._proc = proc

        rc = supervisor.stop(timeout=0.01)
        proc.send_signal.assert_called_once()
        proc.kill.assert_called_once()
        assert rc == -9


class TestEventHandlerFilter:

    def _make(self):
        fired: list[int] = []
        restarter = dwh._DebouncedRestarter(
            callback=lambda: fired.append(1),
            debounce_seconds=0.05,
        )
        handler = dwh._make_event_handler(restarter)
        return restarter, handler, fired

    def test_py_modification_triggers(self, mocker):
        _, handler, fired = self._make()
        evt = mocker.MagicMock()
        evt.is_directory = False
        evt.src_path = "/repo/manager/views.py"
        evt.dest_path = ""
        handler.on_modified(evt)
        time.sleep(0.2)
        assert len(fired) == 1

    def test_txt_modification_ignored(self, mocker):
        _, handler, fired = self._make()
        evt = mocker.MagicMock()
        evt.is_directory = False
        evt.src_path = "/repo/manager/notes.txt"
        evt.dest_path = ""
        handler.on_modified(evt)
        time.sleep(0.2)
        assert fired == []

    def test_pycache_ignored(self, mocker):
        _, handler, fired = self._make()
        evt = mocker.MagicMock()
        evt.is_directory = False
        evt.src_path = "/repo/manager/__pycache__/foo.cpython-314.pyc"
        evt.dest_path = ""
        handler.on_modified(evt)
        time.sleep(0.2)
        assert fired == []

    def test_directory_event_ignored(self, mocker):
        _, handler, fired = self._make()
        evt = mocker.MagicMock()
        evt.is_directory = True
        evt.src_path = "/repo/manager/newdir"
        evt.dest_path = ""
        handler.on_modified(evt)
        time.sleep(0.2)
        assert fired == []
