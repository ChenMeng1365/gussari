@echo off
rem ============================================
rem  gussari - AI Credential Keeper Launcher (hidden/background)
rem  Double-click: start python server.py in background, no console window
rem  Optional arg: start.bat 3366  (custom port)
rem  Log: server.log   Stop: stop.bat
rem  For zero flash window: double-click start-hidden.vbs directly
rem ============================================
set "PORT=%~1"

rem Use --version instead of where, to bypass WindowsApps store stub
python --version >nul 2>nul
if errorlevel 1 (
    echo [Error] Python not found. Please install from https://www.python.org/
    pause
    exit /b 1
)

rem Route through wscript to run hidden
wscript "%~dp0start-hidden.vbs" %PORT%
