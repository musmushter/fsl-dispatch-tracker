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
echo.
echo [3] Files:
if not exist tracker.py (echo     FAIL: tracker.py missing in %cd% & goto end)
echo     tracker.py found in %cd%
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
