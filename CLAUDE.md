# CLAUDE.md — read this first in every session

This repository is **HSCO Attendance & Leave Intelligence** — an on‑premise web app for
**Hasmukh Shah & Co. LLP** (a CA firm, Mumbai + Delhi) that automates month‑end leave
calculation before payroll and produces board‑level employee metrics.

## Golden rule: the `.md` files are the project's memory
The owner requires that context is never lost. **After every change** (code, rule, decision,
fix) update the relevant docs in the same change:

| File | What it holds | Update when |
|---|---|---|
| `docs/PROJECT_CONTEXT.md` | Client, requirements (as stated by owner), scope, open questions | Requirements / scope / questions change |
| `docs/DATA_ANALYSIS.md` | Every reference export: sheets, columns, formulas, manual workings, data quirks | A new export format or quirk is found |
| `docs/BUSINESS_RULES.md` | The calculation spec (leave, hours, deductions, comp‑off, WFH, flags) | Any rule/setting/default changes |
| `docs/METRICS.md` | Board metric catalogue and definitions | Metrics added/changed |
| `docs/ARCHITECTURE.md` | Stack, module map, DB schema, security design | Code structure / schema / security changes |
| `docs/DEPLOYMENT.md` | Windows Server install, HTTPS, service, backup, upgrade | Install/ops changes |
| `docs/USER_GUIDE.md` | Monthly HR workflow, screens | UI / workflow changes |
| `docs/TESTING.md` | Test strategy, how to run, latest results | Tests added / results change |
| `docs/CHANGELOG.md` | Dated log of every change | **Always** |

## Non‑negotiables
- **Data stays on the server.** No CDNs, no telemetry, no cloud APIs by default. All JS/CSS is vendored
  under `app/static/vendor`. The only optional outbound calls are SMTP (email OTP) and online reverse
  geocoding — both **off by default** and admin‑controlled.
- **Real employee data never goes to git.** `reference/`, `data/`, `exports/`, `*.db`, certs are git‑ignored.
  The GitHub repo (`iodex99/hsco_emp_app`) is **public**. Tests that need real data skip when `reference/` is absent;
  synthetic fixtures (`tests/fixtures/make_fixtures.py`) cover CI.
- 2FA is mandatory and multiple methods per user are supported (TOTP, WebAuthn/passkey, email OTP, recovery codes).
- Every manual change (override, master edit, setting) is audit‑logged with user + reason.

## Quick commands (Windows, from repo root)
```powershell
.venv\Scripts\python -m pytest -q                 # unit + integration tests
.venv\Scripts\python -m pytest -q tests/e2e       # browser end‑to‑end (needs playwright chromium)
.venv\Scripts\python run_server.py --dev          # http://127.0.0.1:8443 (dev, no TLS)
.venv\Scripts\python -m app.cli create-admin      # first admin user
```

## Where things are
- `app/engine/` — pure calculation engine (no web deps). Start at `compute.py`.
- `app/importers/` — parsers for each greytHR export; `detect.py` auto‑identifies uploads.
- `app/reports/` — Excel workbook + standalone interactive HTML board pack.
- `app/security/` — passwords, sessions, CSRF, TOTP, WebAuthn, email OTP, recovery codes.
- `app/routes/` — FastAPI routers; `app/templates/` — Jinja2 pages.
