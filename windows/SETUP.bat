@echo off
REM One-click setup for the FSL Dispatch Tracker.
REM Everything is logged to setup_log.txt; the window always stays open.
cd /d "%~dp0"
echo FSL Tracker setup started %date% %time% > setup_log.txt
where powershell >nul 2>nul
if errorlevel 1 (
    echo PowerShell not found on this machine. >> setup_log.txt
    echo PowerShell not found - contact support. & pause & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_tracker.ps1" >> setup_log.txt 2>&1
echo.
echo ============================================================
echo  Setup finished. Full log saved to setup_log.txt in this
echo  folder - if anything failed, send that file back.
echo ============================================================
notepad setup_log.txt
pause
