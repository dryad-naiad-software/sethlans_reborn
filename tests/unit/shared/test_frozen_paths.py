# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``shared/frozen_paths.py``.

Covers source-mode and frozen-mode path resolution, OS-specific data
directories, environment variable overrides, and error handling.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.frozen_paths import (
    get_app_dir,
    get_branding_dir,
    get_data_dir,
    get_frontend_dist_dir,
    get_install_root,
    get_manager_dir,
    get_worker_dir,
    is_frozen,
)


# ---- is_frozen() -----------------------------------------------------------

class TestIsFrozen:

    def test_returns_false_in_source_mode(self):
        """Normal development mode: sys.frozen is absent."""
        assert is_frozen() is False

    def test_returns_true_when_frozen(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        assert is_frozen() is True


# ---- Source-mode paths (not frozen) ----------------------------------------

class TestSourceModePaths:
    """All path functions in source (development) mode."""

    def test_get_app_dir_is_project_root(self):
        result = get_app_dir()
        # shared/ is in the project root, so parent.parent of the module
        # should equal the project root containing manager/ and worker/.
        assert result.is_absolute()
        assert (result / 'manager').exists() or (result / 'shared').exists()

    def test_get_install_root_source_mode(self):
        result = get_install_root()
        assert result == get_app_dir()

    def test_get_manager_dir(self):
        result = get_manager_dir()
        assert result == get_app_dir() / 'manager'

    def test_get_worker_dir(self):
        result = get_worker_dir()
        assert result == get_app_dir() / 'worker'

    def test_get_frontend_dist_dir(self):
        result = get_frontend_dist_dir()
        expected = (
            get_app_dir() / 'manager' / 'frontend'
            / 'dist' / 'browser' / 'browser'
        )
        assert result == expected

    def test_get_branding_dir(self):
        result = get_branding_dir()
        assert result == get_app_dir() / 'packaging' / 'branding'

    def test_get_branding_dir_contains_wordmark_asset(self):
        # The startup splash relies on this file — make sure
        # packaging/branding/logo-text-dark.png exists in source mode.
        asset = get_branding_dir() / 'logo-text-dark.png'
        assert asset.is_file(), (
            f"Expected wordmark asset at {asset}; the launcher "
            "splash loads it at runtime."
        )


# ---- Frozen-mode paths (PyInstaller) --------------------------------------

class TestFrozenModePaths:
    """Path functions when sys.frozen is True and sys._MEIPASS is set."""

    @pytest.fixture(autouse=True)
    def _freeze(self, mocker, tmp_path):
        """Simulate a frozen environment from the manager process with full sibling layout."""
        bin_dir = tmp_path / 'bin'
        for component, binary in [
            ('manager', 'run_manager.exe'),
            ('launcher', 'run_launcher.exe'),
            ('worker', 'run_worker.exe'),
            ('wizard', 'run_wizard.exe'),
            ('tray_helper', 'run_tray_helper.exe'),
        ]:
            exe = bin_dir / component / binary
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.touch()
        manager_exe = bin_dir / 'manager' / 'run_manager.exe'
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(manager_exe))
        meipass = bin_dir / 'manager' / '_internal'
        meipass.mkdir(parents=True, exist_ok=True)
        mocker.patch.object(sys, '_MEIPASS', str(meipass), create=True)
        self._exe = manager_exe
        self._meipass = meipass
        self._bin_dir = bin_dir

    def test_get_app_dir_returns_executable_parent(self):
        result = get_app_dir()
        assert result == self._exe.resolve().parent

    def test_get_install_root_frozen_from_manager(self):
        result = get_install_root()
        assert result == self._bin_dir.resolve()

    def test_get_manager_dir_frozen_returns_install_root_slash_manager(self):
        # TP-4: assert new correct value, not the old buggy executable parent
        result = get_manager_dir()
        assert result == self._bin_dir.resolve() / 'manager'

    def test_get_worker_dir_frozen_returns_install_root_slash_worker(self):
        # TP-5: assert new correct value, not the old buggy executable parent
        result = get_worker_dir()
        assert result == self._bin_dir.resolve() / 'worker'

    def test_get_frontend_dist_dir_uses_meipass(self):
        result = get_frontend_dist_dir()
        expected = (
            self._meipass / 'frontend' / 'dist' / 'browser' / 'browser'
        )
        assert result == expected

    def test_get_branding_dir_uses_meipass(self):
        result = get_branding_dir()
        expected = self._meipass / 'branding'
        assert result == expected


# ---- get_data_dir() — env var overrides ------------------------------------

class TestGetDataDirEnvOverride:

    _ABS = (
        "C:\\sethlans\\data" if sys.platform == "win32" else "/data"
    )

    def test_manager_env_override(self):
        with patch.dict(
            os.environ, {"SETHLANS_MANAGER_DATA_DIR": self._ABS},
        ):
            result = get_data_dir("manager")
        assert result == Path(self._ABS)

    def test_worker_env_override(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": self._ABS},
        ):
            result = get_data_dir("worker")
        assert result == Path(self._ABS)

    def test_relative_path_raises(self):
        with patch.dict(
            os.environ, {"SETHLANS_MANAGER_DATA_DIR": "relative/path"},
        ):
            with pytest.raises(ValueError, match="absolute path"):
                get_data_dir("manager")

    def test_error_includes_bad_path(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": "bad/path"},
        ):
            with pytest.raises(ValueError, match="bad/path"):
                get_data_dir("worker")


# ---- get_data_dir() — OS-specific defaults ---------------------------------

class TestGetDataDirWindows:

    def test_uses_localappdata(self, mocker):
        env = os.environ.copy()
        env.pop("SETHLANS_MANAGER_DATA_DIR", None)
        env["LOCALAPPDATA"] = "C:\\Users\\artist\\AppData\\Local"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = get_data_dir("manager")
        expected = (
            Path("C:\\Users\\artist\\AppData\\Local")
            / "Sethlans" / "manager"
        )
        assert result == expected

    def test_falls_back_to_userprofile(self, mocker):
        env = os.environ.copy()
        env.pop("SETHLANS_MANAGER_DATA_DIR", None)
        env.pop("LOCALAPPDATA", None)
        env["USERPROFILE"] = "C:\\Users\\artist"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = get_data_dir("manager")
        # os.path.join uses forward slashes on POSIX even with
        # backslash-containing inputs, so build expected the same way.
        base = os.path.join("C:\\Users\\artist", "AppData", "Local")
        expected = Path(base) / "Sethlans" / "manager"
        assert result == expected

    def test_falls_back_to_home(self, mocker):
        fake_home = Path("C:/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_MANAGER_DATA_DIR", None)
        env.pop("LOCALAPPDATA", None)
        env.pop("USERPROFILE", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = get_data_dir("manager")
        expected = fake_home / "AppData" / "Local" / "Sethlans" / "manager"
        assert result == expected


class TestGetDataDirDarwin:

    def test_default_path(self, mocker):
        fake_home = Path("/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_MANAGER_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Darwin")
            result = get_data_dir("manager")
        expected = (
            fake_home / "Library" / "Application Support"
            / "Sethlans" / "manager"
        )
        assert result == expected


class TestGetDataDirLinux:

    def test_with_xdg(self, mocker):
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        env["XDG_DATA_HOME"] = "/home/artist/.local/share"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = get_data_dir("worker")
        expected = (
            Path("/home/artist/.local/share") / "sethlans" / "worker"
        )
        assert result == expected

    def test_without_xdg(self, mocker):
        fake_home = Path("/home/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        env.pop("XDG_DATA_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = get_data_dir("worker")
        expected = (
            fake_home / ".local" / "share" / "sethlans" / "worker"
        )
        assert result == expected


class TestGetDataDirComponentInPath:
    """The component name appears in the final path segment."""

    def test_manager_component(self, mocker):
        fake_home = Path("/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_MANAGER_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Darwin")
            result = get_data_dir("manager")
        assert result.name == "manager"

    def test_worker_component(self, mocker):
        fake_home = Path("/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Darwin")
            result = get_data_dir("worker")
        assert result.name == "worker"
