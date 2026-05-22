# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-component frozen-path tests for ``shared/frozen_paths.py`` (issue #192).

Verifies that ``get_manager_dir()``, ``get_worker_dir()``, and
``get_install_root()`` return the correct sibling-component paths regardless
of which component process (launcher, wizard, tray_helper, worker, manager)
is the caller in frozen mode.

Test plan references: TP-3, TP-6 through TP-13.
"""

import sys

import pytest

from shared.frozen_paths import get_install_root, get_manager_dir, get_worker_dir


_COMPONENTS = [
    ('manager', 'run_manager.exe'),
    ('launcher', 'run_launcher.exe'),
    ('worker', 'run_worker.exe'),
    ('wizard', 'run_wizard.exe'),
    ('tray_helper', 'run_tray_helper.exe'),
]


class TestFrozenModeCrossComponentPaths:
    """get_manager_dir / get_worker_dir return correct sibling dirs
    regardless of which component process is calling them (FR-TESTS4)."""

    @pytest.fixture()
    def _layout(self, mocker, tmp_path):
        """Build the full bin/<component>/<exe> sibling tree and set frozen=True."""
        bin_dir = tmp_path / 'bin'
        for component, binary in _COMPONENTS:
            (bin_dir / component / binary).parent.mkdir(parents=True, exist_ok=True)
        mocker.patch.object(sys, 'frozen', True, create=True)
        return bin_dir

    def _stub_caller(self, mocker, bin_dir, component, binary):
        """Touch the exe file and point sys.executable at it."""
        exe = bin_dir / component / binary
        exe.touch()
        mocker.patch.object(sys, 'executable', str(exe))

    # TP-3: get_install_root from the launcher process
    def test_get_install_root_frozen_from_launcher(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'launcher', 'run_launcher.exe')
        result = get_install_root()
        assert result == _layout.resolve()

    # TP-6: launcher calling get_manager_dir
    def test_launcher_get_manager_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'launcher', 'run_launcher.exe')
        result = get_manager_dir()
        assert result == _layout / 'manager'
        assert result != _layout / 'launcher'  # must NOT return caller's dir

    # TP-7: launcher calling get_worker_dir
    def test_launcher_get_worker_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'launcher', 'run_launcher.exe')
        result = get_worker_dir()
        assert result == _layout / 'worker'

    # TP-8: wizard calling get_manager_dir
    def test_wizard_get_manager_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'wizard', 'run_wizard.exe')
        result = get_manager_dir()
        assert result == _layout / 'manager'

    # TP-9: wizard calling get_worker_dir
    def test_wizard_get_worker_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'wizard', 'run_wizard.exe')
        result = get_worker_dir()
        assert result == _layout / 'worker'

    # TP-10: tray_helper calling get_manager_dir
    def test_tray_helper_get_manager_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'tray_helper', 'run_tray_helper.exe')
        result = get_manager_dir()
        assert result == _layout / 'manager'

    # TP-11: tray_helper calling get_worker_dir
    def test_tray_helper_get_worker_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'tray_helper', 'run_tray_helper.exe')
        result = get_worker_dir()
        assert result == _layout / 'worker'

    # TP-12: worker process calling get_manager_dir (cross-component self-to-sibling)
    def test_worker_get_manager_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'worker', 'run_worker.exe')
        result = get_manager_dir()
        assert result == _layout / 'manager'

    # TP-13: manager process calling get_worker_dir (cross-component self-to-sibling)
    def test_manager_get_worker_dir(self, mocker, _layout):
        self._stub_caller(mocker, _layout, 'manager', 'run_manager.exe')
        result = get_worker_dir()
        assert result == _layout / 'worker'
