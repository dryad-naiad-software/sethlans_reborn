# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the dev watchdog's child process (issue #293).

``run_manager.py --dev`` used to spawn a bare ``manage.py`` child,
which printed Django's help and exited 0. The child must be
``run_manager.py`` itself without ``--dev``.

Covers:
* The child command built for ``run_manager.py --dev``.
* Watched directories (manager tree + ``shared/``).
* Process-tree signalling helpers (group kwargs, stop signal, kill).
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

import run_manager
from sethlans_manager import dev_watchdog
from sethlans_manager import dev_watchdog_process as dwp


@pytest.fixture(autouse=True)
def _restore_dev_env(monkeypatch):
    """``run_dev_watchdog`` sets the dev markers in ``os.environ``;
    record them as absent so monkeypatch removes them afterwards."""
    for name in ("SETHLANS_DEV_MODE", "SETHLANS_DEV_IS_PARENT"):
        monkeypatch.setenv(name, "0")
        monkeypatch.delenv(name)


def _run_watchdog_with_fake_popen(mocker, argv):
    """Run ``run_dev_watchdog`` for ``run_manager.py`` with no real
    observer, signal handlers or child; return the Popen mock."""
    mocker.patch.object(dev_watchdog, "_start_observer")
    mocker.patch.object(dev_watchdog, "_install_parent_signal_handlers")
    popen = mocker.patch.object(dev_watchdog.subprocess, "Popen")
    popen.return_value.wait.return_value = 0
    popen.return_value.poll.return_value = 0
    rc = dev_watchdog.run_dev_watchdog(run_manager.RUN_MANAGER_PY, argv)
    assert rc == 0
    return popen


class TestChildCommandForRunManagerDev:

    def test_main_hands_run_manager_py_to_the_watchdog(self, mocker):
        """Reproduces #293: ``main()`` passed ``manage.py`` as the
        script the child runs."""
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager.py", "--dev"],
        )
        rdw = mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
            return_value=0,
        )
        with pytest.raises(SystemExit):
            run_manager.main()

        script, argv = rdw.call_args.args
        assert Path(script).name == "run_manager.py"
        assert Path(script) == Path(run_manager.__file__).resolve()
        assert argv == ["--dev"]

    def test_child_is_run_manager_without_dev(self, mocker):
        popen = _run_watchdog_with_fake_popen(mocker, ["--dev"])

        cmd = popen.call_args.args[0]
        assert cmd == [sys.executable, str(run_manager.RUN_MANAGER_PY)]
        assert not any(Path(part).name == "manage.py" for part in cmd)
        assert "--dev" not in cmd

    def test_other_arguments_reach_the_child(self, mocker):
        popen = _run_watchdog_with_fake_popen(mocker, ["--dev", "--extra"])

        cmd = popen.call_args.args[0]
        assert cmd == [
            sys.executable, str(run_manager.RUN_MANAGER_PY), "--extra",
        ]

    def test_child_gets_its_own_process_group(self, mocker):
        popen = _run_watchdog_with_fake_popen(mocker, ["--dev"])

        kwargs = popen.call_args.kwargs
        for key, value in dwp.popen_group_kwargs().items():
            assert kwargs[key] == value
        assert "SETHLANS_DEV_IS_PARENT" not in kwargs["env"]
        assert kwargs["env"]["SETHLANS_DEV_MODE"] == "1"


class TestWatchedDirectories:

    def test_manager_tree_and_shared(self):
        script = run_manager.RUN_MANAGER_PY
        dirs = list(dev_watchdog._watched_directories(script))
        assert dirs == [script.parent, script.parent.parent / "shared"]

    def test_without_shared_dir_only_manager_tree(self, tmp_path):
        script = tmp_path / "manager" / "run_manager.py"
        script.parent.mkdir()
        dirs = list(dev_watchdog._watched_directories(script))
        assert dirs == [script.parent.resolve()]


class TestProcessGroupHelpers:

    def test_windows_group_kwargs(self, monkeypatch):
        monkeypatch.setattr(dwp.sys, "platform", "win32")
        monkeypatch.setattr(
            dwp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False,
        )
        assert dwp.popen_group_kwargs() == {"creationflags": 0x200}

    def test_posix_group_kwargs(self, monkeypatch):
        monkeypatch.setattr(dwp.sys, "platform", "linux")
        assert dwp.popen_group_kwargs() == {"start_new_session": True}

    def test_stop_signal_skips_exited_child(self, mocker):
        proc = mocker.MagicMock()
        proc.poll.return_value = 0
        killpg = mocker.patch.object(dwp.os, "killpg", create=True)
        dwp.send_stop_signal(proc)
        proc.send_signal.assert_not_called()
        killpg.assert_not_called()

    def test_windows_stop_signal_is_ctrl_break_to_group(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(dwp.sys, "platform", "win32")
        monkeypatch.setattr(
            dwp.signal, "CTRL_BREAK_EVENT", 1, raising=False,
        )
        proc = mocker.MagicMock()
        proc.poll.return_value = None
        dwp.send_stop_signal(proc)
        proc.send_signal.assert_called_once_with(1)

    def test_posix_stop_signal_is_sigterm_to_group(
        self, mocker, monkeypatch,
    ):
        monkeypatch.setattr(dwp.sys, "platform", "linux")
        killpg = mocker.patch.object(dwp.os, "killpg", create=True)
        proc = mocker.MagicMock()
        proc.poll.return_value = None
        proc.pid = 4321
        dwp.send_stop_signal(proc)
        killpg.assert_called_once_with(4321, signal.SIGTERM)
        proc.send_signal.assert_not_called()

    def test_kill_process_tree_kills_grandchild(self):
        """A real child that started a grandchild (as run_manager.py
        starts ``manage.py migrate``): both are gone after the kill."""
        code = (
            "import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(60)'])\n"
            "print(g.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
        )
        try:
            grandchild_pid = int(proc.stdout.readline())
            grandchild = psutil.Process(grandchild_pid)
            dwp.kill_process_tree(proc)
            proc.wait(timeout=10)
            grandchild.wait(timeout=10)
            assert not grandchild.is_running()
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
