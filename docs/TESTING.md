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
| `test_web.py` (22) | **v1.4:** unrecognised file → Map columns → validation (missing / duplicate) → import → layout remembered → next upload automatic → forget layout; expired/bogus tokens; one-time place-name upgrade; Insights KPIs, 12-month absence days and spells (spells across weekends), no Bradford anywhere (v1.5.2), matrix, scorecard, Insights & Scorecard pages render; board pack contains every tab table. Earlier: login required everywhere, lockout, forced TOTP enrolment, TOTP challenge, TOTP replay rejected, recovery codes single-use, CSRF enforced, email OTP enrol + sign-in, passkey options, roles (viewer 403s), user creation → temp password → forced change, imports + calculations on synthetic data, every page renders, overrides + ledger + audit, settings/calendars/geofence/master edits recompute, finalise lock (409) + re-open, Excel (sheets, live formulas, formula logic = app), HTML pack self-contained, templates round-trip, bad upload reported, non-Latin-1 flash messages, DB commit-before-response, review queue (mark / mark-all / re-open / survives recompute / viewer forbidden), quick-search API, previous/next employee, checklist |
| `test_web_reference.py` (1) | real files through HTTP → HR settings via UI → reconciliation page shows **91 of 97** → dashboard/flags/drill-downs → Excel deductions → board pack |
| `e2e/test_disconnect.py` (2) | live server: overlapping signed-in requests must not deadlock (fails on v1.0 code); a client disconnect must not leave the DB locked |
| `test_updater.py` (5) | release package contents, release parsing, zip validation (zip-slip, data/, incomplete, wrong build), download with progress, checksum mismatch rejected, staging, source checkouts never self-update, supervisor apply + DB backup + rollback |
| `e2e/test_update_flow.py` (1) | **installed release copy under the supervisor + mock GitHub + Chromium:** banner → What's new → Update now → progress bar moves → restart → build 2 running, toast, data & session intact, DB backup + rollback copy present; then a broken build 3 is rolled back automatically and the dialog explains it |
| `e2e/test_supervisor.py` (1) | killing the supervisor stops the web server (no orphan holding the port) |
| `e2e/test_tables.py` (3) | + bulk select (checkbox, row click, select-all respects filters, indeterminate state), bulk review with one note, bulk re-open, legend multi-select calendar filter with counts, flags card → employee-filtered queue |
| `e2e/test_tables.py` (earlier 2) | table toolkit on a live server: search, enum facets with counts, numeric/date filters, chip removal, URL state survives reload, saved view, header sort cycle (num high→low → low→high → off), Shift multi-sort, group-by with collapse, columns + presets, CSV equals visible rows, filter-aware totals; review-queue severity filter/sort; employees date filter; day chips + late sort; audit server filters |
| `e2e/test_browser.py` (1) | Chromium: sign-in + TOTP enrolment, drag-drop import with file preview, `/` quick search, filter chips, calendar-day correction drawer, review-queue drawer, tab switching, user-menu sign-out, upload, dashboard charts render, sheet filtering, drill-down, day correction form, Excel & board-pack downloads, new board pack offline (file://): toolkit search → drill-down → day-table sort, every tab, **passkey registration and passkey sign-in** (virtual authenticator), no JS errors on any page |

## Latest result — 2026-11-17
`152 passed` (v1.7.5; v1.7.4: 150; v1.7.1: 149; v1.7.0: 147; v1.6.7: 140; v1.6.6 and v1.6.5: 137; v1.6.4: 136; v1.6.3: 131; v1.6.2 and v1.6.1: 130; v1.6.0: 125, board-pack tests replaced by locations-export tests; v1.5.2: 125; v1.5.1: 123; v1.5.0: 119; 111 unit/integration + 8 browser; Windows 11, Python 3.13.15, Chromium via Playwright 1.65), real
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

## v1.5.0 — sign-ins with coordinates (2026-11-18)
New tests: real Nov-2026 export recognised as swipes with lat/lon the right way round and every point named (skips
without real data); synthetic fixture now in the new 17-column layout (GPS, phone, remarks, permission reason, a
rejected sign-in); punch log never read as a muster; swapped coordinates corrected; location checks (office/away,
shared phone, rejected and biometric ignored); schema upgrade adds columns to an old database, idempotent; Locations
page, Settings suggestions, naming the office → OFF_SITE_OFFICE in the review queue, rejected sign-in note, remark in
the day view; shared phone flag; punches-only month (pending page with correct counts, no dead downloads); Excel
sheets and board-pack tab; real-data HTTP flow (October + November together, suggestions "Mumbai office"/"Delhi
office", office check).
Manual browser run on the real files: import of all six exports 2.6 s; Locations, suggestions, Name it → save,
office check (48 of 91 "Office" sign-ins elsewhere), crawl of 232 pages as admin and viewer — no errors.

## v1.5.1 — logo
Tests: SVG sanitiser refuses script / event handler / external link / foreignObject / DOCTYPE-ENTITY / @import /
external url() / embedded image; upload → served with sandbox CSP, cropped favicon, sidebar + favicon links, board
pack embeds it, sign-in page shows it to anonymous visitors, malicious upload refused (old logo kept), remove; viewer
gets 403; the real logo passes the sanitiser (skips without it). Visual check: sign-in desktop/phone, sidebar,
Settings card, board-pack header (white backing on navy), tab icon at 64/32/16 px.

## v1.6.1 — data safety
New tests: a correction and a review made in the app appear in the next Excel download (Corrections sheet with reason,
Flags review columns, payroll figure changed); daily backup is verified, copies the key, `due()`, health check,
retention rule (14 days + newest per older month), restore round-trip with safety copy, a corrupt backup is refused;
*Back up now*; supervisor refuses a package that contains `data/` (code and data untouched, result recorded) with the
data folder inside the app folder (PC layout); pre-update backup verified and pruned to 10.

## v1.6.2 — named places removed
Tests updated: Settings has no Locations tab and `/settings/geofences` is gone (404/405); the Locations page uses
built-in map names with no office-check column or "Name your offices" prompt and no OFF_SITE_OFFICE flag;
location checks only flag a shared phone (rejected and biometric punches ignored); Punches sheet without the
office-check column (latitude/longitude in columns 11–12); real-data flow: every GPS punch has a map place name.
130 passed.

## v1.6.3 — ranked lists + heatmap Total
New test: `top_lists` keeps every qualifying employee (40-person sample, no cut-off), sorted high→low, shortfall lists
all 30 who are short; the Overview renders all 8 lists with the toolbar markup; the heatmap has the Total column (one
cell per row), `tfoot` and per-day footer cells. Browser check on real October data (copy of the installed database):
57 / 43 / 31 people in late / regularisation / missing-punch lists, Top 5 → all lists follow, All, search "shah" →
real ranks 10/16/19, no-match message, choice remembered; heatmap Total = Late column when Late highlighted (1,018
days in total), Late + WFH adds up, sort by Total, search "Article" → footer 15 people / 252; no console errors. 131 passed.

## v1.6.4 — WFH on working days only; targeted holidays; asset URLs
Engine: WFH on a weekly off (pattern), a greytHR holiday, an ALL_SAT_OFF Saturday, the person's own calendar holiday,
a targeted holiday and a day corrected to weekly off is not WFH (note, off-day work, `offday_wfh_days`; also with
`swipes_half`); targeted holiday without a calendar keeps greytHR's H; matching rules (locations AND groups, listed
employees always, case-insensitive, at least one target). Real October: 18 off-day WFH days for 17 people, over-limit
3 (HS016, HS179, HS195); manual-MIS comparison unchanged once those days are added back. Web: add/validate/delete a
targeted holiday (group and named employee), recalculation, audit, holiday-list import with targeted columns and an
unknown name reported, template headers; rules-version recalculation runs once; pages use `?v=` asset URLs;
Reconciliation 74/97 identical + 17 "app rule" only. Browser on real data: preview "Applies to 5 people" (Delhi),
picker, saved list "Delhi · plus <name> (HS004)", 21-Oct holiday for the four Delhi staff + HS004 while HS068
keeps their Wednesday weekly off. 136 passed.

## v1.6.5 — WFH over the limit deducts leave
Engine: 1–5 WFH days → 0, 0, ½, 1, 1½ days; notes on the 3rd+ WFH day; remark and flag text; shortfall + WFH add up;
setting 0 = flag only; exempt employee flagged, not deducted; off-day WFH never counts. Real October: shortfall part
still equals HR's (HS112 1, HS138 ½, HS205 ½, HS207 ½) and WFH part HS016 2, HS179 1, HS195 ½; Reconciliation 72/97
identical + 19 app-rule-only. Web: Excel deduction formula includes the WFH term and equals the app for T001/T004
(T004 Article = exempt), ledger column, Settings field. Browser on a copy of the installed data: start-up
recalculation applied the new rule; employee KPI, ledger line, review-queue messages and Settings field checked.
137 passed.

## v1.6.6 — WFH deduction for hours-exempt staff
Engine: employee-exempt, Article, SS and Trainee over the limit are deducted (remark "no hours deduction"); Partner and a
month exception are not; empty Partner list deducts Partners too. Web: T004 (Article, 4 WFH) → 1 day, Partner row has
no WFH term in the Excel formula; e2e sort/filter/CSV expectations updated (deductions T001, T002, T004 = 2.5 d).
137 passed.

## v1.6.7 — Punch exceptions
New test: every stored day's missing / WFH / late>1h mark appears as an issue and nothing else; T006 missing punch +
regularisation, T004 4 WFH → 2 over the limit (3rd and 4th day) and 1 day deduction, T005 absences, partner on
automatic attendance not checked; page, chips, sidebar link, `?day=` deep link; Excel sheets match the rows.
Browser on real October data: chips and counts, Missing punch filter (77 rows), a Regularisation row opens the
employee's day drawer, By employee tab; no console errors.
Session review (v1.6.2–1.6.7): independent code review (9 findings, all fixed) + new tests — WFH on full-leave days,
hours flag vs WFH-only / hours-exempt deductions, manual deduction wording, swipes ÷ 2 notes, Excel WFH term for
old-rule months (`_wfh_term`), HS045 leave-day WFH on real data. Full suite **140 passed** (incl. 10 browser tests).
Browser crawl of every page on real data: admin 227 pages, 0 errors, 0 JS errors (slowest: Punch exceptions 0.5 s
server, 1.1 MB); viewer 226 pages, only the 4 admin pages refused (403) as intended. Heatmap footer filled on load.

## v1.7.0 — employee categories
Unit: loose name matching, category from file name, `effective()` precedence (employee / category / additional /
greytHR / default), pasted lists (6 formats, "Good Friday" kept, headers skipped), optional holiday taken / not taken,
migration MUSTER → CATEGORY once, footer rows never employees, real files all detected (category report, 7 holiday
sheets, Location never the calendar, Day column). Web (synthetic): category report + Mum list (optional holiday, no
20-Oct) + Carlyle list without a Category column, Article via additional category, conflict listed then resolved by a
revised list that replaces the year (moved date removed, wrong year fixed by the Day column), category weekly offs and
hours, employee override, rename keeps people, delete refused, targeted by category, paste a list, categories on every
page and in the Excel, audit. Real data: 96 employees categorised (counts per category), report spelling kept, two MDS
files → one list with 2 optional, no default calendar, Carlyle 2-Oct holiday and the conflict list, Insights. Browser:
the journey now clicks every tab of Overview / Insights / Settings / Punch exceptions / Locations (a chart error only
shows when its tab is drawn — verified by re-introducing the bug). Visual check on a copy of the installed data:
upload of the 7 files 1.2 s, Categories tab, calendars by year, employees list / form, Overview and Insights by
category, day view of a Carlyle holiday. Full suite 147 passed.

## v1.7.1 — category review fixes
Web: a category list with only another year's holidays → greytHR's 20-Oct still a holiday (`calendar_from` "not
loaded"); a category pointing at the default calendar gets its own list, the default untouched; Jan–Jun then Jul–Dec
files keep 12 holidays, an optional-only file keeps the fixed ones; "(1)" file name no category; a master with groups in
"Category" → group; a DOJ-style file with Category keeps the joining date; MUSTER in a master → follow category; blank
additional category clears; Delhi list vs Mumbai people noted; rename updates and delete drops targeted holidays; an
employee's category can be cleared; "=SUM(1,1)" category stays text in Excel. Unit: optional wording, letter-only
employee numbers, file names, `effective(loaded)`, pasted weekday-after-date and year-less lines. Full suite 149 passed.

## CI diagnostics (v1.7.2)
`.github/workflows/release.yml` runs `pytest -rfE --junitxml=test-results/junit.xml`; on failure
`scripts/ci_annotate.py` prints a `::error` annotation per failed test (message + last traceback lines), visible via
`GET /repos/<owner>/<repo>/check-runs/<job id>/annotations` without authentication.
Flaky test fixed (v1.7.3): the update-flow toast race (see CHANGELOG) — 3 consecutive local runs green.

## v1.7.4 — closed Saturdays
Engine: 6-day-week employee, WFH on Sat 3 (open) and 10 / 17 / 24 (closed) + two weekdays → closed days flagged, still
WFH, not counted (3 WFH days, ½ day deduction), also with `swipes_half`; every Saturday open → all count; open 1st and
3rd → only 10 and 24 closed. Real October: HS016 4 closed-Saturday WFH days (6 → 2, no deduction); over the limit
HS179 and HS195. Web: setting saved ("1, 3, 9" → 1, 3) and shown. Full suite 150 passed.

## v1.7.5 — approved extra WFH
Engine: 5 WFH days (3 over) → 1.5 days; 2 approved → 0.5 (flag, day notes, remark say so); all approved → none. Web:
review queue shows *Approve WFH* for T004; a reason is required; approving 1 of 2 → 0.5 day, flag reviewed with the
note, audit entry; the Excel downloaded next has the new deduction, a formula with "−1" that equals the app, and the
approval in *Corrections*; 2 → no deduction; 0 → removed (1 day again); the correction is offered on the employee
page. Browser on real data: approved HS195's extra day from the review queue (drawer, chips, recalculated, reviewed).

## v1.8.0 — whole-app review
New: filters (`0.5`, `34.5`, plural, readable audit details); **Waive** from the review queue (reason required,
correction + audit, deduction 0, info flag, Excel *Exempt (hours)* = Waived with a formula giving 0, finalised months
refused); day drawer groups status codes and warns about fixed month figures; Overview opening-balance banner;
import templates round trip (drop-downs, frozen header, dates, Yes/No; master re-import changes nothing — the hidden
*Lists* sheet is not data; leave opening: one balance filled → only that saved, a stored balance not cleared).
Updated: export tests for the Excel *Contents* / *Payroll Input* sheets and Category columns; e2e CSV — *Columns shown*
vs *All columns*, Employee No + Name split, rows in screen order, raw numbers; real-data review-queue count.
Browser on the real October copy: review queue at 1200 / 1440 px, Waive drawer, day drawer, Overview banner, holiday
calendars, audit log, print preview, CSV of the payroll sheet (97 rows) and punch exceptions (1,198 rows), Excel
"Preparing…" — no console errors. Full suite: **157 passed** (incl. e2e and real data).

## v1.9.0 — holidays by category; no leave balances; Payroll Input split
New / rewritten: engine — a one-off holiday is a date in the category list (no targeting field); categories — a stale
employee calendar is ignored; web — *Holiday for several categories* (validation, added to the ticked list only, the
"no holidays yet that year" warning, audit), holiday file with Date/Day/Holiday/Category/Optional (two categories in a
cell, blank → default list, Day check, optional not taken), old targeting routes 404; migration (category-targeted →
list, location-targeted listed in audit, own calendar cleared, runs once); no leave balances anywhere (pages,
settings, imports, template 404, no ledger keys or flags); Payroll Input columns, formulas (hours part = the app's
shortfall deduction, V − hours = WFH part, every deducted person) and remarks; Comp-off Ledger columns; payroll sheet
columns; holiday template pre-filled and re-import = "no changes". Real data copy (browser): migration log, HS207
unchanged (0.5 day), firm totals unchanged (6 people, 4 days), Payroll Input 6 rows with remarks, holiday template 69
rows, pages render with no console errors. Full suite: **158 passed** (incl. e2e and real data).

## v1.9.1 — one-click install
`tests/test_deploy.py`: Install.cmd elevates and runs the installer; the installer parses (PowerShell parser); a dry
run prints the address / folders / Python and creates nothing; `create-admin` refuses two different passwords.
`test_release_package_contents`: Install.cmd and the installer are in the zip. Checked by hand here: the office-PC
script as generated (desktop path incl. a OneDrive desktop, shortcut file). **CI job `install`** (blocks the release):
the real installer on a clean Windows runner from the unpacked release zip — HTTPS `/healthz` and `/login` with the
certificate verified, database, scheduled task as SYSTEM, desktop icon, office-PC kit — then a second run and the same
checks.
