# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for path resolution functions in ``launcher/run_launcher.py``.

Covers ``_get_data_dir``, ``_get_install_dir``, ``_get_bin_dir``,
and ``_find_component_exe``.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from launcher.run_launcher import (
    _get_bin_dir,
    _get_data_dir,
    _get_install_dir,
    _find_component_exe,
)


# ---- _get_data_dir() -------------------------------------------------------

class TestGetDataDir:

    def test_windows(self, mocker):
        env = os.environ.copy()
        env["LOCALAPPDATA"] = "C:\\Users\\artist\\AppData\\Local"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = _get_data_dir()
        expected = (
            Path("C:\\Users\\artist\\AppData\\Local") / "Sethlans"
        )
        assert result == expected

    def test_windows_fallback_userprofile(self, mocker):
        env = os.environ.copy()
        env.pop("LOCALAPPDATA", None)
        env["USERPROFILE"] = "C:\\Users\\artist"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = _get_data_dir()
        expected = (
            Path("C:\\Users\\artist\\AppData\\Local") / "Sethlans"
        )
        assert result == expected

    def test_windows_fallback_home(self, mocker):
        fake_home = Path("C:/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("LOCALAPPDATA", None)
        env.pop("USERPROFILE", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = _get_data_dir()
        expected = fake_home / "AppData" / "Local" / "Sethlans"
        assert result == expected

    def test_darwin(self, mocker):
        fake_home = Path("/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Darwin")
            result = _get_data_dir()
        expected = (
            fake_home / "Library" / "Application Support" / "Sethlans"
        )
        assert result == expected

    def test_linux_with_xdg(self, mocker):
        env = os.environ.copy()
        env["XDG_DATA_HOME"] = "/home/artist/.local/share"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = _get_data_dir()
        assert result == Path("/home/artist/.local/share") / "sethlans"

    def test_linux_without_xdg(self, mocker):
        fake_home = Path("/home/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("XDG_DATA_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = _get_data_dir()
        expected = fake_home / ".local" / "share" / "sethlans"
        assert result == expected


# ---- _get_install_dir() / _get_bin_dir() ----------------------------------

class TestInstallAndBinDir:

    def test_source_mode_install_dir(self):
        """Source mode: install dir is the project root."""
        result = _get_install_dir()
        assert result.is_absolute()
        assert result.exists()

    def test_frozen_mode_install_dir(self, mocker, tmp_path):
        exe = tmp_path / 'bin' / 'launcher' / 'sethlans.exe'
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        result = _get_install_dir()
        assert result == tmp_path

    def test_frozen_mode_bin_dir(self, mocker, tmp_path):
        exe = tmp_path / 'bin' / 'launcher' / 'sethlans.exe'
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        result = _get_bin_dir()
        assert result == tmp_path / 'bin'


# ---- _find_component_exe() ------------------------------------------------

class TestFindComponentExe:

    def test_source_mode_manager(self):
        """Source mode: returns the run_manager.py script path."""
        result = _find_component_exe("manager")
        assert result.name == "run_manager.py"

    def test_source_mode_worker(self):
        result = _find_component_exe("worker")
        assert result.name == "run_worker.py"

    def test_frozen_mode_windows(self, mocker, tmp_path):
        exe = tmp_path / 'bin' / 'launcher' / 'sethlans.exe'
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        mocker.patch(
            'launcher.run_launcher.platform.system',
            return_value='Windows',
        )
        result = _find_component_exe("manager")
        assert result.name == "run_manager.exe"

    def test_frozen_mode_linux(self, mocker, tmp_path):
        exe = tmp_path / 'bin' / 'launcher' / 'sethlans'
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        mocker.patch(
            'launcher.run_launcher.platform.system',
            return_value='Linux',
        )
        result = _find_component_exe("manager")
        assert result.name == "run_manager"
