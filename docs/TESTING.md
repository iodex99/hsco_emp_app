# Testing

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium        # once, for tests/e2e
.venv\Scripts\python -m pytest -q                           # everything (≈ 1 min)
.venv\Scripts\python -m pytest -q --ignore=tests/e2e        # without browser
```
Tests marked as needing `reference/` (real client data, git-ignored) skip automatically elsewhere; synthetic
greytHR-format files from `tests/fixtures/make_fixtures.py` cover the same paths.

| File | What it proves |
|---|---|
| `test_engine.py` (37) | deduction blocks incl. boundaries (0.35 h → ½, 4.0 → ½, 4.0001 → 1), shift parsing, weekly-off patterns (Oct-2026 = 23/22/27 days), off-day hours excluded, comp-off ≥ 5 h, PL/A/LOP/COF buckets, half days, hours/day & month overrides, day overrides, calendar overrides, WFH methods, late/very-late, persistent late basis, missing punch, regularisation, auto attendance, present-without-punch, joiners/leavers, no-attendance |
| `test_parsers.py` (12) | **universal importer:** shuffled + renamed columns parse identically; another vendor's register (word statuses, time-only in/out, decimal hours, half day, PL/P, night shift); biometric log with split date/time, direction and lat/lng columns; inferred column → needs review → manual mapping → remembered layout; value normalisation; mapping suggestions from values. Also: value parsing, detection of every export & template, bad files, coordinates in columns or text, offline geocoder (Andheri, Connaught Place), real files detected incl. wrong `<dimension>` (DOJ) and 1M-row used range (MIS) |
| `test_reference_reconciliation.py` (5) | **engine vs manual October MIS: 91/97 identical on all 12 columns; the 6 known differences asserted field by field; deductions identical (+ HS207 finding); Summary total hours reconcile for all; WFH-over-limit list; behaviour findings** |
| `test_web.py` (22) | **v1.4:** unrecognised file → Map columns → validation (missing / duplicate) → import → layout remembered → next upload automatic → forget layout; expired/bogus tokens; one-time place-name upgrade; Insights KPIs, Bradford = S²×D, spells across weekends, bands, matrix, scorecard, Insights & Scorecard pages render; board pack contains every tab table. Earlier: login required everywhere, lockout, forced TOTP enrolment, TOTP challenge, TOTP replay rejected, recovery codes single-use, CSRF enforced, email OTP enrol + sign-in, passkey options, roles (viewer 403s), user creation → temp password → forced change, imports + calculations on synthetic data, every page renders, overrides + ledger + audit, settings/calendars/geofence/master edits recompute, finalise lock (409) + re-open, Excel (sheets, live formulas, formula logic = app), HTML pack self-contained, templates round-trip, bad upload reported, non-Latin-1 flash messages, DB commit-before-response, review queue (mark / mark-all / re-open / survives recompute / viewer forbidden), quick-search API, previous/next employee, checklist |
| `test_web_reference.py` (1) | real files through HTTP → HR settings via UI → reconciliation page shows **91 of 97** → dashboard/flags/drill-downs → Excel deductions → board pack |
| `e2e/test_disconnect.py` (2) | live server: overlapping signed-in requests must not deadlock (fails on v1.0 code); a client disconnect must not leave the DB locked |
| `test_updater.py` (5) | release package contents, release parsing, zip validation (zip-slip, data/, incomplete, wrong build), download with progress, checksum mismatch rejected, staging, source checkouts never self-update, supervisor apply + DB backup + rollback |
| `e2e/test_update_flow.py` (1) | **installed release copy under the supervisor + mock GitHub + Chromium:** banner → What's new → Update now → progress bar moves → restart → build 2 running, toast, data & session intact, DB backup + rollback copy present; then a broken build 3 is rolled back automatically and the dialog explains it |
| `e2e/test_supervisor.py` (1) | killing the supervisor stops the web server (no orphan holding the port) |
| `e2e/test_tables.py` (3) | + bulk select (checkbox, row click, select-all respects filters, indeterminate state), bulk review with one note, bulk re-open, legend multi-select calendar filter with counts, flags card → employee-filtered queue |
| `e2e/test_tables.py` (earlier 2) | table toolkit on a live server: search, enum facets with counts, numeric/date filters, chip removal, URL state survives reload, saved view, header sort cycle (num high→low → low→high → off), Shift multi-sort, group-by with collapse, columns + presets, CSV equals visible rows, filter-aware totals; review-queue severity filter/sort; employees date filter; day chips + late sort; audit server filters |
| `e2e/test_browser.py` (1) | Chromium: sign-in + TOTP enrolment, drag-drop import with file preview, `/` quick search, filter chips, calendar-day correction drawer, review-queue drawer, tab switching, user-menu sign-out, upload, dashboard charts render, sheet filtering, drill-down, day correction form, Excel & board-pack downloads, new board pack offline (file://): toolkit search → drill-down → day-table sort, every tab, **passkey registration and passkey sign-in** (virtual authenticator), no JS errors on any page |

## Latest result — 2026-11-17
`109 passed` (101 unit/integration + 8 browser; Windows 11, Python 3.13.15, Chromium via Playwright 1.65), real
reference data present (v1.4.1). Previous: 90 (v1.4.0). Board pack additionally exercised offline from file:// with all network blocked: no requests, no JS
errors, chips/sort/drill-down/legend filter/←→/Esc/needs-attention jump all working, at 1440 px and 390 px.
Screenshots of the e2e run: `test-results/*.png` (git-ignored).

## Live verification against GitHub (2026-11-17)
1. Push `115604a` → Actions run 1 (Windows tests 77/77 green) → release **build-1** (v1.2.0, zip 864 KB + sha256).
2. build-1 downloaded from GitHub, checksum verified, extracted to a fresh folder, started with the supervisor
   (`run_server.py --dev --port 8450`, no `.git`): its first check against `api.github.com` reported "up to date".
3. Push `97c8953` (v1.2.1) → Actions run 2 green → release **build-2**.
4. In Chromium on the installed copy: Settings → Updates → *Check now* → banner "Version 1.2.1 is available" →
   *Update now* → progress `0% → 76% → Verifying checksum → Backing up, installing and restarting → Updated to
   v1.2.1 (build 2)`; restart took ~5 s; `/healthz` = 1.2.1 build 2; sidebar shows "v1.2.1 · build 2";
   `last_result.json` = applied; `backups/pre-update-build2-*.db` and `updates/rollback/build1-*` present; no JS errors.
5. Stopping the supervisor process freed the port within seconds (child watchdog).

## Bugs found by testing (fixed)
- openpyxl read-only mode trusted greytHR's wrong `<dimension>` → DOJ report read as 1 column → `reset_dimensions()`.
- Jinja autoescaped embedded JSON → dashboard charts did not render → `Markup` with `</` neutralised.
- Board pack: search box `change` on blur re-rendered rows mid-click → click lost → listen to `input` only.
- **Found in manual use (1.0.1):** first 2FA code → HTTP 500 (`database is locked` because commits happened after
  the response; then a non-Latin-1 flash cookie). Fixed + regression tests; the in-process TestClient could not
  reproduce the timing, so the live server is now also checked with a real browser.
- **1.1.0:** the real cause of the 500s was an event-loop deadlock (async auth dependency doing blocking SQLite
  writes), reproduced with a live-server test before fixing. Screenshot review with real data found layout bugs the
  tests could not (clipped columns, totals ignoring filters, wrong logo initials, squeezed mobile search).
- **1.3.0:** toolkit tests caught a header sort cycle that skipped ascending for numbers, and a Columns button
  shadowed by the `cols` property; a shell-escaped `\n` had broken a regex (now `node --check` before testing).
- Fixture/time arithmetic and test expectations corrected (auto-attendance semantics).

## Full re-test — v1.4.1 (2026-11-17)
Beyond the automated suite (scripts kept out of the repo; they need real or generated data):
- **Crawl:** every internal link (≈ 220 pages incl. all 97 employees, filters, downloads) as admin and as viewer —
  all 200 (viewer correctly 403 on HR/admin pages), no JS errors; typical server time < 0.35 s.
- **Fresh install, real data, browser:** drop all five greytHR exports at once → 2.8 s, all recognised, no errors.
- **Awkward uploads:** empty workbook, header only, CSV muster, corrupt zip, PDF, .xls renamed .xlsx, 32k-char cell,
  far-future dates, Unicode names, formula-like text, same file twice — each gives a clear result; nothing crashes.
- **Scale:** synthetic 300 employees × 12 months (≈ 8.7k muster rows and 11k swipes with GPS per month) imported via
  HTTP; page and export timings in CHANGELOG 1.4.1.
- **Concurrency:** two HR users importing different months while three viewers browse (≈ 50 requests/s): no errors,
  read median 0.04 s, p95 0.21 s. Start-up maintenance on that data (50k renames, 12 recalculations, 3.4k backfills)
  ran in the background with every request succeeding.
- **Independent code review** of v1.4.0: 12 findings, all fixed with regression tests (CHANGELOG 1.4.1).
New tests: numeric IDs, status words, hours/time edge cases, warning rows, title periods, layout replay validation,
thread-safe detection (test_parsers, 19); attention at 0 %, stored summaries = day detail, joining/leaving list,
two unsure sheets, data month vs title, one recalculation per upload, escaped board pack + formula-safe Excel,
upgrade skips finalised months, partial files, content-based file type, future dates refused, delete month (test_web).
