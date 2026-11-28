# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.machine_loop` (FR-LN-1; ``FAILED`` per owner Q2/Q3; FR-21)."""

from __future__ import annotations

import pytest

from launcher import loop_decisions as ld
from launcher import machine_loop, supervision
from launcher.health_probe import QuitRequested
from tests.unit.launcher._loop_fakes import FakeProc, broadcasting, make_ctx


@pytest.fixture
def parts(mocker):
    m = mocker.MagicMock()
    m.state = mocker.patch.object(machine_loop, "setup_state")
    m.cascade = mocker.patch.object(machine_loop, "cascade")
    m.stop_caddy = mocker.patch.object(machine_loop.caddy_wiring, "stop_caddy")
    m.markers = mocker.patch("launcher.loop_context.LoopContext.consume_markers", return_value=(None, None, None))
    m.reset = mocker.patch.object(machine_loop.reset_setup, "reset_running")
    m.wait = mocker.patch.object(machine_loop.supervision, "wait_or_quit",
                                 side_effect=lambda timeout: supervision.request_quit("all"))
    return m


@pytest.mark.parametrize("finished, expected, deletes_token", [
    pytest.param(None, ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH), False, id="not-finished-setup-fresh"),
    pytest.param("worker", ld.Decision(ld.STARTING, mode="worker"), True, id="finished-starts-its-mode"),
])
def test_initial_decision(parts, mocker, finished, expected, deletes_token):
    """FR-LN-1, FR-SF-4: setup_finished() alone decides where the loop starts; a finished
    setup deletes a token file an earlier run left (security review L1)."""
    mocker.patch.object(machine_loop.settings_file, "setup_finished", return_value=finished)
    assert machine_loop.initial_decision() == expected
    assert parts.state.delete_token.called is deletes_token


def test_states_follow_each_other_until_a_quit(parts, mocker, tmp_path):
    """FR-LN-1: each state returns the next; a quit stops everything and returns 0, the
    splash leaving through its success path (issue #163)."""
    mocker.patch.object(machine_loop.settings_file, "setup_finished", return_value=None)
    seen = []

    def setup(ctx, decision):
        seen.append(decision.state)
        return ld.Decision(ld.STARTING, mode="manager")

    mocker.patch.object(machine_loop.starting, "start_mode",
                        side_effect=lambda ctx, mode: seen.append(mode) or ld.Decision(ld.RUNNING, mode=mode))
    enter = mocker.patch.object(machine_loop.starting, "enter_running")
    mocker.patch.object(machine_loop.running, "run", side_effect=QuitRequested())
    ctx = make_ctx(tmp_path)

    assert machine_loop.run_loop(ctx, setup) == 0

    assert seen == [ld.SETUP, "manager"]
    enter.assert_called_once_with(ctx, None)
    ctx.on_ready.assert_called_once()
    parts.cascade.cascade_quit.assert_called_once()


def test_an_unexpected_error_still_stops_the_children(parts, mocker, tmp_path):
    """FR-6 (no orphans): any error stops the children and Caddy, then is raised."""
    mocker.patch.object(machine_loop, "initial_decision", return_value=ld.Decision(ld.STARTING, mode="manager"))
    mocker.patch.object(machine_loop.starting, "start_mode", side_effect=RuntimeError("bug"))
    ctx = make_ctx(tmp_path, manager=FakeProc())

    with pytest.raises(RuntimeError, match="bug"):
        machine_loop.run_loop(ctx)

    parts.cascade.cascade_quit.assert_called_once()
    parts.stop_caddy.assert_called_once()
    ctx.on_ready.assert_not_called()


def test_stop_all_stops_worker_then_manager_or_setup_then_tray(parts, tmp_path):
    """FR-21: STOPPING stops the worker, the manager (or setup mode) and the tray in that
    order with the second-quit check, then Caddy, and deletes the token file."""
    worker, setup, tray = FakeProc(1), FakeProc(2), FakeProc(3)
    ctx = make_ctx(tmp_path, worker=worker, setup_proc=setup, tray=tray, token="t")

    machine_loop.stop_all(ctx)

    args, kwargs = parts.cascade.cascade_quit.call_args
    assert args == (worker, setup, tray) and kwargs["second_quit_check"] == ctx.second_quit_check
    parts.stop_caddy.assert_called_once()
    parts.state.write_launcher_state.assert_called_once_with(ld.STOPPING)
    parts.state.delete_token.assert_called_once()
    assert (ctx.worker, ctx.setup_proc, ctx.token) == (None, None, None)


def test_failed_closes_setup_and_waits_for_quit(parts, tmp_path):
    """Owner Q2/Q3: FAILED stops Caddy and the broadcaster (nothing open or advertised on
    the LAN), deletes the token file, shows the reason in the tray and the splash, retries
    nothing, takes only tray requests made from now on, and leaves on Quit."""
    parts.markers.return_value = (None, None, "manager")  # a restart is not a retry
    ctx = make_ctx(tmp_path)
    params = broadcasting(ctx)

    with pytest.raises(QuitRequested):
        machine_loop.failed(ctx, "db down")

    assert not params.exists() and supervision._get_broadcaster_supervisor_for_tests() is None
    assert ctx.markers_since is not None
    parts.stop_caddy.assert_called_once()
    parts.state.delete_token.assert_called_once()
    parts.state.write_launcher_state.assert_called_once_with(ld.FAILED, reason="db down")
    ctx.on_failed.assert_called_once_with("db down", "")
    parts.reset.assert_not_called()


def test_a_reset_from_failed_opens_setup(parts, tmp_path):
    """FR-RS-1: the tray's Reset also works from FAILED."""
    parts.markers.return_value = (None, "all", None)
    ctx = make_ctx(tmp_path)

    assert machine_loop.failed(ctx, "db down") == ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH)
    parts.reset.assert_called_once_with(ctx)
