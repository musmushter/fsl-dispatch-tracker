@echo off
cd /d "%~dp0"
REM pick a Python: py launcher first, then plain python
set PYEXE=
py -3 --version >nul 2>&1 && set PYEXE=py -3
if not defined PYEXE python --version >nul 2>&1 && set PYEXE=python
if not defined PYEXE (
    echo Python not found. Run SETUP.bat first.
    pause
    exit /b 1
)
echo Starting FSL tracker...
start "FSL Tracker" cmd /k %PYEXE% tracker.py
echo Tracker started in the "FSL Tracker" window.
echo Dashboard: http://127.0.0.1:8787/dashboard.html
echo (keep the console Chrome window open while tracking)
pause
