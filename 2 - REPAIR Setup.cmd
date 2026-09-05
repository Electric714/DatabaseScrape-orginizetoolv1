@echo off
setlocal
title Repair Public Data Monitor
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" -Repair -SetupOnly
echo.
echo This window can now be closed. Your collected data was not deleted.
pause
endlocal
