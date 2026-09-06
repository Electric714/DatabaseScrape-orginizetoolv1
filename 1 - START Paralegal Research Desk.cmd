@echo off
setlocal
title Paralegal Database Tool
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1"
if errorlevel 1 (
  echo.
  echo Setup or startup did not finish. See logs\setup.log for details.
  echo You can try "2 - REPAIR Setup.cmd" and then start again.
  pause
)
endlocal

