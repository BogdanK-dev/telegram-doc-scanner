@echo off
rem ===== ScanBot: document scanner bot for Telegram =====
rem Double-click this file to start the bot. Close the window to stop it.
cd /d "%~dp0"
title ScanBot - Telegram document scanner

if not exist "%~dp0run.py" (
    echo Please extract the ZIP archive first ^(right click - "Extract All"^),
    echo then run start.bat from the extracted folder.
    pause
    exit /b 1
)

set "PYEXE="
py -3 -c "import sys" >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (
    python -c "import sys" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE goto nopython

%PYEXE% run.py
if errorlevel 1 pause
exit /b

:nopython
echo.
echo  Python is not installed on this computer.
echo.
echo  1) Install Python 3.12 or newer: https://www.python.org/downloads/
echo     IMPORTANT: tick "Add python.exe to PATH" in the installer.
echo     (or in PowerShell:  winget install -e --id Python.Python.3.13 )
echo  2) Run start.bat again.
echo.
start "" https://www.python.org/downloads/
pause
exit /b 1
