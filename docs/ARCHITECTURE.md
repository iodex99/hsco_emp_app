# Architecture

## Stack
Python 3.13 · FastAPI/Starlette · Uvicorn (TLS) · SQLAlchemy 2 + SQLite (WAL) · Jinja2 server-rendered pages ·
vanilla JS + Chart.js 4.4.1 (vendored) · openpyxl/xlrd · argon2 · pyotp · py_webauthn · cryptography.
No CDN, no external calls by default.

## Module map
```
run_server.py              start server (HTTPS, or --dev HTTP on localhost)
app/main.py                app factory, security headers, error pages, router registration
app/config.py              env config: HSCO_DATA_DIR, HSCO_RP_ID, HSCO_ORIGIN, session timeouts …
app/db.py, app/models.py   engine/session, ORM schema
app/cli.py                 create-admin, reset-mfa, backup, make-cert, import
app/web.py                 per-request DB session, auth context, role guards + CSRF, render helpers
app/engine/                PURE calculation engine (no DB/web): model.py (types, Policy), compute.py
app/importers/             readers.py (xlsx/xls/csv → rows, filters ignored), parsers.py (detect + parse each format)
app/services/              settings, audit, imports (pipeline), compute (DB→engine→results + ledgers), metrics, geo
app/security/              core.py (passwords, sessions, CSRF, TOTP, recovery, email OTP), passkeys.py (WebAuthn)
app/reports/               excel.py (workbook), html_report.py (standalone board pack)
app/routes/                auth, pages (dashboard/imports/flags/recon/audit), periods (sheet, drill-down, overrides,
                           finalise), employees, settings, users, reports (downloads, templates)
app/templates/, app/static/ UI; app/data/in_places.csv.gz offline gazetteer (GeoNames CC-BY 4.0)
deploy/install_windows.ps1  installer (venv, env vars, cert, admin, scheduled tasks, firewall)
tests/                      unit, parser, real-data reconciliation, HTTP integration, browser e2e
```

## Concurrency rule (important, see CHANGELOG 1.1.0)
SQLite allows one writer. **Never run a blocking DB call on the event loop.** Route handlers that touch the DB are
plain `def` (FastAPI runs them in a thread pool); an `async def` handler may only `await` the request body and must
then hand off with `anyio.to_thread.run_sync(...)`. `get_db` is an async generator with shielded commit/rollback/close
and `scope="function"` (commits before the response). Middleware must be pure ASGI (no `BaseHTTPMiddleware`).
Regression tests: `tests/e2e/test_disconnect.py`.

## Front end
Server-rendered Jinja pages; shared macros in `templates/_ui.html` (icon, person, badges, KPI with delta, toplist,
empty state). `static/app.css` is the design system (tokens in `:root`); `static/app.js` provides tabs, filter chips
(rows carry `data-tags`), sortable tables, footer totals that follow filters, quick search (`/api/employees`),
drawers, toasts, the month switcher, Chart.js defaults and WebAuthn helpers. Icons: Lucide sprite
`static/icons.svg` (`<use href="/static/icons.svg#i-name">`); font: Inter (`static/fonts`). All vendored.

## Data flow
0. Every request gets one DB session via `Depends(get_db, scope="function")`: committed **before** the response is
   sent (required with SQLite — otherwise the next browser request can hit a held write lock).
1. Upload → `readers.read_workbook` → `parsers.detect` (header signatures, any sheet, any header row ≤ 15) →
   parser per kind → stored rows (muster/swipes/summary/manual MIS replace the month; master/DOJ/holidays/openings upsert).
2. `services.compute.compute_period` builds engine inputs (employees + holiday calendars + overrides), runs
   `engine.compute_month`, adds ledgers, stores one `MonthResult` per employee (scalars, flags, serialized days) and
   cascades to the next draft month (ledger carry-forward).
3. Pages, Excel and HTML read `MonthResult` (fast; no recomputation on view).

## Database (SQLite file `DATA_DIR/hsco.db`)
| Table | Purpose |
|---|---|
| users, mfa_methods, recovery_codes, email_otps, sessions | identity, 2FA, server-side sessions |
| audit_log | every login, import, override, setting, user change |
| settings | `policy` and `general` JSON |
| employees, holiday_calendars, holidays, geofences, geo_cache | master data |
| periods | month + status DRAFT/FINALISED |
| flag_reviews | review-queue state per period/employee/flag (note, who, when); survives recomputation |
| imports | file name, sha256, sheet, rows, warnings, who/when |
| muster_rows, swipes, summary_rows, manual_mis_rows | imported data per period |
| day_overrides, month_overrides, leave_openings | manual entries (reason mandatory) |
| month_results | computed output (JSON `data`, `flags`, `days`) |
Schema is created with `create_all` at start-up (additive). For future column changes add a small migration step in
`db.init_db` and record it here.

## Security design
- **Passwords**: argon2id; policy (length, mixed case, digit, symbol, no username); lockout after N failures.
- **Sessions**: random 256-bit token in an HttpOnly, Secure, SameSite=Strict cookie; only its SHA-256 is stored;
  absolute expiry (10 h) + idle timeout (60 min; 10 min while 2FA pending); sessions revoked on password change,
  deactivation, 2FA reset.
- **2FA** (mandatory by default; users may register several methods):
  TOTP (RFC 6238, ±30 s, replay-protected, secret encrypted with a key derived from `secret.key`),
  WebAuthn passkeys/security keys/Windows Hello (needs HTTPS + RP ID = host name),
  email one-time codes (if SMTP configured; argon2-hashed, 10-min expiry, 5 attempts),
  10 single-use recovery codes (argon2-hashed). Failed second factors count towards lockout.
- **CSRF**: per-session token checked on every POST (form field or `X-CSRF-Token`).
- **Headers**: CSP (self only), HSTS, X-Frame-Options DENY, nosniff, no-store on data pages.
- **Roles**: viewer (board, read-only) < hr (import, correct, finalise) < admin (rules, users, re-open).
- **Data at rest**: on the server's disk; installer restricts `DATA_DIR` ACL to Administrators/SYSTEM; recommend
  BitLocker. `secret.key` lives in DATA_DIR — back it up with the DB (needed to decrypt TOTP secrets).
- **Uploads**: size limit (40 MB), parsed in memory, never executed; unknown formats rejected.
- Board pack HTML contains employee data — treat downloaded files as confidential.
