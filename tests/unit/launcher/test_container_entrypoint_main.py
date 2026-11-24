# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :func:`launcher.container_entrypoint.main` (#296, #301).

Split from ``test_container_entrypoint.py`` to keep each file under the
300-line limit. Every collaborator of ``main`` is a fake; nothing is
spawned.
"""

from __future__ import annotations

import logging
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
    monkeypatch.setattr(ce, "data_dir_problem", lambda: None)
    monkeypatch.setattr(ce, "setup_marker_problem", lambda _d: None)
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


@pytest.mark.parametrize("code", [2, 3])
def test_main_data_dir_problem_exits_before_anything(main_env, monkeypatch, caplog, code):
    caddy, spawn, _ = main_env
    built = MagicMock()
    monkeypatch.setattr(ce, "build_supervisor", built)
    monkeypatch.setattr(ce, "data_dir_problem", lambda: (code, "data folder message"))

    assert ce.main([], stop=ce.StopFlag()) == code
    assert "data folder message" in caplog.text
    built.assert_not_called()
    caddy.start.assert_not_called()
    spawn.assert_not_called()


# -- setup-complete marker (issue #301) -------------------------------------

DATA_ENV = "SETHLANS_MANAGER_DATA_DIR"


def test_main_marker_failure_exits_4_before_anything(main_env, monkeypatch, caplog):
    caddy, spawn, _ = main_env
    built = MagicMock()
    monkeypatch.setattr(ce, "build_supervisor", built)
    monkeypatch.setattr(ce, "setup_marker_problem", lambda _d: (4, "marker message"))

    with caplog.at_level(logging.ERROR, logger="sethlans.container"):
        assert ce.main([], stop=ce.StopFlag()) == 4
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == ["marker message"]
    built.assert_not_called()
    caddy.start.assert_not_called()
    spawn.assert_not_called()


def test_main_data_dir_failure_skips_the_marker(main_env, monkeypatch):
    marker = MagicMock(return_value=None)
    monkeypatch.setattr(ce, "setup_marker_problem", marker)
    monkeypatch.setattr(ce, "data_dir_problem", lambda: (3, "not writable"))

    assert ce.main([], stop=ce.StopFlag()) == 3
    marker.assert_not_called()


def test_main_writes_marker_before_caddy_and_manager(main_env, monkeypatch):
    caddy, spawn, _ = main_env
    order = []
    monkeypatch.setattr(ce, "data_dir_problem", lambda: order.append("data dir"))
    monkeypatch.setattr(ce, "setup_marker_problem", lambda _d: order.append("marker"))
    caddy.start.side_effect = lambda: order.append("caddy")
    spawn.side_effect = lambda _a: order.append("manager") or MagicMock()

    assert ce.main([], stop=ce.StopFlag()) == 0
    assert order == ["data dir", "marker", "caddy", "manager"]


@pytest.mark.parametrize("use_env", [True, False])
def test_main_marker_goes_to_the_resolved_data_dir(main_env, monkeypatch, tmp_path, use_env):
    if use_env:
        monkeypatch.setenv(DATA_ENV, str(tmp_path))
    else:
        monkeypatch.delenv(DATA_ENV, raising=False)
    seen = []
    monkeypatch.setattr(ce, "setup_marker_problem", lambda d: seen.append(d))

    assert ce.main([], stop=ce.StopFlag()) == 0
    assert seen == [tmp_path if use_env else ce.MANAGER_DIR]


def test_entrypoint_and_heartbeat_resolve_the_same_folder(monkeypatch, tmp_path, settings):
    from workers.services.data_dir import get_manager_data_dir

    monkeypatch.setenv(DATA_ENV, str(tmp_path))
    assert ce.source_data_dir(ce.MANAGER_DIR) == get_manager_data_dir() == tmp_path


@pytest.fixture
def heartbeat_cache():
    from workers.views import heartbeat
    heartbeat._reset_setup_complete_cache()
    yield heartbeat
    heartbeat._reset_setup_complete_cache()


def test_main_marker_makes_the_heartbeat_report_setup_complete(
    main_env, monkeypatch, tmp_path, settings, heartbeat_cache,
):
    # The real marker step, not a fake: what main() writes is what the
    # heartbeat reads, so enrolled workers take work.
    from launcher.container_setup_marker import setup_marker_problem
    from workers.services.sentinel import SENTINEL_FILENAME

    data, code = tmp_path / "data", tmp_path / "code"
    code.mkdir()
    # A regression to MANAGER_DIR must not leave a marker in the repo.
    monkeypatch.setattr(ce, "MANAGER_DIR", code)
    monkeypatch.setenv(DATA_ENV, str(data))
    monkeypatch.setattr(ce, "setup_marker_problem", setup_marker_problem)
    assert heartbeat_cache._is_setup_complete_cached() is False

    assert ce.main([], stop=ce.StopFlag()) == 0
    assert heartbeat_cache._is_setup_complete_cached() is True
    assert (data / SENTINEL_FILENAME).is_file()
    assert list(code.iterdir()) == []
