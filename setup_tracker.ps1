<#
.SYNOPSIS
  One-click setup for the FSL Dispatch Tracker on a fresh Windows machine.
  Run by right-clicking -> "Run with PowerShell" (or: powershell -File setup_tracker.ps1)

WHAT IT DOES
  1. Checks/installs Python 3.11 (winget; falls back to direct installer download)
  2. Checks/installs Google Chrome (winget)
  3. pip install websockets
  4. Fixes the .bat launchers to use THIS machine's paths (no hardcoded C:\Users\musta)
  5. Offers to create Start Menu shortcuts ("FSL Tracker Console", "FSL Tracker")
  6. Launches the console Chrome so the user can log in with THEIR AAA credentials
#>
$ErrorActionPreference = "Stop"
# folder this script lives in: $PSScriptRoot (PS3+), fall back to Invocation/cwd
$dir = $PSScriptRoot
if (-not $dir) { $dir = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $dir) { $dir = (Get-Location).Path }
Set-Location $dir
Start-Transcript -Path (Join-Path $dir "setup_log.txt") -Append | Out-Null

# PowerShell 5.1 defaults to TLS 1.0 for downloads; python.org / dl.google.com
# need TLS 1.2 or the installer download throws and (with Stop) kills the script
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------- 0. am I on Windows? ----------
if ($PSVersionTable.Platform -eq "Unix") {
    Write-Host "This setup is for Windows only." -ForegroundColor Red; exit 1
}

# ---------- 1. Python ----------
Say "Checking Python..."
# Returns a hashtable: { exe = <full path or name>, arg = optional first arg }
function Find-Python {
    # 1) py launcher (present even when python.exe is not on PATH)
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $v = & $pyLauncher.Source -3 --version 2>&1
        if ($v -match "^Python 3") {
            return @{ exe = $pyLauncher.Source; arg = "-3" }
        }
    }
    # 2) real python.exe on PATH — but NOT the Microsoft Store alias
    foreach ($c in @("python", "python3")) {
        $g = Get-Command $c -ErrorAction SilentlyContinue
        if ($g -and $g.Source -notlike "*WindowsApps*") {
            $v = & $g.Source --version 2>&1
            if ($v -match "^Python 3") { return @{ exe = $g.Source; arg = $null } }
        }
    }
    # 3) per-user install dirs
    $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
    if ($cand) { return @{ exe = $cand.FullName; arg = $null } }
    return $null
}
$pyInfo = Find-Python
$pyOK = ($null -ne $pyInfo)
$pythonExe = if ($pyInfo) { $pyInfo.exe } else { $null }
$pyArg     = if ($pyInfo) { $pyInfo.arg } else { $null }
if ($pyInfo) { Write-Host "  found: $pythonExe $($pyArg)" }
if (-not $pyOK) {
    Say "Installing Python 3.11 (this takes a few minutes)..."
    $installed = $false
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            & winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) { $installed = $true }
        } catch { Write-Host "  winget install failed: $_" -ForegroundColor Yellow }
    }
    if (-not $installed) {
        if (-not $winget) { Write-Host "  winget not available - downloading installer directly..." }
        try {
            $inst = "$env:TEMP\python311-install.exe"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $inst -UseBasicParsing
            # per-user install, adds to PATH, no UI
            Start-Process -FilePath $inst -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
            $installed = $true
        } catch { Write-Host "  direct install failed: $_" -ForegroundColor Yellow }
    }
    if (-not $installed) { Write-Host "Python install FAILED - install manually from python.org then re-run." -ForegroundColor Red; pause; exit 1 }
    # refresh PATH for this session (per-user installs land in %LOCALAPPDATA%)
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "  Python installed."
}
$pyInfo = Find-Python
if (-not $pyInfo) { Write-Host "No Python 3 found even after install - reboot once and re-run this script." -ForegroundColor Red; pause; exit 1 }
$pythonExe = $pyInfo.exe
$pyArg     = $pyInfo.arg
Write-Host "  using: $pythonExe $($pyArg)"

# ---------- 2. Chrome ----------
Say "Checking Google Chrome..."
$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Say "Installing Google Chrome..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & winget install --id Google.Chrome --silent --accept-package-agreements --accept-source-agreements
    } else {
        $inst = "$env:TEMP\chrome-install.exe"
        Invoke-WebRequest -Uri "https://dl.google.com/chrome/install/latest/chrome_installer.exe" -OutFile $inst -UseBasicParsing
        Start-Process -FilePath $inst -ArgumentList "/silent /install" -Wait
    }
    $chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $chrome) { Write-Host "Chrome install FAILED - install manually then re-run." -ForegroundColor Red; pause; exit 1 }
Write-Host "  found: $chrome"

# ---------- 3. websockets ----------
Say "Installing Python packages (websockets, tzdata)..."
if ($pyArg) { & $pythonExe $pyArg -m pip install --quiet websockets tzdata }
else        { & $pythonExe -m pip install --quiet websockets tzdata }
if ($LASTEXITCODE -ne 0) { Write-Host "  pip install FAILED - send setup_log.txt back." -ForegroundColor Red; pause; exit 1 }
Write-Host "  done."

# ---------- 4. rewrite the .bat launchers for THIS machine ----------
Say "Writing launchers for this machine..."
$pyExe = $pythonExe.Trim('"')
$pyArgs = if ($pyArg) { " $pyArg" } else { "" }
$trackerBat = @"
@echo off
cd /d "$dir"
start "" "$pyExe"$pyArgs tracker.py
"@
Set-Content -Path "$dir\start_tracker.bat" -Value $trackerBat -Encoding ASCII

$chromeBat = @"
@echo off
start "" "$chrome" --remote-debugging-port=9222 --user-data-dir="$dir\console_profile" "https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"
"@
Set-Content -Path "$dir\start_chrome.bat" -Value $chromeBat -Encoding ASCII
Write-Host "  start_tracker.bat / start_chrome.bat updated."

# ---------- 5. Start Menu shortcuts ----------
Say "Creating Start Menu shortcuts..."
$sm = [Environment]::GetFolderPath("Programs")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$sm\FSL Tracker Console.lnk")
$lnk.TargetPath = $chrome
$lnk.Arguments = "--remote-debugging-port=9222 --user-data-dir=`"$dir\console_profile`" `"https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console`""
$lnk.WorkingDirectory = $dir
$lnk.Save()
$lnk = $ws.CreateShortcut("$sm\FSL Tracker.lnk")
$lnk.TargetPath = "$dir\start_tracker.bat"
$lnk.WorkingDirectory = $dir
$lnk.Save()
Write-Host "  'FSL Tracker Console' + 'FSL Tracker' in the Start Menu."

# ---------- 6. first run: open the console for login ----------
Say "Opening the dispatch console for first login..."
Start-Process $chrome -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=`"$dir\console_profile`"", "https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " SETUP COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host " 1. In the Chrome window that just opened: log in with YOUR"
Write-Host "    AAA username/password (the tick box to stay signed in)."
Write-Host " 2. After logging in, double-click  start_tracker.bat"
Write-Host "    (or Start Menu -> 'FSL Tracker')."
Write-Host " 3. Open  http://127.0.0.1:8787/dashboard.html  in any browser."
Write-Host ""
Write-Host " Daily use (after reboot):"
Write-Host "   Start Menu -> 'FSL Tracker Console'  (log in if asked)"
Write-Host "   Start Menu -> 'FSL Tracker'          (the tracker itself)"
Write-Host " Keep the console Chrome window OPEN during your shift"
Write-Host " (minimizing is fine)."
Write-Host "============================================================" -ForegroundColor Green
Stop-Transcript | Out-Null
pause
