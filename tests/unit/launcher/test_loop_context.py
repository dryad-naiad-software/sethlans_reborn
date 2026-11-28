# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.loop_context` and ``broadcaster_params_dir``."""

from __future__ import annotations

import pytest

from launcher import loop_context, supervision
from launcher.broadcaster_supervisor import broadcaster_params_dir
from launcher.health_probe import QuitRequested
from launcher.tray_ipc import MARKER_QUIT
from shared import frozen_paths
from tests.unit.launcher._loop_fakes import FakeProc, make_ctx
from tests.unit.launcher._marker_helpers import write_marker


@pytest.mark.parametrize("env, expected", [
    pytest.param({}, 8081, id="default"),
    pytest.param({"SETHLANS_WORKER_UI_PORT": "9081"}, 9081, id="ui-port"),
    pytest.param({"SETHLANS_WORKER_UI_PORT": "9081", "SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT": "9443"}, 9443,
                 id="caddy-port-wins"),
    pytest.param({"SETHLANS_WORKER_UI_PORT": "x"}, ValueError, id="bad-value-refused"),
])
def test_worker_health_port(monkeypatch, env, expected):
    """FR-LN-1 (health on the worker's own port; 8081 by default): the launcher reads the
    worker's own variables, in the worker's order; a bad value fails loudly."""
    for name in loop_context.WORKER_PORT_ENVS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if expected is ValueError:
        with pytest.raises(ValueError):
            loop_context.worker_health_port()
    else:
        assert loop_context.worker_health_port() == expected


def test_wait_for_exit_returns_the_code_or_raises_on_quit():
    """Issue #163: every wait of the loop gives way to a quit request."""
    assert loop_context.wait_for_exit(FakeProc(code=10)) == 10
    supervision.request_quit("all")
    with pytest.raises(QuitRequested):
        loop_context.wait_for_exit(FakeProc(), poll=0)


def test_second_quit_check_needs_a_first_quit(tmp_path):
    """FR-21, concurrency review #3: with no quit pending a quit marker is a first click
    (left for the poll thread), not a second one."""
    write_marker(tmp_path / MARKER_QUIT, target="all")
    assert make_ctx(tmp_path).second_quit_check() is False
    assert (tmp_path / MARKER_QUIT).exists()


def test_second_quit_check_reads_the_marker_and_makes_the_quit_full(tmp_path):
    """FR-21: a second Quit during a cascade kills at once, and is a full quit even when
    the first was Quit Manager."""
    supervision.request_quit("manager")
    ctx = make_ctx(tmp_path)
    assert ctx.second_quit_check() is False
    write_marker(tmp_path / MARKER_QUIT, target="all")
    assert ctx.second_quit_check() is True
    assert supervision.quit_target() == "all"


def test_secrets_stay_out_of_the_repr(tmp_path):
    """Security review L2: the IPC secret and the setup token never reach a log via repr."""
    ctx = make_ctx(tmp_path, token="the-setup-token")
    assert ctx.secret not in repr(ctx) and "the-setup-token" not in repr(ctx)


@pytest.mark.parametrize("env, expected", [
    pytest.param(False, lambda tmp: frozen_paths.get_manager_dir(), id="source-default"),
    pytest.param(True, lambda tmp: tmp / "m", id="manager-data-dir"),
])
def test_broadcaster_params_dir_is_where_the_manager_writes(monkeypatch, tmp_path, env, expected):
    """FR-RS-1: Reset deletes the params file the manager wrote (``runtime_init``'s folder)."""
    if env:
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(tmp_path / "m"))
    else:
        monkeypatch.delenv("SETHLANS_MANAGER_DATA_DIR", raising=False)
    assert broadcaster_params_dir() == expected(tmp_path)
