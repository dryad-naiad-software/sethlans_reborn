# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The native installer builds no longer need the old setup wizard bundle.

The setup rebuild replaced the standalone wizard with a setup page served
by the manager, and ``packaging/pyinstaller/wizard.spec`` is gone, so
``dist/wizard`` is never built. Any installer line that still installs,
requires or signs that bundle would break every installer build.

The Windows installer keeps two cleanup steps for good, because an older
install may still run the wizard or hold its firewall rule (FR-IN-1/2):
the ``run_wizard.exe`` taskkill and the "Sethlans Wizard (Setup)" rule
delete, in both the upgrade path and the uninstaller.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NSI_PATH = REPO_ROOT / "packaging" / "windows" / "sethlans.nsi"
BUILD_SCRIPTS = [
    REPO_ROOT / "packaging" / "macos" / "build_dmg.sh",
    REPO_ROOT / "packaging" / "linux" / "build_run.sh",
]
OLD_WIZARD_RULE = "Sethlans Wizard (Setup)"


@pytest.fixture(scope="module")
def nsi_contents() -> str:
    assert NSI_PATH.is_file(), f"Expected NSI script at {NSI_PATH}"
    return NSI_PATH.read_text(encoding="utf-8")


def _code_lines(text: str, comment: str) -> list[str]:
    """Lines that are not blank and not whole-line comments."""
    return [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(comment)
    ]


def test_nsi_installs_no_wizard_bundle(nsi_contents: str) -> None:
    """Chunk 7a: wizard.spec is gone, so an NSIS line that installs dist\\wizard breaks every Windows build."""
    for line in _code_lines(nsi_contents, ";"):
        assert "\\wizard" not in line.lower(), (
            f"sethlans.nsi still installs or targets the wizard bundle:\n  {line.strip()}"
        )
    assert f'add rule name="{OLD_WIZARD_RULE}"' not in nsi_contents, (
        "sethlans.nsi must not add the old wizard firewall rule; nothing listens for it."
    )


@pytest.mark.parametrize(
    "block_pattern",
    [r"Function \.onInit\b(.*?)\bFunctionEnd", r'Section "Uninstall"(.*?)\bSectionEnd'],
    ids=["onInit_upgrade", "uninstall"],
)
def test_nsi_keeps_old_wizard_cleanup(nsi_contents: str, block_pattern: str) -> None:
    match = re.search(block_pattern, nsi_contents, re.DOTALL)
    assert match, f"Expected a block matching {block_pattern!r} in sethlans.nsi."
    body = match.group(1)
    assert re.search(r"taskkill\s+/F\s+/IM\s+run_wizard\.exe", body), (
        "Keep the run_wizard.exe taskkill: an older install may still run the wizard."
    )
    assert f'delete rule name="{OLD_WIZARD_RULE}"' in body, (
        f"Keep the {OLD_WIZARD_RULE!r} firewall delete: older installs added that rule."
    )


@pytest.mark.parametrize("script", BUILD_SCRIPTS, ids=lambda p: p.name)
def test_build_script_needs_no_wizard_bundle(script: Path) -> None:
    """Chunk 7a: dist/wizard is never built, so a DMG or .run line that needs it breaks the build."""
    assert script.is_file(), f"Expected build script at {script}"
    for line in _code_lines(script.read_text(encoding="utf-8"), "#"):
        assert "wizard" not in line.lower(), (
            f"{script.name} still requires, copies or signs the wizard bundle:\n  {line.strip()}"
        )
