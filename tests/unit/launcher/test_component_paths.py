# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.component_paths.find_component_exe`` (FR-L12).

Covers the ``manager``/``worker``/``tray`` branches in both source and
frozen mode; the old ``wizard`` component is gone (setup rebuild 6b).
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

    @pytest.mark.parametrize("component", ["nonexistent", "wizard"])
    def test_unknown_component_raises_value_error(self, component):
        """FR-L12; setup rebuild: the wizard component no longer exists."""
        with pytest.raises(ValueError, match="unknown component"):
            find_component_exe(component)


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
