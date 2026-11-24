# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every source-mode state location follows ``SETHLANS_MANAGER_DATA_DIR`` (#297).

The Docker image sets ``SETHLANS_MANAGER_DATA_DIR=/data`` so the settings
file, the TLS cert, broadcaster params, logs, the database, the secret key
and FFmpeg all land in the volume. These tests pin each reader to the one
switch in :mod:`sethlans_manager.state_dir` (and the settings file to
:func:`shared.settings_file.settings_path`), and pin the paths that must
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

from sethlans_manager import state_dir, tls_setup
from shared import settings_file

ENV = "SETHLANS_MANAGER_DATA_DIR"
SETTINGS_ENV = ("SETHLANS_SETTINGS_FILE", "SETHLANS_SETTINGS_DIR")
REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"


@pytest.fixture
def no_env(monkeypatch):
    """No path env var at all, so the settings file follows the real order."""
    for name in (ENV, "SETHLANS_TLS_DATA_DIR", "SETHLANS_DATA_ROOT") + SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_env(no_env, monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv(ENV, str(data))
    return data


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Installed manager: tls_setup sees frozen mode and a per-user identity dir."""
    user_dir = tmp_path / "user-data"
    monkeypatch.setattr(tls_setup, "is_frozen", lambda: True)
    monkeypatch.setattr(tls_setup, "get_identity_dir", lambda _c: user_dir)
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


# -- the settings file ----------------------------------------------------

def test_ini_source_without_env(no_env):
    assert tls_setup.get_config_file_path() == MANAGER_DIR / "sethlans.ini"


def test_ini_source_with_env(data_env):
    assert tls_setup.get_config_file_path() == data_env / "sethlans.ini"


def test_ini_is_settings_path(data_env, monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere" / "sethlans.ini"
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(explicit))
    assert tls_setup.get_config_file_path() == explicit == settings_file.settings_path()


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


def test_tls_frozen_is_in_the_identity_dir(data_env, frozen, tmp_path):
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path) == frozen / "tls"


@pytest.fixture
def frozen_moved_data(no_env, monkeypatch, tmp_path):
    """Installed manager whose data folder was moved; returns the settings folder."""
    settings_dir = tmp_path / "settings"
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setenv("SETHLANS_DATA_ROOT", str(tmp_path / "bulk"))
    monkeypatch.setattr("shared.frozen_paths.is_frozen", lambda: True)
    monkeypatch.setattr(tls_setup, "is_frozen", lambda: True)
    monkeypatch.delenv("SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS", raising=False)
    return settings_dir


def test_tls_frozen_never_moves_with_the_data_folder(frozen_moved_data, tmp_path):
    assert tls_setup.get_tls_dir(False, tmp_path, tmp_path) == frozen_moved_data / "manager" / "tls"


def test_broadcaster_params_frozen_stay_in_the_identity_dir(frozen_moved_data):
    """The launcher reads them from ``<settings folder>/manager``."""
    from sethlans_manager import runtime_init
    (frozen_moved_data / "manager").mkdir(parents=True)
    runtime_init._publish_broadcaster_params()
    assert (frozen_moved_data / "manager" / "broadcaster_params.json").is_file()
    assert not (frozen_moved_data.parent / "bulk").exists()


# -- broadcaster params ---------------------------------------------------

def test_broadcaster_params_follow_env(data_env, monkeypatch):
    from sethlans_manager import runtime_init
    monkeypatch.delenv(runtime_init.PUBLISH_BROADCASTER_PARAMS_ENV, raising=False)
    data_env.mkdir(parents=True)
    runtime_init._publish_broadcaster_params()
    assert (data_env / "broadcaster_params.json").is_file()


# -- run_manager: enrollment config and the stale-[setup] sweep ------------

@pytest.mark.parametrize("use_env", [True, False])
def test_prepare_runtime_reads_the_settings_file(no_env, monkeypatch, tmp_path, use_env):
    import run_manager as rm

    if use_env:
        monkeypatch.setenv(ENV, str(tmp_path))
    seen = []
    monkeypatch.setattr(rm, "is_frozen", lambda: False)
    monkeypatch.setattr("django.setup", lambda: None)
    monkeypatch.setattr("sethlans_manager.logging_config.configure", lambda: None)
    monkeypatch.setattr(rm, "get_enrollment_config", lambda p: seen.append(p) or {})
    monkeypatch.setattr(rm, "cleanup_stale_setup_section", seen.append)
    monkeypatch.setattr(rm, "initialize_runtime_state", lambda *_a: None)

    rm._prepare_runtime(None, "0.0.0.0", "8080")

    expected = (tmp_path if use_env else MANAGER_DIR) / "sethlans.ini"
    assert seen == [expected, expected]


# -- module-level paths, in a fresh interpreter ----------------------------

_PROBE = """
import json, sys
sys.path[:0] = [{repo!r}, {manager!r}]
from sethlans_manager import config_loader, db_config, logging_config, tls_setup
from shared.frozen_paths import get_data_dir, get_identity_dir
from workers.services.parts_check.ffmpeg_download_pkg.locate import get_ffmpeg_dir
print(json.dumps({{
    "ini": str(config_loader._config_file_path),
    "tls_ini": str(tls_setup.get_config_file_path()),
    "tls": str(tls_setup.get_tls_dir(False, config_loader.BASE_DIR, config_loader.BASE_DIR.parent)),
    "db": str(db_config._get_default_sqlite_path()),
    "logs": str(logging_config.LOGS_DIR),
    "secret_key_dir": str(get_identity_dir("manager")),
    "ffmpeg": str(get_ffmpeg_dir(get_data_dir("manager"))),
}}))
"""

_SCRUBBED = (ENV, "SETHLANS_TLS_DATA_DIR", "SETHLANS_DATA_ROOT") + SETTINGS_ENV


def _probe(env_dir):
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
    if env_dir is not None:
        env[ENV] = str(env_dir)
    code = _PROBE.format(repo=str(REPO_ROOT), manager=str(MANAGER_DIR))
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {k: Path(v) for k, v in json.loads(out.stdout).items()}


def test_module_level_paths_follow_env(tmp_path):
    """Docker-style env-only start: everything stays under the one folder."""
    data = tmp_path / "data"
    paths = _probe(data)

    assert paths["ini"] == data / "sethlans.ini"
    assert paths["tls_ini"] == paths["ini"]
    assert paths["tls"] == data / "tls"
    assert paths["db"] == data / "db.sqlite3"
    assert paths["logs"] == data / "logs"
    assert paths["logs"].is_dir()
    assert paths["secret_key_dir"] == data
    assert paths["ffmpeg"].parent.parent == data / "bin"
