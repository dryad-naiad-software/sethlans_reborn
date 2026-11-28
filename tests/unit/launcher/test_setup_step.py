# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.setup_step` (the ``SETUP`` state; FR-LN-1, -3, -6, FR-SEC-1)."""

from __future__ import annotations

from unittest.mock import call

import pytest

from launcher import loop_decisions as ld
from launcher import setup_step
from launcher.health_probe import QuitRequested
from tests.unit.launcher._loop_fakes import FakeProc, make_ctx

PORT = 8083


@pytest.fixture
def parts(mocker):
    """Every collaborator of ``enter_setup`` mocked; setup mode answers and then exits 10."""
    proc = FakeProc(code=None)
    m = mocker.MagicMock()
    m.proc = proc
    m.state = mocker.patch.object(setup_step, "setup_state")
    m.state.write_fresh_token.return_value = "fresh-token"
    m.stop_broadcaster = mocker.patch.object(setup_step.supervision, "stop_broadcaster")
    m.choose = mocker.patch.object(setup_step.setup_port, "choose_setup_port", return_value=PORT)
    m.held = mocker.patch.object(setup_step.caddy_wiring, "running_public_port", return_value=8080)
    m.caddy = mocker.patch.object(setup_step.caddy_wiring, "start_or_retarget")
    m.start = mocker.patch.object(setup_step.components, "start_setup_mode", return_value=proc)
    m.banner = mocker.patch.object(setup_step.browser_launch, "print_setup_banner")
    m.headless = mocker.patch.object(setup_step.browser_launch, "is_headless", return_value=False)
    m.browser = mocker.patch.object(setup_step.browser_launch, "open_browser")
    m.health = mocker.patch.object(setup_step.health_probe, "wait_for_health", return_value=True)
    m.finished = mocker.patch.object(setup_step.settings_file, "setup_finished", return_value="manager")
    m.reason = mocker.patch.object(setup_step.components, "failure_reason", return_value="why")

    def exit_with(code):
        def _wait(p, *a, **k):
            p.end(code)
            return code
        mocker.patch.object(setup_step, "wait_for_exit", side_effect=_wait)
    m.exit_with = exit_with
    exit_with(10)
    return m


def test_saved_setup_starts_the_mode_and_deletes_the_token_file(parts, tmp_path):
    """D3, FR-LN-3, FR-SEC-1, owner Q5: Caddy serves the chosen setup port (our running one
    counts as free); the tray sees a neutral state first and SETUP with the port only once
    setup answers; no broadcaster runs in setup; the page opens; a saved
    setup deletes the token file and the loop keeps the token for FR-LN-6."""
    ctx = make_ctx(tmp_path)

    nxt = setup_step.enter_setup(ctx, ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH))

    assert nxt == ld.Decision(ld.STARTING, mode="manager")
    parts.choose.assert_called_once_with(held_by_us=8080)
    parts.caddy.assert_called_once_with(PORT)
    parts.start.assert_called_once_with(PORT, ctx.setup_log)
    parts.banner.assert_called_once_with(PORT, "/setup/", "fresh-token")
    assert parts.state.write_launcher_state.call_args_list == [
        call(ld.STARTING), call(ld.SETUP, port=PORT, reason=None)]
    parts.stop_broadcaster.assert_called_once_with(ctx.params_dir)
    parts.browser.assert_called_once_with(PORT, False, False, "/setup/")
    ctx.on_ready.assert_called_once()
    assert (ctx.token, ctx.setup_kind, ctx.setup_proc) == ("fresh-token", ld.SETUP_BY_BROWSER, None)
    parts.state.clear_last_failure.assert_called_once()
    parts.state.delete_token.assert_called_once()


def test_after_a_failed_first_start_the_same_token_and_reason(parts, tmp_path):
    """FR-LN-6: the same token is written again (the open page keeps working) and the
    reason goes to last_failure and the tray."""
    ctx = make_ctx(tmp_path, token="kept-token", setup_kind=ld.SETUP_BY_BROWSER)
    decision = ld.Decision(ld.SETUP, token=ld.TOKEN_SAME, reason="db down", last_failure="db down")

    setup_step.enter_setup(ctx, decision)

    parts.state.rewrite_token.assert_called_once_with("kept-token")
    parts.state.write_fresh_token.assert_not_called()
    parts.state.write_last_failure.assert_called_once_with("db down")
    assert parts.state.write_launcher_state.call_args == call(ld.SETUP, port=PORT, reason="db down")


@pytest.mark.parametrize("broken", ["choose", "caddy", "start"])
def test_a_setup_that_cannot_start_fails_with_its_reason(parts, tmp_path, broken):
    """FR-LN-3 (no free port is a clear error), D3: a port, Caddy or start failure is FAILED."""
    getattr(parts, broken).side_effect = OSError("nope")

    nxt = setup_step.enter_setup(make_ctx(tmp_path), ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH))

    assert nxt == ld.Decision(ld.FAILED, reason="Setup could not start: nope")
    parts.state.write_launcher_state.assert_called_once_with(ld.STARTING)


def test_setup_that_never_answers_is_stopped(parts, tmp_path):
    """FR-LN-1: a live setup mode that does not answer in time is stopped, not left half-open."""
    parts.health.return_value = False
    ctx = make_ctx(tmp_path)

    nxt = setup_step.enter_setup(ctx, ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH))

    assert nxt.state == ld.FAILED and "did not answer" in nxt.reason
    assert parts.proc.terminated and ctx.setup_proc is None


def test_setup_that_ends_unfinished_fails_with_its_reason(parts, tmp_path):
    """D8: setup mode that ends before answering, with the file not finished, is FAILED
    with the reason from its stderr (the loop's FAILED then closes the token file)."""
    parts.health.return_value = False
    parts.proc.end(2)
    parts.exit_with(2)
    parts.finished.return_value = None

    nxt = setup_step.enter_setup(make_ctx(tmp_path), ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH))

    assert nxt == ld.Decision(ld.FAILED, reason="why")
    parts.reason.assert_called_once()
    assert parts.reason.call_args.args[1:] == (2, "Setup")
    parts.state.delete_token.assert_not_called()


def test_quit_while_setup_runs_is_raised(parts, tmp_path):
    """Issue #163: a tray Quit during setup leaves through the loop's quit path."""
    parts.health.side_effect = QuitRequested()
    with pytest.raises(QuitRequested):
        setup_step.enter_setup(make_ctx(tmp_path), ld.Decision(ld.SETUP, token=ld.TOKEN_FRESH))
