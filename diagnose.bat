@echo off
cd /d "%~dp0"
echo ============================================
echo  FSL TRACKER - DIAGNOSTIC RUN
echo  (this window stays open; copy ALL its text)
echo ============================================
echo.
echo [1] Python check:
set PYEXE=
py -3 --version 2>nul && set PYEXE=py -3
if not defined PYEXE python --version 2>nul && set PYEXE=python
if not defined PYEXE (
    echo     FAIL: no Python 3 found. Run SETUP.bat first.
    goto end
)
echo     OK: %PYEXE%
echo.
echo [2] Python packages:
%PYEXE% -c "import websockets; print('    websockets OK', websockets.__version__)" 2>&1
if errorlevel 1 (
    echo     FAIL: websockets missing. Fix: %PYEXE% -m pip install websockets
    goto end
)
%PYEXE% -c "import tzdata; print('    tzdata OK')" 2>&1
if errorlevel 1 (
    echo     FAIL: tzdata missing. Fix: %PYEXE% -m pip install tzdata
    goto end
)
echo.
echo [3] Files:
if not exist tracker.py (echo     FAIL: tracker.py missing in %cd% & goto end)
echo     tracker.py found in %cd%
echo.
echo [3b] Console Chrome (debug port 9222):
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 'http://127.0.0.1:9222/json/version').StatusCode | Out-Null; Write-Host '    OK: Chrome debug port reachable' } catch { Write-Host '    NOT REACHABLE - start_chrome.bat must run FIRST and stay open' }"
echo.
echo [4] Starting tracker IN THIS WINDOW (errors will show here):
echo ------------------------------------------------------------
%PYEXE% tracker.py
echo -----------------------------------------------------------
echo [5] Tracker exited with code %errorlevel%.
:end
echo.
echo Copy this whole window (right-click title bar -> Edit -> Select All, Enter)
echo and send it back.
pause
