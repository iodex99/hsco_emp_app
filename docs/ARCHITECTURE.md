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
                           rules, recompute_from), compute (DB→engine→results), metrics, insights
                           (scorecards, absence, heatmaps), attendance_stats (compact month summaries), geo,
                           maintenance (start-up background jobs)
app/security/              core.py (passwords, sessions, CSRF)
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
   parser per kind → stored rows (muster/swipes/summary/manual MIS replace the month; master/DOJ/holidays upsert).
2. `services.compute.compute_period` builds engine inputs (employees + holiday calendars + overrides), runs
   `engine.compute_month`, stores one `MonthResult` per employee (scalars, flags, serialized days). Months are
   independent (v1.9: no ledger carry-forward), so nothing cascades.
3. Pages, Excel and HTML read `MonthResult` (fast; no recomputation on view).

## Database (SQLite file `DATA_DIR/hsco.db`)
| Table | Purpose |
|---|---|
| users, sessions | identity, server-side sessions (`mfa_methods`, `recovery_codes`, `email_otps`: 2FA, removed v1.10 — kept empty) |
| audit_log | every login, import, override, setting, user change |
| settings | `policy` and `general` JSON |
| employees, categories, holiday_calendars, holidays, geofences, geo_cache | master data (`targeted_holidays` and `employees.holiday_calendar_id`: retired v1.9, kept empty) |
| periods | month + status DRAFT/FINALISED |
| flag_reviews | review-queue state per period/employee/flag (note, who, when); survives recomputation |
| imports | file name, sha256, sheet, rows, warnings, who/when |
| muster_rows, swipes, summary_rows, manual_mis_rows | imported data per period |
| day_overrides, month_overrides | manual entries (reason mandatory); `leave_openings` retired v1.9 (kept, unused) |
| month_results | computed output (JSON `data`, `flags`, `days`) |
Schema is created with `create_all` at start-up (additive). For future column changes add a small migration step in
`db.init_db` and record it here.

## Security design
- **Passwords**: argon2id; policy (length, mixed case, digit, symbol, no username); lockout after N failures.
- **Sessions**: random 256-bit token in an HttpOnly, Secure, SameSite=Strict cookie; only its SHA-256 is stored;
  absolute expiry (10 h) + idle timeout (60 min); sessions revoked on password change and deactivation.
- **No two-step verification** (owner, v1.10): the password is the only factor; the app is used by Remote Desktop on
  the server, so Windows sign-in is the outer lock. A user created or reset by an admin gets a temporary password and
  must set their own before any page opens. (Until v1.9: TOTP, passkeys, email codes, recovery codes — migration
  `remove_2fa_v1` deleted their stored secrets and the email-server settings incl. its password.)
  `UserSession.mfa_passed` / `pending` are legacy columns (always true / unused). `secret.key` protects nothing now.
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

## Targeted holidays, rule versions, asset URLs (v1.6.4)
`models.TargetedHoliday` (day, name, locations, groups, emp_nos — comma-separated; created_by/at) — a new table, created
by `create_all` on upgrade. `services/holidays.py`: `applies`, `by_employee`, `for_month`, `describe`, `split`.
`compute_period` passes each person's `{date: name}` as `engine.Employee.targeted_holidays`. Routes:
`POST /settings/targeted-holidays`, `/settings/targeted-holidays/{id}/delete` (HR, audit `holiday.targeted.*`).
Holiday-list import: fields `for_locations`, `for_groups`, `for_employees`. `engine.compute.RULES_VERSION` +
`maintenance.recalc_on_rules_change` (setting `rules_version`). `web.ASSET_V` = SHA-1 of app.css/app.js/table.js/
icons.svg (10 hex), Jinja global `asset_v`, appended as `?v=` to those URLs; `window.HSCO_ICONS` carries the sprite
URL for icons built in JS.

## Punch exceptions (v1.6.7)
`services/exceptions.py`: `day_issues(day, wfh_types, auto)` → issue keys; `month_exceptions(db, period)` → rows,
per-employee counts, KPIs (from `MonthResult.days`, no extra queries). Route `GET /exceptions` (viewer) in
`routes/pages.py`, template `exceptions.html` (table toolkit + chips). Employee page opens a day from `?day=YYYY-MM-DD`.
Excel: sheets *Punch Exceptions*, *Exceptions by Employee* in `build_workbook`. Real October: 1,227 rows, service
0.05 s, page 0.33 s server / 1.7 s total, 1.1 MB.

## Employee categories (v1.7)
`models.Category`; `Employee.category` / `additional_category` (names), `Employee.weekly_off` "CATEGORY" = follow;
`Holiday.optional`; `TargetedHoliday.categories`; `MonthResult.category` (+ `data["category"]`). New columns by
`add_missing_columns`; `db.migrate_data()` runs one-time data migrations recorded in setting `migrations`
(`categories_v1`: weekly_off MUSTER → CATEGORY). `services/categories.py`: `key`, `find`, `ensure`, `by_key`,
`name_from_filename`, `effective()` → `Effective` (pattern / hours / calendar + their sources), `overview`,
`uncategorised`, `calendar_conflicts`. `compute_period` builds each engine employee from `effective()`; the engine gets
`optional_holidays`. Importer: kind `employee_category` (same handler as `employee_master`), holiday fields `category`,
`optional`, `weekday`, `location`, `for_categories`; `imports._apply_holiday_lists` (per file, per calendar and year),
`_check_weekdays`, `_category_from_report`; `parsers._row_emp`. Routes: `POST /settings/categories/save`,
`/settings/categories/{id}/delete`, `/settings/calendars/{id}/paste` (`holidays.parse_pasted`). Templates: Settings
*Categories* tab, calendar year groups / optional / paste; employees list + form; category tables on dashboard and
insights (`metrics._breakdown`, `insights._compare`). RULES_VERSION 6.

v1.7.1: `calendars_for_month` also returns the calendars loaded for the year; `effective(…, loaded)` skips the others.
`imports._shared_calendar`; `categories.category_in_filename`; employee form posts `cat_fields=1` so a blank clears.

v1.7.5: `POST /flags/wfh-approve` (HR; period, emp_no, days, reason) → `MonthOverrideRow(field="wfh_approved_days")` +
`FlagReview` note + audit + `compute_period`; `MONTH_OVERRIDABLE` includes `wfh_approved_days`;
`excel._wfh_term` subtracts the approved days in the formula.

## Whole-app review (v1.8.0)
- **CSV standard** (`static/table.js` `csv(btn)` → `exportCsv(all)`): rows = `this._order` (on-screen order) filtered
  by `_shown`; columns = visible (or all) exportable ones (`type != none` or `data-export`), plus identity columns
  (group / category / location) always. A column whose cells are mostly people (`.person` block or
  `data-csv="Name (EMPNO)"`) is split into *Employee No* + *Name*. Value order: `data-csv` → numeric `data-v` (raw,
  ≤ 4 dp) → date `data-v` (ISO) → text (multiple badges/tags joined `; `, "—" → blank). Formula-like text is
  prefixed with `'`. File `hsco-<data-csv>-<yyyymmdd-hhmm>.csv`, UTF-8 BOM, CRLF.
- **Excel** (`reports/excel.py`): `SHEET_GUIDE` (order, tab colour, one-line purpose) → `_finish(wb, title, org,
  status, contents_lines, guide)` builds *Contents*, orders and colours tabs and calls `_polish(ws)` (freeze, filter,
  print: landscape, fit to width, print titles, footer). *Payroll Input* links to the payroll sheet by formula.
- **Waive**: `POST /flags/waive-shortfall` (HR; period, emp_no, reason; draft months only) →
  `MonthOverrideRow(field="shortfall_waived", value="true")` + audit + `compute_period`. `services/compute.py`
  passes it as text (with `remark`, `exempt`); engine `MONTH_OVERRIDABLE` includes it; RULES_VERSION 9.
- Day-override redirect lists pinned month figures (`OVERRIDE_LABELS`); `employee_month.html` groups status codes
  by `status_meaning` (policy.status_map).
- Template filters `plural`, `readable` (`web.py`); `_fmt_num` strips trailing zeros. `metrics.compute_kpis` adds
  `no_opening` (Overview banner). `base.html` `.print-head` (print only).
- Import templates (`routes/reports.py` `_wb`): hidden *Lists* sheet + list validations (no error alert), date
  formats, freeze/filter, Instructions second. `imports.py` leave opening: rows without any balance skipped; a
  missing balance never overwrites a stored one.

## Holidays by category, no leave balances (v1.9.0)
- **Holidays**: `categories.effective` takes the calendar from category → additional category → default only (no
  employee calendar). Engine `Employee.targeted_holidays` removed. `services/holidays.py` = `split` + `parse_pasted`.
  Parser kind `holidays`: date, holiday_name (+ category, optional, weekday). `imports._apply_holiday_lists` targets
  `("category", name)`, `("default", "")` (default list, "Standard" created as default when missing) and
  `("calendar_id", id)` (paste into a chosen calendar); a comma-separated Category cell → one plan row per category.
  `POST /settings/holidays/categories` (HR; day, name, optional, categories[]) → those lists + audit `holiday.add`.
  Removed: `/settings/targeted-holidays*`, the employee form's calendar, the master template's Holiday Calendar column.
  `db._holidays_by_category` (migration `holidays_by_category_v1`): category-targeted rows → category lists; the rest
  listed in audit `migration.holidays_by_category`; `employees.holiday_calendar_id` cleared.
- **No leave balances**: `services/compute._ledger`, `prorata_entitlement`, the `cascade` argument and the
  `/periods/{key}/employee/{emp}/opening` route are gone; parser kind `leave_opening` and its template removed; policy
  fields `leave_credit_mode`, `absent_debits_leave`, `cof_overdraft_to_leave` removed (stored values ignored).
  `recompute_from` recalculates only the months given. Metrics drop `no_opening`, `salary_deduction_days`,
  `payroll_lop_days`; insights drop liabilities / encashment.
- **Excel**: payroll sheet columns 27–29 = Comp-off Earned, Flags, Category (`TEXT_COLS` 28, 29). *Payroll Input*:
  F = hours part (same formula as column V's hours term, referencing the payroll sheet), G = `V − F`, H = `V`, I =
  `_deduction_remark(d, pol, manual)`; a hand-set deduction (month correction) uses the app's stored split.
  *Comp-off Ledger* (was *Leave & Comp-off Ledger*): A–D + earned + taken. RULES_VERSION 10.

## Installer (v1.9.1)
`Install.cmd` (zip root; elevates via `Start-Process -Verb RunAs`; returns the installer's exit code) →
`deploy/install_windows.ps1` (Windows PowerShell 5.1 compatible; defaults from an existing install: task working
directory, `HSCO_DATA_DIR`, `HSCO_RP_ID`). Python: `py -3.13` → Program Files Python 3.13/3.12/3.11 → `python` on PATH
(not the Store stub) → winget → python.org 3.13.1 silent all-users. Writes the office-PC kit next to the app folder.
`scripts/build_release.py` ships `Install.cmd`; `.gitattributes` keeps `*.cmd` / `*.ps1` CRLF. CI job `install` in
`.github/workflows/release.yml`.

## Deleting an upload (v1.11)
`batch_id` (the `imports.id`, no foreign key) on `muster_rows`, `swipes`, `summary_rows`, `manual_mis_rows` and
`holidays`; `ImportBatch.undo` = `{"created": [emp_no], "changed": {emp_no: {field: [before, after]}}}` from a
snapshot of `imports.EMP_FIELDS` before and after each sheet. `import_file` creates the batch first (flush → id) and
fills period / rows / warnings / undo at the end; errors roll the whole file back. `imports.import_effects` (what a
delete removes) and `imports.delete_import` (refuses finalised months; deletes rows by `batch_id`; restores a field
only if it still has the value the file set; deletes created employees without rows in any `ROW_TABLES`; audit
`import.delete`; recalculates the month or clears its results and `computed_at`, else all draft months). Route
`POST /imports/{id}/delete` (HR, reason). Migration `import_batch_links_v1` links pre-v1.11 rows to the latest upload
of their kind and month.

## RH, review types, data folder (v1.12)
- Engine: `model.RH_CODES = ("RH",)`; a WORKDAY with RH in s1/s2 → HOLIDAY ("Restricted holiday (RH)"),
  `DayDetail.is_rh`, comp-off not earned on it; serialized as `rh`. RULES_VERSION 11.
- `workflow.REVIEW_CODES = ("WFH_OVER_LIMIT", "GROUP_NOT_SET")`; `actionable_flags` marks others `reviewed=True`,
  `auto=True`, `reviewed_by="auto"`; the queue lists only actionable rows; Excel Flags status "Reviewed automatically".
  `/flags/waive-shortfall` takes `back` (the employee page).
- Data folder: `config.base_data_dir()` (HSCO_DATA_DIR or `<app>/data`), `config.resolve_data_dir()` follows
  `<app>/data_location.json` {"base", "dir"} only while the base matches; `run_server.data_dir()` does the same and is
  re-read at every (re)start; the supervisor sets `HSCO_SUPERVISOR_FEATURES=datadir` (older supervisors: the move is
  offered only after a server restart); `data_location.json` is in `run_server.PROTECTED` and `.gitignore`.
  `services/datadir.py`: `info`, `check_target` (absolute, new/empty, outside the current folder and the app, drive
  exists, free space), `move` (copytree without `updates/staged|rollback|download`, DB via `sqlite3.backup`,
  integrity check, icacls when admin, pointer written atomically, MOVED.txt). `POST /settings/data-folder` (admin) →
  audit `settings.data_folder` before the copy → restart hook. The installer honours the pointer.

## Offline place names: self-repair (v1.12.1)
`geo.upgrade_place_names` (start-up maintenance) returns early while the gazetteer is missing (version not recorded);
when it is present it also re-names, at every start, swipes in any month whose `place` has no letter (bare
coordinates — SQLite `GLOB '*[A-Za-z]*'`), deletes GeoCache rows that hold coordinates, and recalculates touched draft
months. `Namer` never caches a coordinate fallback. Settings gets `geo_ok = geo.SRC.exists()`. Installer copy:
`robocopy /XD <full paths of top-level folders> __pycache__` and a check for the gazetteer.

## v1.13 — scope, click-through, PDF
- `services/scope.py`: `ignored_emp_nos(db)` / `employees(db)` / `is_ignored(group)` from `policy.ignored_groups`
  (case-insensitive). Used by `services/compute.compute_period` (muster, swipes and employees filtered before the
  engine), `metrics.scoped_employees` (KPIs, people movement, tenure, Excel), `insights`, `locations.report`, the
  locations workbook, `/api/employees`, `/employees` (`?ignored=1` lists only them), reconciliation.
- Engine: `B_LOP` is retired (BUCKETS without it; `_bucket` and `policy_from_json` read a stored `lop` as `leave`);
  `EmployeeMonth.lop_days / missing_punch_days / no_punch_present_days` and `DayDetail.is_missing_punch` removed;
  `Policy.lop_bucket` removed, `Policy.ignored_groups` added. RULES_VERSION 12. `metrics.RETIRED_FLAGS` /
  `live_flags()` hide LOP / MISSING_PUNCHES / PRESENT_WITHOUT_PUNCH of months finalised earlier.
- Drill-down data: `period_metrics` returns `who` {emp: [name, group]}, `person_rows`, `wfh_people` and
  `drill` {daily: {date: {present|leave|absent|wfh: [[emp, days]]}}, loc, late_wd, in_time, tenure}; `insights` returns
  `drill` {hours, arrival} and `top` {absence_month, absence_12m}; Insights page JSON adds `who` and `people`.
- Front end (`static/app.js`): `HSCO.drill({title, sub, cols, rows, who, href, link})` — a drawer with a data-table
  (search, Filter → Group, sort, CSV, totals); `HSCO.chart(id, cfg)` accepts `dates` (day tooltips), `total` (stacked
  footer), `share`, `drill(index, datasetIndex)` (clicked bar / segment); charts kept in `HSCO.charts`. "i": macro
  `ui.info(text)` / `ui.kpi(…, tip=)` + one floating tooltip (hover, focus, tap; never follows the link it sits in).
  Ranked lists: `ui.toplist` adds `data-g` / `data-v`, a group menu (`details.tl-grp`) and a total footer.
- PDF: `[data-pdf="title"]` buttons; `app.js` loads `vendor/jspdf.umd.min.js`, `vendor/jspdf.plugin.autotable.min.js`
  and `static/pdf.js` on first use. `pdf.js` shows every tab (`body.pdf-all`, behind a cover), draws charts at 2× and walks
  `main.content`: page header, KPI / mini boxes, card titles, charts as PNG (side by side when the screen shows them side
  by side), ranked lists (side by side when short), tables via AutoTable with each cell's background / text colour, totals
  rows; text limited to Western European characters (→, ≥, − … spelled out). Header / footer / "Page x of y" added last.
