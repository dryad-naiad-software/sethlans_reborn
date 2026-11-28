# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.reset_setup` (FR-RS-1, FR-RS-2, owner Q4)."""

from __future__ import annotations

from launcher import reset_setup
from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME
from shared import settings_file
from tests.unit.launcher._loop_fakes import FakeProc, make_ctx

FINISHED = ("[setup]\nversion = 1\nmode = manager\ncompleted_at = 2026-11-28T10:00:00+00:00\n\n"
            "[server]\nport = 8080\n")


def test_reset_from_the_tray_stops_worker_then_manager_and_the_broadcaster(mocker, tmp_path):
    """FR-RS-1: the worker and the manager stop the way Quit does (not the tray), the
    broadcaster stops with its params, and only completed_at is removed."""
    cascade = mocker.patch.object(reset_setup.cascade, "cascade_quit")
    stop_broadcaster = mocker.patch.object(reset_setup.supervision, "stop_broadcaster")
    remove = mocker.patch.object(reset_setup.setup_state, "remove_completed_at", return_value=True)
    worker, manager = FakeProc(1), FakeProc(2)
    ctx = make_ctx(tmp_path, worker=worker, manager=manager, token="t", setup_kind="browser")

    reset_setup.reset_running(ctx)

    args, kwargs = cascade.call_args
    assert args == (worker, manager, None) and kwargs["second_quit_check"] == ctx.second_quit_check
    stop_broadcaster.assert_called_once_with(ctx.params_dir)
    remove.assert_called_once_with()
    assert (ctx.worker, ctx.manager, ctx.token, ctx.setup_kind) == (None, None, None, None)


def test_reset_from_the_command_line_changes_only_completed_at(settings_dir, tmp_path):
    """Owner Q4 (not running: reset at once), FR-RS-2: completed_at goes, every other line
    stays, and stale broadcaster params are deleted."""
    settings_dir.mkdir()
    ini = settings_dir / "sethlans.ini"
    ini.write_text(FINISHED, encoding="utf-8")
    params = tmp_path / BROADCASTER_PARAMS_FILENAME
    params.write_text("{}", encoding="utf-8")

    assert reset_setup.reset_stopped(tmp_path) is True

    assert settings_file.setup_finished(ini) is None
    parser = settings_file.read_settings(ini)
    assert dict(parser["setup"]) == {"version": "1", "mode": "manager"}
    assert dict(parser["server"]) == {"port": "8080"}
    assert not params.exists()
    assert reset_setup.reset_stopped(tmp_path) is False
