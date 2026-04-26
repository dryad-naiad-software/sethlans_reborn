# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.component_paths.find_component_exe`` (FR-L12).

Covers the new ``wizard`` branch alongside the existing
``manager``/``worker``/``tray`` branches in both source and frozen mode.
"""

import sys

import pytest

from launcher.component_paths import find_component_exe


class TestFindComponentExeSourceMode:
    """Source mode: returns the run_*.py script under the repo root."""

    def test_manager(self):
        result = find_component_exe("manager")
        assert result.name == "run_manager.py"
        assert result.parent.name == "manager"

    def test_worker(self):
        result = find_component_exe("worker")
        assert result.name == "run_worker.py"
        assert result.parent.name == "worker"

    def test_tray(self):
        result = find_component_exe("tray")
        assert result.name == "run_tray.py"
        assert result.parent.name == "shared"

    def test_wizard_branch_returns_run_wizard_py(self):
        """FR-L12: source mode wizard → wizard/run_wizard.py."""
        result = find_component_exe("wizard")
        assert result.name == "run_wizard.py"
        assert result.parent.name == "wizard"

    def test_unknown_component_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown component"):
            find_component_exe("nonexistent")


class TestFindComponentExeFrozenWindows:
    """Frozen mode on Windows: returns bin/<component>/run_<component>.exe."""

    def _stub_frozen_windows(self, mocker, tmp_path):
        # bin/launcher/sethlans.exe → bin = exe_parent_parent
        exe = tmp_path / "bin" / "launcher" / "sethlans.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
        mocker.patch.object(sys, "frozen", True, create=True)
        mocker.patch.object(sys, "executable", str(exe))
        mocker.patch(
            "launcher.component_paths.platform.system",
            return_value="Windows",
        )

    def test_manager(self, mocker, tmp_path):
        self._stub_frozen_windows(mocker, tmp_path)
        result = find_component_exe("manager")
        assert result.name == "run_manager.exe"
        assert result.parent.name == "manager"

    def test_worker(self, mocker, tmp_path):
        self._stub_frozen_windows(mocker, tmp_path)
        result = find_component_exe("worker")
        assert result.name == "run_worker.exe"

    def test_tray(self, mocker, tmp_path):
        self._stub_frozen_windows(mocker, tmp_path)
        result = find_component_exe("tray")
        assert result.name == "run_tray_helper.exe"
        assert result.parent.name == "tray_helper"

    def test_wizard_branch_returns_run_wizard_exe(self, mocker, tmp_path):
        """FR-L12: frozen Windows wizard → bin/wizard/run_wizard.exe."""
        self._stub_frozen_windows(mocker, tmp_path)
        result = find_component_exe("wizard")
        assert result.name == "run_wizard.exe"
        assert result.parent.name == "wizard"

    def test_unknown_component_raises_value_error_frozen(
        self, mocker, tmp_path,
    ):
        self._stub_frozen_windows(mocker, tmp_path)
        with pytest.raises(ValueError, match="unknown component"):
            find_component_exe("nonexistent")


class TestFindComponentExeFrozenPosix:
    """Frozen mode on Linux: returns bin/<component>/run_<component>."""

    def _stub_frozen_linux(self, mocker, tmp_path):
        exe = tmp_path / "bin" / "launcher" / "sethlans"
        exe.parent.mkdir(parents=True)
        exe.touch()
        mocker.patch.object(sys, "frozen", True, create=True)
        mocker.patch.object(sys, "executable", str(exe))
        mocker.patch(
            "launcher.component_paths.platform.system",
            return_value="Linux",
        )
        mocker.patch(
            "launcher.paths.platform.system", return_value="Linux",
        )

    def test_manager(self, mocker, tmp_path):
        self._stub_frozen_linux(mocker, tmp_path)
        result = find_component_exe("manager")
        assert result.name == "run_manager"

    def test_tray(self, mocker, tmp_path):
        self._stub_frozen_linux(mocker, tmp_path)
        result = find_component_exe("tray")
        assert result.name == "run_tray_helper"
        assert result.parent.name == "tray_helper"

    def test_wizard_branch_returns_run_wizard_no_extension(
        self, mocker, tmp_path,
    ):
        """FR-L12: frozen POSIX wizard → bin/wizard/run_wizard."""
        self._stub_frozen_linux(mocker, tmp_path)
        result = find_component_exe("wizard")
        assert result.name == "run_wizard"
        assert result.parent.name == "wizard"


class TestRunLauncherReExport:
    """Verify ``launcher.run_launcher._find_component_exe`` re-export works."""

    def test_re_export_finds_wizard(self):
        from launcher.run_launcher import _find_component_exe

        result = _find_component_exe("wizard")
        assert result.name == "run_wizard.py"
