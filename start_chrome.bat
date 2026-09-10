@echo off
cd /d "%~dp0"
set CHROME=
for %%P in (
    "C:\Program Files\Google\Chrome\Application\chrome.exe"
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) do (
    if exist %%~f set CHROME=%%~f
    if defined CHROME goto :found
)
echo Chrome not found. Run SETUP.bat first.
pause
exit /b 1
:found
REM fixed profile path (no dependence on the folder this bat sits in)
set PROFILE=%LOCALAPPDATA%\FSLTrackerConsoleProfile
if not exist "%PROFILE%" mkdir "%PROFILE%"
echo Starting console Chrome (profile: %PROFILE%)
echo If a Chrome window opens WITHOUT reaching the console, close ALL Chrome
echo windows and run this again.
start "" "%CHROME%" --remote-debugging-port=9222 --no-first-run --no-default-browser-check --user-data-dir="%PROFILE%" "https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"
echo.
echo Waiting for the debug port...
set /a tries=0
:waitport
timeout /t 2 /nobreak >nul
set /a tries+=1
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:9222/json/version').StatusCode | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    if %tries% lss 15 goto waitport
    echo DEBUG PORT DID NOT COME UP after 30 s.
    echo Close every Chrome window (all of them), then run this bat again.
    pause
    exit /b 1
)
echo Debug port 9222 is UP. You can start the tracker now (start_tracker.bat).
pause
