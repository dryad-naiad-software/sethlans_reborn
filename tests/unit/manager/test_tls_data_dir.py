# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``SETHLANS_TLS_DATA_DIR`` env var override in
``manager/run_manager.py`` ``get_tls_dir()``.

Validates that:
  * When the env var is set to an absolute path, it returns that path
    regardless of ``dev_mode``.
  * When the env var is set to a relative path, ``ValueError`` is raised.
  * When the env var is NOT set, the default dev-tls or prod tls path
    is returned based on ``dev_mode``.

Note: On Windows, POSIX-style paths like ``/data/tls`` are NOT absolute
(``Path.is_absolute()`` requires a drive letter), so the "valid absolute"
tests use a Windows-native path that is absolute on all platforms.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from run_manager import get_tls_dir, MANAGER_DIR, PROJECT_ROOT

# A path that is absolute on the current OS.
_ABS_TLS_DIR = "C:\\sethlans\\tls" if sys.platform == "win32" else "/data/tls"


class TestTlsDirEnvOverride:
    """When SETHLANS_TLS_DATA_DIR is set to an absolute path."""

    def test_absolute_path_returned_when_env_set(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": _ABS_TLS_DIR},
        ):
            result = get_tls_dir(dev_mode=False)
        assert result == Path(_ABS_TLS_DIR)

    def test_absolute_path_ignores_dev_mode_true(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": _ABS_TLS_DIR},
        ):
            result = get_tls_dir(dev_mode=True)
        assert result == Path(_ABS_TLS_DIR)

    def test_absolute_path_ignores_dev_mode_false(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": _ABS_TLS_DIR},
        ):
            result = get_tls_dir(dev_mode=False)
        assert result == Path(_ABS_TLS_DIR)

    def test_returns_path_instance(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": _ABS_TLS_DIR},
        ):
            result = get_tls_dir(dev_mode=False)
        assert isinstance(result, Path)


class TestTlsDirRelativePathRejected:
    """When SETHLANS_TLS_DATA_DIR is set to a relative path."""

    def test_relative_path_raises_value_error(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": "relative/tls"},
        ):
            with pytest.raises(ValueError, match="absolute path"):
                get_tls_dir(dev_mode=False)

    def test_dot_relative_path_raises_value_error(self):
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": "./tls"},
        ):
            with pytest.raises(ValueError, match="absolute path"):
                get_tls_dir(dev_mode=True)

    def test_error_message_includes_bad_path(self):
        bad_path = "data/certs"
        with patch.dict(
            os.environ, {"SETHLANS_TLS_DATA_DIR": bad_path},
        ):
            with pytest.raises(ValueError, match=bad_path):
                get_tls_dir(dev_mode=False)


class TestTlsDirDefaults:
    """When SETHLANS_TLS_DATA_DIR is NOT set, mode-based defaults."""

    def test_dev_mode_returns_dev_tls_path(self):
        env = os.environ.copy()
        env.pop("SETHLANS_TLS_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = get_tls_dir(dev_mode=True)
        assert result == PROJECT_ROOT / "temp" / "dev-tls"

    def test_prod_mode_returns_manager_tls_path(self):
        env = os.environ.copy()
        env.pop("SETHLANS_TLS_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = get_tls_dir(dev_mode=False)
        assert result == MANAGER_DIR / "tls"
