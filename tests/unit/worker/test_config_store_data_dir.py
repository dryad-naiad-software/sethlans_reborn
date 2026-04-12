# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``SETHLANS_WORKER_DATA_DIR`` env var override in
``worker/sethlans_worker_agent/config_store/paths.py``.

Validates that:
  * When the env var is set, ``get_data_dir()`` returns exactly that path.
  * When the env var is set to a relative path, ``ValueError`` is raised.
  * When the env var is NOT set, OS-specific defaults are returned.
  * ``user_config_path()`` and ``lockfile_path()`` derive from the
    overridden data dir.

Note: ``get_data_dir()`` returns ``Path()`` objects.  On Windows the
concrete type is always ``WindowsPath`` regardless of the path string,
so assertions compare ``.parts`` (platform-neutral) instead of using
``PurePosixPath`` / ``PureWindowsPath``.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sethlans_worker_agent.config_store.paths import (
    get_data_dir,
    lockfile_path,
    user_config_path,
)

_ABS_DATA_DIR = (
    "C:\\sethlans\\data" if sys.platform == "win32" else "/data"
)


class TestGetDataDirEnvOverride:
    """When SETHLANS_WORKER_DATA_DIR is set, it takes precedence."""

    def test_returns_env_override_path(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": _ABS_DATA_DIR},
        ):
            result = get_data_dir()
        assert result == Path(_ABS_DATA_DIR)

    def test_returns_exact_deep_path(self):
        deep = (
            "C:\\sethlans\\render\\worker-data"
            if sys.platform == "win32"
            else "/mnt/render/worker-data"
        )
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": deep},
        ):
            result = get_data_dir()
        assert result == Path(deep)

    def test_result_is_path_instance(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": _ABS_DATA_DIR},
        ):
            result = get_data_dir()
        assert isinstance(result, Path)


class TestGetDataDirRelativePathRejected:
    """A relative SETHLANS_WORKER_DATA_DIR must raise ValueError."""

    def test_relative_path_raises(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": "relative/data"},
        ):
            with pytest.raises(ValueError, match="absolute path"):
                get_data_dir()

    def test_dot_relative_path_raises(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": "./data"},
        ):
            with pytest.raises(ValueError, match="absolute path"):
                get_data_dir()

    def test_error_includes_bad_path(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": "bad/path"},
        ):
            with pytest.raises(ValueError, match="bad/path"):
                get_data_dir()


class TestGetDataDirOsDefaults:
    """When the env var is NOT set, OS-specific defaults apply.

    Since ``get_data_dir()`` returns ``Path()`` and these tests run on
    Windows, we compare the result's ``.parts`` tuple so the assertion
    is independent of the concrete ``Path`` subclass.
    """

    def test_windows_default(self, mocker):
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        env["LOCALAPPDATA"] = "C:\\Users\\artist\\AppData\\Local"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Windows")
            result = get_data_dir()
        expected = Path("C:\\Users\\artist\\AppData\\Local") / "Sethlans" / "worker"
        assert result == expected

    def test_darwin_default(self, mocker):
        fake_home = Path("/Users/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Darwin")
            result = get_data_dir()
        expected = fake_home / "Library" / "Application Support" / "Sethlans" / "worker"
        assert result == expected

    def test_linux_default_with_xdg(self, mocker):
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        env["XDG_DATA_HOME"] = "/home/artist/.local/share"
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = get_data_dir()
        expected = Path("/home/artist/.local/share") / "sethlans" / "worker"
        assert result == expected

    def test_linux_default_without_xdg(self, mocker):
        fake_home = Path("/home/artist")
        mocker.patch("pathlib.Path.home", return_value=fake_home)
        env = os.environ.copy()
        env.pop("SETHLANS_WORKER_DATA_DIR", None)
        env.pop("XDG_DATA_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            mocker.patch("platform.system", return_value="Linux")
            result = get_data_dir()
        expected = fake_home / ".local" / "share" / "sethlans" / "worker"
        assert result == expected


class TestDerivedPathsUseOverride:
    """user_config_path() and lockfile_path() respect the override."""

    def test_user_config_path_derives_from_override(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": _ABS_DATA_DIR},
        ):
            result = user_config_path()
        assert result == Path(_ABS_DATA_DIR) / "config.json"

    def test_lockfile_path_derives_from_override(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": _ABS_DATA_DIR},
        ):
            result = lockfile_path()
        assert result == Path(_ABS_DATA_DIR) / "config.json.lock"

    def test_both_paths_share_same_parent(self):
        with patch.dict(
            os.environ, {"SETHLANS_WORKER_DATA_DIR": _ABS_DATA_DIR},
        ):
            cfg = user_config_path()
            lock = lockfile_path()
        assert cfg.parent == lock.parent == Path(_ABS_DATA_DIR)
