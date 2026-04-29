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
        # os.path.join uses forward slashes on POSIX even with
        # backslash-containing inputs, so build expected the same way.
        base = os.path.join("C:\\Users\\artist", "AppData", "Local")
        expected = Path(base) / "Sethlans"
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

    def test_env_override_wins_over_platform(self, mocker, tmp_path):
        """Issue #181: ``SETHLANS_DATA_DIR`` overrides the per-OS default.

        Mirrors :func:`shared.frozen_paths.get_shared_data_dir` so the
        launcher resolves to the same shared root the wizard / manager /
        worker subprocesses do — without the override the launcher
        polled ``%LOCALAPPDATA%\\Sethlans`` while the wizard wrote to
        the dev tree under ``temp/dev-data``, hanging the wizard
        hand-off (issue #180).
        """
        override = (tmp_path / "shared-data").resolve()
        env = os.environ.copy()
        env["SETHLANS_DATA_DIR"] = str(override)
        # Set every platform-specific signal too so the test fails if
        # the override is silently ignored on any branch.
        env["LOCALAPPDATA"] = "C:\\Users\\artist\\AppData\\Local"
        env["XDG_DATA_HOME"] = "/home/artist/.local/share"
        for system in ("Windows", "Darwin", "Linux"):
            with patch.dict(os.environ, env, clear=True):
                mocker.patch("platform.system", return_value=system)
                result = _get_data_dir()
            assert result == override, (
                f"override ignored on {system}: got {result}"
            )

    def test_env_override_must_be_absolute(self, mocker):
        env = os.environ.copy()
        env["SETHLANS_DATA_DIR"] = "relative/path"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            try:
                _get_data_dir()
            except ValueError as exc:
                assert "absolute path" in str(exc)
                assert "relative/path" in str(exc)
            else:
                raise AssertionError(
                    "expected ValueError for relative SETHLANS_DATA_DIR"
                )

    def test_env_override_empty_string_falls_through(self, mocker):
        """Empty ``SETHLANS_DATA_DIR`` is treated as unset (matches shared)."""
        fake_home = Path("/home/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env["SETHLANS_DATA_DIR"] = ""
        env.pop("XDG_DATA_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = _get_data_dir()
        assert result == fake_home / ".local" / "share" / "sethlans"


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
        mocker.patch(
            'launcher.paths.platform.system',
            return_value='Windows',
        )
        result = _get_bin_dir()
        assert result == tmp_path / 'bin'

    def test_frozen_mode_bin_dir_darwin(self, mocker, tmp_path):
        # Inside .app/Contents/MacOS/<exe>; components live under
        # Contents/Resources/bin/ per build_dmg.sh. get_bin_dir() must
        # account for the Apple bundle layout — otherwise the launcher
        # looks for Contents/tray_helper/run_tray_helper and exits on
        # startup (issue #87).
        app_root = tmp_path / 'Sethlans.app'
        macos = app_root / 'Contents' / 'MacOS'
        macos.mkdir(parents=True, exist_ok=True)
        exe = macos / 'sethlans'
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        mocker.patch(
            'launcher.paths.platform.system',
            return_value='Darwin',
        )
        result = _get_bin_dir()
        assert result == app_root / 'Contents' / 'Resources' / 'bin'


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
            'launcher.component_paths.platform.system',
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
            'launcher.component_paths.platform.system',
            return_value='Linux',
        )
        mocker.patch(
            'launcher.paths.platform.system',
            return_value='Linux',
        )
        result = _find_component_exe("manager")
        assert result.name == "run_manager"

    def test_frozen_mode_darwin_tray(self, mocker, tmp_path):
        # End-to-end assertion for issue #87: on macOS,
        # _find_component_exe("tray") must resolve to the tray_helper
        # binary inside the bundle's Contents/Resources/bin tree — not
        # Contents/tray_helper/, which does not exist in the DMG.
        app_root = tmp_path / 'Sethlans.app'
        macos = app_root / 'Contents' / 'MacOS'
        macos.mkdir(parents=True, exist_ok=True)
        exe = macos / 'sethlans'
        exe.touch()
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(sys, 'executable', str(exe))
        mocker.patch(
            'launcher.component_paths.platform.system',
            return_value='Darwin',
        )
        mocker.patch(
            'launcher.paths.platform.system',
            return_value='Darwin',
        )
        result = _find_component_exe("tray")
        expected = (
            app_root / 'Contents' / 'Resources' / 'bin'
            / 'tray_helper' / 'run_tray_helper'
        )
        assert result == expected
