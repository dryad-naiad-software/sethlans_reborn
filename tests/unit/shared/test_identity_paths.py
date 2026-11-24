# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity files never move with the data folder.

Without overrides the identity and data folders are both
``<settings>/<component>`` (today's layout). A data root, from the env or
from ``[paths] data_dir``, moves only the data folder. A per-component
``SETHLANS_{COMPONENT}_DATA_DIR`` (Docker's ``/data``) keeps both where
they are today, whatever else is set.
"""
import pytest

from shared import frozen_paths, settings_file
from sethlans_worker_agent import config_store, tls_setup
from sethlans_worker_agent.config_store import paths as worker_paths
from tests.unit.shared._settings_env import ABS_ROOT, clear_path_env, write_settings

SETTINGS = ABS_ROOT / "settings"
DATA = ABS_ROOT / "data"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    clear_path_env(monkeypatch)
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(SETTINGS))
    # No settings file unless a test writes one.
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(tmp_path / "sethlans.ini"))


@pytest.mark.parametrize("component", ["manager", "worker"])
def test_default_layout_identity_equals_data(monkeypatch, component):
    """An installed (frozen) default: both under the settings folder."""
    monkeypatch.setattr(frozen_paths, "is_frozen", lambda: True)
    assert frozen_paths.get_identity_dir(component) == SETTINGS / component
    assert frozen_paths.get_data_dir(component) == SETTINGS / component


def test_source_manager_data_defaults_to_the_manager_folder():
    """From source the manager's data stays in ``<repo>/manager``; identity does not follow."""
    assert frozen_paths.get_data_dir("manager") == frozen_paths.get_manager_dir()
    assert frozen_paths.get_identity_dir("manager") == SETTINGS / "manager"
    assert frozen_paths.get_data_dir("worker") == SETTINGS / "worker"


@pytest.mark.parametrize("component", ["manager", "worker"])
def test_data_root_env_moves_data_only(monkeypatch, component):
    monkeypatch.setenv("SETHLANS_DATA_ROOT", str(DATA))
    assert frozen_paths.get_data_dir(component) == DATA / component
    assert frozen_paths.get_identity_dir(component) == SETTINGS / component


@pytest.mark.parametrize("component", ["manager", "worker"])
def test_settings_data_dir_moves_data_only(tmp_path, component):
    write_settings(tmp_path / "sethlans.ini", f"[paths]\ndata_dir = {DATA}\n")
    assert frozen_paths.get_data_dir(component) == DATA / component
    assert frozen_paths.get_identity_dir(component) == SETTINGS / component


@pytest.mark.parametrize("component", ["manager", "worker"])
def test_component_override_keeps_both_in_place(monkeypatch, tmp_path, component):
    pinned = ABS_ROOT / "pinned"
    monkeypatch.setenv(f"SETHLANS_{component.upper()}_DATA_DIR", str(pinned))
    monkeypatch.setenv("SETHLANS_DATA_ROOT", str(DATA))
    write_settings(tmp_path / "sethlans.ini", f"[paths]\ndata_dir = {ABS_ROOT / 'ini'}\n")
    assert frozen_paths.get_identity_dir(component) == pinned
    assert frozen_paths.get_data_dir(component) == pinned


class TestWorkerLayout:

    def test_default(self):
        assert worker_paths.user_config_path() == SETTINGS / "worker" / "config.json"
        assert worker_paths.lockfile_path() == SETTINGS / "worker" / "config.json.lock"
        assert tls_setup.get_tls_dir() == SETTINGS / "worker" / "tls"
        assert config_store.get_data_dir() == SETTINGS / "worker"

    def test_data_root_keeps_config_and_tls(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_DATA_ROOT", str(DATA))
        assert config_store.get_data_dir() == DATA / "worker"
        assert config_store.get_identity_dir() == SETTINGS / "worker"
        assert worker_paths.user_config_path() == SETTINGS / "worker" / "config.json"
        assert tls_setup.get_tls_dir() == SETTINGS / "worker" / "tls"

    def test_worker_tls_override_still_wins(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_WORKER_TLS_DATA_DIR", str(ABS_ROOT / "wtls"))
        monkeypatch.setenv("SETHLANS_DATA_ROOT", str(DATA))
        assert tls_setup.get_tls_dir() == ABS_ROOT / "wtls"


class TestDockerEnvOnly:
    """The image sets only the per-component dirs to ``/data`` and runs from source."""

    @pytest.fixture
    def docker_env(self, monkeypatch, mocker):
        monkeypatch.delenv("SETHLANS_SETTINGS_DIR", raising=False)
        monkeypatch.delenv("SETHLANS_SETTINGS_FILE", raising=False)
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(DATA))
        monkeypatch.setenv("SETHLANS_WORKER_DATA_DIR", str(DATA))
        mocker.patch.object(frozen_paths, "is_frozen", return_value=False)

    def test_settings_file_in_data(self, docker_env):
        assert settings_file.settings_path() == DATA / "sethlans.ini"

    def test_manager_identity_and_data_in_data(self, docker_env):
        # The manager's TLS folder (<identity>/tls) and SQLite file
        # (<data>/db.sqlite3) stay at /data/tls and /data/db.sqlite3.
        assert frozen_paths.get_identity_dir("manager") / "tls" == DATA / "tls"
        assert frozen_paths.get_data_dir("manager") / "db.sqlite3" == DATA / "db.sqlite3"

    def test_worker_config_in_data(self, docker_env):
        assert worker_paths.user_config_path() == DATA / "config.json"
        assert tls_setup.get_tls_dir() == DATA / "tls"
        assert config_store.get_data_dir() == DATA

    def test_data_root_does_not_move_docker_identity(self, docker_env, monkeypatch):
        monkeypatch.setenv("SETHLANS_DATA_ROOT", str(ABS_ROOT / "elsewhere"))
        assert worker_paths.user_config_path() == DATA / "config.json"
        assert frozen_paths.get_identity_dir("manager") == DATA


@pytest.mark.parametrize("component", ["manager", "worker"])
def test_settings_file_env_alone_does_not_move_identity(monkeypatch, mocker, component):
    monkeypatch.delenv("SETHLANS_SETTINGS_DIR", raising=False)
    mocker.patch("platform.system", return_value="Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(ABS_ROOT / "xdg"))
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(ABS_ROOT / "elsewhere" / "sethlans.ini"))
    assert settings_file.settings_path() == ABS_ROOT / "elsewhere" / "sethlans.ini"
    assert settings_file.get_settings_dir() == ABS_ROOT / "xdg" / "sethlans"
    assert frozen_paths.get_identity_dir(component) == ABS_ROOT / "xdg" / "sethlans" / component
    if component == "worker":
        assert worker_paths.user_config_path() == ABS_ROOT / "xdg" / "sethlans" / "worker" / "config.json"
