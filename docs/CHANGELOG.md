# Changelog

## 1.4.0 — 2026-11-17 — universal importer, automatic place names, Insights, new board pack
- **Universal importer** (`app/importers/parsers.py` rewritten): reports are recognised by *meaning*, not by exact
  layout. Each report type is described by fields with synonyms; headers are matched exactly, by whole words or fuzzily,
  in any order, on any of the first 30 rows; the values under each column are checked (dates must be dates, IDs look
  like IDs, coordinates are in range) and a required column with an unhelpful header is inferred from its values.
  Values are normalised: dates in any common format or Excel serials, times with or without a date (night shifts
  crossing midnight), hours as `08 : 29` / `8:29` / `8.5` / `8h 29m` (or computed from in/out), status as codes or
  words (`Present`, `Week Off`, `Half Day`, `PL/P`…) in one column or two session columns, swipes with one date-time
  column or separate date + time (+ IN/OUT direction), coordinates in one cell or two columns. Detection of the
  real exports is 40× faster (≈ 0.3 s instead of ≈ 11 s).
- **Map columns once, remembered forever:** a sheet recognised with low confidence (or a file nothing matches) is *not*
  imported blindly — the result says "not imported yet · Map columns". The mapping screen pre-fills its guesses
  (from headers and values), shows a live preview with the chosen columns highlighted, refuses missing required
  fields or one column used twice, and imports. "Remember this layout" stores it (settings `import_layouts`, by header
  signature) so the next file like it imports automatically; remembered layouts are listed (and can be forgotten) on
  Import files. Each import result lists the columns used ("Employee number ← 'Emp Code'").
- **Location names straight from coordinates, offline:** bundled GeoNames gazetteer of 566,055 Indian places (towns,
  villages, neighbourhoods, localities, stations, landmarks) with district and state, indexed on first start into
  `DATA_DIR/geo` (≈ 4 s, background). Names like "Andheri East, Mumbai Suburban, Maharashtra", "near Thane,
  Maharashtra (1.3 km)" or "Girgaum, Mumbai, Maharashtra · by Marine Lines". Nothing leaves the server. Swipes named by
  the older city list are renamed once automatically and draft months recalculated.
- **Insights page** (new sidebar item) and per-employee **Scorecard** tab: unplanned absence rate, Bradford factor
  (rolling 12 months, bands), on-time arrivals, hours vs required, overtime/shortfall, long days, WFH share,
  regularisation and missing-punch rates, leave used this FY, leave & comp-off liability, attrition (12 months),
  average tenure, and a 0–100 **attention score** with its main reasons. Tabs: scorecard (full table toolkit),
  employee × day **attendance heatmap** with legend highlighting, arrivals heatmap (weekday × half hour) and working-day
  length histogram, Bradford list & bands, groups compared, trend. The employee page compares the person with their
  group and the firm. Excel gains *Scorecard* and *Groups Compared* sheets.
- **Board pack rebuilt:** same look and the same table toolkit as the app, fully offline in one file (stylesheet, font,
  icons, Chart.js, toolkit inlined; no network requests). Tabs: Overview (KPIs, daily attendance, needs-attention list
  that jumps to the exceptions, charts, top lists), Payroll leave sheet (chips, search, filters, multi-sort, group-by,
  columns, views, CSV, live totals), Scorecard, Attendance heatmap, Exceptions, Absence & hours, Groups, People, Trend.
  Clicking any person opens a drill-down with KPIs, calendar with multi-select legend filters, flags, attention
  breakdown, comparison with group and firm, and a sortable/filterable day-by-day table; ← → step through the people in
  the order of the table you came from; Esc closes. Print / PDF prints every section.

## 1.3.1 — 2026-11-17
- **Review queue bulk review:** a checkbox on every row and a select-all box in the header (selects only the rows the
  current filters show); clicking a row toggles it. A floating bar shows "N selected · Mark reviewed · Re-open · Clear";
  one note is saved on every item. API: `POST /flags/review` accepts `items: [{emp_no, code}]` (unknown items ignored).
- **Review queue for one employee:** `/flags?emp=HS055`, with a "Showing only … · Show everyone" banner. The Flags card on
  an employee's page links to it ("Review queue →"), and each flag line opens the queue on that flag for that person.
- **Calendar legend filters:** click one or more legend items (Absent, Leave, Comp-off, Holiday, Late, WFH, Missing
  punch, Regularised, Worked off-day, Corrected) to show only matching days (a day matches if it has any selected mark);
  each item shows its count and is disabled when it never occurs that month; "Show all days" resets.
- Fixed small scroll arrows that appeared next to tab bars.

## 1.3.0 — 2026-11-17 — sorting & filtering everywhere
- New table toolkit (`app/static/table.js`) on every data table (payroll sheet, employees, review queue, import
  history, audit log, reconciliation, groups, an employee's day-by-day):
  search · typed **filters** shown as removable chips (text: contains / not / is / empty; numbers: ≥ ≤ > < = ≠ between;
  dates: on / before / after / between; lists: tick-boxes with counts) · **multi-level sort** (click a header; Shift-click
  adds a level; or the Sort menu) with natural ID order · **group by** with collapsible groups, counts and subtotals ·
  **columns** show/hide (remembered per page) · **saved views** (reusable across months) · **shareable link** (state in
  the URL) · **CSV export** of exactly what is shown · "Showing X of Y" and totals that follow the filters.
- Quick-filter chips kept and combined with the toolkit; new day chips on the employee page (Working days, Late,
  Missing punch, Regularised, WFH, Absent/LOP, Leave/comp-off, Worked off-day, Corrected) and a Late (min) column.
- Review queue: separate Group / Severity / Exception / Status columns; severity sorts Critical → High → Medium → Info.
- Reconciliation: one row per difference with a Difference column (no merged cells, so it filters and sorts).
- Audit log: server-side filters (search, person, type, date range) with paging of 200.

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
