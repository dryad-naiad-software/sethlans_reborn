# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``shared.frozen_paths.get_caddy_path``.

Covers:
 * source (dev) mode — path under ``.venv-build/caddy/``
 * frozen (PyInstaller one-dir) mode — path next to the executable
 * Windows vs. POSIX binary name (``caddy.exe`` vs ``caddy``)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from shared.frozen_paths import get_app_dir, get_caddy_path


class TestGetCaddyPathSourceMode:

    def test_posix_uses_caddy_name(self, mocker):
        mocker.patch("shared.frozen_paths.platform.system", return_value="Linux")
        result = get_caddy_path()
        expected = get_app_dir() / ".venv-build" / "caddy" / "caddy"
        assert result == expected

    def test_windows_uses_exe_extension(self, mocker):
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Windows",
        )
        result = get_caddy_path()
        expected = get_app_dir() / ".venv-build" / "caddy" / "caddy.exe"
        assert result == expected

    def test_darwin_uses_caddy_name(self, mocker):
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Darwin",
        )
        result = get_caddy_path()
        assert result.name == "caddy"

    def test_path_is_under_venv_build(self, mocker):
        mocker.patch("shared.frozen_paths.platform.system", return_value="Linux")
        result = get_caddy_path()
        assert ".venv-build" in result.parts
        assert "caddy" in result.parts


class TestGetCaddyPathFrozenMode:

    @pytest.fixture
    def _freeze(self, mocker, tmp_path):
        exe = tmp_path / "bin" / "launcher" / "run_launcher.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        mocker.patch.object(sys, "frozen", True, create=True)
        mocker.patch.object(sys, "executable", str(exe))
        meipass = tmp_path / "bin" / "launcher" / "_internal"
        meipass.mkdir(parents=True, exist_ok=True)
        mocker.patch.object(sys, "_MEIPASS", str(meipass), create=True)
        return exe

    def test_posix_returns_caddy_in_meipass(self, _freeze, mocker):
        # PyInstaller 6.x places binaries under _MEIPASS (== _internal/
        # in one-dir mode), not next to the entry-point exe (#104).
        mocker.patch("shared.frozen_paths.platform.system", return_value="Linux")
        result = get_caddy_path()
        meipass = Path(sys._MEIPASS)
        assert result == meipass / "caddy"

    def test_windows_returns_caddy_exe_in_meipass(
        self, _freeze, mocker,
    ):
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Windows",
        )
        result = get_caddy_path()
        meipass = Path(sys._MEIPASS)
        assert result == meipass / "caddy.exe"

    def test_meipass_path_is_under_internal(self, _freeze, mocker):
        # Sanity: the frozen path must live under the _internal/ contents
        # dir so it matches where PyInstaller actually drops binaries.
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Linux",
        )
        result = get_caddy_path()
        assert "_internal" in result.parts, (
            f"expected caddy to live under _internal/, got {result}"
        )

    def test_frozen_path_does_not_reference_venv_build(self, _freeze, mocker):
        """In frozen mode we must NOT point at a dev-only path."""
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Linux",
        )
        result = get_caddy_path()
        assert ".venv-build" not in result.parts


class TestGetCaddyPathReturnsPath:

    def test_returns_path_object(self):
        assert isinstance(get_caddy_path(), Path)

    def test_does_not_check_existence(self, mocker):
        """Callers are responsible for checking ``is_file()`` — the
        helper must not raise when the binary is absent."""
        mocker.patch(
            "shared.frozen_paths.platform.system", return_value="Linux",
        )
        # Just calling it with a non-existent path should not raise.
        result = get_caddy_path()
        assert result is not None
