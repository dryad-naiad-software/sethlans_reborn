# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher lookup in ``tools/_resolver_smoke.py`` (issue #236).

The macOS installer job now smoke-tests the wizard from inside the
installed ``Sethlans.app``. There the launcher is not a ``launcher/``
sibling of the wizard bundle but ``Contents/MacOS/sethlans``. The #192
resolver harness must still find it, or the check is silently skipped.
"""

from __future__ import annotations

from pathlib import Path

import _resolver_smoke as rs


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_flat_layout_finds_launcher_sibling(tmp_path):
    exe = _touch(tmp_path / "dist" / "launcher" / "run_launcher")
    assert rs._find_launcher_exe(tmp_path / "dist", "run_launcher") == exe


def test_macos_app_layout_finds_contents_macos_sethlans(tmp_path):
    contents = tmp_path / "Sethlans.app" / "Contents"
    exe = _touch(contents / "MacOS" / "sethlans")
    bin_dir = contents / "Resources" / "bin"
    _touch(bin_dir / "wizard" / "run_wizard")
    assert rs._find_launcher_exe(bin_dir, "run_launcher") == exe


def test_flat_launcher_wins_over_app_launcher(tmp_path):
    contents = tmp_path / "Sethlans.app" / "Contents"
    _touch(contents / "MacOS" / "sethlans")
    bin_dir = contents / "Resources" / "bin"
    flat = _touch(bin_dir / "launcher" / "run_launcher")
    assert rs._find_launcher_exe(bin_dir, "run_launcher") == flat


def test_bin_outside_app_resources_is_not_treated_as_app(tmp_path):
    _touch(tmp_path / "MacOS" / "sethlans")
    bin_dir = tmp_path / "opt" / "bin"
    bin_dir.mkdir(parents=True)
    assert rs._find_launcher_exe(bin_dir, "run_launcher") is None


def test_app_layout_without_launcher_returns_none(tmp_path):
    bin_dir = tmp_path / "Sethlans.app" / "Contents" / "Resources" / "bin"
    bin_dir.mkdir(parents=True)
    assert rs._find_launcher_exe(bin_dir, "run_launcher") is None


def test_resolver_check_skips_without_launcher(tmp_path, capsys):
    bin_dir = tmp_path / "Sethlans.app" / "Contents" / "Resources" / "bin"
    _touch(bin_dir / "manager" / "run_manager")
    wizard = bin_dir / "wizard"
    wizard.mkdir(parents=True)
    assert rs.check_manager_exe_resolver(wizard) is True
    assert "#192 SKIPPED" in capsys.readouterr().out
