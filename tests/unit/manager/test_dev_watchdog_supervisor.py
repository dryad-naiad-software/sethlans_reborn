# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the dev watchdog supervisor's orphan-child guards.

Covers (issue #293 review):
* Overlapping restarts run one at a time, so only one child is live.
* A child spawned after shutdown began is stopped at once.
* Shutdown waits for an in-flight restart before the final stop.
* ``stop()`` works while the same thread holds the supervisor lock.
* A restart that finished before the main loop noticed the old child's
  exit is not reported as an unexpected exit.
* Parent stop signals: SIGHUP on POSIX, SIGBREAK on Windows, missing
  names skipped.
* POSIX kill fallback sends SIGKILL to the child's process group.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time

import run_manager
from sethlans_manager import dev_watchdog
from sethlans_manager import dev_watchdog_process as dwp


def _supervisor(shutdown=None):
    return dev_watchdog._ChildSupervisor(
        server_script=run_manager.RUN_MANAGER_PY,
        child_argv=[],
        shutdown_event=shutdown or threading.Event(),
    )


class TestRestartSerialisation:

    def test_overlapping_restarts_run_one_at_a_time(self, mocker):
        supervisor = _supervisor()
        active = []
        peak = []
        guard = threading.Lock()

        def slow_stop(*_args, **_kwargs):
            with guard:
                active.append(1)
                peak.append(len(active))
            time.sleep(0.2)
            with guard:
                active.pop()

        mocker.patch.object(supervisor, "stop", side_effect=slow_stop)
        spawn = mocker.patch.object(supervisor, "spawn")

        threads = [
            threading.Thread(target=supervisor.restart) for _ in range(2)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)

        assert max(peak) == 1
        assert spawn.call_count == 2

    def test_restart_after_shutdown_does_nothing(self, mocker):
        shutdown = threading.Event()
        shutdown.set()
        supervisor = _supervisor(shutdown)
        stop = mocker.patch.object(supervisor, "stop")
        spawn = mocker.patch.object(supervisor, "spawn")
        supervisor.restart()
        stop.assert_not_called()
        spawn.assert_not_called()


class TestSpawnDuringShutdown:

    def test_child_spawned_after_shutdown_is_stopped(self, mocker):
        shutdown = threading.Event()
        supervisor = _supervisor(shutdown)
        new_proc = mocker.MagicMock()

        def popen(*_args, **_kwargs):
            shutdown.set()  # shutdown began while Popen ran
            return new_proc

        mocker.patch.object(
            dev_watchdog.subprocess, "Popen", side_effect=popen,
        )
        stop = mocker.patch.object(supervisor, "stop")

        assert supervisor.spawn() is new_proc
        assert supervisor._proc is new_proc
        stop.assert_called_once_with()

    def test_spawn_without_shutdown_does_not_stop(self, mocker):
        supervisor = _supervisor()
        mocker.patch.object(dev_watchdog.subprocess, "Popen")
        stop = mocker.patch.object(supervisor, "stop")
        supervisor.spawn()
        stop.assert_not_called()

    def test_shutdown_waits_for_in_flight_restart(self, mocker):
        supervisor = _supervisor()
        release = threading.Event()
        lock_held = threading.Event()
        order = []

        def blocking_stop(*_args, **_kwargs):
            lock_held.set()  # restart() calls stop() under the lock
            release.wait(timeout=5)
            order.append("restart-stop")

        mocker.patch.object(supervisor, "stop", side_effect=blocking_stop)
        mocker.patch.object(
            supervisor, "spawn",
            side_effect=lambda: order.append("restart-spawn"),
        )
        restarter = threading.Thread(target=supervisor.restart)
        restarter.start()
        assert lock_held.wait(timeout=5)  # restart holds the restart lock

        threading.Timer(0.2, release.set).start()
        supervisor.wait_for_restart(timeout=5)
        order.append("drained")
        restarter.join(timeout=5)

        assert order == ["restart-stop", "restart-spawn", "drained"]

    def test_stop_while_lock_held_by_same_thread(self):
        """The signal handler runs ``stop()`` on the main thread, which
        may be inside ``with self._lock`` when interrupted."""
        supervisor = _supervisor()
        with supervisor._lock:
            assert supervisor.stop() is None


class TestWaitForUnexpectedExit:

    def test_finished_restart_is_not_an_unexpected_exit(self, mocker):
        """The old child exits, and the restart thread has already
        spawned the new child and cleared the restart flag before the
        main loop looks: the loop must carry on with the new child."""
        shutdown = threading.Event()
        supervisor = _supervisor(shutdown)
        old_proc = mocker.MagicMock()
        new_proc = mocker.MagicMock()

        def old_wait(timeout=None):
            supervisor._proc = new_proc  # restart done, flag cleared
            return 1

        def new_wait(timeout=None):
            shutdown.set()
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

        old_proc.wait.side_effect = old_wait
        new_proc.wait.side_effect = new_wait
        supervisor._proc = old_proc

        assert supervisor.wait_for_unexpected_exit() == 0
        new_proc.wait.assert_called()


class TestParentSignalHandlers:

    def _registered(self, mocker):
        registered = mocker.patch.object(dwp.signal, "signal")
        dwp.install_parent_signal_handlers(
            threading.Event(), mocker.MagicMock(),
        )
        return [c.args[0] for c in registered.call_args_list]

    def test_real_platform_signals(self, mocker):
        signums = self._registered(mocker)
        assert signal.SIGINT in signums
        assert signal.SIGTERM in signums
        if hasattr(signal, "SIGBREAK"):  # Windows
            assert signal.SIGBREAK in signums
        if hasattr(signal, "SIGHUP"):  # POSIX
            assert signal.SIGHUP in signums

    def test_sighup_registered_when_present(self, mocker, monkeypatch):
        monkeypatch.setattr(dwp.signal, "SIGHUP", 1001, raising=False)
        assert 1001 in self._registered(mocker)

    def test_missing_names_are_skipped(self, mocker, monkeypatch):
        monkeypatch.delattr(dwp.signal, "SIGHUP", raising=False)
        monkeypatch.delattr(dwp.signal, "SIGBREAK", raising=False)
        signums = self._registered(mocker)
        assert sorted(signums) == sorted([signal.SIGINT, signal.SIGTERM])


class TestPosixKillFallback:

    def test_sigkill_to_group_before_descendant_sweep(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(dwp.sys, "platform", "linux")
        monkeypatch.setattr(dwp.signal, "SIGKILL", 9, raising=False)
        calls = []
        grandchild = mocker.MagicMock()
        grandchild.kill.side_effect = lambda: calls.append("grandchild")
        mocker.patch.object(
            dwp.psutil, "Process",
        ).return_value.children.return_value = [grandchild]
        mocker.patch.object(
            dwp.os, "killpg", create=True,
            side_effect=lambda pid, sig: calls.append(("killpg", pid, sig)),
        )
        proc = mocker.MagicMock()
        proc.pid = 4321
        proc.kill.side_effect = lambda: calls.append("child")

        dwp.kill_process_tree(proc)

        assert calls == [("killpg", 4321, 9), "grandchild", "child"]

    def test_missing_group_is_ignored(self, mocker, monkeypatch):
        monkeypatch.setattr(dwp.sys, "platform", "linux")
        monkeypatch.setattr(dwp.signal, "SIGKILL", 9, raising=False)
        mocker.patch.object(
            dwp.psutil, "Process",
        ).return_value.children.return_value = []
        mocker.patch.object(
            dwp.os, "killpg", create=True, side_effect=ProcessLookupError,
        )
        proc = mocker.MagicMock()
        dwp.kill_process_tree(proc)
        proc.kill.assert_called_once_with()
