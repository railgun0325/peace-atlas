; -*- coding: utf-8 -*-
; Compiled only through installer.py after frozen package/native validation.
Unicode true
RequestExecutionLevel user
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "settings.nsh"
Name "PEACE ATLAS"
OutFile "${OUTPUT}"
!ifdef TEST_ROOT
InstallDir "${TEST_ROOT}"
!else
InstallDir "$LOCALAPPDATA\Programs\PEACE ATLAS"
!endif
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
!define MUI_WELCOMEPAGE_TEXT "This installs PEACE ATLAS for your Windows account. Close the application before updating. Research, profiles and connection settings in AppData are preserved. The application will not start automatically."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Var AllowedRoot
Var DesktopLink
Var MenuRoot
Var GuardResult

!macro PrepareGuard
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File "${STAGING}\installer-guard.ps1"
 File "${STAGING}\installer-manifest.json"
!macroend

!macro SetPaths
 SetShellVarContext current
 !ifdef TEST_ROOT
  StrCpy $AllowedRoot "${TEST_ROOT}"
  StrCpy $DesktopLink "${TEST_SHORTCUTS}\desktop\${INSTANCE}.lnk"
  StrCpy $MenuRoot "${TEST_SHORTCUTS}\start-menu\${INSTANCE}"
 !else
  StrCpy $AllowedRoot "$LOCALAPPDATA\Programs\PEACE ATLAS"
  StrCpy $DesktopLink "$DESKTOP\PEACE ATLAS.lnk"
  StrCpy $MenuRoot "$SMPROGRAMS\PEACE ATLAS"
 !endif
!macroend

!macro Guard Mode
 nsExec::ExecToStack '"$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-guard.ps1" -Mode "${Mode}" -InstallRoot "$INSTDIR" -Manifest "$PLUGINSDIR\installer-manifest.json" -ExpectedManifest "${MANIFEST_SHA}" -Instance "${INSTANCE}" -AllowedRoot "$AllowedRoot" -RegistryKey "${REGISTRY_KEY}" -ProcessName "${PROCESS_NAME}"'
 Pop $GuardResult
 Pop $0
 ${If} $GuardResult != 0
  !ifdef TEST_ROOT
   FileOpen $1 "$EXEDIR\test-failure.log" a
   FileWrite $1 "${Mode}: $GuardResult $0$\r$\n"
   FileClose $1
  !endif
  MessageBox MB_OK|MB_ICONSTOP "$0" /SD IDOK
  SetErrorLevel 10
  Quit
 ${EndIf}
!macroend

Function .onInit
 ${IfNot} ${RunningX64}
  MessageBox MB_OK|MB_ICONSTOP "This package requires 64-bit Windows." /SD IDOK
  SetErrorLevel 10
  Quit
 ${EndIf}
 !insertmacro SetPaths
 !insertmacro PrepareGuard
 !insertmacro Guard "install"
FunctionEnd

Function un.onInit
 !insertmacro SetPaths
 !insertmacro PrepareGuard
 !insertmacro Guard "uninstall"
FunctionEnd

Section "PEACE ATLAS"
 !insertmacro Guard "install"
 !include "payload.nsh"
 SetOutPath "$INSTDIR"
 File "${STAGING}\installer-manifest.json"
 !insertmacro Guard "verify"
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 !insertmacro Guard "record"
 !ifdef TEST_ROOT
 CreateDirectory "${TEST_SHORTCUTS}\desktop"
 !endif
 CreateDirectory "$MenuRoot"
 CreateShortcut "$DesktopLink" "$INSTDIR\PEACE ATLAS.exe"
 CreateShortcut "$MenuRoot\PEACE ATLAS.lnk" "$INSTDIR\PEACE ATLAS.exe"
 CreateShortcut "$MenuRoot\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
 WriteRegStr HKCU "${REGISTRY_KEY}" "DisplayName" "${INSTANCE}"
 WriteRegStr HKCU "${REGISTRY_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
 WriteRegStr HKCU "${REGISTRY_KEY}" "InstallLocation" "$INSTDIR"
 WriteRegStr HKCU "${REGISTRY_KEY}" "DisplayIcon" "$INSTDIR\PEACE ATLAS.exe"
 WriteRegStr HKCU "${REGISTRY_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
 WriteRegStr HKCU "${REGISTRY_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
 WriteRegDWORD HKCU "${REGISTRY_KEY}" "NoModify" 1
 WriteRegDWORD HKCU "${REGISTRY_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
 !insertmacro Guard "uninstall"
 !include "remove.nsh"
 Delete "$INSTDIR\installer-manifest.json"
 Delete "$INSTDIR\installation-state.json"
 Delete "$INSTDIR\Uninstall.exe"
 Delete "$DesktopLink"
 Delete "$MenuRoot\PEACE ATLAS.lnk"
 Delete "$MenuRoot\Uninstall.lnk"
 RMDir "$MenuRoot"
 DeleteRegKey HKCU "${REGISTRY_KEY}"
 ; No recursive removal: unrecognized user files remain in the application root.
 RMDir "$INSTDIR"
SectionEnd
