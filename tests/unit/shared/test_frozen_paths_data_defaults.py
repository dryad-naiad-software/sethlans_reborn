# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the OS-specific defaults of ``shared.frozen_paths.get_data_dir``.

Split from ``test_frozen_paths.py`` (300-line limit). The OS default is
the installed (frozen) layout; from source the manager's data folder
defaults to ``<repo>/manager`` instead.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.frozen_paths import get_data_dir


# ---- get_data_dir() — OS-specific defaults ---------------------------------

@pytest.fixture
def installed(mocker):
    mocker.patch("shared.frozen_paths.is_frozen", return_value=True)


@pytest.mark.usefixtures("installed")  # source-mode manager data is <repo>/manager
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


@pytest.mark.usefixtures("installed")  # source-mode manager data is <repo>/manager
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
