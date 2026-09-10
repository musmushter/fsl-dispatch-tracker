@echo off
cd /d "%~dp0"
REM pick a Chrome the way SETUP.bat installed/found it
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
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%~dp0console_profile" "https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"
