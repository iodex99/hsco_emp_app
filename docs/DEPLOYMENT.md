# Deployment — Windows Server

## Requirements
Windows Server 2016+ (or Windows 10/11), Python 3.11+ installed **for all users** with "Add to PATH",
~1 GB disk, port 8443 reachable on the LAN. No internet needed after installation.

## Install (first time)
```powershell
# as Administrator, in the app folder (git clone or copied zip)
powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 -HostName hsco-app -DataDir D:\HSCOData
#   -HostName : the name users will type (must match the certificate and WebAuthn RP ID)
#   -ExtraNames 192.168.1.10 other-alias : extra names/IPs on the certificate
#   -SkipCert : you will place your own data\certs\server.crt + server.key (e.g. from the firm's CA)
```
The script installs dependencies, sets machine env vars (`HSCO_DATA_DIR`, `HSCO_RP_ID`, `HSCO_ORIGIN`, `HSCO_PORT`),
creates a self-signed certificate, prompts for the first admin, and registers two Scheduled Tasks:
**HSCO Attendance App** (at boot, as SYSTEM, auto-restart) and **HSCO Attendance Backup** (02:00 daily).

Then:
1. Distribute `D:\HSCOData\certs\server.crt` to client PCs → *Trusted Root Certification Authorities* (Group Policy or
   double-click → Install). Without this browsers warn, and passkeys/security keys will not work.
2. Add a DNS record (or hosts entry) for `hsco-app` → server IP.
3. Open `https://hsco-app:8443`, sign in as admin, set up 2FA, then create users (Users page).
4. Rules & calendars: confirm policy defaults, add the 2026/2027 holiday calendar(s), office/client geofences.
5. Import the employee master (or last month's manual MIS to seed groups) and opening leave balances.

## Upgrade
Stop the task, replace the code (git pull), re-run the installer (idempotent), it restarts the task.
Data in `DATA_DIR` is untouched. Check `docs/CHANGELOG.md` for notes.

## Backup & restore
- Nightly: `python -m app.cli backup` → `DATA_DIR\backups\hsco_YYYYMMDD_HHMMSS.db` (last 60 kept). Copy `DATA_DIR`
  (DB backups **and** `secret.key`) to the firm's backup system.
- Restore: stop the task, copy a backup over `DATA_DIR\hsco.db`, start the task.

## Operations
| Task | Command |
|---|---|
| Create another admin | `.venv\Scripts\python -m app.cli create-admin` |
| User lost all 2FA methods & recovery codes | admin → Users → Reset 2FA, or on server `python -m app.cli reset-mfa <user>` |
| Import from command line | `python -m app.cli import file1.xlsx file2.xlsx` |
| Dev / local test (HTTP) | `python run_server.py --dev` → http://localhost:8443 |
| Logs | Task Scheduler history; run `run_server.py` in a console to see live logs |

## Email codes (optional)
Settings → Security & system → SMTP host/port/from/user/password. Only the 6-digit code is sent by email.

## Ports & network
Single HTTPS port (default 8443, `HSCO_PORT`). Firewall rule is created for Domain/Private profiles only.
Do not expose to the internet; use the firm VPN for remote access.
