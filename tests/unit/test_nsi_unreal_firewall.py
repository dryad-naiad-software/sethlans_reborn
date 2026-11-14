# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Static checks for the Windows installer's Unreal editor firewall rules
(issue #233, ``packaging/windows/unreal_firewall.nsh``).

The installer adds one rule per line of the record file the bundled worker
writes, and the upgrade path and the uninstaller delete exactly the names
in that file. These tests keep the add / upgrade-delete / uninstall-delete
blocks symmetric and in step with ``unreal_editor_list``.
"""
import re
from pathlib import Path

import pytest

from sethlans_worker_agent import unreal_editor_list as uel

WINDOWS_DIR = Path(__file__).resolve().parents[2] / "packaging" / "windows"
NSI_PATH = WINDOWS_DIR / "sethlans.nsi"
NSH_PATH = WINDOWS_DIR / "unreal_firewall.nsh"


@pytest.fixture(scope="module")
def nsi() -> str:
    return NSI_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nsh() -> str:
    assert NSH_PATH.is_file(), f"Expected {NSH_PATH}"
    return NSH_PATH.read_text(encoding="utf-8")


def _block(text: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.DOTALL)
    assert match, f"Expected a block starting {start!r}"
    return match.group(1)


def _define(nsh: str, name: str) -> str:
    match = re.search(r'^!define\s+' + name + r'\s+"?([^"\r\n]*)"?\s*$', nsh, re.MULTILINE)
    assert match, f"Expected !define {name}"
    return match.group(1)


def _add_function(nsh: str) -> str:
    return _block(nsh, "Function AddUnrealEditorFirewallRules", "FunctionEnd")


def _delete_function(nsh: str) -> str:
    return _block(nsh, "Function ${UN}DeleteUnrealEditorFirewallRules", "FunctionEnd")


class TestIncludeAndHeader:

    def test_nsi_includes_the_unreal_firewall_header(self, nsi: str) -> None:
        assert re.search(r'^!include\s+"unreal_firewall\.nsh"', nsi, re.MULTILINE)

    def test_spdx_header(self, nsh: str) -> None:
        assert nsh.startswith("; SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC")
        assert "; SPDX-License-Identifier: GPL-2.0-or-later" in nsh.splitlines()[2]

    def test_constants_match_the_worker_subcommand(self, nsh: str) -> None:
        assert _define(nsh, "UNREAL_LIST_ARG") == uel.LIST_ARG
        assert _define(nsh, "UNREAL_OUTPUT_OPT") == uel.OUTPUT_OPT
        assert _define(nsh, "UNREAL_RULE_PREFIX") == uel.rule_name("X").split("X")[0]
        assert _define(nsh, "UNREAL_RULES_FILE").endswith(".txt")


class TestAddRules:

    def test_add_is_inbound_tcp_allow_all_profiles_program_scoped(self, nsh: str) -> None:
        adds = re.findall(r"netsh advfirewall firewall add rule [^']*", nsh)
        assert adds == [
            'netsh advfirewall firewall add rule name="$R1" dir=in action=allow protocol=TCP '
            'profile=private,public,domain program="$R2"'
        ]
        # Program-scoped on purpose: TraceLog falls back to a random port.
        assert "localport" not in adds[0]

    def test_add_runs_the_installed_worker_into_the_record_file(self, nsh: str) -> None:
        body = _add_function(nsh)
        assert ('\'"$INSTDIR\\bin\\worker\\run_worker.exe" ${UNREAL_LIST_ARG} ${UNREAL_OUTPUT_OPT} '
                '"$INSTDIR\\${UNREAL_RULES_FILE}"\'') in body
        assert "/TIMEOUT=" in body

    def test_add_clears_earlier_rules_first(self, nsh: str) -> None:
        body = _add_function(nsh)
        assert body.index("Call DeleteUnrealEditorFirewallRules") < body.index("run_worker.exe")

    def test_failed_listing_adds_nothing(self, nsh: str) -> None:
        body = _add_function(nsh)
        after = body[body.index("${UNREAL_LIST_ARG}"):]
        # The exit code is checked against "0" before the record file is read.
        assert re.search(r'Pop \$1\s+StrCmp \$1 "0" aur_read', after)
        assert after.index('StrCmp $1 "0" aur_read') < after.index("FileOpen")
        failure = after[after.index('StrCmp $1 "0" aur_read'):after.index("aur_read:")]
        assert 'Delete "$INSTDIR\\${UNREAL_RULES_FILE}"' in failure and "Goto aur_done" in failure

    def test_add_called_from_core_section(self, nsi: str) -> None:
        core = _block(nsi, 'Section "Sethlans Core"', "SectionEnd")
        assert "Call AddUnrealEditorFirewallRules" in core
        # After the worker bundle is on disk.
        assert core.index('File /r "${DIST_ROOT}\\worker\\*.*"') < core.index("Call AddUnrealEditorFirewallRules")


class TestDeleteSymmetry:

    def test_delete_function_defined_for_installer_and_uninstaller(self, nsh: str) -> None:
        assert re.search(r'^!insertmacro _SETHLANS_UNREAL_DELETE_FUNC ""', nsh, re.MULTILINE)
        assert re.search(r'^!insertmacro _SETHLANS_UNREAL_DELETE_FUNC "un\."', nsh, re.MULTILINE)

    def test_delete_uses_the_same_record_file_and_name_as_add(self, nsh: str) -> None:
        body = _delete_function(nsh)
        assert 'FileOpen $0 "$INSTDIR\\${UNREAL_RULES_FILE}" r' in body
        assert 'FileOpen $0 "$INSTDIR\\${UNREAL_RULES_FILE}" r' in _add_function(nsh)
        assert re.findall(r"netsh advfirewall firewall delete rule [^']*", nsh) == [
            'netsh advfirewall firewall delete rule name="$R1"']
        assert 'Delete "$INSTDIR\\${UNREAL_RULES_FILE}"' in body
        # Both sides parse lines the same way.
        assert body.count("!insertmacro _SETHLANS_UNREAL_PARSE_LINE") == 1
        assert _add_function(nsh).count("!insertmacro _SETHLANS_UNREAL_PARSE_LINE") == 1

    def test_parser_rejects_quotes_and_foreign_names(self, nsh: str) -> None:
        parser = _block(nsh, "!macro _SETHLANS_UNREAL_PARSE_LINE", "!macroend")
        assert 'StrCmp $R5 "$\\"" sur_bad' in parser
        assert 'StrCmp $R5 "${UNREAL_RULE_PREFIX}" sur_done' in parser

    def test_upgrade_path_deletes_before_running_old_uninstaller(self, nsi: str) -> None:
        on_init = _block(nsi, "Function .onInit", "FunctionEnd")
        assert "Call DeleteUnrealEditorFirewallRules" in on_init
        assert on_init.index("Call DeleteUnrealEditorFirewallRules") < on_init.index("ExecWait")

    def test_uninstall_deletes_before_removing_instdir(self, nsi: str) -> None:
        uninstall = nsi[nsi.index('Section "Uninstall"'):]
        assert "Call un.DeleteUnrealEditorFirewallRules" in uninstall
        rmdir = re.search(r'^\s*RMDir "\$INSTDIR"\s*$', uninstall, re.MULTILINE)
        assert rmdir and uninstall.index("Call un.DeleteUnrealEditorFirewallRules") < rmdir.start()

    def test_calls_live_in_the_right_halves(self, nsi: str) -> None:
        uninstall_idx = nsi.index('Section "Uninstall"')
        assert nsi.index("Call AddUnrealEditorFirewallRules") < uninstall_idx
        assert "Call un.DeleteUnrealEditorFirewallRules" not in nsi[:uninstall_idx]
        assert "Call DeleteUnrealEditorFirewallRules" not in nsi[uninstall_idx:]
        assert "Call AddUnrealEditorFirewallRules" not in nsi[uninstall_idx:]


class TestStackBalance:

    def test_every_nsexec_result_is_popped(self, nsh: str) -> None:
        lines = [line.strip() for line in nsh.splitlines()]
        for index, line in enumerate(lines):
            if line.startswith("nsExec::"):
                assert lines[index + 1].startswith("Pop "), f"nsExec result not popped: {line}"

    @pytest.mark.parametrize("name", ["AddUnrealEditorFirewallRules", "${UN}DeleteUnrealEditorFirewallRules"])
    def test_registers_saved_and_restored_in_reverse(self, nsh: str, name: str) -> None:
        body = _block(nsh, f"Function {name}", "FunctionEnd")
        pushes = re.findall(r"^\s*Push (\$\w+)\s*$", body, re.MULTILINE)
        pops = re.findall(r"^\s*Pop (\$\w+)\s*$", body, re.MULTILINE)
        tail = pops[-len(pushes):]
        assert pushes and tail == list(reversed(pushes))
