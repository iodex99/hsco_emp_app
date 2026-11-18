# Architecture

## Stack
Python 3.13 · FastAPI/Starlette · Uvicorn (TLS) · SQLAlchemy 2 + SQLite (WAL) · Jinja2 server-rendered pages ·
vanilla JS + Chart.js 4.4.1 (vendored) · openpyxl/xlrd · argon2 · pyotp · py_webauthn · cryptography.
No CDN, no external calls by default.

## Module map
```
run_server.py              supervisor: starts app/serve.py, applies updates, health-check + rollback
app/serve.py               uvicorn server (HTTPS, or --dev HTTP); exit 75 = restart for update
app/updater.py, app/version.py   update check/download/stage; running version + build
scripts/build_release.py   release zip + sha256 (CI and tests)
.github/workflows/release.yml   CI: test on Windows → publish release build-<n>
app/main.py                app factory, security headers, error pages, router registration
app/config.py              env config: HSCO_DATA_DIR, HSCO_RP_ID, HSCO_ORIGIN, session timeouts …
app/db.py, app/models.py   engine/session, ORM schema
app/cli.py                 create-admin, reset-mfa, backup, make-cert, import
app/web.py                 per-request DB session, auth context, role guards + CSRF, render helpers
app/engine/                PURE calculation engine (no DB/web): model.py (types, Policy), compute.py
app/importers/             readers.py (xlsx/xls/csv → rows, filters ignored), parsers.py (universal recognition + parse)
app/services/              settings, audit, imports (pipeline, remembered layouts, pending uploads, partial-file
                           rules, recompute_from), compute (DB→engine→results + ledgers), metrics, insights
                           (scorecards, absence, heatmaps), attendance_stats (compact month summaries), geo,
                           maintenance (start-up background jobs)
app/security/              core.py (passwords, sessions, CSRF, TOTP, recovery, email OTP), passkeys.py (WebAuthn)
app/reports/               excel.py (payroll workbook; punches & locations workbook)
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
**Keep write transactions short (v1.4.1):** multi-month recalculations commit after each month
(`recompute_from(commit_each=True)`, settings/employee edits, override cascades); slow reads (gazetteer
lookups) happen before an import's first delete; start-up maintenance writes in small batches. Verified with two
simultaneous 300-person imports plus three busy readers: no failures, no "database is locked".

## Front end
Server-rendered Jinja pages; shared macros in `templates/_ui.html` (icon, person, badges, KPI with delta, toplist,
empty state). `static/app.css` is the design system (tokens in `:root`); `static/app.js` provides tabs, filter chips
(rows carry `data-tags`), sortable tables, footer totals that follow filters, quick search (`/api/employees`),
drawers, toasts, the month switcher, Chart.js defaults and WebAuthn helpers. `static/table.js` is the table
toolkit: any `<table data-table>` with `<th data-key data-type="text|num|date|enum|none">` gets search, typed filters,
multi-sort, group-by, columns, saved views, URL state and CSV. Cells use `data-v` (raw value), `data-f` (filter
label), `data-s` (sort key), `data-csv`; rows `data-tags` for quick chips (`data-chips="#id"`); footer cells
`data-sum`. Options: `data-toolbar="none"`, `data-default-hidden`, `data-csv`. Icons: Lucide sprite
`static/icons.svg` (`<use href="/static/icons.svg#i-name">`); font: Inter (`static/fonts`). All vendored.

## Update system (v1.2)
```
push to main ──► GitHub Actions: tests (windows) ──► build_release.py ──► GitHub Release build-<n> (zip + sha256)
                                                                                 │  GET releases/latest (every 10 min)
run_server.py (supervisor, stdlib) ──spawns──► app/serve.py (uvicorn) ◄──────────┘ app/updater.py
      ▲  exit 75 = restart          │ admin "Update now": download → sha256 → validate → DATA_DIR/updates/staged
      └──────────────────────────────┘
supervisor on restart: backup DB → move code to updates/rollback → move staged code in → pip if needed →
start child → /healthz == new build within 90 s ? done : restore previous code and start it
```
- Version: `app/__init__.py` `__version__`; build stamp: `BUILD.json` (only in release zips; source checkout = build 0).
- Managed files replaced by an update: everything in the zip (`app/`, `run_server.py`, `requirements*.txt`, `deploy/`,
  `docs/`, `README.md`, `CLAUDE.md`, `BUILD.json`). Never touched: `DATA_DIR`, `.venv`, `reference/`.
- The supervisor must stay stdlib-only and backwards compatible: a new `run_server.py` only takes effect on the next
  full service restart (the running supervisor keeps its in-memory code).
- Endpoints: `GET /api/update/status` (any signed-in user), `POST /api/update/check`, `POST /api/update/install` (admin).

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
- Excel downloads contain employee data — treat downloaded files as confidential.

## Universal importer (v1.4)
`parsers.F` lists every field (synonyms + value type: id, date, datetime, time, hours, number, status, lat, lon, coords,
text); `parsers.SCHEMAS` lists each report kind's required fields (a tuple = any one of), optional fields and a
specificity weight. `detect_sheet()`:
1. header-row candidates = rows in the first 30 that are mostly short text with few digits (max 6);
2. a remembered layout (`Setting import_layouts`, matched by `layout_signature()` of the normalised header set) wins;
3. otherwise for each kind: score headers against synonyms (exact 1.0, whole-word containment ≈ 0.9, all words 0.82,
   fuzzy ≥ 0.86 similarity; synonyms ≤ 3 chars only as whole words), weight by the share of sample values that fit the
   field's type, assign greedily (one column per field); if ≥ 2 fields matched by header, a missing required
   id/date/datetime/coords field is inferred from values (`inferred`);
4. best score wins; `confidence` = mean score of required fields. `needs_review()` (confidence < 0.72 or anything
   inferred) → not imported; the upload is stashed in `DATA_DIR/uploads/pending/<token>` (expires after 2 days) and the
   user maps columns at `/imports/map/<token>` (pre-filled by `suggest()`, which also uses value patterns). The manual
   mapping becomes a `DetectedSheet(source="manual")` and can be saved as a layout.
`DetectedSheet.map` (field → column index) drives every `parse_*`; value normalisers: `parse_dt` (cached), `parse_time`,
`parse_hhmm`, `status_codes`. Detection is memoised per sheet (`_FIT_CACHE`, `_fscore`).

## Insights (v1.4)
`services/insights.py` reads `MonthResult` rows (current month + the last 12 computed months for 12-month absence and FY leave) and returns KPIs, per-employee rows with the attention score, group comparison, arrival heatmap, hours histogram,
employee × day matrix and a trend. Results are cached in-process keyed by (period, latest `computed_at`, latest settings
change, employee count/last edit). Used by `/insights`, the employee Scorecard tab and Excel.

## Board pack (v1.4 – v1.5.2, removed in v1.6.0)
The standalone HTML board pack (`reports/html_report.py`, `templates/board_pack.html`) was removed at the owner's
request; the app's own pages replace it. `table.js` keeps `window.HSCO_ICONS` and `HSCO.Table` as generic hooks.

## Performance notes (v1.4.1, measured on 300 employees × 12 months)
- `MonthResult.days` (~20 KB JSON per employee-month) is only loaded where day detail is shown (current month's
  Insights/dashboard charts, drill-down, Excel daily sheet). Everything historical reads
  `MonthResult.data` incl. `data["_ins"]` (`attendance_stats.month_stats`: working days, unplanned days/spells,
  first/last-day absence for joining spells across months, present days, punctuality counts). Months calculated by
  older versions are back-filled by `maintenance.backfill_month_stats` (and derived on the fly until then).
- `metrics.results_for(..., with_days=False)` defers the day detail; the dashboard trend reads data only.
- Imports: employees preloaded, muster/swipe rows inserted with one executemany, place names resolved before any
  delete, GeoCache loaded once per import and written at the end. One recalculation per upload batch.
- Geo: thread-local read-only gazetteer connection; 3 km window first, full radius only if needed; longitude span
  scaled by cos(latitude).
- Insights cached per (period, latest calculation, settings change, employee edits); scorecard aggregates computed
  once per result (`_sc_aggregates`).
- Excel: the bottom border is registered once per workbook (`_thin`); remaining cost is openpyxl XML writing
  (~4 s for 300 people, ~2 s for the real firm).

## Schema upgrades (v1.5)
`db.init_db()` runs `create_all()` (new tables) and then `add_missing_columns()`: for every model table that exists,
any column missing in the database is added with `ALTER TABLE … ADD COLUMN` and the model's scalar default. Additive
only — nothing is dropped or altered; idempotent; logged. New columns must therefore be nullable or have a scalar
default. First used for `swipes.note/reason/approval/device/device_id`.

## Locations (v1.5)
`services/location_checks.py` (pure: shared phones → SHARED_DEVICE flags per employee — used by `compute_period`) ·
`services/locations.py` (report for a month from `SwipeRow`, no muster needed) · `routes/pages.py`: `/locations`,
`pending_month()` for months without results. Place names: `geo.Namer` (cache → offline gazetteer → optional OSM).
v1.6.2 removed named places (Settings Locations tab, `/settings/geofences*`, `geo.unnamed_clusters`, office check);
the `Geofence` model/table stays so existing databases are untouched.

## Branding (v1.5.1)
`services/branding.py` — `save()` validates by magic bytes (PNG/JPEG/WebP) or sanitises SVG (`sanitize_svg`: element
allow-list, no `on*` attributes, `href` only to `#ids`, no external `url()`, no DOCTYPE/ENTITY), stores
`DATA_DIR/branding/logo.<ext>`; `favicon_svg()` crops an SVG logo's viewBox to its left mark (2.04 × height);
Routes: `GET /branding/logo`, `GET /branding/favicon.svg` (public, sandbox CSP,
nosniff, cached with `?v=<mtime>`), `POST /settings/branding` and `/settings/branding/delete` (admin, audit-logged).
Templates read `brand_info()` (Jinja global).

## Exports (v1.6)
`GET /exports/<key>.xlsx` — payroll workbook (calculated months). `GET /exports/<key>/locations.xlsx` —
`excel.build_locations_workbook`: sheets Punches (every SwipeRow; mobile rows enriched by `locations.report`: place,
remark, permission reason, phone, status, coordinates), Places, By person; any month with punches (404 otherwise);
audit `export.locations`.

## Data safety (v1.6.1)
`services/backups.py` — `make_backup` (sqlite backup API → `.partial` → integrity_check → rename; key copy; prune),
`prune` (14 days full + newest per month ×24; pre-update ×10), `check_health` (quick_check at start), `restore`
(verified backup only; safety copy first), `summary` for Settings. `maintenance.backup_loop` runs every 30 min and
backs up when the last regular backup is ≥ 24 h old. Supervisor (`run_server.py`, stdlib only): `PROTECTED` names are
never moved or replaced; `backup_db` verifies the pre-update copy and aborts the update before any file is touched.
SQLite: WAL + `synchronous=FULL` + `busy_timeout=30000` + foreign keys. Exports read the database at download time
(no cached files).

## UI lists & heatmap (v1.6.3)
`_ui.toplist(rows, unit, pkey, n=10, neg=False)` renders the whole ranked list with the top n visible (no-JS
fallback); `app.js` adds Top N (localStorage `hsco.toplist.n`, synced across lists) and search. `metrics.top_lists`
returns every qualifying row. Heatmap Total: inline script in `insights.html` recounts cells per row (sets `data-v`),
then calls `HSCO.tables.hm.apply()` so sorting and the footer (`data-sum`) use the new totals; `tt:apply` refreshes the
per-day footer.
