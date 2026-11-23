<#
  HSCO Attendance & Leave Intelligence - Windows installer (server PC).

  Normal use: double-click Install.cmd in the extracted release folder. No questions except the first administrator's
  user name and password. Safe to run again (repair / upgrade): existing folders, server name and data are kept.

  Advanced (elevated PowerShell, from the extracted folder):
    powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 [-HostName hsco-app] [-DataDir D:\HSCOData]
        [-AppDir C:\HSCO\app] [-Port 8443] [-ExtraNames 192.168.1.10,alias] [-SkipCert] [-DryRun]
        [-AdminUser name -AdminPassword pw]   (unattended first administrator, e.g. for automated test installs)

  What it does:
    1. Copies the app to C:\HSCO\app (or the existing install's folder)
    2. Finds Python 3.11+; if there is none, installs Python 3.13 for all users (winget, else python.org)
    3. Creates the app's own Python environment (.venv) and installs its packages
    4. Data folder C:\HSCOData (administrators + SYSTEM only) and machine settings
    5. A security certificate for this PC's name and IP addresses (trusted on this PC)
    6. The first administrator (asked once)
    7. Starts the app at every boot (Scheduled Task as SYSTEM, restarts on failure), nightly backup, firewall (LAN only)
    8. Starts it (in the background - no window, also after every restart), puts an icon on the desktop of every
       user of this PC (Remote Desktop users included) and makes "C:\HSCO\Office PC setup" in case other PCs ever
       open it over the network
  -DryRun shows what would happen and changes nothing.
#>
param(
  [string]$HostName = "",
  [string]$DataDir = "",
  [string]$AppDir = "",
  [int]$Port = 8443,
  [string[]]$ExtraNames = @(),
  [switch]$SkipCert,
  [switch]$DryRun,
  [string]$AdminUser = "",
  [string]$AdminPassword = ""
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Windows PowerShell downloads are very slow with a progress bar
$Here = (Resolve-Path "$PSScriptRoot\..").Path
$PyVersion = "3.13.1"                              # only used when no Python 3.11+ is installed yet
$TaskName = "HSCO Attendance App"

function Say([string]$msg, [string]$color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step([int]$n, [string]$msg) { Write-Host ""; Write-Host "[$n/8] $msg" -ForegroundColor Cyan }

function Find-Python {
  # a real Python 3.11+ (not the Microsoft Store placeholder); prefers 3.13, the version the app is tested on
  try { $p = & py -3.13 -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return "$p".Trim() } } catch {}
  foreach ($c in @("$env:ProgramFiles\Python313\python.exe", "$env:ProgramFiles\Python312\python.exe",
                   "$env:ProgramFiles\Python311\python.exe")) {
    if (Test-Path $c) { return $c }
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
    try {
      $v = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
      if ([version]"$v".Trim() -ge [version]"3.11") { return $cmd.Source }
    } catch {}
  }
  return $null
}

# ---------------------------------------------------------------- defaults (an existing install keeps its settings)
$task = $null
try { $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
if (-not $AppDir) {
  if ($task -and $task.Actions[0].WorkingDirectory) { $AppDir = $task.Actions[0].WorkingDirectory } else { $AppDir = "C:\HSCO\app" }
}
$AppDir = $AppDir.TrimEnd('\')
$Home_ = Split-Path $AppDir -Parent
if (-not $DataDir) {
  $DataDir = [Environment]::GetEnvironmentVariable("HSCO_DATA_DIR", "Machine")
  if (-not $DataDir) { $DataDir = "C:\HSCOData" }
  # moved from the app (Settings -> Data folder): data_location.json next to the app says where it went (v1.12)
  $ptr = Join-Path $AppDir "data_location.json"
  if (Test-Path $ptr) {
    try {
      $j = Get-Content -Raw $ptr | ConvertFrom-Json
      if ($j.dir -and $j.base -and ($j.base.TrimEnd('') -ieq $DataDir.TrimEnd(''))) { $DataDir = $j.dir }
    } catch {}
  }
}
if (-not $HostName) {
  $HostName = [Environment]::GetEnvironmentVariable("HSCO_RP_ID", "Machine")
  if (-not $HostName) { $HostName = $env:COMPUTERNAME.ToLower() }
}
$ips = @()
try {
  $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
           Select-Object -ExpandProperty IPAddress)
  # the address of the network card that reaches the office network (has a default gateway) first
  $main = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway } |
            ForEach-Object { $_.IPv4Address.IPAddress })
  $ips = @($main) + @($ips | Where-Object { $main -notcontains $_ })
} catch {}
$names = @($ExtraNames) + $ips
try {
  $fqdn = [System.Net.Dns]::GetHostEntry("").HostName.ToLower()
  if ($fqdn -and $fqdn -ne $HostName) { $names += $fqdn }
} catch {}
$Url = "https://$HostName`:$Port"
$py = Find-Python

Say ""
Say "HSCO Attendance & Leave - installer" "White"
Say "  App folder : $AppDir"
Say "  Data folder: $DataDir"
Say "  Address    : $Url"
if ($ips) { Say "               (also https://$($ips[0])`:$Port)" }
if ($py) { Say "  Python     : $py" } else { Say "  Python     : not found - Python $PyVersion will be installed" }
if ($task) { Say "  Existing install found - it will be updated in place; data is kept." "Yellow" }

if ($DryRun) {
  Say ""
  Say "DRY RUN - nothing was changed." "Green"
  exit 0
}
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Administrator rights are needed: double-click Install.cmd (it asks for them)." }

# ---------------------------------------------------------------- 1. app folder
if ($task) { try { Stop-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 3 } catch {} }
if ($Here.TrimEnd('\') -ne $AppDir) {
  Step 1 "Copying the app to $AppDir"
  New-Item -ItemType Directory -Force $AppDir | Out-Null
  # skip only these top-level folders (full paths): a bare "/XD data" would also skip app\data - the offline
  # place names - so punches showed coordinates instead of place names (fixed v1.12.1)
  $skip = ".venv", "data", ".git", "dist", "test-results", "reference", "exports", "backups" | ForEach-Object { Join-Path $Here $_ }
  robocopy $Here $AppDir /E /NFL /NDL /NJH /NJS /NP /XD @skip __pycache__ /XF *.pyc | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Copying the app failed (robocopy code $LASTEXITCODE)." }
  if (-not (Test-Path (Join-Path $AppDir "app\data\in_places.csv.gz"))) {
    throw "The offline place-name file app\data\in_places.csv.gz is missing from $Here - unpack the complete package and run Install.cmd again."
  }
} else {
  Step 1 "App folder: $AppDir"
}
$App = $AppDir

# ---------------------------------------------------------------- 2. Python
Step 2 "Python"
if (-not $py) {
  Say "Installing Python $PyVersion for all users (one time, about 30 MB)..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    try {
      & winget install -e --id Python.Python.3.13 --scope machine --silent --accept-package-agreements --accept-source-agreements | Out-Host
    } catch { Say "winget did not work - downloading from python.org instead." "Yellow" }
    $py = Find-Python
  }
  if (-not $py) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $exe = Join-Path $env:TEMP "python-$PyVersion-amd64.exe"
    Invoke-WebRequest -UseBasicParsing "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-amd64.exe" -OutFile $exe
    $p = Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "The Python installer failed (code $($p.ExitCode))." }
    $py = Find-Python
  }
  if (-not $py) {
    throw "Python could not be installed automatically. Install Python 3.13 from python.org (tick 'Add python.exe to PATH' and 'Install for all users'), then double-click Install.cmd again."
  }
}
Say "Using $py" "Green"

# ---------------------------------------------------------------- 3. app environment + packages
Step 3 "Installing the app's components (a few minutes the first time)"
$vpy = "$App\.venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) {
  & $py -m venv "$App\.venv"
  if ($LASTEXITCODE -ne 0) { throw "Could not create the app's Python environment." }
}
& $vpy -m pip install --disable-pip-version-check -q --upgrade pip
& $vpy -m pip install --disable-pip-version-check -q -r "$App\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Installing the app's components failed - check the internet connection and run Install.cmd again." }
Say "Done" "Green"

# ---------------------------------------------------------------- 4. data folder + machine settings
Step 4 "Data folder $DataDir"
New-Item -ItemType Directory -Force $DataDir | Out-Null
[Environment]::SetEnvironmentVariable("HSCO_DATA_DIR", $DataDir, "Machine")
[Environment]::SetEnvironmentVariable("HSCO_RP_ID", $HostName, "Machine")
[Environment]::SetEnvironmentVariable("HSCO_ORIGIN", $Url, "Machine")
[Environment]::SetEnvironmentVariable("HSCO_PORT", "$Port", "Machine")
$env:HSCO_DATA_DIR = $DataDir; $env:HSCO_RP_ID = $HostName; $env:HSCO_ORIGIN = $Url; $env:HSCO_PORT = "$Port"
# employee data and the secret key: administrators and SYSTEM only
icacls $DataDir /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
Say "Done (only administrators can open it)" "Green"

# ---------------------------------------------------------------- 5. certificate
Step 5 "Security certificate for $HostName"
$crt = "$DataDir\certs\server.crt"
if (-not $SkipCert -and -not (Test-Path $crt)) {
  Push-Location $App
  & $vpy -m app.cli make-cert $HostName @names | Out-Null
  Pop-Location
  if (-not (Test-Path $crt)) { throw "The certificate could not be created." }
  Say "Created (valid for $HostName, localhost$(if ($names) { ', ' + ($names -join ', ') }))" "Green"
} else {
  Say "Keeping the existing certificate" "Green"
}
if (Test-Path $crt) {           # trusted on this PC too (certutil: no PowerShell module needed)
  certutil -addstore -f Root $crt | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "The certificate could not be trusted on this PC (certutil code $LASTEXITCODE)." }
}

# ---------------------------------------------------------------- 6. first administrator
Step 6 "Administrator"
Push-Location $App
$hasUser = @(& $vpy -c "from app.db import init_db, session_scope; from app.models import User; from sqlalchemy import select; init_db(); s=session_scope().__enter__(); print(1 if s.scalar(select(User.id).limit(1)) else 0)")[-1]
if ("$hasUser".Trim() -eq "0" -and $AdminUser -and $AdminPassword) {
  & $vpy -m app.cli create-admin $AdminUser $AdminPassword
  if ($LASTEXITCODE -ne 0) { throw "The administrator could not be created." }
} elseif ("$hasUser".Trim() -eq "0") {
  Say "Create the first administrator - you will sign in with this." "Yellow"
  Say "Password: at least 10 characters, upper and lower case letters, a number and a symbol (typing is hidden)."
  do { & $vpy -m app.cli create-admin; $code = $LASTEXITCODE; if ($code -ne 0) { Say "Let's try that again." "Yellow" } } while ($code -ne 0)
} else {
  Say "Users already exist - nothing to do" "Green"
}
Pop-Location

# ---------------------------------------------------------------- 7. start at boot, backups, firewall
Step 7 "Start automatically with Windows"
$action = New-ScheduledTaskAction -Execute $vpy -Argument "`"$App\run_server.py`" --port $Port" -WorkingDirectory $App
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
$baction = New-ScheduledTaskAction -Execute $vpy -Argument "-m app.cli backup" -WorkingDirectory $App
$btrigger = New-ScheduledTaskTrigger -Daily -At 2am
Register-ScheduledTask -TaskName "HSCO Attendance Backup" -Action $baction -Trigger $btrigger -Principal $principal -Force | Out-Null
if (-not (Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
}
Say "Done (office network only - not reachable from the internet)" "Green"

# ---------------------------------------------------------------- 8. start, shortcuts, office PC kit
Step 8 "Starting the app"
Start-ScheduledTask -TaskName $TaskName
[System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$up = $false
for ($i = 0; $i -lt 90 -and -not $up; $i++) {
  try { Invoke-WebRequest -UseBasicParsing "https://localhost:$Port/healthz" -TimeoutSec 3 | Out-Null; $up = $true } catch { Start-Sleep -Seconds 1 }
}
if ($up) { Say "Running" "Green" } else { Say "It has not answered yet - give it a minute (Task Scheduler > '$TaskName')." "Yellow" }

$shortcut = "[InternetShortcut]`r`nURL=$Url/`r`n"
Set-Content -Path "$env:PUBLIC\Desktop\HSCO Attendance.url" -Value $shortcut -Encoding ASCII

# everything the other office PCs need, in one folder to copy (USB stick / shared drive)
$kit = Join-Path $Home_ "Office PC setup"
New-Item -ItemType Directory -Force $kit | Out-Null
if (Test-Path $crt) { Copy-Item $crt "$kit\hsco-server.crt" -Force }
$setup = @"
@echo off
title HSCO Attendance - set up this PC
cd /d "%~dp0"
echo.
echo   This PC will trust the HSCO app's security certificate and get a desktop icon.
echo   Windows asks you to confirm the certificate - click YES.
echo.
certutil -user -addstore -f Root "hsco-server.crt" >nul
if errorlevel 1 (
  echo   The certificate was not added. Run this again and click Yes.
  pause
  exit /b 1
)
for /f "usebackq delims=" %%d in (``powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"``) do set "DESK=%%d"
(echo [InternetShortcut]& echo URL=$Url/) > "%DESK%\HSCO Attendance.url"
start "" "$Url/"
echo   Done. Next time use the "HSCO Attendance" icon on the desktop.
echo.
pause
"@
Set-Content -Path "$kit\Set up this PC.cmd" -Value $setup -Encoding ASCII
$alt = if ($ips) { "https://$($ips[0])`:$Port" } else { "" }
$readme = @"
HSCO Attendance & Leave - office PCs
====================================

Address: $Url
$(if ($alt) { "(if the name does not open: $alt)" })

Not needed when people use the app by Remote Desktop on the server - only for PCs that open it over the network.

On each such PC (once):
  1. Copy this folder to the PC (or open it from a shared drive / USB stick).
  2. Double-click  "Set up this PC.cmd"  and click YES when Windows asks about the certificate.
  3. Use the "HSCO Attendance" icon it puts on the desktop.

No installation is needed on office PCs - the app runs in Chrome or Edge.

For IT (instead of step 2 on every PC): deploy hsco-server.crt to "Trusted Root Certification Authorities" with
Group Policy, and push the address $Url/ as a browser favourite.
"@
Set-Content -Path "$kit\READ ME.txt" -Value $readme -Encoding UTF8

Say ""
Say "==============================================================" "Green"
Say " HSCO Attendance & Leave is installed and running." "Green"
Say ""
Say "  Open it at   $Url" "White"
if ($alt) { Say "  (or         $alt)" }
Say "  Users:       everyone who signs in to this PC (Remote Desktop included) has the"
Say "               'HSCO Attendance' icon on their desktop - nothing else to set up."
Say "  It runs in the background (no window) and starts by itself whenever this PC starts."
Say "  (Only if other PCs ever open it over the network: '$kit')"
Say "  Data:        $DataDir  (backed up every night; copy it off this PC now and then)"
Say "==============================================================" "Green"
if ($up) { try { Start-Process "$Url/" } catch {} }
