# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.starting` (``STARTING`` and the entry into ``RUNNING``)."""

from __future__ import annotations

import pytest

from launcher import loop_decisions as ld
from launcher import starting
from tests.unit.launcher._loop_fakes import FakeProc, make_ctx

PORT = 9443


@pytest.fixture
def parts(mocker):
    """Collaborators mocked; manager and worker both start and answer."""
    m = mocker.MagicMock()
    m.order = []
    m.manager, m.worker = FakeProc(pid=1), FakeProc(pid=2)
    m.state = mocker.patch.object(starting, "setup_state")
    m.stop_broadcaster = mocker.patch.object(starting.supervision, "stop_broadcaster")
    mocker.patch.object(starting.caddy_wiring, "configured_public_port", return_value=PORT)
    m.caddy = mocker.patch.object(starting.caddy_wiring, "start_or_retarget",
                                  side_effect=lambda port: m.order.append("caddy"))
    m.stop_caddy = mocker.patch.object(starting.caddy_wiring, "stop_caddy")
    m.start_manager = mocker.patch.object(starting.components, "start_manager",
                                          side_effect=lambda log: m.order.append("manager") or m.manager)
    m.start_worker = mocker.patch.object(starting.components, "start_worker",
                                         side_effect=lambda: m.order.append("worker") or m.worker)
    m.health = mocker.patch.object(starting.health_probe, "wait_for_health",
                                   side_effect=lambda url, proc, timeout: m.order.append(f"health:{proc.pid}") or True)
    mocker.patch.object(starting, "worker_health_port", return_value=8081)
    m.browser = mocker.patch.object(starting.browser_launch, "open_browser")
    mocker.patch.object(starting.browser_launch, "is_headless", return_value=False)
    return m


def test_manager_worker_starts_the_worker_after_the_manager_answers(parts, tmp_path):
    """FR-LN-1, FR-FS-3, D3: Caddy on [server] port, the manager (its stderr in
    manager-start.log), its health, and only then the worker. Entering STARTING deletes the
    token file (security review L1) and stops the old broadcaster."""
    ctx = make_ctx(tmp_path)

    nxt = starting.start_mode(ctx, "manager_worker")

    assert nxt == ld.Decision(ld.RUNNING, mode="manager_worker")
    assert parts.order == ["caddy", "manager", "health:1", "worker", "health:2"]
    parts.start_manager.assert_called_once_with(ctx.manager_log)
    assert parts.health.call_args_list[0].args[0] == f"https://127.0.0.1:{PORT}/api/health/"
    assert parts.health.call_args_list[0].kwargs["timeout"] == starting.HEALTH_TIMEOUT
    assert (ctx.manager, ctx.worker, ctx.port) == (parts.manager, parts.worker, PORT)
    parts.state.write_launcher_state.assert_called_once_with(ld.STARTING)
    parts.state.delete_token.assert_called_once_with()
    parts.stop_broadcaster.assert_called_once_with(ctx.params_dir)  # before the manager writes its own


def test_worker_mode_stops_caddy_and_starts_only_the_worker(parts, tmp_path):
    """D3: in worker mode the manager's Caddy stops and only the worker runs."""
    ctx = make_ctx(tmp_path)

    assert starting.start_mode(ctx, "worker").state == ld.RUNNING

    parts.stop_caddy.assert_called_once()
    parts.start_manager.assert_not_called()
    assert parts.order == ["worker", "health:2"] and ctx.port is None


@pytest.mark.parametrize("setup_kind, expected", [
    pytest.param(ld.SETUP_BY_BROWSER, ld.Decision(ld.SETUP, token=ld.TOKEN_SAME, reason="db", last_failure="db"),
                 id="right-after-setup"),
    pytest.param(None, ld.Decision(ld.FAILED, reason="db"), id="normal-start"),
])
def test_a_manager_that_ends_is_decided_with_its_reason(parts, mocker, tmp_path, setup_kind, expected):
    """FR-LN-6, owner Q2/Q3: the manager's end goes to decide_after_manager_exit with its
    exit code and the reason from manager-start.log."""
    parts.health.side_effect = lambda url, proc, timeout: proc.end(11) or False
    reason = mocker.patch.object(starting.components, "failure_reason", return_value="db")
    ctx = make_ctx(tmp_path, setup_kind=setup_kind)

    assert starting.start_mode(ctx, "manager") == expected
    reason.assert_called_once_with(ctx.manager_log, 11)
    assert ctx.manager is None
    assert parts.stop_broadcaster.call_count == 2  # before the start, and after the failure
    parts.start_worker.assert_not_called()


def test_a_manager_that_never_answers_is_stopped(parts, tmp_path):
    """FR-LN-1: health waits 180 s while the manager lives; then it is stopped and fails."""
    parts.health.side_effect = lambda url, proc, timeout: False

    nxt = starting.start_mode(make_ctx(tmp_path), "manager")

    assert nxt == ld.Decision(ld.FAILED, reason="The manager did not answer within 180 s")
    assert parts.manager.terminated


def test_a_held_start_log_is_a_clear_failure(parts, tmp_path):
    """6a review gate: on Windows an earlier manager holding manager-start.log makes its
    replacement fail; that is a clear failure, never a hang or a raise."""
    parts.start_manager.side_effect = PermissionError("in use")

    nxt = starting.start_mode(make_ctx(tmp_path), "manager")

    assert nxt.state == ld.FAILED
    assert nxt.reason == ("The manager could not start: manager-start.log could not be replaced "
                          "(an earlier manager may still hold it)")


@pytest.mark.parametrize("mode, expected", [
    pytest.param("worker", ld.Decision(ld.FAILED, reason="The worker stopped with exit code 4"), id="worker-mode"),
    pytest.param("manager_worker", ld.Decision(ld.RUNNING, mode="manager_worker",
                                               reason="The worker stopped with exit code 4"), id="manager-runs-on"),
])
def test_a_worker_that_ends(parts, tmp_path, mode, expected):
    """FR-LN-1: in worker mode the worker's end fails the start; in manager_worker the
    manager runs on and the worker's end is reported."""
    def health(url, proc, timeout):
        if proc is parts.worker:
            proc.end(4)
            return False
        return True
    parts.health.side_effect = health

    assert starting.start_mode(make_ctx(tmp_path), mode) == expected


@pytest.mark.parametrize("setup_kind, print_url, opens", [
    pytest.param(ld.SETUP_BY_BROWSER, False, False, id="after-browser-setup"),
    pytest.param(ld.SETUP_UNATTENDED, False, True, id="after-unattended-setup"),
    pytest.param(ld.SETUP_UNATTENDED, True, False, id="print-url-prints-instead"),
])
def test_enter_running(parts, tmp_path, capsys, setup_kind, print_url, opens):
    """FR-LN-2 with owner Q5, FR-LN-6: RUNNING clears last_failure, tells the tray, dismisses
    the splash, opens the dashboard only after an unattended setup (--print-url prints its
    address instead), and forgets the token."""
    ctx = make_ctx(tmp_path, setup_kind=setup_kind, token="t", port=PORT, mode="manager", print_url=print_url)

    starting.enter_running(ctx, None)

    assert (f"https://localhost:{PORT}/" in capsys.readouterr().out) is print_url
    parts.state.clear_last_failure.assert_called_once()
    parts.state.write_launcher_state.assert_called_once_with(ld.RUNNING, port=PORT, reason=None)
    ctx.on_ready.assert_called_once()
    assert ctx.markers_since is not None
    assert parts.browser.called is opens
    assert (ctx.setup_kind, ctx.token) == (None, None)
