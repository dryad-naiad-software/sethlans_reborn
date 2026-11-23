# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :func:`launcher.container_entrypoint.main` (issue #296).

Split from ``test_container_entrypoint.py`` to keep each file under the
300-line limit. Every collaborator of ``main`` is a fake; nothing is
spawned.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from launcher import container_entrypoint as ce
from shared.cert_utils import CertificateError


def _fake_caddy():
    caddy = MagicMock()
    caddy.error_event = threading.Event()
    return caddy


# -- main -----------------------------------------------------------------

@pytest.fixture
def main_env(monkeypatch):
    """Patch everything main() touches outside this module."""
    monkeypatch.setattr(ce.logging, "basicConfig", lambda **_k: None)
    monkeypatch.setattr(ce, "install_signal_handlers", lambda _e: None)
    caddy = _fake_caddy()
    monkeypatch.setattr(ce, "build_supervisor", lambda: caddy)
    spawn = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(ce, "spawn_manager", spawn)
    supervise = MagicMock(return_value=0)
    monkeypatch.setattr(ce, "supervise", supervise)
    return caddy, spawn, supervise


def test_main_starts_caddy_then_manager_with_args(main_env):
    caddy, spawn, supervise = main_env

    assert ce.main(["--dev"], stop=ce.StopFlag()) == 0
    caddy.start.assert_called_once_with()
    spawn.assert_called_once_with(["--dev"])
    supervise.assert_called_once()


@pytest.mark.parametrize("error", [ValueError("bad port"), CertificateError("bad cert")])
def test_main_config_error_exits_2_without_starting(main_env, monkeypatch, error):
    caddy, spawn, _ = main_env

    def _raise():
        raise error

    monkeypatch.setattr(ce, "build_supervisor", _raise)
    assert ce.main([], stop=ce.StopFlag()) == ce.EXIT_CONFIG_ERROR
    caddy.start.assert_not_called()
    spawn.assert_not_called()


def test_main_caddy_start_failure_exits_1_without_manager(main_env):
    caddy, spawn, _ = main_env
    caddy.start.side_effect = RuntimeError("Caddy binary not found")

    assert ce.main([], stop=ce.StopFlag()) == 1
    spawn.assert_not_called()


def test_main_stop_signal_during_startup_skips_manager(main_env, monkeypatch):
    caddy, spawn, _ = main_env
    monkeypatch.setattr(ce, "install_signal_handlers", lambda s: setattr(s, "requested", True))

    assert ce.main([], stop=ce.StopFlag()) == 0
    spawn.assert_not_called()
    caddy.stop.assert_called_once()


def test_main_manager_spawn_failure_stops_caddy(main_env):
    caddy, spawn, _ = main_env
    spawn.side_effect = OSError("no python")

    assert ce.main([], stop=ce.StopFlag()) == 1
    caddy.stop.assert_called_once()
