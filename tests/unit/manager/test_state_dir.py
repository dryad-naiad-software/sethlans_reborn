# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every source-mode state location follows ``SETHLANS_MANAGER_DATA_DIR`` (#297).

The Docker image sets ``SETHLANS_MANAGER_DATA_DIR=/data`` so manager.ini,
the TLS cert, the setup marker, broadcaster params, logs, the secret key
and FFmpeg all land in the volume. These tests pin each reader to the one
switch in :mod:`sethlans_manager.state_dir`, and pin the paths that must
not change: a frozen (installed) manager, and a source run without the
variable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sethlans_manager import state_dir, tls_setup, waitress_config

ENV = "SETHLANS_MANAGER_DATA_DIR"
REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"


@pytest.fixture
def no_env(monkeypatch):
    for name in (ENV, "SETHLANS_TLS_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_env(no_env, monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv(ENV, str(data))
    return data


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Installed manager: tls_setup sees frozen mode and a per-user dir."""
    user_dir = tmp_path / "user-data"
    monkeypatch.setattr(tls_setup, "is_frozen", lambda: True)
    monkeypatch.setattr(tls_setup, "get_data_dir", lambda _c: user_dir)
    return user_dir


# -- the switch -----------------------------------------------------------

def test_default_without_env(no_env, tmp_path):
    assert state_dir.source_data_dir(tmp_path) == tmp_path


def test_env_wins(data_env, tmp_path):
    assert state_dir.source_data_dir(tmp_path / "code") == data_env


def test_relative_env_is_rejected(no_env, monkeypatch, tmp_path):
    monkeypatch.setenv(ENV, "relative/data")
    with pytest.raises(ValueError, match=ENV):
        state_dir.source_data_dir(tmp_path)


# -- manager.ini readers agree ------------------------------------------------

def test_ini_source_without_env_is_unchanged(no_env, tmp_path):
    assert tls_setup.get_config_file_path(tmp_path) == tmp_path / "manager.ini"
    assert waitress_config.resolve_ini_path(tmp_path) == tmp_path / "manager.ini"


def test_ini_source_with_env(data_env, tmp_path):
    assert tls_setup.get_config_file_path(tmp_path) == data_env / "manager.ini"
    assert waitress_config.resolve_ini_path(tmp_path) == data_env / "manager.ini"


def test_ini_frozen_is_unchanged(data_env, frozen, tmp_path):
    assert tls_setup.get_config_file_path(tmp_path) == frozen / "manager.ini"


# -- TLS dir --------------------------------------------------------------

def test_tls_prod_without_env_is_unchanged(no_env, tmp_path):
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path.parent) == tmp_path / "tls"


def test_tls_prod_with_env(data_env, tmp_path):
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path.parent) == data_env / "tls"


def test_tls_dev_is_unchanged(data_env, tmp_path):
    assert tls_setup.get_tls_dir(True, tmp_path, tmp_path) == tmp_path / "temp" / "dev-tls"


def test_tls_explicit_override_still_wins(data_env, monkeypatch, tmp_path):
    explicit = tmp_path / "explicit-tls"
    monkeypatch.setenv("SETHLANS_TLS_DATA_DIR", str(explicit))
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path) == explicit


def test_tls_frozen_is_unchanged(data_env, frozen, tmp_path):
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path) == frozen / "tls"


# -- Django-side readers (setup marker, summary, broadcaster params) ------

def test_marker_dir_follows_env(data_env, settings):
    from workers.services.data_dir import get_manager_data_dir
    assert get_manager_data_dir() == data_env


def test_marker_dir_without_env_is_base_dir(no_env, settings):
    from workers.services.data_dir import get_manager_data_dir
    assert get_manager_data_dir() == Path(settings.BASE_DIR)


def test_summary_dir_follows_env(data_env):
    from workers.views.manager_summary import _data_dir
    assert _data_dir() == data_env


def test_summary_dir_without_env_is_base_dir(no_env, settings):
    from workers.views.manager_summary import _data_dir
    assert _data_dir() == Path(settings.BASE_DIR)


def test_broadcaster_params_follow_env(data_env, monkeypatch):
    from sethlans_manager import runtime_init
    monkeypatch.delenv(runtime_init.PUBLISH_BROADCASTER_PARAMS_ENV, raising=False)
    data_env.mkdir(parents=True)
    runtime_init._publish_broadcaster_params()
    assert (data_env / "broadcaster_params.json").is_file()


# -- run_manager: enrollment config and the stale-[setup] sweep ------------

@pytest.mark.parametrize("use_env", [True, False])
def test_prepare_runtime_config_dir(no_env, monkeypatch, tmp_path, use_env):
    import run_manager as rm

    if use_env:
        monkeypatch.setenv(ENV, str(tmp_path))
    seen = []
    monkeypatch.setattr(rm, "is_frozen", lambda: False)
    monkeypatch.setattr("django.setup", lambda: None)
    monkeypatch.setattr("sethlans_manager.logging_config.configure", lambda: None)
    monkeypatch.setattr(rm, "get_enrollment_config", lambda d: seen.append(d) or {})
    monkeypatch.setattr(rm, "cleanup_stale_setup_section", seen.append)
    monkeypatch.setattr(rm, "initialize_runtime_state", lambda *_a: None)

    rm._prepare_runtime(None, "0.0.0.0", "8080")

    expected = tmp_path if use_env else rm.MANAGER_DIR
    assert seen == [expected, expected]


# -- module-level paths, in a fresh interpreter ----------------------------

_PROBE = """
import json, sys
sys.path[:0] = [{repo!r}, {manager!r}]
from sethlans_manager import config_loader, logging_config, tls_setup
from shared.frozen_paths import get_data_dir
from workers.services.parts_check.ffmpeg_download_pkg.locate import get_ffmpeg_dir
print(json.dumps({{
    "ini": str(config_loader._config_file_path),
    "tls_ini": str(tls_setup.get_config_file_path(config_loader.BASE_DIR)),
    "logs": str(logging_config.LOGS_DIR),
    "secret_key_dir": str(get_data_dir("manager")),
    "ffmpeg": str(get_ffmpeg_dir(get_data_dir("manager"))),
}}))
"""


def _probe(env_dir):
    env = {k: v for k, v in os.environ.items() if k not in (ENV, "SETHLANS_TLS_DATA_DIR")}
    if env_dir is not None:
        env[ENV] = str(env_dir)
    code = _PROBE.format(repo=str(REPO_ROOT), manager=str(MANAGER_DIR))
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {k: Path(v) for k, v in json.loads(out.stdout).items()}


def test_module_level_paths_follow_env(tmp_path):
    data = tmp_path / "data"
    paths = _probe(data)

    assert paths["ini"] == data / "manager.ini"
    assert paths["tls_ini"] == paths["ini"]
    assert paths["logs"] == data / "logs"
    assert paths["logs"].is_dir()
    assert paths["secret_key_dir"] == data
    assert paths["ffmpeg"].parent.parent == data / "bin"
