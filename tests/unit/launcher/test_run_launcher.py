# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.run_launcher` (FR-LN-5, FR-LN-7, owner Q4)."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from launcher import reset_setup, run_launcher

SECRETS = {"SETHLANS_ADMIN_PASSWORD": "admin-secret", "SETHLANS_SETUP_ADMIN_PASSWORD": "setup-secret",
           "SETHLANS_TRAY_IPC_SECRET": "stray"}


class _LiveTray:
    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("tray", timeout)

    def terminate(self):
        pass

    kill = terminate


@pytest.fixture
def boot(mocker, monkeypatch, settings_dir):
    """main() with the lock, logging and the loop mocked; the real scrub and tray start."""
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    m = mocker.MagicMock()
    m.lock = mocker.patch.object(run_launcher, "acquire_single_instance_lock", return_value=object())
    mocker.patch.object(run_launcher, "release_lock")
    mocker.patch("launcher.logging_setup.configure")
    mocker.patch.object(run_launcher.supervision, "install_signal_handlers")
    mocker.patch.object(run_launcher.supervision, "start_ipc_poll_thread")
    m.seen_env = {}

    def popen(cmd, **kwargs):
        m.seen_env.update(os.environ)
        m.tray_env = kwargs["env"]
        state_file = settings_dir / "setup" / "launcher_state.json"
        m.state_at_tray_start = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        return _LiveTray()

    m.popen = mocker.patch("launcher.components.subprocess.Popen", side_effect=popen)
    m.loop = mocker.patch.object(run_launcher.machine_loop, "run_loop", return_value=0)
    m.reset = mocker.patch.object(run_launcher.reset_setup, "reset_stopped")
    return m


def test_main_scrubs_before_the_first_spawn(boot, settings_dir):
    """FR-LN-7 (6a gate): the setup secrets are out of the launcher's own environment
    before the first child starts, the settings location is exported, and only the tray
    gets the tray secret."""
    assert run_launcher.main(["--no-browser"]) == 0

    boot.popen.assert_called_once()
    assert not set(SECRETS) & set(boot.seen_env)
    assert boot.seen_env["SETHLANS_SETTINGS_DIR"] == os.path.abspath(settings_dir)
    assert boot.tray_env["SETHLANS_TRAY_IPC_SECRET"] != "stray"
    assert "SETHLANS_ADMIN_PASSWORD" not in boot.tray_env
    boot.loop.assert_called_once()


def test_reset_setup_while_running_is_refused_and_changes_nothing(boot, capsys):
    """Owner Q4 (a): while Sethlans runs (the lock is held) --reset-setup refuses with
    the tray hint and changes nothing."""
    boot.lock.return_value = None

    assert run_launcher.main(["--reset-setup", "--no-browser"]) == 1

    assert reset_setup.REFUSED in capsys.readouterr().err
    boot.reset.assert_not_called()
    boot.popen.assert_not_called()


def test_reset_setup_when_not_running_resets_then_starts(boot):
    """Owner Q4 (a), FR-RS-1: not running, the flag is the confirmation: reset at once,
    then the launcher starts (into SETUP)."""
    order = []
    boot.reset.side_effect = lambda: order.append("reset")
    boot.loop.side_effect = lambda ctx: order.append("loop") or 0

    assert run_launcher.main(["--reset-setup", "--no-browser"]) == 0
    assert order == ["reset", "loop"]


def test_already_running_starts_nothing(boot, capsys):
    """FR-14b: a second launcher on the same settings folder only says so."""
    boot.lock.return_value = None
    assert run_launcher.main(["--no-browser"]) == 0
    assert "already running" in capsys.readouterr().err
    boot.popen.assert_not_called()


def test_tray_starts_with_a_fresh_starting_state(boot, settings_dir):
    """6c review F1: a STOPPING (or, after a crash, RUNNING) state left by the last run
    is replaced by STARTING before the tray starts, so the tray never shows it."""
    (settings_dir / "setup").mkdir(parents=True)
    (settings_dir / "setup" / "launcher_state.json").write_text(
        json.dumps({"state": "RUNNING", "port": 8080, "reason": None}), encoding="utf-8")
    assert run_launcher.main(["--no-browser"]) == 0
    assert boot.state_at_tray_start == {"state": "STARTING", "port": None, "reason": None}
