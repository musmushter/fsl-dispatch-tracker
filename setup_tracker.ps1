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
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------- 0. am I on Windows? ----------
if ($PSVersionTable.Platform -eq "Unix") {
    Write-Host "This setup is for Windows only." -ForegroundColor Red; exit 1
}

# ---------- 1. Python ----------
Say "Checking Python..."
$py = Get-Command python -ErrorAction SilentlyContinue
$pyOK = $false
if ($py) {
    $v = & python --version 2>&1
    if ($v -match "3\.(\d+)") { $pyOK = [int]$Matches[1] -ge 9 }   # 3.9+ works, 3.11 recommended
    Write-Host "  found: $v"
}
if (-not $pyOK) {
    Say "Installing Python 3.11 (this takes a few minutes)..."
    $installed = $false
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { $installed = $true }
    } else {
        Write-Host "  winget not available - downloading installer directly..."
        $inst = "$env:TEMP\python311-install.exe"
        Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $inst -UseBasicParsing
        # machine-wide, adds to PATH, no UI
        Start-Process -FilePath $inst -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
        $installed = $true
    }
    if (-not $installed) { Write-Host "Python install FAILED - install manually from python.org then re-run." -ForegroundColor Red; pause; exit 1 }
    # refresh PATH for this session
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "  Python installed."
}
# find a usable python (py launcher preferred on Windows)
$pythonExe = $null
foreach ($c in @("py -3", "python", "python3")) {
    try {
        $v = Invoke-Expression "$c --version" 2>$null
        if ($v -match "^Python 3") { $pythonExe = $c; break }
    } catch {}
}
if (-not $pythonExe) { Write-Host "No Python 3 found even after install - reboot once and re-run this script." -ForegroundColor Red; pause; exit 1 }
Write-Host "  using: $pythonExe"

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
Say "Installing Python packages (websockets)..."
Invoke-Expression "$pythonExe -m pip install --quiet websockets"
Write-Host "  done."

# ---------- 4. rewrite the .bat launchers for THIS machine ----------
Say "Writing launchers for this machine..."
$trackerBat = @"
@echo off
cd /d "$dir"
start "" $pythonExe tracker.py
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
pause
