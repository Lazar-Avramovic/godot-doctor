; Windows installer customisation for Godot Doctor.
;
; The installer ships both the desktop app and the CLI. The CLI is the same
; frozen backend that Electron spawns for --gui, exposed through a shim in a
; dedicated bin directory so the install root never lands on PATH itself.
;
; PATH edits go through PowerShell (registry untouched semantics preserved)
; because NSIS has no case-insensitive string search; the change is broadcast
; afterwards so new shells see it without a sign-out.

!include "WinMessages.nsh"

!macro GD_WRITE_PATH_HELPER
  FileOpen $0 "$INSTDIR\path.ps1" w
  FileWrite $0 "param([string]$$Action, [string]$$Dir)$\r$\n"
  FileWrite $0 "$$ErrorActionPreference = 'Stop'$\r$\n"
  FileWrite $0 "$$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $$true)$\r$\n"
  FileWrite $0 "if ($$null -eq $$key) { exit 0 }$\r$\n"
  FileWrite $0 "$$current = [string]$$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)$\r$\n"
  FileWrite $0 "$$parts = @($$current -split ';' | Where-Object { $$_ -ne '' })$\r$\n"
  FileWrite $0 "$$norm = $$Dir.TrimEnd('\')$\r$\n"
  FileWrite $0 "$$hit = @($$parts | Where-Object { $$_.TrimEnd('\') -ieq $$norm }).Count$\r$\n"
  FileWrite $0 "if ($$Action -eq 'add') {$\r$\n"
  FileWrite $0 "  if ($$hit -eq 0) { $$parts += $$Dir }$\r$\n"
  FileWrite $0 "} else {$\r$\n"
  FileWrite $0 "  $$parts = @($$parts | Where-Object { $$_.TrimEnd('\') -ine $$norm })$\r$\n"
  FileWrite $0 "}$\r$\n"
  FileWrite $0 "$$new = ($$parts -join ';')$\r$\n"
  FileWrite $0 "if ($$new -ne $$current) { $$key.SetValue('Path', $$new, [Microsoft.Win32.RegistryValueKind]::ExpandString) }$\r$\n"
  FileWrite $0 "$$key.Close()$\r$\n"
  FileClose $0
!macroend

!macro customInstall
  CreateDirectory "$INSTDIR\bin"

  FileOpen $0 "$INSTDIR\bin\godot-doctor.cmd" w
  FileWrite $0 "@echo off$\r$\n"
  FileWrite $0 "setlocal$\r$\n"
  FileWrite $0 '"%~dp0..\resources\backend\godot-doctor-backend.exe" %*$\r$\n'
  FileWrite $0 "exit /b %errorlevel%$\r$\n"
  FileClose $0

  FileOpen $0 "$INSTDIR\uninstall.cmd" w
  FileWrite $0 "@echo off$\r$\n"
  FileWrite $0 "setlocal EnableExtensions$\r$\n"
  FileWrite $0 "set $\"SCRIPT_DIR=%~dp0$\"$\r$\n"
  FileWrite $0 "set $\"PURGE=$\"$\r$\n"
  FileWrite $0 "set $\"ARGS=$\"$\r$\n"
  FileWrite $0 ":parse$\r$\n"
  FileWrite $0 'if "%~1"=="" goto run$\r$\n'
  FileWrite $0 'if /I "%~1"=="--purge" ($\r$\n'
  FileWrite $0 "  set $\"PURGE=1$\"$\r$\n"
  FileWrite $0 ") else ($\r$\n"
  FileWrite $0 "  set $\"ARGS=%ARGS% %1$\"$\r$\n"
  FileWrite $0 ")$\r$\n"
  FileWrite $0 "shift$\r$\n"
  FileWrite $0 "goto parse$\r$\n"
  FileWrite $0 ":run$\r$\n"
  FileWrite $0 'taskkill /IM "Godot Doctor.exe" /T /F >nul 2>&1$\r$\n'
  FileWrite $0 "if defined PURGE rmdir /s /q $\"%APPDATA%\Godot Doctor$\" 2>nul$\r$\n"
  FileWrite $0 '"%SCRIPT_DIR%Uninstall Godot Doctor.exe" %ARGS%$\r$\n'
  FileClose $0

  !insertmacro GD_WRITE_PATH_HELPER
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\path.ps1" add "$INSTDIR\bin"'
  Pop $0
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=2000
!macroend

!macro customUnInstall
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\path.ps1" remove "$INSTDIR\bin"'
  Pop $0
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=2000
!macroend
