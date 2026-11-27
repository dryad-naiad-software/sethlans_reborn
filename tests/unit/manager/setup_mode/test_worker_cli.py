# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``worker_cli``: how setup mode starts the worker program's setup commands (FR-WK-1, FR-WK-2, FR-WZ-2).

A fake ``subprocess.Popen`` stands in for the worker program; nothing is started.
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from sethlans_manager.setup_mode import worker_cli

REAL_RUN = worker_cli.run   # the setup-mode conftest replaces worker_cli.run in every test
ARGV = ["/opt/sethlans/worker/run_worker"]
SECRET = "Payload-Secret-Pw!"
PID = 4242


class FakePopen:
    """One worker program run: ``communicate`` answers (or times out) from ``steps``."""

    def __init__(self, steps, argv, **kwargs):
        self.steps, self.argv, self.kwargs = list(steps), argv, kwargs
        self.pid, self.returncode, self.stdin_data, self.killed = PID, None, [], False

    def communicate(self, data=None, timeout=None):
        self.stdin_data.append((data, timeout))
        step = self.steps.pop(0)
        if step == "timeout":
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode, out = step
        return out, None

    def kill(self):
        self.killed = True


@pytest.fixture
def popen(monkeypatch):
    """``popen.steps`` drives the next run; ``popen.runs`` holds each ``FakePopen``."""
    state = type("PopenState", (), {"steps": [(0, b"{}")], "runs": []})()

    def start(argv, **kwargs):
        proc = FakePopen(state.steps, argv, **kwargs)
        state.runs.append(proc)
        return proc

    monkeypatch.setattr(worker_cli.subprocess, "Popen", start)
    monkeypatch.setattr(worker_cli, "worker_command", lambda: list(ARGV))
    return state


class TestWorkerCommand:

    @pytest.mark.parametrize("frozen, platform, expected", [
        (True, "win32", ["run_worker.exe"]),
        (True, "linux", ["run_worker"]),
        (False, "linux", [sys.executable, "run_worker.py"]),
    ])
    def test_program_location(self, monkeypatch, tmp_path, frozen, platform, expected):
        """FR-WK-1: frozen, the sibling bundle's run_worker; from source, this interpreter and run_worker.py."""
        monkeypatch.setattr(worker_cli, "is_frozen", lambda: frozen)
        monkeypatch.setattr(worker_cli, "get_worker_dir", lambda: tmp_path)
        monkeypatch.setattr(worker_cli.sys, "platform", platform)
        *prefix, script = expected
        assert worker_cli.worker_command() == prefix + [str(tmp_path / script)]


class TestChildEnv:

    def test_secrets_and_settings_are_stripped(self):
        """FR-WK-2 / FR-SEC-6: no setup, admin, database, security, worker secret or *_FILE variable reaches the worker."""
        kept = {"PATH": "/bin", "SETHLANS_SETTINGS_FILE": "/s/sethlans.ini", "SETHLANS_SETTINGS_DIR": "/s",
                "SETHLANS_COMPUTE_DEVICES": "0", "SETHLANS_DATA_ROOT": "/data"}
        stripped = {name: "x" for name in (
            "SETHLANS_SETUP_TOKEN", "sethlans_setup_session", "SETHLANS_ADMIN_PASSWORD", "SETHLANS_ADMIN_USERNAME",
            "SETHLANS_DB_PASSWORD", "SETHLANS_DATABASE_URL", "SETHLANS_SECURITY_SECRET_KEY",
            "SETHLANS_TLS_KEY_FILE", "SETHLANS_WORKER_UI_PASSWORD_FILE", "SOME_TOOL_FILE",
            "SETHLANS_WORKER_UI_PASSWORD", "SETHLANS_WORKER_ENROLLMENT_KEY", "DJANGO_SETTINGS_MODULE")}
        assert worker_cli.child_env({**kept, **stripped}, data_root=Path("/other")) == kept

    @pytest.mark.parametrize("base", [{}, {"SETHLANS_DATA_ROOT": ""}])
    def test_data_root_given_when_the_environment_has_none(self, base):
        """FR-WK-1: the worker's log goes to the data folder this setup saves (an env value wins, above)."""
        assert worker_cli.child_env(base, data_root=Path("/data")) == {"SETHLANS_DATA_ROOT": str(Path("/data"))}


class TestRun:

    def test_payload_only_on_stdin_and_never_logged(self, popen, monkeypatch, caplog):
        """FR-WK-2: the payload goes on stdin only; the log has the argv and exit code, never stdin or stdout."""
        monkeypatch.setenv("SETHLANS_SETUP_TOKEN", "setup-secret")
        popen.steps = [(0, json.dumps({"echo": SECRET}).encode())]
        caplog.set_level(logging.INFO, logger=worker_cli.__name__)
        result = REAL_RUN(worker_cli.APPLY, {"ui_password": SECRET}, 30.0, data_root=Path("/d"))
        assert (result.ok, result.answer) == (True, {"echo": SECRET})
        (proc,) = popen.runs
        assert proc.argv == ARGV + [worker_cli.APPLY]
        assert proc.stdin_data == [(json.dumps({"ui_password": SECRET}).encode(), 30.0)]
        assert (proc.kwargs["stdin"], proc.kwargs["stdout"], proc.kwargs["stderr"]) == (
            subprocess.PIPE, subprocess.PIPE, subprocess.DEVNULL)
        assert "SETHLANS_SETUP_TOKEN" not in proc.kwargs["env"]
        assert proc.kwargs["env"]["SETHLANS_DATA_ROOT"] == str(Path("/d"))
        assert SECRET not in caplog.text
        assert [r.getMessage().split(" after ")[0] for r in caplog.records] == [
            f"Worker command {ARGV + [worker_cli.APPLY]} exited 0"]

    def test_unknown_command_is_refused(self, popen):
        """FR-WK-1: only the three setup commands run."""
        with pytest.raises(ValueError):
            REAL_RUN("--agent", {}, 1.0)
        assert popen.runs == []

    def test_program_that_cannot_start(self, popen, monkeypatch):
        """FR-WZ-2: a worker program that would not start is broken (500 class), not raised."""
        def missing(*_args, **_kwargs):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(worker_cli.subprocess, "Popen", missing)
        result = REAL_RUN(worker_cli.CARDS, {}, 20.0)
        assert (result.error, result.broken) == (worker_cli.UNAVAILABLE, True)
        assert result.message == f"Could not start the worker program ({ARGV[0]}): No such file or directory"

    @pytest.mark.parametrize("second_wait", [(0, b""), "timeout"])
    def test_timeout_kills_the_tree(self, popen, monkeypatch, caplog, second_wait):
        """FR-WK-1 / FR-WZ-2: after the timeout the process tree is killed and the answer is worker_timeout."""
        child = type("Child", (), {"kill": lambda self: killed.append("child")})()
        killed = []
        monkeypatch.setattr(worker_cli.psutil, "Process", lambda pid: type(
            "P", (), {"children": lambda self, recursive: [child] if pid == PID and recursive else []})())
        popen.steps = ["timeout", second_wait]
        result = REAL_RUN(worker_cli.DISCOVER, {"seconds": 5}, 10.0)
        assert (result.error, result.broken, result.answer) == (worker_cli.TIMED_OUT, True, {})
        (proc,) = popen.runs
        assert killed == ["child"] and proc.killed
        assert [timeout for _data, timeout in proc.stdin_data] == [10.0, worker_cli.KILL_WAIT]
        assert [r.levelname for r in caplog.records] == ["ERROR"] * (2 if second_wait == "timeout" else 1)

    @pytest.mark.parametrize("failure", ["process_gone", "child_gone"])
    def test_kill_tree_survives_processes_that_already_ended(self, monkeypatch, failure):
        """FR-WK-1: a process that ended on its own does not stop the kill."""
        def gone(*_args, **_kwargs):
            raise psutil.NoSuchProcess(PID)

        child = type("Child", (), {"kill": gone})()
        if failure == "process_gone":
            monkeypatch.setattr(worker_cli.psutil, "Process", gone)
        else:
            monkeypatch.setattr(worker_cli.psutil, "Process", lambda pid: type(
                "P", (), {"children": lambda self, recursive: [child]})())
        proc = FakePopen([], ARGV)
        worker_cli._kill_tree(proc)
        assert proc.killed


@pytest.mark.parametrize("out, ok", [
    (b'{"a": 1}', True),
    (b' \n{"a": 1}\r\n ', True),
    (b"", False),
    (b"[1]", False),
    (b'"text"', False),
    (b"{}{}", False),
    (b'{"a": 1}\n{"b": 2}', False),
    (b"\xff{}", False),
    (b"[" * 100000, False),
], ids=["object", "whitespace_around", "empty", "list", "string", "two_objects", "two_lines", "not_utf8",
        "too_deep"])
def test_parse_answer_takes_exactly_one_object(out, ok):
    """FR-WK-1: stdout must be exactly one JSON object."""
    assert (worker_cli.parse_answer(out) is not None) is ok


@pytest.mark.parametrize("extra, ok", [(0, True), (1, False)], ids=["at_cap", "over_cap"])
def test_parse_answer_size_cap(extra, ok):
    """FR-WK-1: an answer over 1 MiB is refused."""
    head, tail = b'{"a": "', b'"}'
    out = head + b"x" * (worker_cli.MAX_ANSWER_BYTES - len(head) - len(tail) + extra) + tail
    assert (worker_cli.parse_answer(out) is not None) is ok


@pytest.mark.parametrize("code, answer, error, broken, message", [
    (0, {"a": 1}, None, False, ""),
    (1, {"error": "invalid_key", "message": "Wrong key."}, "invalid_key", False, "Wrong key."),
    (1, {"error": "", "message": "m"}, worker_cli.FAILED, True, "m"),
    (1, {"error": "invalid_key"}, worker_cli.FAILED, True, "The worker program failed; see the worker log."),
    (2, {"error": "internal", "message": "Boom."}, worker_cli.FAILED, True, "Boom."),
    (3, {}, worker_cli.FAILED, True, "The worker program failed; see the worker log."),
    (1, None, worker_cli.BAD_ANSWER, True,
     "The worker program gave no readable answer (exit code 1); see the worker log."),
    (1, {"error": "invalid_key", "message": "\x1b[1mBad\x00 key" + "k" * 400}, "invalid_key", False,
     ("[1mBad key" + "k" * 400)[:300]),
    (1, {"error": "invalid_key", "message": "\x07\n"}, "invalid_key", False,
     "The worker program refused the answers; see the worker log."),
    (2, {"error": "internal", "message": "\u202e" + "m" * 301}, worker_cli.FAILED, True, "m" * 300),
    (2, {"error": "internal", "message": "\t"}, worker_cli.FAILED, True,
     "The worker program failed; see the worker log."),
], ids=["ok", "refused", "empty_code", "no_message", "exit_2", "exit_3", "unreadable", "refused_cleaned",
        "refused_empty_after_cleaning", "broken_cleaned", "broken_empty_after_cleaning"])
def test_result_classes(code, answer, error, broken, message):
    """FR-WK-1 / FR-WZ-2: exit 0 ok, exit 1 with error and message refused, anything else broken.

    Security S1 (review): the worker's message, shown on the setup page, is printable text capped at 300.
    """
    out = b"not json" if answer is None else json.dumps(answer).encode()
    result = worker_cli._result(worker_cli.APPLY, code, out)
    assert (result.error, result.broken, result.message) == (error, broken, message)
    assert result.ok is (error is None)


@pytest.mark.parametrize("call, expected", [
    (lambda: worker_cli.cards(), (worker_cli.CARDS, {}, 20.0)),
    (lambda: worker_cli.discover(), (worker_cli.DISCOVER, {"seconds": 5}, 10.0)),
    (lambda: worker_cli.discover(15), (worker_cli.DISCOVER, {"seconds": 15}, 20.0)),
    (lambda: worker_cli.apply({"compute": "cpu"}), (worker_cli.APPLY, {"compute": "cpu"}, 30.0)),
])
def test_timeouts_per_command(fake_worker_cli, call, expected):
    """FR-WK-1: --setup-cards 20 s, --setup-discover seconds + 5, --setup-apply 30 s."""
    call()
    assert fake_worker_cli.calls == [expected]


@pytest.mark.parametrize("platform, expected", [("win32", {"creationflags": 0x08000000}), ("linux", {})])
def test_no_console_window_on_windows(monkeypatch, platform, expected):
    """FR-WK-1: a console-mode worker started from the windowless manager opens no window."""
    monkeypatch.setattr(worker_cli.sys, "platform", platform)
    monkeypatch.setattr(worker_cli.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert worker_cli._popen_flags() == expected
