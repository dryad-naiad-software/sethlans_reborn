; SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
;
; SPDX-License-Identifier: GPL-2.0-or-later

; Windows Firewall allow rules for Unreal Engine's command-line editor
; (issue #233). Included by sethlans.nsi.
;
; UE 5.8's TraceLog always opens a TCP control listener on every interface
; (port 1985, or a random fallback port in 32768-40959 when 1985 is taken)
; and it cannot be turned off at runtime, so Windows Firewall would prompt
; once per machine for UnrealEditor-Cmd.exe. An unattended render node must
; never show that prompt, so the installer pre-creates one inbound TCP
; allow rule per supported engine.
;
; Engines live outside $INSTDIR, in each machine's UE installs. The bundled
; worker lists them with the same detection the farm uses
;   run_worker.exe --list-unreal-editors --output <record file>
; (fixed local disks only, never a UNC path, launcher installs outside
; Program Files untrusted, supported engine builds only), one
; "<rule name><TAB><editor path>" line per engine. That file, kept in
; $INSTDIR, is also the record of the rules this installer created:
; netsh cannot wildcard rule names, so upgrade and uninstall delete exactly
; the names it lists, then delete the file.
;
; Rules are program-scoped, not port-scoped: TraceLog's fallback port is
; random within an 8192-port range, so a port-scoped rule would either miss
; the fallback (and the prompt would come back) or have to cover the whole
; range, and later engine builds may move the port.
;
; An engine installed after Sethlans gets no rule: re-run the installer
; (it upgrades in place) to add one; otherwise Windows prompts once for it.
; Engines trusted only through SETHLANS_UNREAL_ENGINE_PATHS are not listed
; either. If the worker cannot list the engines, the install still succeeds
; and simply adds no Unreal rules.

!include "TextFunc.nsh"

!define UNREAL_RULES_FILE "unreal_firewall_rules.txt"
!define UNREAL_RULE_PREFIX "Sethlans Unreal Editor ("
!define UNREAL_LIST_ARG "--list-unreal-editors"
!define UNREAL_OUTPUT_OPT "--output"
!define UNREAL_LIST_TIMEOUT_MS 60000

; Parse one record-file line held in $R0 into $R1 (rule name) and $R2
; (program path). Leaves both empty unless the line is exactly
; "<name><TAB><path>", the name starts with ${UNREAL_RULE_PREFIX} and the
; line has no double quote (which could break out of a netsh argument).
; Clobbers $R0 and $R3-$R5. Insert at most once per function (fixed labels).
!macro _SETHLANS_UNREAL_PARSE_LINE
  ${TrimNewLines} $R0 $R0
  StrCpy $R1 ""
  StrCpy $R2 ""
  StrCpy $R3 0
  StrCpy $R4 -1
  sur_scan:
    StrCpy $R5 $R0 1 $R3
    StrCmp $R5 "" sur_scanned
    StrCmp $R5 "$\"" sur_bad
    StrCmp $R5 "$\t" 0 sur_next
    IntCmp $R4 -1 0 sur_bad sur_bad
    StrCpy $R4 $R3
  sur_next:
    IntOp $R3 $R3 + 1
    Goto sur_scan
  sur_scanned:
    IntCmp $R4 -1 sur_bad
    StrCpy $R1 $R0 $R4
    IntOp $R3 $R4 + 1
    StrCpy $R2 $R0 "" $R3
    StrLen $R3 "${UNREAL_RULE_PREFIX}"
    StrCpy $R5 $R1 $R3
    StrCmp $R5 "${UNREAL_RULE_PREFIX}" sur_done
  sur_bad:
    StrCpy $R1 ""
    StrCpy $R2 ""
  sur_done:
!macroend

; Delete every rule named in $INSTDIR\${UNREAL_RULES_FILE}, then the file.
; A no-op when the file is absent. Preserves all registers.
!macro _SETHLANS_UNREAL_DELETE_FUNC UN
Function ${UN}DeleteUnrealEditorFirewallRules
  Push $0
  Push $1
  Push $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  ClearErrors
  FileOpen $0 "$INSTDIR\${UNREAL_RULES_FILE}" r
  IfErrors dur_done
  dur_loop:
    ClearErrors
    FileRead $0 $R0
    IfErrors dur_close
    !insertmacro _SETHLANS_UNREAL_PARSE_LINE
    StrCmp $R1 "" dur_loop
    nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="$R1"'
    Pop $1
    Goto dur_loop
  dur_close:
    FileClose $0
    Delete "$INSTDIR\${UNREAL_RULES_FILE}"
  dur_done:
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
  Pop $1
  Pop $0
FunctionEnd
!macroend

!insertmacro _SETHLANS_UNREAL_DELETE_FUNC ""
!insertmacro _SETHLANS_UNREAL_DELETE_FUNC "un."

; Remove any rules from an earlier run, ask the installed worker for the
; supported editors, and add one inbound TCP allow rule per editor. Call
; after the worker bundle is on disk. Preserves all registers.
Function AddUnrealEditorFirewallRules
  Push $0
  Push $1
  Push $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  Call DeleteUnrealEditorFirewallRules
  IfFileExists "$INSTDIR\bin\worker\run_worker.exe" 0 aur_done
  nsExec::ExecToLog /TIMEOUT=${UNREAL_LIST_TIMEOUT_MS} '"$INSTDIR\bin\worker\run_worker.exe" ${UNREAL_LIST_ARG} ${UNREAL_OUTPUT_OPT} "$INSTDIR\${UNREAL_RULES_FILE}"'
  Pop $1
  StrCmp $1 "0" aur_read
    DetailPrint "Could not list Unreal Engine installs ($1); no Unreal firewall rules added."
    Delete "$INSTDIR\${UNREAL_RULES_FILE}"
    Goto aur_done
  aur_read:
  ClearErrors
  FileOpen $0 "$INSTDIR\${UNREAL_RULES_FILE}" r
  IfErrors aur_done
  aur_loop:
    ClearErrors
    FileRead $0 $R0
    IfErrors aur_close
    !insertmacro _SETHLANS_UNREAL_PARSE_LINE
    StrCmp $R1 "" aur_loop
    StrCmp $R2 "" aur_loop
    DetailPrint "Allowing $R2 through Windows Firewall ($R1)."
    nsExec::ExecToLog 'netsh advfirewall firewall add rule name="$R1" dir=in action=allow protocol=TCP profile=private,public,domain program="$R2"'
    Pop $1
    Goto aur_loop
  aur_close:
    FileClose $0
  aur_done:
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
  Pop $1
  Pop $0
FunctionEnd
