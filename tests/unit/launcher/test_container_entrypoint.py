# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.container_entrypoint` (issue #296).

The manager image starts Caddy in front of the manager through this
entrypoint. No Caddy or manager process is spawned here: the Caddy
supervisor and the manager child are fakes, and the real-image boot is
covered by ``tools/manager_boot_smoke.sh`` in CI.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from launcher import container_entrypoint as ce
from shared.cert_utils import generate_self_signed_cert

_PORT_ENV = (
    "SETHLANS_MANAGER_PORT",
    "SETHLANS_TLS_CERT_FILE",
    "SETHLANS_TLS_KEY_FILE",
    "SETHLANS_TLS_DATA_DIR",
    "SETHLANS_MANAGER_CADDYFILE_PATH",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No inherited port / TLS / Caddyfile env; the settings file in tmp_path."""
    for name in _PORT_ENV:
        monkeypatch.delenv(name, raising=False)
    ini = tmp_path / "sethlans.ini"
    monkeypatch.setattr(ce, "get_config_file_path", lambda: ini)
    monkeypatch.setattr(ce, "get_waitress_public_port", lambda _p: 18090)
    monkeypatch.setattr(ce, "get_waitress_internal_port", lambda _p: 18088)
    return ini


def _fake_caddy():
    caddy = MagicMock()
    caddy.error_event = threading.Event()
    return caddy


def _fake_manager(poll_values):
    manager = MagicMock()
    manager.poll.side_effect = list(poll_values) + [None] * 1000
    return manager


# -- resolve_public_port ------------------------------------------------

def test_public_port_defaults_to_8080(clean_env):
    assert ce.resolve_public_port(clean_env) == 8080


def test_public_port_from_manager_ini(clean_env):
    clean_env.write_text("[server]\nport = 9443\n", encoding="utf-8")
    assert ce.resolve_public_port(clean_env) == 9443


def test_public_port_env_beats_manager_ini(clean_env, monkeypatch):
    clean_env.write_text("[server]\nport = 9443\n", encoding="utf-8")
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "9000")
    assert ce.resolve_public_port(clean_env) == 9000


def test_public_port_not_a_number_is_an_error(clean_env, monkeypatch):
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "eighty")
    with pytest.raises(ValueError, match="eighty"):
        ce.resolve_public_port(clean_env)


# -- build_supervisor ---------------------------------------------------

def test_build_supervisor_requires_static_caddyfile_env(clean_env):
    with pytest.raises(ValueError, match="SETHLANS_MANAGER_CADDYFILE_PATH"):
        ce.build_supervisor()


def test_build_supervisor_generates_cert_in_tls_data_dir(
    clean_env, monkeypatch, tmp_path,
):
    tls_dir = tmp_path / "data" / "tls"
    caddyfile = tmp_path / "Caddyfile"
    monkeypatch.setenv("SETHLANS_TLS_DATA_DIR", str(tls_dir))
    monkeypatch.setenv("SETHLANS_MANAGER_CADDYFILE_PATH", str(caddyfile))

    sv = ce.build_supervisor()

    assert (tls_dir / "cert.pem").is_file()
    assert (tls_dir / "key.pem").is_file()
    assert sv._caddyfile_path == caddyfile
    assert sv._caddyfile_path_env == "SETHLANS_MANAGER_CADDYFILE_PATH"
    kwargs = sv._template_kwargs
    assert kwargs["cert_path"] == tls_dir / "cert.pem"
    assert kwargs["key_path"] == tls_dir / "key.pem"
    assert kwargs["public_tls_port"] == 8080
    assert kwargs["loopback_plaintext_port"] == ce.CADDY_LOOPBACK_PORT
    assert kwargs["waitress_public_port"] == 18090
    assert kwargs["waitress_internal_port"] == 18088


def test_build_supervisor_uses_byo_cert(clean_env, monkeypatch, tmp_path):
    cert = tmp_path / "byo" / "server.crt"
    key = tmp_path / "byo" / "server.key"
    generate_self_signed_cert(cert, key)
    monkeypatch.setenv("SETHLANS_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("SETHLANS_TLS_KEY_FILE", str(key))
    monkeypatch.setenv("SETHLANS_TLS_DATA_DIR", str(tmp_path / "unused"))
    monkeypatch.setenv("SETHLANS_MANAGER_CADDYFILE_PATH", str(tmp_path / "C"))

    kwargs = ce.build_supervisor()._template_kwargs

    assert kwargs["cert_path"] == cert
    assert kwargs["key_path"] == key
    assert not (tmp_path / "unused").exists()


# -- supervise / stop_all -----------------------------------------------

@pytest.fixture
def fast_poll(monkeypatch):
    """Replace the loop's sleep with a recorder (no real waiting)."""
    sleeps = []
    monkeypatch.setattr(ce, "time", types.SimpleNamespace(sleep=sleeps.append))
    return sleeps


def test_stop_signal_stops_manager_then_caddy_and_exits_0(fast_poll):
    manager, caddy = _fake_manager([None]), _fake_caddy()
    stop = ce.StopFlag()
    stop.requested = True

    assert ce.supervise(manager, caddy, stop) == 0
    manager.terminate.assert_called_once_with()
    caddy.stop.assert_called_once_with(timeout=ce.CADDY_STOP_TIMEOUT)
    manager.wait.assert_called_once_with(timeout=ce.MANAGER_STOP_TIMEOUT)


def test_manager_exit_stops_caddy_and_passes_code_on(fast_poll):
    manager, caddy = _fake_manager([None, 3]), _fake_caddy()

    assert ce.supervise(manager, caddy, ce.StopFlag()) == 3
    caddy.stop.assert_called_once_with(timeout=ce.CADDY_STOP_TIMEOUT)
    manager.terminate.assert_not_called()


def test_manager_killed_by_signal_maps_to_shell_code(fast_poll):
    manager, caddy = _fake_manager([-9]), _fake_caddy()

    assert ce.supervise(manager, caddy, ce.StopFlag()) == 137
    caddy.stop.assert_called_once()


def test_caddy_failure_stops_manager_and_exits_1(fast_poll):
    manager, caddy = _fake_manager([None]), _fake_caddy()
    caddy.error_event.set()

    assert ce.supervise(manager, caddy, ce.StopFlag()) == 1
    manager.terminate.assert_called_once_with()
    caddy.stop.assert_called_once()


def test_signal_during_sleep_is_seen_on_next_pass(monkeypatch):
    manager, caddy = _fake_manager([None]), _fake_caddy()
    stop = ce.StopFlag()

    def _sleep(seconds):
        assert seconds == ce.POLL_SECONDS
        stop.requested = True  # what the signal handler does

    monkeypatch.setattr(ce, "time", types.SimpleNamespace(sleep=_sleep))
    assert ce.supervise(manager, caddy, stop) == 0
    manager.terminate.assert_called_once_with()


def test_stop_all_kills_manager_that_ignores_sigterm():
    manager, caddy = _fake_manager([None]), _fake_caddy()
    manager.wait.side_effect = [
        subprocess.TimeoutExpired("run_manager.py", ce.MANAGER_STOP_TIMEOUT),
        -9,
    ]

    ce.stop_all(manager, caddy)

    manager.terminate.assert_called_once_with()
    manager.kill.assert_called_once_with()
    assert manager.wait.call_count == 2


def test_stop_all_without_manager_only_stops_caddy():
    caddy = _fake_caddy()
    ce.stop_all(None, caddy)
    caddy.stop.assert_called_once_with(timeout=ce.CADDY_STOP_TIMEOUT)


@pytest.mark.parametrize("returncode, expected", [(0, 0), (4, 4), (-15, 143)])
def test_exit_code_for(returncode, expected):
    assert ce.exit_code_for(returncode) == expected


# -- signals and spawning -----------------------------------------------

def test_signal_handlers_set_the_stop_flag(monkeypatch):
    installed = {}
    monkeypatch.setattr(
        ce.signal, "signal",
        lambda signum, handler: installed.__setitem__(signum, handler),
    )
    stop = ce.StopFlag()

    ce.install_signal_handlers(stop)

    assert set(installed) == {ce.signal.SIGTERM, ce.signal.SIGINT}
    assert not stop.requested
    installed[ce.signal.SIGTERM](ce.signal.SIGTERM, None)
    assert stop.requested


def test_spawn_manager_runs_run_manager_with_args(monkeypatch):
    popen = MagicMock()
    monkeypatch.setattr(ce.subprocess, "Popen", popen)

    ce.spawn_manager(["--dev"])

    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args == ([sys.executable, str(ce.RUN_MANAGER_PY), "--dev"],)
    assert kwargs["cwd"] == str(ce.MANAGER_DIR)
    # No launcher broadcaster in the container: the manager must not
    # write broadcaster_params.json into its code dir (#296).
    assert kwargs["env"]["SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS"] == "0"
    assert kwargs["env"]["PATH"] == ce.os.environ["PATH"]
    assert ce.RUN_MANAGER_PY.is_file()
    assert ce.RUN_MANAGER_PY.parent.name == "manager"


def test_module_paths_match_source_layout():
    root = Path(ce.__file__).resolve().parent.parent
    assert ce.PROJECT_ROOT == root
    assert (root / "shared" / "__init__.py").is_file()
    assert (ce.MANAGER_DIR / "sethlans_manager").is_dir()
