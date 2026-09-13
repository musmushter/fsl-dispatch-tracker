@echo off
REM One-click setup for the FSL Dispatch Tracker.
REM Keeps the window open no matter what, so errors can be read/copied.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_tracker.ps1"
echo.
echo ------------------------------------------------------------
echo Setup window finished. If something failed above, copy
echo this whole window and send it back.
pause
