; SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
;
; SPDX-License-Identifier: GPL-2.0-or-later

; Sethlans NSIS Installer Script
; Builds a Windows installer from PyInstaller output directories.

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; --- Installer metadata ---
!define PRODUCT_NAME "Sethlans"
!define PRODUCT_PUBLISHER "Dryad and Naiad Software LLC"
!define PRODUCT_WEB_SITE "https://github.com/dryad-naiad-software/sethlans_reborn"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
!define PRODUCT_REG_KEY "Software\Sethlans"

; Version is injected at build time via /DPRODUCT_VERSION=x.y.z
!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.1.0"
!endif

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "sethlans-${PRODUCT_VERSION}-windows-x64.exe"
InstallDir "$PROGRAMFILES64\Sethlans"
InstallDirRegKey HKLM "${PRODUCT_REG_KEY}" "InstallDir"
RequestExecutionLevel admin

; --- UI configuration ---
!define MUI_ABORTWARNING
!define MUI_ICON "sethlans.ico"
!define MUI_UNICON "sethlans.ico"

; --- Pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; --- Silent install parameters ---
; /TOPOLOGY=manager|manager_worker|worker
; /MANAGER_HOST=https://host:port
; /AUTOSTART=0|1
; /SKIP_WIZARD=0|1
; Enrollment key: via SETHLANS_ENROLLMENT_KEY env var (never CLI arg)

Var TOPOLOGY
Var MANAGER_HOST
Var AUTOSTART
Var SKIP_WIZARD

Function .onInit
  ; Read silent install parameters
  ${GetParameters} $0

  StrCpy $TOPOLOGY ""
  StrCpy $MANAGER_HOST ""
  StrCpy $AUTOSTART "1"
  StrCpy $SKIP_WIZARD "0"

  ${GetOptions} $0 "/TOPOLOGY=" $TOPOLOGY
  ${GetOptions} $0 "/MANAGER_HOST=" $MANAGER_HOST
  ${GetOptions} $0 "/AUTOSTART=" $AUTOSTART
  ${GetOptions} $0 "/SKIP_WIZARD=" $SKIP_WIZARD
FunctionEnd

; --- Main install section ---
Section "Sethlans Core" SEC_CORE
  SectionIn RO  ; Required, cannot be deselected

  SetOutPath "$INSTDIR"

  ; Copy PyInstaller output directories
  SetOutPath "$INSTDIR\bin\manager"
  File /r "..\..\dist\manager\*.*"

  SetOutPath "$INSTDIR\bin\worker"
  File /r "..\..\dist\worker\*.*"

  SetOutPath "$INSTDIR\bin\tray_helper"
  File /r "..\..\dist\tray_helper\*.*"

  SetOutPath "$INSTDIR\bin\launcher"
  File /r "..\..\dist\launcher\*.*"

  ; Copy license and version metadata
  SetOutPath "$INSTDIR"
  File "..\..\LICENSE.txt"

  ; Write version.json
  FileOpen $0 "$INSTDIR\version.json" w
  FileWrite $0 '{"version": "${PRODUCT_VERSION}", "platform": "windows-x64"}'
  FileClose $0

  ; Write registry keys
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "Version" "${PRODUCT_VERSION}"

  ; Register uninstaller in Add/Remove Programs
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\bin\launcher\run_launcher.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoRepair" 1

  ; Calculate install size
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" $0

  ; Handle silent install configuration
  StrCmp $TOPOLOGY "" skip_silent_config
    ; Write topology.json to data dir
    ReadEnvStr $1 LOCALAPPDATA
    CreateDirectory "$1\Sethlans"
    FileOpen $0 "$1\Sethlans\topology.json" w
    FileWrite $0 '{"topology": "$TOPOLOGY"}'
    FileClose $0

    ; Write setup_complete sentinel if SKIP_WIZARD=1
    StrCmp $SKIP_WIZARD "1" 0 skip_sentinel
      FileOpen $0 "$1\Sethlans\.setup_complete" w
      FileWrite $0 "setup complete"
      FileClose $0

      ; Perform enrollment if key is in environment
      ReadEnvStr $2 SETHLANS_ENROLLMENT_KEY
      StrCmp $2 "" skip_enrollment
        nsExec::ExecToLog '"$INSTDIR\bin\worker\run_worker.exe" --enroll-and-exit'
      skip_enrollment:
    skip_sentinel:
  skip_silent_config:
SectionEnd

; --- Start Menu shortcuts ---
Section "Start Menu Shortcuts" SEC_STARTMENU
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Sethlans.lnk" "$INSTDIR\bin\launcher\run_launcher.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\uninstall.exe"
SectionEnd

; --- Desktop shortcut (optional) ---
Section /o "Desktop Shortcut" SEC_DESKTOP
  CreateShortCut "$DESKTOP\Sethlans.lnk" "$INSTDIR\bin\launcher\run_launcher.exe"
SectionEnd

; --- Section descriptions ---
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_CORE} "Core Sethlans application files (required)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTMENU} "Create Start Menu shortcuts."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} "Create a Desktop shortcut."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; --- Uninstaller ---
Section "Uninstall"
  ; Stop services if running
  nsExec::ExecToLog 'taskkill /F /IM run_manager.exe'
  nsExec::ExecToLog 'taskkill /F /IM run_worker.exe'
  nsExec::ExecToLog 'taskkill /F /IM run_tray_helper.exe'

  ; Remove files
  RMDir /r "$INSTDIR\bin"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\version.json"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  ; Remove shortcuts
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Sethlans.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
  Delete "$DESKTOP\Sethlans.lnk"

  ; Remove registry keys
  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_REG_KEY}"

  ; Note: user data in %LOCALAPPDATA%\Sethlans is NOT removed
  ; to preserve databases, configs, and render outputs.
SectionEnd
