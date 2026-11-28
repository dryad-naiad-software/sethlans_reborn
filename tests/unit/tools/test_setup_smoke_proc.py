# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The processes, environment and log output of the setup-mode smoke
(``tools/_setup_smoke_proc.py``, ``tools/_smoke_common.py``).

They guard the 7a review findings: every process the smoke starts can
be ended by the watchdog (gatekeeper I-1), no child writes outside the
scratch folder (M-3, security L3), secrets never print in a log dump
(security L2), and the #191 ``--manage`` check keeps its exit-code and
argparse rules.
"""
import pathlib
import subprocess

import pytest

import _setup_smoke_proc as proc_mod
import _smoke_common as common

PORTS = {"public": 1001, "internal": 1002, "setup": 1003}


def test_child_env_isolates_every_per_user_and_temp_folder():
    """Gatekeeper M-3 + security L3: home, app data and temp all point into the scratch folder."""
    root = pathlib.Path("/scratch")
    base = {"PATH": "/bin", "SETHLANS_DB_HOST": "db", "DJANGO_SETTINGS_MODULE": "x",
            "XDG_DATA_HOME": "/real/data", "XDG_CONFIG_HOME": "/real/config", "XDG_RUNTIME_DIR": "/run/u"}
    env = proc_mod.child_env(base, root, root / "settings", PORTS)
    folders = proc_mod.scratch_folders(root)
    assert {key: env[key] for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                                      "TEMP", "TMP", "TMPDIR")} == {
        "HOME": str(folders["home"]), "USERPROFILE": str(folders["home"]),
        "LOCALAPPDATA": str(folders["localappdata"]), "APPDATA": str(folders["appdata"]),
        "TEMP": str(folders["tmp"]), "TMP": str(folders["tmp"]), "TMPDIR": str(folders["tmp"])}
    assert all(folder.parent == root for folder in folders.values())
    assert env["SETHLANS_SETTINGS_DIR"] == str(root / "settings")
    assert env["SETHLANS_SETUP_PORT"] == "1003"
    for dropped in ("SETHLANS_DB_HOST", "DJANGO_SETTINGS_MODULE", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        assert dropped not in env
    assert env["PATH"] == "/bin" and env["XDG_RUNTIME_DIR"] == "/run/u"


class _FakePopen:
    """A stand-in child: ``communicate`` times out once when asked to."""

    def __init__(self, times_out):
        self.times_out = times_out
        self.returncode = 0

    def communicate(self, stdin=None, timeout=None):
        if self.times_out:
            self.times_out = False
            raise subprocess.TimeoutExpired("cmd", timeout)
        return b"out", b"err"


def test_run_tracks_the_child_while_it_runs_and_stop_ends_it(mocker):
    """Gatekeeper I-1: a short command is tracked, so the watchdog's stop ends it (and the setup process)."""
    seen = {}
    tracker = proc_mod.ChildTracker({})
    fake = _FakePopen(times_out=False)

    def communicate(stdin=None, timeout=None):
        seen["child"] = tracker.child
        tracker.stop()
        return b"out", b"err"

    fake.communicate = communicate
    mocker.patch.object(proc_mod.subprocess, "Popen", return_value=fake)
    ended = mocker.patch.object(proc_mod, "terminate")
    tracker.proc = setup = object()

    assert tracker.run(["cmd"], b"{}", 5) == (0, b"out", b"err")
    assert seen["child"] is fake
    assert [call.args[0] for call in ended.call_args_list] == [fake, setup]
    assert tracker.child is None


def test_run_ends_a_child_that_times_out(mocker):
    """Gatekeeper I-1: past its timeout a command's tree is ended and the step fails."""
    fake = _FakePopen(times_out=True)
    mocker.patch.object(proc_mod.subprocess, "Popen", return_value=fake)
    ended = mocker.patch.object(proc_mod, "terminate")
    tracker = proc_mod.ChildTracker({})

    with pytest.raises(proc_mod.SmokeFailure, match="did not finish within 5s"):
        tracker.run(["cmd"], b"", 5)
    ended.assert_called_once_with(fake)
    assert tracker.child is None


def test_dump_logs_redacts_the_given_secrets(tmp_path, capsys):
    """Security L2: the setup token, session token and admin password print as <redacted>."""
    log = tmp_path / "setup.stderr.log"
    log.write_text("token TOK-1 session SESS-1 password PASS-1 end")
    common.dump_logs(log, redact=["TOK-1", "SESS-1", "PASS-1", ""])
    printed = capsys.readouterr().err
    assert "token <redacted> session <redacted> password <redacted> end" in printed
    assert "TOK-1" not in printed and "SESS-1" not in printed and "PASS-1" not in printed


@pytest.mark.parametrize("returncode, stderr, ok", [
    (0, b"", True),
    (1, b"You have unapplied migrations", True),
    (2, b"run_manager: error: unrecognized arguments: --manage", False),
    (1, b"error: argument --manage", False),
    (3, b"", False),
])
def test_manage_mode_problems(returncode, stderr, ok):
    """#191: --manage must reach Django (exit 0/1) and never be refused by argparse."""
    assert (common.manage_mode_problems(returncode, stderr) == []) is ok
