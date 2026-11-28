# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.running` (the ``RUNNING`` state; FR-LN-1, FR-RS-1)."""

from __future__ import annotations

import threading

import pytest

from launcher import loop_decisions as ld
from launcher import running, supervision
from launcher.health_probe import QuitRequested
from tests.unit.launcher._loop_fakes import FakeProc, broadcasting, make_ctx


@pytest.fixture
def parts(mocker):
    m = mocker.MagicMock()
    m.markers = mocker.patch("launcher.loop_context.LoopContext.consume_markers", return_value=(None, None, None))
    m.state = mocker.patch.object(running, "setup_state")
    m.cascade = mocker.patch.object(running, "cascade")
    m.reset = mocker.patch.object(running.reset_setup, "reset_running")
    m.restart = mocker.patch.object(running.restart_orchestrator, "handle_restart_request")
    m.start_manager = mocker.patch.object(running.components, "start_manager")
    # One poll, then a quit, so run() ends when nothing else does.
    mocker.patch.object(running.supervision, "wait_or_quit",
                        side_effect=lambda timeout: supervision.request_quit("all"))
    return m


def _ctx(tmp_path, **fields):
    return make_ctx(tmp_path, mode="manager_worker", port=8080, manager=FakeProc(pid=1), worker=FakeProc(pid=2),
                    **fields)


def test_quit_stops_everything(parts, tmp_path):
    """FR-21, issue #163: a tray Quit leaves RUNNING through the loop's quit path."""
    supervision.request_quit("all")
    with pytest.raises(QuitRequested):
        running.run(_ctx(tmp_path))


@pytest.mark.parametrize("manager_running", [True, False])
def test_quit_manager_while_a_worker_runs_never_stops_the_worker(parts, tmp_path, manager_running):
    """Tray Quit Manager (concurrency review #2): the manager stops if it still runs, the
    broadcaster stops with its params, and the worker and the launcher run on, also when the
    manager had already ended."""
    ctx = _ctx(tmp_path)
    manager = ctx.manager
    if not manager_running:
        ctx.manager = None
    params = broadcasting(ctx)
    supervision.request_quit("manager")

    with pytest.raises(QuitRequested):  # the fixture's later full quit ends the test
        running.run(ctx)

    assert parts.cascade.quit_manager_only.call_count == int(manager_running)
    if manager_running:
        parts.cascade.quit_manager_only.assert_called_once_with(manager)
    assert ctx.manager is None and ctx.worker is not None
    assert not params.exists() and supervision._get_broadcaster_supervisor_for_tests() is None
    parts.cascade.cascade_quit.assert_not_called()


def test_reset_resets_and_opens_setup_fresh(parts, tmp_path):
    """FR-RS-1: a tray Reset runs the reset and goes to SETUP with a fresh token."""
    parts.markers.return_value = (None, "all", "manager")
    ctx = _ctx(tmp_path)

    assert running.run(ctx) == ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH)
    parts.reset.assert_called_once_with(ctx)
    parts.restart.assert_not_called()


@pytest.mark.parametrize("new_manager, note", [
    pytest.param(FakeProc(pid=3), None, id="restarted"),
    pytest.param(None, "The manager could not be restarted; see launcher.log", id="could-not"),
])
def test_restart_manager(parts, tmp_path, new_manager, note):
    """Issue #345 (tray Restart reaches the loop), 6a gate: the restart sequence starts the
    new manager through components (only after the old one ended); a failure is reported."""
    parts.markers.side_effect = [(None, None, "manager"), (None, None, None)]
    parts.restart.return_value = new_manager
    ctx = _ctx(tmp_path)
    old = ctx.manager

    with pytest.raises(QuitRequested):
        running.run(ctx)

    proc, folder, respawn = parts.restart.call_args.args
    assert (proc, folder) == (old, tmp_path)
    respawn()
    parts.start_manager.assert_called_once_with(ctx.manager_log)
    assert ctx.manager is new_manager
    parts.state.write_launcher_state.assert_called_with(ld.RUNNING, port=8080, reason=note)


def test_restart_all_stops_both_and_starts_again(parts, tmp_path):
    """FR-LN-1: Restart with target all is a stop of both (worker first) and a new STARTING."""
    parts.markers.return_value = (None, None, "all")
    ctx = _ctx(tmp_path)
    worker, manager = ctx.worker, ctx.manager

    assert running.run(ctx) == ld.Decision(ld.STARTING, mode="manager_worker")
    assert parts.cascade.cascade_quit.call_args.args[:3] == (worker, manager, None)


def test_a_child_that_ends_is_reported_and_the_rest_runs_on(parts, mocker, tmp_path):
    """FR-LN-1: on_process_ended logs and tells the tray; the other child and the launcher
    keep running (no restart yet)."""
    reason = mocker.patch.object(running.components, "failure_reason", return_value="The worker stopped")
    ctx = _ctx(tmp_path)
    ctx.worker.end(3)
    manager = ctx.manager

    with pytest.raises(QuitRequested):
        running.run(ctx)

    reason.assert_called_once_with(None, 3, "The worker")
    parts.state.write_launcher_state.assert_called_once_with(ld.RUNNING, port=8080, reason="The worker stopped")
    assert ctx.worker is None and ctx.manager is manager


def test_a_signal_stops_the_running_loop(mocker, tmp_path):
    """Concurrency review #1: SIGTERM/SIGINT (the handler only sets a flag) ends RUNNING
    within a poll, through the loop's quit path."""
    mocker.patch("launcher.loop_context.LoopContext.consume_markers", return_value=(None, None, None))
    mocker.patch.object(running, "setup_state")
    ctx = _ctx(tmp_path)
    outcome = []

    def run():
        try:
            running.run(ctx)
        except QuitRequested:
            outcome.append("quit")
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    supervision._graceful_shutdown(15, None)
    thread.join(timeout=10)

    assert outcome == ["quit"]
