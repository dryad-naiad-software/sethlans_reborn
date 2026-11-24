# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolution order of ``settings_path``, ``get_settings_dir`` and ``get_data_dir``.

Each env var is checked against the ones below it, so a test fails when
two levels swap places.
"""
import os
from pathlib import Path

import pytest

from shared import frozen_paths, settings_file
from tests.unit.shared._settings_env import ABS_ROOT, clear_path_env, write_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clear_path_env(monkeypatch)


def _frozen(mocker, value=True):
    mocker.patch.object(frozen_paths, "is_frozen", return_value=value)


class TestSettingsPath:

    def test_settings_file_env_wins_over_everything(self, monkeypatch, mocker):
        _frozen(mocker)
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(ABS_ROOT / "custom" / "my.ini"))
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "settings"))
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "data"))
        assert settings_file.settings_path() == ABS_ROOT / "custom" / "my.ini"

    def test_relative_settings_file_env_raises(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", "relative/sethlans.ini")
        with pytest.raises(ValueError, match="SETHLANS_SETTINGS_FILE must be an absolute path"):
            settings_file.settings_path()

    def test_settings_dir_env_wins_over_manager_data_dir(self, monkeypatch, mocker):
        _frozen(mocker, False)
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "settings"))
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "data"))
        assert settings_file.settings_path() == ABS_ROOT / "settings" / "sethlans.ini"

    def test_source_mode_with_manager_data_dir(self, monkeypatch, mocker):
        # Docker: SETHLANS_MANAGER_DATA_DIR=/data gives /data/sethlans.ini.
        _frozen(mocker, False)
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "data"))
        assert settings_file.settings_path() == ABS_ROOT / "data" / "sethlans.ini"

    def test_frozen_ignores_manager_data_dir(self, monkeypatch, mocker):
        _frozen(mocker)
        mocker.patch("platform.system", return_value="Linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(ABS_ROOT / "xdg"))
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "data"))
        assert settings_file.settings_path() == ABS_ROOT / "xdg" / "sethlans" / "sethlans.ini"

    def test_source_default_is_repo_manager_dir(self, mocker):
        _frozen(mocker, False)
        expected = Path(frozen_paths.__file__).resolve().parent.parent / "manager" / "sethlans.ini"
        assert settings_file.settings_path() == expected


class TestGetSettingsDir:

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "s"))
        assert settings_file.get_settings_dir() == ABS_ROOT / "s"

    def test_relative_env_override_raises(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", "relative")
        with pytest.raises(ValueError, match="SETHLANS_SETTINGS_DIR must be an absolute path"):
            settings_file.get_settings_dir()

    def test_never_derived_from_manager_data_dir(self, monkeypatch, mocker):
        mocker.patch("platform.system", return_value="Linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(ABS_ROOT / "xdg"))
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "data"))
        assert settings_file.get_settings_dir() == ABS_ROOT / "xdg" / "sethlans"

    def test_windows_localappdata(self, monkeypatch, mocker):
        mocker.patch("platform.system", return_value="Windows")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\artist\\AppData\\Local")
        assert settings_file.get_settings_dir() == Path("C:\\Users\\artist\\AppData\\Local") / "Sethlans"

    def test_windows_userprofile_fallback(self, monkeypatch, mocker):
        mocker.patch("platform.system", return_value="Windows")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("USERPROFILE", "C:\\Users\\artist")
        expected = Path(os.path.join("C:\\Users\\artist", "AppData", "Local")) / "Sethlans"
        assert settings_file.get_settings_dir() == expected

    def test_darwin(self, mocker):
        mocker.patch("platform.system", return_value="Darwin")
        mocker.patch("pathlib.Path.home", return_value=Path("/Users/artist"))
        expected = Path("/Users/artist") / "Library" / "Application Support" / "Sethlans"
        assert settings_file.get_settings_dir() == expected

    def test_linux_without_xdg(self, monkeypatch, mocker):
        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("pathlib.Path.home", return_value=Path("/home/artist"))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert settings_file.get_settings_dir() == Path("/home/artist") / ".local" / "share" / "sethlans"


class TestGetDataDirOrder:

    @pytest.fixture
    def ini(self, monkeypatch, tmp_path):
        path = write_settings(tmp_path / "sethlans.ini", f"[paths]\ndata_dir = {ABS_ROOT / 'from-ini'}\n")
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(path))
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "settings"))
        return path

    def test_component_env_wins(self, monkeypatch, ini):
        monkeypatch.setenv("SETHLANS_WORKER_DATA_DIR", str(ABS_ROOT / "worker-env"))
        monkeypatch.setenv("SETHLANS_DATA_ROOT", str(ABS_ROOT / "root"))
        assert frozen_paths.get_data_dir("worker") == ABS_ROOT / "worker-env"

    def test_data_root_beats_settings_file(self, monkeypatch, ini):
        monkeypatch.setenv("SETHLANS_DATA_ROOT", str(ABS_ROOT / "root"))
        assert frozen_paths.get_data_dir("worker") == ABS_ROOT / "root" / "worker"
        assert frozen_paths.get_data_dir("manager") == ABS_ROOT / "root" / "manager"

    def test_settings_file_beats_default(self, ini):
        assert frozen_paths.get_data_dir("manager") == ABS_ROOT / "from-ini" / "manager"

    def test_default_is_settings_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(tmp_path / "missing.ini"))
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "settings"))
        assert frozen_paths.get_data_dir("worker") == ABS_ROOT / "settings" / "worker"

    def test_other_component_env_does_not_leak(self, monkeypatch, ini):
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(ABS_ROOT / "m"))
        assert frozen_paths.get_data_dir("worker") == ABS_ROOT / "from-ini" / "worker"

    def test_relative_data_root_raises(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_DATA_ROOT", "rel")
        with pytest.raises(ValueError, match="SETHLANS_DATA_ROOT must be an absolute path"):
            frozen_paths.get_data_dir("worker")

    def test_relative_settings_value_raises(self, monkeypatch, tmp_path):
        path = write_settings(tmp_path / "sethlans.ini", "[paths]\ndata_dir = relative/dir\n")
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(path))
        with pytest.raises(ValueError, match="must be an absolute path"):
            frozen_paths.get_data_dir("worker")

    def test_corrupt_settings_file_is_ignored(self, monkeypatch, tmp_path):
        path = write_settings(tmp_path / "sethlans.ini", "no section header\n")
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(path))
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(ABS_ROOT / "settings"))
        assert frozen_paths.get_data_dir("worker") == ABS_ROOT / "settings" / "worker"

    @pytest.mark.parametrize("bad", ["", "Worker", "../x", "a/b", "w-1"])
    def test_invalid_component_name_raises(self, bad):
        with pytest.raises(ValueError, match="Invalid component name"):
            frozen_paths.get_data_dir(bad)
