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


class TestNsiFirewallRules:
    """GitHub issue #69: installer must add/remove Windows Firewall rules."""

    ADD_RULES = [
        (
            'Sethlans Manager (TCP 8080)',
            'protocol=TCP',
            'localport=8080',
            r'program="$INSTDIR\bin\manager\run_manager.exe"',
        ),
        (
            'Sethlans Worker (TCP 8081)',
            'protocol=TCP',
            'localport=8081',
            r'program="$INSTDIR\bin\worker\run_worker.exe"',
        ),
        (
            'Sethlans Broadcast (UDP 8082)',
            'protocol=UDP',
            'localport=8082',
            r'program="$INSTDIR\bin\manager\run_manager.exe"',
        ),
    ]

    DELETE_RULE_NAMES = [
        'Sethlans Manager (TCP 8080)',
        'Sethlans Worker (TCP 8081)',
        'Sethlans Broadcast (UDP 8082)',
    ]

    def test_add_rules_present_with_all_profiles(self, nsi_contents: str) -> None:
        for name, protocol, localport, program in self.ADD_RULES:
            # Locate the add-rule line for this rule name.
            line_pattern = re.compile(
                r'netsh advfirewall firewall add rule name="' + re.escape(name) + r'"[^\']*',
            )
            match = line_pattern.search(nsi_contents)
            assert match, f"Missing add rule for {name!r}"
            line = match.group(0)
            assert 'dir=in' in line, f"add rule for {name!r} must be dir=in"
            assert 'action=allow' in line, f"add rule for {name!r} must be action=allow"
            assert protocol in line, f"add rule for {name!r} must include {protocol}"
            assert localport in line, f"add rule for {name!r} must include {localport}"
            assert 'profile=private,public,domain' in line, (
                f"add rule for {name!r} must cover private,public,domain profiles"
            )
            assert program in line, f"add rule for {name!r} must target {program}"

    def test_delete_rules_present(self, nsi_contents: str) -> None:
        for name in self.DELETE_RULE_NAMES:
            pattern = re.compile(
                r'netsh advfirewall firewall delete rule name="' + re.escape(name) + r'"',
            )
            assert pattern.search(nsi_contents), f"Missing delete rule for {name!r}"

    def test_add_rules_in_install_section_delete_in_uninstall(self, nsi_contents: str) -> None:
        uninstall_idx = nsi_contents.find('Section "Uninstall"')
        assert uninstall_idx != -1, "Expected a 'Section \"Uninstall\"' block in the NSI."

        # Add-rule lines must appear before the Uninstall section header.
        for name, *_ in self.ADD_RULES:
            add_idx = nsi_contents.find(f'add rule name="{name}"')
            assert add_idx != -1, f"Missing add rule for {name!r}"
            assert add_idx < uninstall_idx, (
                f"add rule for {name!r} must appear in the install section (before Uninstall)."
            )

        # A delete-rule call MUST exist within the Uninstall section.
        # (Phase F5b added duplicate delete-rule calls in .onInit's upgrade
        # path — those are intentional. The Uninstall section must still
        # contain its own copies.)
        uninstall_section = nsi_contents[uninstall_idx:]
        for name in self.DELETE_RULE_NAMES:
            assert f'delete rule name="{name}"' in uninstall_section, (
                f"delete rule for {name!r} must appear in the Uninstall section."
            )

    def test_onInit_upgrade_path_deletes_firewall_rules(self, nsi_contents: str) -> None:
        """Phase G installer-specialist HIGH-3: .onInit must delete firewall
        rules during upgrade so re-adding doesn't produce duplicates."""
        on_init_match = re.search(
            r'Function \.onInit\b(.*?)\bFunctionEnd',
            nsi_contents, re.DOTALL,
        )
        assert on_init_match, "Expected a Function .onInit ... FunctionEnd block."
        on_init_body = on_init_match.group(1)
        for name in self.DELETE_RULE_NAMES:
            assert f'delete rule name="{name}"' in on_init_body, (
                f".onInit upgrade path must delete firewall rule {name!r} "
                "before re-adding it (Phase F5b HIGH-3 fix; prevents stale "
                "duplicate rules after upgrade-in-place)."
            )


class TestNsiUpgradeFlow:
    """GitHub issue #72: installer must auto-upgrade over an existing install."""

    UPGRADE_TASKKILL_EXES = [
        "run_manager.exe",
        "run_worker.exe",
        "run_tray_helper.exe",
        "run_launcher.exe",
    ]

    @pytest.fixture
    def on_init_body(self, nsi_contents: str) -> str:
        """Extract the Function .onInit ... FunctionEnd body."""
        match = re.search(
            r"Function\s+\.onInit\b(.*?)FunctionEnd",
            nsi_contents,
            re.DOTALL,
        )
        assert match, "Expected a Function .onInit ... FunctionEnd block in the NSI."
        return match.group(1)

    def test_on_init_reads_uninstall_string(self, on_init_body: str) -> None:
        pattern = re.compile(
            r'ReadRegStr\s+\$R0\s+HKLM\s+"?\$\{PRODUCT_UNINST_KEY\}"?\s+"UninstallString"',
        )
        assert pattern.search(on_init_body), (
            "Expected .onInit to ReadRegStr $R0 HKLM ${PRODUCT_UNINST_KEY} \"UninstallString\"."
        )

    def test_on_init_execwait_uses_silent_keep_instdir(self, on_init_body: str) -> None:
        # ExecWait '"$R0" /S _?=$INSTDIR' — the _?= form blocks and prevents self-delete.
        pattern = re.compile(
            r'ExecWait\s+\'"\$R0"\s+/S\s+_\?=\$INSTDIR\'',
        )
        assert pattern.search(on_init_body), (
            "Expected .onInit to ExecWait the prior uninstaller with '/S _?=$INSTDIR'."
        )

    def test_on_init_kills_all_sethlans_processes(self, on_init_body: str) -> None:
        for exe in self.UPGRADE_TASKKILL_EXES:
            pattern = re.compile(
                r"taskkill\s+/F\s+/IM\s+" + re.escape(exe),
            )
            assert pattern.search(on_init_body), (
                f"Expected .onInit to taskkill /F /IM {exe} before running old uninstaller."
            )

    def test_on_init_messagebox_guarded_by_ifnot_silent(self, on_init_body: str) -> None:
        # MessageBox must live inside an ${IfNot} ${Silent} ... ${EndIf} guard.
        pattern = re.compile(
            r"IfNot.*?Silent.*?MessageBox",
            re.DOTALL | re.IGNORECASE,
        )
        assert pattern.search(on_init_body), (
            "Expected .onInit MessageBox to be guarded by ${IfNot} ${Silent}."
        )
