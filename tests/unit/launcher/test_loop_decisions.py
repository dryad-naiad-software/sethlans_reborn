# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.loop_decisions` (D8, FR-LN-1, -2, -6; owner Q2, Q3, Q5)."""

from __future__ import annotations

import pytest

from launcher import loop_decisions as ld
from launcher.loop_decisions import Decision

REASON = "First start failed: database error"


@pytest.mark.parametrize("exit_code, finished, expected", [
    pytest.param(10, "manager_worker", Decision(ld.STARTING, mode="manager_worker"), id="saved"),
    pytest.param(1, "worker", Decision(ld.STARTING, mode="worker"), id="file-decides-over-exit-code"),
    pytest.param(2, None, Decision(ld.FAILED, reason=REASON), id="start-failure-stays-stopped"),
    pytest.param(10, None, Decision(
        ld.FAILED, reason="Setup reported the settings saved, but the settings file is not finished",
    ), id="saved-but-file-not-finished"),
])
def test_decide_after_setup(exit_code, finished, expected):
    """D8: after setup mode ends the settings file decides; not finished means
    stopped with a tray message (spec launcher-loop sketch)."""
    assert ld.decide_after_setup(exit_code, finished, REASON) == expected


SAME = Decision(ld.SETUP, token=ld.TOKEN_SAME, reason=REASON, last_failure=REASON)
FRESH = Decision(ld.SETUP, token=ld.TOKEN_FRESH, reason=REASON, last_failure=REASON)
FAILED = Decision(ld.FAILED, reason=REASON)


@pytest.mark.parametrize("exit_code, right_after_setup, expected", [
    pytest.param(11, True, SAME, id="after-setup-11-same-token"),
    pytest.param(12, True, SAME, id="after-setup-12-same-token"),
    pytest.param(13, True, SAME, id="after-setup-13-same-token"),
    pytest.param(1, True, SAME, id="after-setup-other-same-token"),
    pytest.param(12, False, FRESH, id="normal-start-12-fresh-token"),
    pytest.param(11, False, FAILED, id="normal-start-11-failed-setup-closed"),
    pytest.param(13, False, FAILED, id="normal-start-13-failed-setup-closed"),
    pytest.param(1, False, FAILED, id="normal-start-other-failed"),
])
def test_decide_after_manager_exit(exit_code, right_after_setup, expected):
    """FR-LN-6 (same token right after setup) and owner Q2/Q3: on a normal start
    12 re-opens setup with a fresh token; 11 and 13 fail in the tray with setup
    closed and no retry."""
    assert ld.decide_after_manager_exit(exit_code, right_after_setup=right_after_setup, reason=REASON) == expected


@pytest.mark.parametrize("right_after_setup, expected", [
    pytest.param(True, SAME, id="after-setup-same-token"),
    pytest.param(False, FAILED, id="normal-start-failed"),
])
def test_decide_after_worker_exit(right_after_setup, expected):
    """FR-LN-6 and owner Q3 for worker-only mode: the worker is what the mode stands on;
    it has no "no admin" ending, so a normal start never re-opens setup."""
    assert ld.decide_after_worker_exit(right_after_setup=right_after_setup, reason=REASON) == expected


@pytest.mark.parametrize("entering, setup_kind, headless, no_browser, expected", [
    pytest.param(ld.SETUP, None, False, False, True, id="setup-opens"),
    pytest.param(ld.SETUP, None, True, False, False, id="headless-never"),
    pytest.param(ld.SETUP, None, False, True, False, id="no-browser-never"),
    pytest.param(ld.RUNNING, ld.SETUP_BY_BROWSER, False, False, False, id="running-after-browser-setup"),
    pytest.param(ld.RUNNING, ld.SETUP_UNATTENDED, False, False, True, id="running-after-unattended-setup"),
    pytest.param(ld.RUNNING, None, False, False, False, id="running-normal-start"),
])
def test_should_open_browser(entering, setup_kind, headless, no_browser, expected):
    """FR-LN-2 with owner Q5: SETUP opens the setup page; RUNNING opens the
    login page only after an unattended setup."""
    assert ld.should_open_browser(entering, setup_kind=setup_kind, headless=headless,
                                  no_browser=no_browser) is expected


@pytest.mark.parametrize("pending, expected", [
    pytest.param(("all", "all", "manager"), (ld.IPC_QUIT, "all"), id="quit-beats-all"),
    pytest.param((None, "all", "manager"), (ld.IPC_RESET, "all"), id="reset-beats-restart"),
    pytest.param((None, None, "manager"), (ld.IPC_RESTART, "manager"), id="restart-alone"),
    pytest.param((None, None, None), None, id="nothing"),
])
def test_ipc_precedence(pending, expected):
    """Chunk 6 plan (running.py): quit beats reset, which beats restart."""
    assert ld.ipc_precedence(*pending) == expected
