@echo off
REM Share the FSL tracker dashboard with a read-only link (no install needed
REM for viewers; the link works from anywhere until this window is closed).
REM Requires the tracker to be running (start_tracker.bat).
cd /d "%~dp0"
echo ============================================
echo  FSL Tracker - share link
echo ============================================
echo A temporary public link will appear below.
echo Anyone with it can VIEW the dashboard (read-only).
echo The link stops working when you close this window.
echo.
tools\cloudflared.exe tunnel --url http://127.0.0.1:8787 --no-autoupdate
