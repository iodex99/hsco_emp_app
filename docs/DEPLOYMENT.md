# Deployment — Windows Server

## Install in three steps (v1.9.1)
**Server PC** = the one office PC (or Windows Server) that stays on and holds the data. Windows 10/11 or Server
2016+, about 1 GB free, on the office network, internet during installation (and for automatic updates).

1. On the server PC, download the latest release zip from GitHub → Releases
   (`hsco_emp_app-v<version>-build<n>.zip`). Right-click it → *Properties* → tick **Unblock** → OK, then
   right-click → *Extract All*.
2. In the extracted folder, double-click **`Install.cmd`** → *Yes* when Windows asks for administrator rights.
   (If Windows says "Windows protected your PC": *More info* → *Run anyway*.) It installs everything by itself and
   asks only for the first administrator's user name and password (typed twice). When it says **installed and
   running**, the app opens in the browser and there is an **HSCO Attendance** icon on the desktop.
3. For each office PC that will use the app: copy the folder **`C:\HSCO\Office PC setup`** to it (USB stick or a
   shared drive) and double-click **`Set up this PC.cmd`** → *Yes* to the certificate question. It puts an
   **HSCO Attendance** icon on the desktop. Nothing is installed on office PCs — the app runs in Chrome or Edge.

Then sign in as the administrator, set up two-step verification (phone authenticator app), create the users (Users
page) and do the first imports (below).

**What Install.cmd does** (`deploy\install_windows.ps1`, safe to run again — repair / upgrade keep everything):
copies the app to `C:\HSCO\app`; finds Python 3.11+ or installs Python 3.13 for all users (winget, else python.org);
creates the app's own Python environment and installs its packages; data folder `C:\HSCOData` (administrators and
SYSTEM only) and machine settings; a security certificate for this PC's name, full name and IP addresses (trusted on
this PC); the first administrator; Scheduled Tasks **HSCO Attendance App** (at boot, as SYSTEM, restarts on failure)
and **HSCO Attendance Backup** (02:00); a firewall rule for the office network only (port 8443); starts the app and
checks it answers; the desktop icon; the **Office PC setup** folder (certificate, *Set up this PC.cmd*, *READ ME.txt*).
Address: `https://<server PC name>:8443` (also by IP address; passkeys / security keys need the name).

Options (elevated PowerShell in the extracted folder): `powershell -ExecutionPolicy Bypass -File
deploy\install_windows.ps1 -HostName hsco-app -DataDir D:\HSCOData -AppDir D:\HSCO\app -Port 8443 -ExtraNames
alias,10.0.0.5 -SkipCert` (`-SkipCert`: you place the firm's own `server.crt` / `server.key` in `DataDir\certs`);
`-DryRun` shows the plan and changes nothing; `-AdminUser x -AdminPassword y` for an unattended install (used by the
automated install test). An existing install keeps its folders, name and data by default.

**For IT, instead of step 3:** deploy `C:\HSCOData\certs\server.crt` to *Trusted Root Certification Authorities* by
Group Policy and push `https://<server>:8443/` as a browser favourite. If the server's name doesn't resolve on the
network, add a DNS record (or use the IP address).

**Every release is install-tested:** before a release is published, GitHub Actions unpacks the release zip on a clean
Windows machine, runs `Install.cmd`, checks HTTPS with certificate checking, the scheduled task, shortcuts and the
office-PC kit, runs it again (repair) and checks again.

**First imports** (Import page): greytHR's Employee Category Report and each category's Holiday List Importer file;
then Rules & calendars → Categories (weekly offs / hours per category); the employee master (or last month's manual
MIS to seed groups); then the month's Attendance Muster and Swipe Details. (No leave balances: greytHR keeps them.)

## Updates (automatic)
**Publishing (developer):** bump `__version__` in `app/__init__.py`, add a CHANGELOG entry, push to `main`.
GitHub Actions runs all tests on Windows; if green it publishes release `build-<n>` with the zip + SHA-256.

**Installing (admin):** the server checks GitHub every 10 minutes (Settings → Updates → *Check now* for
immediately). A banner appears for admins → **Update now** → progress dialog:
1. download (progress bar) → 2. verify SHA-256 → 3. unpack to `DATA_DIR\updates\staged` →
4. the web server exits with code 75; the supervisor (`run_server.py`) backs up the database to
   `DATA_DIR\backups\pre-update-build<n>-*.db`, moves the current code to `DATA_DIR\updates\rollback\build<old>-*`,
   moves the new code in, runs `pip install -r requirements.txt` if it changed, starts the new build →
5. if `/healthz` reports the new build within 90 s: done (the page reloads and says *Updated to v…*);
   otherwise the previous code is restored and started again, and the dialog says so.

Users may be signed out for a few seconds while it restarts. Data (`DATA_DIR`) is never part of an update.
The firm logo lives in `DATA_DIR\branding` (uploaded in Settings) and is therefore kept across updates and backups
of the data folder.
After the first start of a new version a background job may run for a short while (building the place-name index
in `DATA_DIR\geo`, refreshing stored place names once, adding summaries to older months). The app stays usable
meanwhile; it only writes in short steps.
Requirements: outbound HTTPS to `api.github.com` and `github.com` (only GETs; nothing is sent). If an update adds a
Python package the server also needs PyPI access, otherwise it rolls back.

**Manual rollback:** stop the task, copy the folders from `DATA_DIR\updates\rollback\build<n>-…` back over the app
folder (or restore a `pre-update` DB backup), start the task.

**Offline servers:** switch off checks in Settings → Updates and install by extracting a newer release zip over the
app folder (keep `data`), then restart the task.

## Upgrade (manual)
Stop the task, extract the new release zip over the app folder (or `git pull`), re-run the installer (idempotent).
Data in `DATA_DIR` is untouched. Check `docs/CHANGELOG.md` for notes.

## Backup & restore
**Automatic (v1.6.1):** the running app backs itself up once a day into `DATA_DIR\backups\hsco_<date>_<time>_daily.db`
(verified; last 14 days + one per month for 2 years) and copies `secret.key` there. Settings → Security & system →
*Backups & data safety* shows the health check, last backup and **Back up now**. Updates take a verified
`pre-update-build<n>-….db` first (last 10 kept) and never touch `DATA_DIR`.
**Off the server (recommended):** copy the whole `DATA_DIR` (or at least `backups\` + `secret.key`) to another disk or a
NAS regularly — backups on the same disk don't protect against that disk failing.
**Restore:** stop the app (scheduled task / window), then `python -m app.cli restore` lists backups and
`python -m app.cli restore <file>` restores one (the current database is saved as `before-restore-….db`; a backup that
fails its integrity check is refused). Start the app again.
**Where data lives:** `DATA_DIR` (server install: `C:\HSCOData`, admins/SYSTEM only; a PC copy: the `data` folder next
to the app). It holds the database, `secret.key`, uploads, logo, place-name index, backups and update state — nothing
in it is part of the code, so reinstalling or updating the code leaves it as it is.

## Operations
| Task | Command |
|---|---|
| Create another admin | `.venv\Scripts\python -m app.cli create-admin` |
| User lost all 2FA methods & recovery codes | admin → Users → Reset 2FA, or on server `python -m app.cli reset-mfa <user>` |
| Import from command line | `python -m app.cli import file1.xlsx file2.xlsx` |
| Dev / local test (HTTP) | `python run_server.py --dev` → http://localhost:8443 |
| Run without supervisor (debug) | `python run_server.py --dev --no-supervisor` |
| Last update result | `DATA_DIR\updates\last_result.json`; supervisor messages are prefixed `[supervisor]` in the console/log |
| Logs | Task Scheduler history; run `run_server.py` in a console to see live logs |

## Email codes (optional)
Settings → Security & system → SMTP host/port/from/user/password. Only the 6-digit code is sent by email.

## Ports & network
Single HTTPS port (default 8443, `HSCO_PORT`). Firewall rule is created for Domain/Private profiles only.
Do not expose to the internet; use the firm VPN for remote access.
