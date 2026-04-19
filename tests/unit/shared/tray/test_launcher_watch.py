# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/launcher_watch.py`` (FR-19c)."""

from __future__ import annotations

import pytest

from shared.tray import launcher_watch


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level init state between tests."""
    launcher_watch._initialized = False
    launcher_watch._launcher_pid = 0
    launcher_watch._launcher_create_time = None
    yield
    launcher_watch._initialized = False
    launcher_watch._launcher_pid = 0
    launcher_watch._launcher_create_time = None


class TestInit:

    def test_missing_env_var_leaves_pid_zero(self, monkeypatch):
        monkeypatch.delenv("SETHLANS_LAUNCHER_PID", raising=False)
        launcher_watch.init()
        assert launcher_watch.launcher_pid() == 0

    def test_invalid_env_var_is_ignored(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_LAUNCHER_PID", "not-a-pid")
        launcher_watch.init()
        assert launcher_watch.launcher_pid() == 0


class TestIsLauncherAlive:

    def test_dev_mode_returns_true_when_env_absent(self, monkeypatch):
        # FR-19c: running standalone (dev mode) — never self-terminate.
        monkeypatch.delenv("SETHLANS_LAUNCHER_PID", raising=False)
        assert launcher_watch.is_launcher_alive() is True

    def test_returns_false_when_pid_gone(self, monkeypatch, mocker):
        monkeypatch.setenv("SETHLANS_LAUNCHER_PID", "12345")

        # Fake psutil: pid_exists=False.
        fake_psutil = mocker.MagicMock()
        fake_psutil.pid_exists.return_value = False
        fake_psutil.Error = Exception
        fake_psutil.NoSuchProcess = type(
            "NoSuchProcess", (Exception,), {},
        )
        mocker.patch.object(launcher_watch, "psutil", fake_psutil)

        launcher_watch.init()
        assert launcher_watch.is_launcher_alive() is False

    def test_returns_true_when_pid_and_create_time_match(
        self, monkeypatch, mocker,
    ):
        monkeypatch.setenv("SETHLANS_LAUNCHER_PID", "12345")

        fake_proc = mocker.MagicMock()
        fake_proc.create_time.return_value = 1000.0
        fake_psutil = mocker.MagicMock()
        fake_psutil.pid_exists.return_value = True
        fake_psutil.Process.return_value = fake_proc
        fake_psutil.Error = Exception
        fake_psutil.NoSuchProcess = type(
            "NoSuchProcess", (Exception,), {},
        )
        mocker.patch.object(launcher_watch, "psutil", fake_psutil)

        launcher_watch.init()
        # Same process returns same create_time on subsequent query.
        assert launcher_watch.is_launcher_alive() is True

    def test_returns_false_when_create_time_differs(
        self, monkeypatch, mocker,
    ):
        # PID reuse: PID exists, but a different process claims it.
        monkeypatch.setenv("SETHLANS_LAUNCHER_PID", "12345")

        call_counter = {"n": 0}

        def _create_time():
            call_counter["n"] += 1
            # First call (during init): 1000.0; later: 2000.0.
            return 1000.0 if call_counter["n"] == 1 else 2000.0

        fake_proc = mocker.MagicMock()
        fake_proc.create_time.side_effect = _create_time

        fake_psutil = mocker.MagicMock()
        fake_psutil.pid_exists.return_value = True
        fake_psutil.Process.return_value = fake_proc
        fake_psutil.Error = Exception
        fake_psutil.NoSuchProcess = type(
            "NoSuchProcess", (Exception,), {},
        )
        mocker.patch.object(launcher_watch, "psutil", fake_psutil)

        launcher_watch.init()
        assert launcher_watch.is_launcher_alive() is False

    def test_psutil_none_returns_true(self, monkeypatch, mocker):
        monkeypatch.setenv("SETHLANS_LAUNCHER_PID", "12345")
        mocker.patch.object(launcher_watch, "psutil", None)
        launcher_watch.init()
        assert launcher_watch.is_launcher_alive() is True
