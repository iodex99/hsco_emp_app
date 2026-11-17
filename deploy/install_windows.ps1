<#
  HSCO Attendance & Leave Intelligence - Windows Server installer.
  Run in an elevated PowerShell from the extracted app folder:

    powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 -HostName hsco-app -DataDir D:\HSCOData

  What it does (idempotent - safe to re-run for upgrades):
    1. Creates a Python virtual environment (.venv) and installs pinned dependencies
    2. Sets machine environment variables (data folder, WebAuthn host/origin)
    3. Generates a self-signed TLS certificate (skip with -SkipCert if you place your own server.crt/key)
    4. Creates the first admin user (prompted) if none exists
    5. Registers a Scheduled Task that runs the server at boot as SYSTEM and restarts it on failure
    6. Registers a nightly database backup task (02:00, keeps last 60)
    7. Opens the firewall port for the LAN only
  Requires Python 3.11+ installed for all users (python.org installer, "Add to PATH").
#>
param(
  [string]$HostName = $env:COMPUTERNAME,
  [string]$DataDir = "C:\HSCOData",
  [int]$Port = 8443,
  [string[]]$ExtraNames = @(),
  [switch]$SkipCert
)
$ErrorActionPreference = "Stop"
$App = (Resolve-Path "$PSScriptRoot\..").Path
Write-Host "Installing from $App  (data: $DataDir, https://$HostName`:$Port)" -ForegroundColor Cyan

# 1. venv + dependencies
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Python not found on PATH. Install Python 3.11+ for all users first." }
if (-not (Test-Path "$App\.venv\Scripts\python.exe")) { & $py -m venv "$App\.venv" }
$vpy = "$App\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install -r "$App\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. environment
New-Item -ItemType Directory -Force $DataDir | Out-Null
[Environment]::SetEnvironmentVariable("HSCO_DATA_DIR", $DataDir, "Machine")
[Environment]::SetEnvironmentVariable("HSCO_RP_ID", $HostName, "Machine")
[Environment]::SetEnvironmentVariable("HSCO_ORIGIN", "https://$HostName`:$Port", "Machine")
[Environment]::SetEnvironmentVariable("HSCO_PORT", "$Port", "Machine")
$env:HSCO_DATA_DIR = $DataDir; $env:HSCO_RP_ID = $HostName; $env:HSCO_ORIGIN = "https://$HostName`:$Port"

# Restrict the data folder to Administrators + SYSTEM (contains employee data and the secret key)
icacls $DataDir /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null

# 3. certificate
if (-not $SkipCert -and -not (Test-Path "$DataDir\certs\server.crt")) {
  & $vpy -m app.cli make-cert $HostName @ExtraNames
}

# 4. first admin
Push-Location $App
$hasUser = & $vpy -c "from app.db import init_db, session_scope; from app.models import User; from sqlalchemy import select; init_db(); s=session_scope().__enter__(); print(1 if s.scalar(select(User.id).limit(1)) else 0)"
if ($hasUser -eq "0") { Write-Host "Create the first administrator:" -ForegroundColor Yellow; & $vpy -m app.cli create-admin }
Pop-Location

# 5. server task
$action = New-ScheduledTaskAction -Execute $vpy -Argument "`"$App\run_server.py`" --port $Port" -WorkingDirectory $App
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "HSCO Attendance App" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

# 6. nightly backup
$baction = New-ScheduledTaskAction -Execute $vpy -Argument "-m app.cli backup" -WorkingDirectory $App
$btrigger = New-ScheduledTaskTrigger -Daily -At 2am
Register-ScheduledTask -TaskName "HSCO Attendance Backup" -Action $baction -Trigger $btrigger -Principal $principal -Force | Out-Null

# 7. firewall (domain/private networks only)
if (-not (Get-NetFirewallRule -DisplayName "HSCO Attendance App" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName "HSCO Attendance App" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
}

Start-ScheduledTask -TaskName "HSCO Attendance App"
Start-Sleep -Seconds 4
try {
  [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
  $r = Invoke-WebRequest -UseBasicParsing "https://localhost:$Port/healthz"
  Write-Host "Server is up: $($r.Content)" -ForegroundColor Green
} catch { Write-Warning "Server did not answer yet; check Task Scheduler > 'HSCO Attendance App'." }
Write-Host ""
Write-Host "Open https://$HostName`:$Port from office PCs." -ForegroundColor Green
Write-Host "Distribute $DataDir\certs\server.crt to client PCs (Trusted Root store, e.g. via Group Policy) - required for passkeys/security keys."
