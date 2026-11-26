@echo off
rem Attendance MIS (HSCO) - one-click install / repair / upgrade on the server PC.
rem Double-click it. Windows asks for administrator rights; everything else is automatic
rem (details: docs\DEPLOYMENT.md). Safe to run again at any time.
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
title Installing Attendance MIS
rem started from PowerShell 7? its module path breaks Windows PowerShell 5.1 modules - use the default one
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install_windows.ps1" %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo   The installation did not finish - see the message above, then run Install.cmd again.
pause
exit /b %RC%
