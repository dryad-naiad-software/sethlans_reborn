# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.components` (FR-LN-6, FR-LN-7)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from launcher import components, setup_state
from shared import owner_only_acl

LOG = ("logs", "manager-start.log")


@pytest.mark.parametrize("start, args, extra", [
    pytest.param(lambda tmp: components.start_setup_mode(8081), ["--setup"],
                 {"SETHLANS_SETUP_PORT": "8081"}, id="setup-mode"),
    pytest.param(lambda tmp: components.start_manager(tmp.joinpath(*LOG)), [],
                 {"SETHLANS_CONSOLE_LOG_LEVEL": "WARNING"}, id="manager"),
    pytest.param(lambda tmp: components.start_worker(), [], {"SETHLANS_WORKER_UI_ENABLED": "true"}, id="worker"),
    pytest.param(lambda tmp: components.start_tray("s"), [], {"SETHLANS_TRAY_IPC_SECRET": "s"}, id="tray"),
])
def test_every_child_gets_child_env_with_its_own_extra(mocker, tmp_path, start, args, extra):
    """FR-LN-7: each child's environment is ``child_env`` (the scrub, tested in
    ``test_child_env``) plus only its own extra: the setup port for setup mode,
    the console log level for the manager (F5), the UI flag for the worker,
    the IPC secret for the tray alone."""
    mocker.patch.object(components, "find_component_exe", return_value=tmp_path / "run_x.py")
    mocker.patch.object(components, "is_frozen", return_value=False)
    env = mocker.patch.object(components, "child_env", return_value={"FROM_CHILD_ENV": "1"})
    popen = mocker.patch.object(components.subprocess, "Popen")

    start(tmp_path)

    env.assert_called_once_with(extra)
    assert popen.call_args.kwargs["env"] == {"FROM_CHILD_ENV": "1"}
    assert popen.call_args.args[0] == [sys.executable, str(tmp_path / "run_x.py"), *args]


def test_manager_stderr_is_a_new_owner_only_file_not_a_pipe(mocker, tmp_path):
    """FR-LN-6, F5 and issue #204: the manager's stderr is ``manager-start.log``,
    new on each start, owner-only from birth in an owner-only folder; never a
    pipe; the launcher's own handle is closed once the child has it."""
    log = tmp_path.joinpath(*LOG)
    log.parent.mkdir()
    log.write_bytes(b"[ERROR] an earlier start\n")
    mocker.patch.object(components, "find_component_exe", return_value=tmp_path / "run_manager.py")
    popen = mocker.patch.object(components.subprocess, "Popen")

    components.start_manager(log)

    stderr = popen.call_args.kwargs["stderr"]
    assert stderr is not subprocess.PIPE
    assert Path(stderr.name) == log
    assert stderr.closed
    assert log.read_bytes() == b""
    assert owner_only_acl.verify_owner_only(log)


@pytest.mark.parametrize("content, expected", [
    pytest.param("[ERROR] one\nnoise\n  [ERROR] two  \n", "two", id="last-error-line-wins"),
    pytest.param("[ERROR] bad\x1b[31m\tcolour\r\n", "bad [31m colour", id="control-chars-become-spaces"),
    pytest.param("[ERROR] " + "y" * 900 + "\n", "y" * setup_state.MAX_REASON_CHARS, id="capped"),
    pytest.param("[1] [ERROR] log line\nTraceback\n", "The manager stopped with exit code 11", id="no-error-line"),
    pytest.param(None, "The manager stopped with exit code 11", id="no-file"),
])
def test_failure_reason(tmp_path, content, expected):
    """FR-LN-6: the reason is the last ``[ERROR]`` line, one line, capped (it is
    shown on the unauthenticated setup health page), else the exit code."""
    path = tmp_path / "manager-start.log"
    if content is not None:
        path.write_bytes(content.encode("utf-8"))
    assert components.failure_reason(path, 11) == expected


def test_tray_keeps_the_launcher_console_others_discard_output(mocker, tmp_path):
    """Issue #204: long-running children never get a pipe nobody drains; the
    tray inherits the launcher's console, the worker writes to DEVNULL."""
    mocker.patch.object(components, "find_component_exe", return_value=tmp_path / "run_x.py")
    popen = mocker.patch.object(components.subprocess, "Popen")

    components.start_tray("s")
    tray = popen.call_args.kwargs
    components.start_worker()
    worker = popen.call_args.kwargs

    assert (tray["stdout"], tray["stderr"]) == (None, None)
    assert tray["env"]["SETHLANS_TRAY_IPC_SECRET"] == "s"
    assert (worker["stdout"], worker["stderr"]) == (subprocess.DEVNULL, subprocess.DEVNULL)
