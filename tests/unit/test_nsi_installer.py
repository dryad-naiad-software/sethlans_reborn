# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the Windows NSIS installer script.

Covers GitHub issue #68: copying ``run_launcher.exe`` to
``$INSTDIR\\sethlans.exe`` orphans the PyInstaller one-dir exe from its
``_internal\\`` sibling directory, producing "Failed to load Python DLL"
at launch. The fix keeps shortcuts and the DisplayIcon pointing at the
original location under ``$INSTDIR\\bin\\launcher\\``.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NSI_PATH = REPO_ROOT / "packaging" / "windows" / "sethlans.nsi"
LAUNCHER_TARGET = r"$INSTDIR\bin\launcher\run_launcher.exe"
ORPHANED_TARGET = r"$INSTDIR\sethlans.exe"


@pytest.fixture(scope="module")
def nsi_contents() -> str:
    assert NSI_PATH.is_file(), f"Expected NSI script at {NSI_PATH}"
    return NSI_PATH.read_text(encoding="utf-8")


class TestNsiInstallerRegression:
    """Static assertions guarding the #68 fix in sethlans.nsi."""

    def test_no_copyfiles_of_launcher_to_sethlans_exe(self, nsi_contents: str) -> None:
        # Any CopyFiles line that copies run_launcher.exe to sethlans.exe re-introduces the bug.
        pattern = re.compile(
            r"CopyFiles\s+.*run_launcher\.exe.*sethlans\.exe",
            re.IGNORECASE,
        )
        assert not pattern.search(nsi_contents), (
            "Regression: CopyFiles of run_launcher.exe to $INSTDIR\\sethlans.exe orphans "
            "the PyInstaller one-dir exe from its _internal\\ directory (issue #68)."
        )

    def test_no_shortcut_targets_orphaned_sethlans_exe(self, nsi_contents: str) -> None:
        pattern = re.compile(
            r'CreateShortCut\s+"[^"]+"\s+"\$INSTDIR\\sethlans\.exe"',
            re.IGNORECASE,
        )
        assert not pattern.search(nsi_contents), (
            "Regression: shortcut points at $INSTDIR\\sethlans.exe instead of the real launcher."
        )

    def test_no_display_icon_points_at_orphaned_exe(self, nsi_contents: str) -> None:
        pattern = re.compile(
            r'DisplayIcon"?\s+"\$INSTDIR\\sethlans\.exe"',
            re.IGNORECASE,
        )
        assert not pattern.search(nsi_contents), (
            "Regression: DisplayIcon points at orphaned $INSTDIR\\sethlans.exe."
        )

    def test_launcher_referenced_as_shortcut_or_icon_target(self, nsi_contents: str) -> None:
        assert LAUNCHER_TARGET in nsi_contents, (
            f"Expected shortcuts/DisplayIcon to reference {LAUNCHER_TARGET} directly."
        )
        # Must be used at least twice: Start Menu shortcut + DisplayIcon (Desktop shortcut adds a third).
        assert nsi_contents.count(LAUNCHER_TARGET) >= 2, (
            "Expected the launcher path to be used for both a shortcut and DisplayIcon."
        )

    def test_uninstaller_does_not_delete_orphaned_sethlans_exe(self, nsi_contents: str) -> None:
        pattern = re.compile(
            r'Delete\s+"\$INSTDIR\\sethlans\.exe"',
            re.IGNORECASE,
        )
        assert not pattern.search(nsi_contents), (
            "Regression: uninstaller still deletes the removed $INSTDIR\\sethlans.exe copy."
        )
