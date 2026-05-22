# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``launcher/manager_exe_resolver.py``.

Covers the FR-LAUNCHER2 path-hardening checks and the issue #192 fix that
changes the bounds check anchor from ``get_app_dir()`` (caller's own
component dir) to ``get_install_root()`` (the common ``bin/`` parent).

Test plan references: TP-14, TP-15, TP-16.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# ---- Happy-path: sibling layout (FR-TESTS6, TP-14) -------------------------

class TestManagerExeResolverHappyPath:
    """manager_exe() resolves correctly when called from a launcher-like
    process in a sibling layout (bin/launcher/ ≠ bin/manager/).

    Replaces the old same-directory mock that hid the #192 regression.
    """

    def test_frozen_mode_resolves_and_returns_path(self, mocker, tmp_path):
        from launcher import manager_exe_resolver as mer
        import shared.frozen_paths as fp

        bin_dir = tmp_path / 'bin'
        # Caller perspective: launcher process
        launcher_dir = bin_dir / 'launcher'
        launcher_dir.mkdir(parents=True)
        # Target: manager exe lives in a sibling directory
        manager_dir = bin_dir / 'manager'
        manager_dir.mkdir(parents=True)
        exe_name = 'run_manager.exe' if sys.platform == 'win32' else 'run_manager'
        manager_exe_file = manager_dir / exe_name
        manager_exe_file.write_text('fake', encoding='utf-8')

        mocker.patch.object(mer, 'is_frozen', return_value=True)
        mocker.patch.object(fp, 'get_manager_dir', return_value=manager_dir)
        mocker.patch.object(fp, 'get_install_root', return_value=bin_dir)

        resolved = mer.manager_exe()
        assert resolved == Path(manager_exe_file).resolve()


# ---- Regression guard: bounds check uses install_root (FR-TESTS7, TP-15) ---

class TestManagerExeResolverBoundsCheckAnchor:
    """Regression guard: the bounds check is anchored at get_install_root(),
    not at the caller's own component directory (get_app_dir()).

    In the pre-fix code, get_app_dir() returned bin/launcher/ when called
    from the launcher. The manager exe at bin/manager/run_manager.exe is NOT
    under bin/launcher/, so the old code raised. This test confirms the new
    anchor (bin/) accepts the cross-component path.
    """

    def test_resolver_bounds_check_uses_install_root_not_caller_dir(self, mocker, tmp_path):
        from launcher import manager_exe_resolver as mer
        import shared.frozen_paths as fp

        bin_dir = tmp_path / 'bin'
        manager_dir = bin_dir / 'manager'
        manager_dir.mkdir(parents=True)
        exe_name = 'run_manager.exe' if sys.platform == 'win32' else 'run_manager'
        manager_exe_file = manager_dir / exe_name
        manager_exe_file.write_text('fake', encoding='utf-8')

        mocker.patch.object(mer, 'is_frozen', return_value=True)
        mocker.patch.object(fp, 'get_manager_dir', return_value=manager_dir)
        # install_root = bin/ (correct cross-component boundary)
        mocker.patch.object(fp, 'get_install_root', return_value=bin_dir)

        # Must succeed: bin/manager/run_manager.exe IS under bin/
        resolved = mer.manager_exe()
        assert resolved == Path(manager_exe_file).resolve()


# ---- Symlink-escape rejection (TP-16) ---------------------------------------

class TestManagerExeResolverSymlinkEscape:
    """FR-LAUNCHER2 bounds check: if the resolved path escapes the install
    root, manager_exe() MUST raise RuntimeError.

    Uses get_install_root as the boundary (issue #192 fix).
    """

    def test_rejects_symlink_escape(self, mocker, tmp_path):
        from launcher import manager_exe_resolver as mer
        import shared.frozen_paths as fp

        bin_dir = tmp_path / 'bin'
        outside_dir = tmp_path / 'outside'
        outside_dir.mkdir(parents=True)

        exe_name = 'run_manager.exe' if sys.platform == 'win32' else 'run_manager'
        outside_exe = outside_dir / exe_name
        outside_exe.write_text('escape', encoding='utf-8')

        mocker.patch.object(mer, 'is_frozen', return_value=True)
        # Candidate resolves to a file outside the install root
        mocker.patch.object(fp, 'get_manager_dir', return_value=outside_dir)
        mocker.patch.object(fp, 'get_install_root', return_value=bin_dir)

        with pytest.raises(RuntimeError) as excinfo:
            mer.manager_exe()

        assert 'escapes install root' in str(excinfo.value)


# ---- Missing file -----------------------------------------------------------

class TestManagerExeResolverMissingFile:
    """FR-LAUNCHER2: if the candidate path does not exist,
    .resolve(strict=True) raises FileNotFoundError. manager_exe() must
    propagate it.
    """

    def test_rejects_missing_file(self, mocker, tmp_path):
        from launcher import manager_exe_resolver as mer
        import shared.frozen_paths as fp

        manager_dir = tmp_path / 'bin' / 'manager'
        manager_dir.mkdir(parents=True)
        # Do NOT create the exe file

        mocker.patch.object(mer, 'is_frozen', return_value=True)
        mocker.patch.object(fp, 'get_manager_dir', return_value=manager_dir)
        mocker.patch.object(fp, 'get_install_root', return_value=tmp_path / 'bin')

        with pytest.raises((FileNotFoundError, RuntimeError)):
            mer.manager_exe()


# ---- Directory instead of file ----------------------------------------------

class TestManagerExeResolverDirectory:
    """FR-LAUNCHER2: if the resolved path is a directory (not a file),
    .is_file() returns False and manager_exe() must raise RuntimeError.
    """

    def test_rejects_directory(self, mocker, tmp_path):
        from launcher import manager_exe_resolver as mer
        import shared.frozen_paths as fp

        bin_dir = tmp_path / 'bin'
        manager_dir = bin_dir / 'manager'
        manager_dir.mkdir(parents=True)
        exe_name = 'run_manager.exe' if sys.platform == 'win32' else 'run_manager'
        # Create a *directory* at the exe path instead of a file
        dir_at_exe_path = manager_dir / exe_name
        dir_at_exe_path.mkdir()

        mocker.patch.object(mer, 'is_frozen', return_value=True)
        mocker.patch.object(fp, 'get_manager_dir', return_value=manager_dir)
        mocker.patch.object(fp, 'get_install_root', return_value=bin_dir)

        with pytest.raises(RuntimeError) as excinfo:
            mer.manager_exe()

        assert 'not a regular file' in str(excinfo.value)


# ---- Source-mode guard ------------------------------------------------------

class TestManagerExeResolverSourceMode:
    """manager_exe() must raise in source mode — callers should use
    sys.executable + manage.py argv instead.
    """

    def test_raises_in_source_mode(self):
        from launcher import manager_exe_resolver as mer
        with pytest.raises(RuntimeError, match='source mode'):
            mer.manager_exe()
