# Changelog

## 1.2.1 — 2026-11-17
- Sidebar shows the running version and build (e.g. "v1.2.1 · build 2") so anyone can tell which version a server runs.
- Release notes in the update dialog no longer include commit trailers.
- First release delivered to an installed server through the full GitHub Actions → in-app update path.

## 1.2.0 — 2026-11-17 — automatic updates (GitHub Actions → in-app update with progress)
- **CI/CD** (`.github/workflows/release.yml`): every push to `main` runs the full test suite on Windows; when green
  it builds `hsco_emp_app-v<version>-build<n>.zip` + `.sha256` (`scripts/build_release.py`) and publishes GitHub
  Release `build-<n>`. A red build never reaches servers.
- **In-app updates** (`app/updater.py`): installed servers check the releases API on start and every 10 minutes
  (Settings → Updates; can be switched off). Admins see a banner → *What's new* / *Update now* → dialog with a live
  download progress bar and steps (Download, Verify checksum, Prepare, Back up / install / restart, Done). Other
  roles see that an update is available.
- **Supervisor** (`run_server.py`, stdlib only) runs the web server (`app/serve.py`) as a child, applies staged
  updates (DB backup, code swap, `pip install` if requirements changed), health-checks the new build and **rolls
  back automatically** if it does not start. Results in `DATA_DIR/updates/last_result.json`, shown in the app.
  The child exits if the supervisor is stopped, so no orphaned server keeps the port.
- Safety: SHA-256 verification, package validation (no `..`/absolute paths, no `data/`, required files, matching
  build), only admins can install, every check/install audit-logged, source checkouts (`.git`) never self-update.
- `/healthz` now reports version, build and commit. Version lives in `app/__init__.py`.
- Tests: 77 (new: updater unit tests, supervisor apply/rollback, live supervisor watchdog, and a full browser test of
  banner → progress → restart → new build → broken build rolled back).

## 1.1.0 — 2026-11-17 — UI/UX redesign + root-cause fix for "database is locked"
**Redesign** (owner: "the UI and UX can be much better, look better and be easier to use")
- New design system: Inter typeface and Lucide icons (both vendored, offline), calm neutral palette with one accent,
  cards, badges, chips, tabs, side drawers, toasts, empty states; responsive down to phone width.
- App shell: icon sidebar with an open-exceptions badge, sticky top bar with **quick employee search** (press `/`,
  arrow keys + Enter), a single **month switcher** for every page, and a user menu.
- **Overview** replaces the long dashboard: a 5-step **month-end checklist** (Import, Review, Corrections,
  Finalise, Share), four headline numbers with **month-on-month change**, a "Needs attention" list, then tabs
  (Attendance, Punctuality, Work location, Hours, By group, People, Trend).
- **Review queue** replaces the flags list: grouped by type, open/reviewed/all, *Mark reviewed* with a note (one-click
  reasons) or *Mark all of this type reviewed*; progress drives the checklist. New table `flag_reviews`.
- **Payroll leave sheet**: totals strip, filter chips (Deduction, Loss of pay, Over WFH limit, Has flags, Corrected,
  Exempt), key/all-columns toggle, sticky name column, whole-row click, totals that follow the filter, re-open in a
  drawer with a reason.
- **Employee page**: header with previous/next employee (arrow keys), four headline numbers, tabs (Month, Day by day,
  Corrections, Leave & comp-off, History). **Click any calendar day** to see its punches and correct it in a side
  panel with quick actions (Mark absent / Paid leave / Half-day / Present).
- Import page: drag-and-drop with file preview and a per-month "imported so far" checklist. Settings in tabs with
  plain-language sections. Users managed in drawers. Audit log in plain English (raw code on hover).
  Split-screen sign-in. The board pack uses the same design with the font embedded.
- Persistent-late flag label is now "Often more than 1 h late".

**Fix — HTTP 500 "database is locked" (2FA confirm; seen again with quick search)**
- Root cause: the auth dependency was `async` and did blocking SQLite writes (session `last_seen`) on the event
  loop. When another request held the write lock, the loop blocked waiting for it, so that request could never
  commit: a deadlock until the 30 s busy timeout. The 1.0.1 fix (commit before response) reduced but did not remove it.
- Fix: all DB work runs in worker threads (`_load` via `anyio.to_thread`, sync endpoints; async endpoints only read
  the body then hand off), DB cleanup shielded from cancellation, security headers as pure ASGI middleware, and
  `last_seen` written at most once a minute.
- Proven by `tests/e2e/test_disconnect.py`: two overlapping signed-in requests on a live server hang on the old code
  and pass in about 1 s now; plus a client-disconnect test.

## 1.0.1 — 2026-11-17 — fix: HTTP 500 when entering the first 2FA code
- **Cause 1:** the DB session dependency committed *after* the response was sent, so the enrolment page still held
  SQLite's write lock when the browser posted the code → `database is locked`. Now `Depends(get_db, scope="function")`
  commits before responding; SQLite `busy_timeout` 30 s added as a backstop.
- **Cause 2:** flash messages are stored in a cookie; one contained an em dash, which is not Latin-1 → crash on the
  retry. Flash cookies are now URL-encoded.
- Regression tests added (66 total). Verified live in Chromium against the running server.

## 1.0.0 — 2026-11-17 — first complete version
**Analysis**
- All 5 reference workbooks (every sheet, formula, pivot) and the HR policy PDF analysed → `docs/DATA_ANALYSIS.md`.
  Found: unanchored VLOOKUP and `LEFT(…,3)` bugs in the manual MIS, greytHR Summary week-days wrong for many,
  per-employee weekly-off/holiday/hours exceptions, HS207 holiday-hours inconsistency, 8 h/day for HS138/HS192.

**Application**
- Importers with auto-detection: greytHR muster, swipe details, attendance summary (.xls), DOJ; manual MIS
  (reconciliation + master seeding); templates for employee master, holidays, leave opening balances.
- Engine: policy rules (8.5 h × days, ½ day per 4 h short, exempt groups/employees, off-day/holiday hours excluded,
  comp-off ≥ 5 h, WFH ≤ 2), weekly-off patterns incl. custom weekdays, holiday calendars, status mapping, half days.
- Leave ledger (FY Apr–Mar, 15 days pro-rata, absence/shortfall debits, salary deduction when exhausted, encashment
  at year end) and comp-off ledger.
- 25 flag types, board KPIs, charts, top lists, group table, people movement, trends, per-employee history.
- Manual entries: day corrections, month figure overrides, opening balances, employee master edits — reason mandatory,
  audit-logged, highlighted in Excel.
- Period finalise/lock and admin re-open with reason.
- Reconciliation vs manual MIS and vs greytHR summary.
- Excel workbook (11 sheets, live formulas) and standalone interactive HTML board pack with drill-down.
- Location names: geofences, offline GeoNames gazetteer, optional OSM (off).
- Security: argon2 passwords, lockout, server-side sessions, CSRF, CSP/HSTS, roles, mandatory 2FA with TOTP,
  passkeys/security keys (WebAuthn), email codes, recovery codes; audit log.
- Windows installer (venv, HTTPS cert, scheduled tasks for server + nightly backup, firewall), CLI tools.
- Tests: 64 (unit, parsers, real-data reconciliation, HTTP integration, browser e2e incl. passkey).

**Defaults chosen from the data** (see PROJECT_CONTEXT decisions): LOP under Leaves; persistent late = > 60 min on
≥ 10 days; WFH distinct days; extra working day = any punch on an off-day.
