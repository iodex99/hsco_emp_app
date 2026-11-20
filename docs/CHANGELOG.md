# Changelog

## 1.9.0 — 2026-11-20 — holidays only by category; leave balances left to greytHR; Payroll Input split by cause
Owner: "for holidays they only need these fields: date, day, holiday, category, optional … holidays are targeted only
on the basis of category … in the payroll input sheet we need to see a bifurcation in a column as well as in remark if
there is a leave deduction because of WFH or shortfall of hours … remove Payroll LOP, Salary deduction, Leave balance
after … they want to calculate leave balances [in greytHR], so remove it from everywhere … Leave & Comp-off Ledger:
columns A–D and then only comp-off earned and comp-off taken".

**Holidays — by category only**
- Holiday files need only **Date, Day, Holiday, Category, Optional** (greytHR's Holiday List Importer files still
  work; their Location column is ignored). Several categories in one Category cell (comma-separated) put the holiday
  in each list; a blank category goes to the default list.
- Removed: targeting by location / group / named employee, the *Calendar* and *For …* columns, and the per-employee
  holiday calendar (employee form and employee-master template). The employee page shows which list applies.
- Rules & calendars → **Holiday for several categories** replaces *Targeted holidays*: date, name, optional, tick
  categories → added to each list (warns when a list had no holiday yet that year).
- The **holiday template** has only the five columns and is pre-filled with the lists in the app from this year on
  (re-importing it unchanged reports "no changes").
- One-time migration: targeted holidays for categories moved into those lists; others listed in the audit log
  (*Holidays moved to category lists*); employees' own calendars cleared. On the installed data: no targeted holidays;
  HS207's own calendar cleared — they now follow HSCO (CDTIPL) Boutique's list (October figures unchanged).

**Leave balances — removed everywhere (kept in greytHR)**
- No opening balances (route, form, import kind and template removed), no leave / comp-off ledger, closing balance,
  salary deduction, payroll LOP, leave liability or encashment; flags *Leave opening balance not set*, *Leave
  exhausted*, *Comp-off overdrawn* no longer raised. Settings: leave credit mode and the two "taken from leave"
  switches removed (paid leave per year stays for *Annual leave used* in Insights).
- Overview tile **LOP days** (approved in greytHR); payroll page drops *Leave bal.* and *Salary ded.*; employee page
  tile *Leave taken* and tab *Leave & comp-off* = this month's PL / comp-off / LOP / absence, the deduction split and
  comp-off earned / taken; Insights tile *Annual leave used* instead of *Leave liability*.
- Months are now independent: changing one month no longer recalculates the months after it (faster).

**Payroll Excel**
- **Payroll Input**: Employee No, Name, Group, Category, Location, **Hours shortfall deduction**, **WFH over-limit
  deduction**, **Total leave deduction**, **Remark** ("Hours shortfall: 3.37 h short of 120 h required → 0.5 day";
  "WFH over the limit: 4 WFH days, limit 2 → 2 extra × 0.5 = 1 day"). Linked to the payroll sheet; the parts add up
  to column V. Payroll LOP, Salary deduction and Leave balance after removed; only people with a leave deduction.
- **Comp-off Ledger** (was *Leave & Comp-off Ledger*): Employee No, Name, Group, Category, Comp-off earned, Comp-off
  taken.
- Payroll Leave Sheet: Leave Opening / Credit / Debit / Closing, Salary Deduction and Payroll LOP columns removed
  (Comp-off Earned, Flags, Category are now columns AA–AC). Contents shows the deduction split and LOP days;
  Scorecard drops the balance columns.
- Calculation rules version 10 (draft months recalculate once after the update).

## 1.8.0 — 2026-11-19 — whole-app review: downloads, CSVs, review queue, corrections
Owner: "go through the whole app, see what can be improved … also the excel downloads, the csv downloads, how we can
add value / how the csv can be improved for every export … do all, check all".

**CSV (every table — one standard)**
- *CSV* asks **Columns shown** or **All columns** when columns are hidden; rows are always the ones on screen (filter,
  search, sort — now in the on-screen order).
- Employee columns become two: **Employee No** and **Name** (sortable / VLOOKUP-able in Excel); numbers are raw
  (`0.5`, `186.6333` — unit in the header, no "d" / "h"); dates ISO (`2026-10-01`, `2026-10-01 11:15`); lists joined
  with `; `; "—" becomes blank; no badges, icons or initials. Group / Category / Location always travel along.
- Text that Excel would run as a formula (`=`, `+`, `-`, `@` …) is neutralised. File name
  `hsco-<table>-<month>-<yyyymmdd-hhmm>.csv`, UTF-8 with BOM (opens correctly in Excel).
- Per page: heatmap exports a status per day (P, WFH, Leave, Holiday …); punch exceptions export issues with the
  minutes late, In and Out as separate columns, and "corrected in the app"; review queue exports the message and
  the review note; employees export the working pattern in words; locations export every person at a place, not
  the first five.

**Payroll Excel**
- New first sheet **Contents**: month, status (DRAFT / FINAL), key totals (deduction days, LOP, salary deduction,
  open exceptions, corrections, balances not set) and every sheet with one line on what it is for; coloured tabs
  (payroll / exceptions / detail).
- New sheet **Payroll Input**: only the people payroll must act on (leave deduction, LOP or salary deduction), linked
  to the payroll sheet by formula, with totals.
- Every sheet: Employee No + Name + Group + **Category**, frozen headers and filters, real dates (`dd-mmm-yyyy`),
  print set-up (landscape, fit to width, repeated header row, "Page n of m" footer with the month and status).
- Leave balances that were never imported say **not set** (grey) instead of blank; the deduction column has a note
  with the rule. Flags sheet lists open items first. Daily detail says *Working day / Weekly off / Holiday — name*.
- Punches & locations workbook: same finish, biometric punches labelled *Biometric*, a **By person** sheet with
  Group / Category / Location.

**Review queue**
- **Waive** on *Hours shortfall → leave deduction*: a partner waived the hours deduction for the month (reason
  required). Saved as the month correction *Hours deduction waived* (audited, undo on the employee page); the flag
  becomes an info note; the Excel shows **Waived** in *Exempt (hours)* and its formula gives 0 for the hours part
  (a WFH-over-limit deduction still applies). Calculation rules version 9.
- The *Mark reviewed* drawer says plainly that reviewing changes no figure (use Approve WFH, Waive or a correction).
- On screens narrower than 1500 px the type list sits above the table (as chips), so the detail text is never
  hidden behind the buttons; the buttons are shorter (*Reviewed*, *Waive*).

**Corrections**
- Day correction: the status lists are grouped by meaning (Present / Leave / Comp-off / Absent / Loss of pay / Off)
  and say what each code means. If the month has fixed figures (e.g. *Absent days* set by hand), a warning says they
  still override what the days add up to — also in the message after saving.

**Elsewhere**
- Overview: a banner when leave opening balances are missing (with the template link) — balances, salary deduction
  and leave liability depend on them.
- Numbers drop trailing zeros everywhere (`0.5 d`, `34.5`, footers `90.5`); "1 day" / "2 days".
- Excel download links show **Preparing…** while the workbook is built.
- Printing a page prints the table only, with a header line: firm, month (Draft / Final), printed when and by whom.
- Employees: chip **Not in <latest month>** (active in the master, missing from that month's attendance).
- Rules & calendars → Holiday calendars: chips to jump to a list (count for this year), long years start collapsed,
  each year shows the next holiday. *Save rules* stays visible at the bottom while scrolling.
- Audit log details are readable (lists joined, no `\u2014` escapes); import history shows file types by name;
  punch exceptions show "Late > 1 h · 1 h 15 m"; employee search lists employee-number matches first.
- Accessibility: every drawer close button is labelled; the faintest text colour has more contrast.

**Import templates**
- Drop-downs for known values (groups, categories, locations, weekly-off patterns, Yes/No, calendars — other values
  can still be typed), frozen header with filters, date columns formatted `dd-mm-yyyy`, Instructions as the second
  sheet. Employee master writes **No** for not-exempt (never blank). Holiday template has worked examples.
- Leave opening template is pre-filled with *As Of* = the 1st of the latest month. **Fix:** rows with no balance
  are skipped and a blank balance cell no longer clears a stored balance (previously a blank row overwrote it).

## 1.7.5 — 2026-11-19 — approve extra WFH from the review queue
Owner: "in some instances more than 2 days WFH is also allowed, on manager / partner discretion, but we won't get that
data from the records … by default it will be deducted in the system but make provision that the user can override it
… the generated Excel is produced as per the updated data in the app".
- **Review queue → WFH above monthly limit → Approve WFH:** enter the extra WFH days approved (pre-filled with the
  days over the limit) and who approved it (required). Only extra days beyond the approved ones are deducted (½ day
  each); the month is recalculated, the flag is marked reviewed with the note ("Extra WFH approved: 1 day(s) —
  Approved by partner"), the change is audit-logged. *Edit WFH approval* changes it; 0 removes it (deduction back).
- Stored as the month correction **Extra WFH approved (days)** — also on the employee page (Corrections); the day
  notes say which WFH days were approved, the flag and remark show the approval.
- **Excel** (always built from the app at the moment of download): the deduction column shows the new figure, its
  formula allows for the approved days (`CEILING(MAX(0, WFH − 2 − approved), 1) × 0.5`), the *Corrections* sheet lists
  the approval with the reason, who and when.
- Calculation rules version 8 (draft months recalculate once after the update).

## 1.7.4 — 2026-11-19 — WFH on a Saturday the office is closed: listed, not counted
Owner: "all the branches are open only on the 1st Saturday of every month, so if someone has all Saturdays working,
they are by default gonna have to work from home, so such instances shall be counted as exceptions".
- New setting *Saturdays the office is open* (Rules → Work from home; default **1** = the 1st Saturday; blank = every
  Saturday). WFH on a working Saturday the office is closed is still shown as WFH, with the note "Work from home on a
  Saturday the office is closed — not counted towards the WFH limit", and is **not counted** towards the 2-day limit
  or the ½-day deduction (`office_closed_wfh_days` per month).
- Punch exceptions: new type *WFH — office closed (Saturday)* (+ column in *By employee* and the Excel sheet);
  Reconciliation explains these days as an app rule.
- October 2026: HS016 worked from home on 10, 17, 24 and 31 Oct (closed Saturdays) — 6 WFH days → 2, the 2-day WFH
  deduction no longer applies. Over the limit now: HS179 (1 day), HS195 (½ day).
- Calculation rules version 7: draft months recalculate once after the update.

## 1.7.3 — 2026-11-19 — the intermittent CI failure found and fixed (test only)
`tests/e2e/test_update_flow.py` failed now and then (builds 17 and 21; reproduced here on a second run): after a
successful update the page reloads itself and shows "Updated to v…" once (remembered in the browser); when that
reload won the race, the test's own reload found the toast already shown and timed out. The test now clears that
memory before its reload. No app change.

## 1.7.2 — 2026-11-19 — release of the 1.7.1 fixes; CI names failing tests
The GitHub build of 1.7.1 (run 21) failed in the test step although the same commit passes in a clean checkout here
(136 passed, 13 real-data tests skipped) — as once before (build-17). The build log needs a login, so the workflow now
writes a JUnit report and, on failure, turns each failed test into a GitHub annotation (`scripts/ci_annotate.py`,
stdlib only) — readable on the run page and through the public API. No change to the app beyond 1.7.1.

## 1.7.1 — 2026-11-19 — categories: fixes from an independent review
- **A holiday list not loaded for a year is skipped** (next list in the chain, finally greytHR's own holiday marks).
  Before, e.g. January 2027 before the 2027 lists were uploaded would have made 1-Jan and 26-Jan working days —
  "absent" for everyone. The month stores which list each person used (`calendar_from`).
- **A shared calendar is never rewritten by a category's list:** when the category points at the default calendar or
  one used by another category / employee, the upload gives the category a list of its own (and says so).
- **Replacing a year only touches the span the file covers** (a list split over two files no longer wipes the first
  half) and a file of optional holidays only never removes fixed ones.
- **File names:** only "HSCO (…)" or an existing category's name counts — "(1)" / "(final)" no longer create a category.
- **Older files:** a master whose *Category* column holds groups (Partner, SS …) imports them as the group; *MUSTER*
  in a master now means "follow the category" (older templates wrote it for everyone; a warning says how many);
  a category report needs a real employee-number column (a holiday list with Location / Category columns could
  otherwise be taken for one); the category report accepts joining dates etc.; a blank additional category in
  greytHR's report clears it; employee numbers without digits are accepted again (only spaced / long text is a
  footer).
- **Employee page:** a category can be cleared (an empty field was ignored).
- **Rename / delete a category** updates targeted holidays (renamed; dropped when only for that category); the delete
  button shows only when nobody — also leavers — has the category.
- **Holiday-list notes:** a list for one location used by people elsewhere is pointed out (the CDTIPL Boutique list
  is for Delhi; 2 of its 3 people are in Mumbai); a new calendar nobody uses says so.
- *Check these dates* counts people under the list they actually use (articles under HSCO (Mum)).
- "Restricted Holiday" / "Optional Holiday" / "Public Holiday" values are understood; the payroll Category column is
  formula-safe; pasted lists understand a weekday after the date and lines without a year.
- Fixed an old importer bug: the check for status words containing "half" (e.g. "Half Day Present") never matched.

## 1.7.0 — 2026-11-19 — employee categories: holiday lists and working patterns per category
Owner: "as every employee is assigned a group, in the same manner they have a category as well … in the app throughout
(also manually editable) … the targeted holidays are targeted to those categories, the calendar and working pattern will
also change as per their category … the holiday list is going to change every where (dates and days both)".
- **Category for every employee** (+ an *additional category*, e.g. Article + HSCO (Mum)): greytHR's *Employee Category
  Report* is recognised on the Import page (96 employees, 8 categories), or set on the employee's page.
- **Per category: holiday list, weekly offs, hours per day** (Rules & calendars → *Categories*). An employee follows
  their category, then their additional category, unless their own page says otherwise ("Follow category" choices
  show what the category gives). Existing employees with the old default pattern now follow their category (one-time
  migration; categories start "not set", so figures only change once a category list / pattern applies).
- **Holiday lists per category:** greytHR's *Holiday List Importer* files (Holiday Date, Description, Is Optional,
  Location, Category) go to that category's own list; a list without a Category column takes it from the file name
  ("… HSCO (Carlyle) 2026.xlsx"); names are matched loosely ("HSCO (CDTIPL Boutique)" = "HSCO (CDTIPL) Boutique",
  "MDS" = "HSCO (MDS)"). **Lists change every year:** a full year (5+ holidays) replaces that year of the list — moved
  dates are removed — with a summary (added / changed / removed). A **Day column is checked** against the date; a wrong
  year is corrected when the weekday fits the list's year (the printed Mum list had 1-May, 15-Aug, 25-Dec as 2025).
  **Paste a list** into any calendar (e.g. copied from the New Delhi PDF). Category lists are never the default.
- **Optional holidays** ("Is Optional: Optional"): a working day unless greytHR marks the person's day H (taken).
- **Targeted holidays by category** (one-off holidays; also the *For Categories* column in a holiday list).
- **Check these dates** (Categories tab): days where a category's list and greytHR disagree for most of its people —
  October: 2-Oct for HSCO (Carlyle) (their list has Gandhi Jayanti; greytHR shows a working day for 12 of 14).
- **Everywhere:** Employees (column, filter "No category", pattern with its source ✎), employee page and month header,
  payroll sheet column, Overview *By group & category*, Insights scorecard / heatmap columns and *Categories
  compared* (+ chart), Punch exceptions, Excel (payroll column *Category*, Board Summary by category, Scorecard,
  *Categories Compared* sheet, templates with Category / Is Optional / Day / For Categories).
- **Fixes found on the way:** a report's footer row ("Total number of records : 101") was created as an employee by
  the employee-list imports — employee numbers must now look like one (≤ 20 characters, no spaces, a digit); the
  category report's "Additional Category" column would have overwritten every employee's *group* ("category" is no
  longer a group synonym); holiday lists were filed by Location (all Mumbai lists merged) and the first imported
  list became everyone's default calendar.
- Calculation rules version 6: draft months recalculate once after the update.

## 1.6.8 — 2026-11-18 — documentation
BUSINESS_RULES and USER_GUIDE: "exempt from deduction" now says it covers the hours-shortfall deduction only (WFH over
the limit is deducted unless the group is in *Groups not deducted for WFH*). No code change.

## 1.6.7 — 2026-11-18 — Punch exceptions: missing / incorrect punches, regularisations, WFH … day by day
Owner: "they also want to see incorrect / missing punches from employees, also more such data like regularisations,
wfh etc".
- **Punch exceptions** (sidebar, every role): one row per employee-day with all its issues — *Missing punch*
  (one punch / in without out), *Present, no punch*, *In/out changed (no matching punch)* (times edited in greytHR),
  *Rejected sign-in*, *Phone sign-in tagged 'Office'*, *Regularisation request* (permission punch, with its reason),
  *WFH*, *WFH over the limit* (the 3rd, 4th … WFH day), *WFH on a day off*, *Late > 1 h* (minutes), *Early exit*,
  *Short day*, *Absent / LOP*, *Worked on a day off* — with in/out, hours, every punch, reason / remark, greytHR
  status and notes. Filter chips with counts, search / filter / sort / group / columns / CSV; KPIs for punch issues,
  regularisations and WFH. *By employee* tab: counts per person and type (+ WFH deduction), with totals.
- Clicking a row opens that employee's month **on that day** (punches, places, correction form): new `?day=` link.
- Payroll Excel: new sheets *Punch Exceptions* (day by day) and *Exceptions by Employee*.
- Built from the stored day detail, so it always agrees with the payroll figures and corrections; partners on
  automatic attendance are not checked for punches. October 2026: 157 days with missing / incorrect punches
  (44 people), 162 regularisations (22 requests + 140 changed times), 53 WFH days (7 over the limit, 19 on days off).

**Full review of v1.6.2–1.6.6 (independent code review + all tests + browser crawl of every page on real data) — fixes:**
- *WFH on a day of full leave / absence is not a WFH day* (it was charged on top of the leave). Counted as
  `leaveday_wfh_days`, noted on the day, listed as *WFH on a leave / absent day* in Punch exceptions. October: HS045
  27-Oct (comp-off all day in greytHR, yet 9.8 h of WFH sign-ins) — HR should check that day.
- *Payroll Excel for older months:* the WFH term is added only to rows calculated under the WFH-deduction rules, at the
  rate actually charged — months finalised before v1.6.5 / v1.6.6 now match the app and their own ledger columns.
- *Flags:* "Hours shortfall → leave deduction" now follows the hours part only (was raised for WFH-only deductions and,
  wrongly, for hours-exempt people, hiding "short but exempt"). Overview KPI reads "people with a leave deduction".
- *Manual deduction corrections* are reported as such in the remark ("set manually; calculated 1"), the flags and the
  day notes (they kept showing the calculated amount).
- *swipes ÷ 2 method:* no "WFH day n" notes (there is no nth day in that method).
- *Heatmap:* the per-day footer is filled when the page opens (was blank until a legend click).
- *Targeted holidays:* only the holiday's month and later draft months are recalculated (was every draft month); a
  holiday in a finalised month says it doesn't change that month; people serving notice can be chosen and are
  counted; in the holiday-list import a name shared by several employees is reported instead of picking one.
- *Wording:* "Exempt from hours deduction" badge; the employee-master template explains that the exemption is for the
  hours shortfall only; the ledger's WFH line shows the rate the month was charged at.
- Calculation rules version 5: draft months recalculate once after the update.

## 1.6.6 — 2026-11-18 — Article, SS and Trainee are also deducted for WFH over the limit
Owner: "yes they shall also lose half day".
- The ½-day-per-extra-WFH-day deduction now applies to everyone except the groups in the new setting **Groups not
  deducted for WFH** (default: Partner) and a month exception. Being exempt from the *hours* deduction (employee
  master flag, or groups Partner/SS/Article/Trainee) no longer exempts from the WFH deduction.
- Settings: "Groups never deducted" is now labelled *for hours shortfall*; the WFH section has its own group list.
- Payroll Excel: the WFH part sits outside the hours exemption (`IF(U="Yes",0,hours) + WFH`); rows of people not
  deducted for WFH have no WFH term. Column U is labelled *Exempt (hours)*.
- October 2026 figures unchanged (the three people over the limit are BSS/Offshore). Draft months recalculate after
  the update (`RULES_VERSION` 4).

## 1.6.5 — 2026-11-18 — half a day of leave per WFH day over the limit
Owner: "more than 2 wfh, then we have to deduct half day for every additional wfh (more than 2 in a month)".
- **Deduction:** each WFH day above the monthly limit (2) takes **½ day of leave** (Settings → Rules → Work from home →
  *Leave deducted per WFH day over the limit*, 0 = flag only). It is added to the hours-shortfall deduction:
  `deduction_days = shortfall part + WFH part`, both stored (`shortfall_deduction_days`, `wfh_deduction_days`,
  `wfh_excess_days`) and taken from the leave balance (ledger line *WFH over the limit*, Excel ledger column
  *WFH over-limit deduction*; salary when leave is exhausted, as before).
- WFH counts only on the person's own working days (v1.6.4); the 3rd, 4th … WFH days carry a note; the flag says
  "6 WFH days (limit 2) → 2 day(s) deducted from leave"; the remark splits the deduction.
- Employees *exempt from deduction* (employee master, exempt groups — default Partner, SS, Article, Trainee — or a
  month exception) are flagged but not deducted. A month correction of *deduction days* still overrides the total.
- Payroll Excel: the deduction column formula adds `CEILING(MAX(0, WFH − 2), 1) × 0.5` (live: edit column J).
- October 2026 (real data): HS016 6 WFH → 2 days, HS179 4 → 1 day, HS195 3 → ½ day. HR's manual sheet did not
  deduct these; Reconciliation labels them *App rule*.
- Draft months are recalculated automatically after the update (`RULES_VERSION` 3); finalised months never change.

## 1.6.4 — 2026-11-18 — WFH only on working days; targeted holidays; no stale JS/CSS after updates
Owner: "employees working from home on their weekly offs, holidays, those wfh shall not be counted as wfh; these weekly
offs and holidays will differ from person to person, in the future we are going to have targeted holidays as well".
- **WFH counts only on the person's own working days** — decided by their weekly-off pattern, their holiday calendar,
  targeted holidays and any day correction. A WFH sign-in on their weekly off / holiday is off-day work (hours,
  comp-off as before), not WFH; the day carries a note; `offday_wfh_days` is stored per month. October 2026: 18 such
  days (17 people); HS020, HS045 and HS093 are no longer over the 2-day limit (3 people over instead of 6).
- **Targeted holidays** (Rules & calendars → Holiday calendars → *Targeted holidays*): a holiday for chosen
  locations and/or groups (both = people matching both) plus named employees, on top of each person's calendar; live
  "applies to N people" preview, employee picker, list with who it covers, audit-logged, recalculates draft months.
  Holiday-list import: optional columns *For Locations*, *For Groups*, *For Employees* (numbers or exact names) make a
  row a targeted holiday; the template has them. A targeted holiday on someone's weekly off stays a weekly off.
- **Reconciliation:** differences caused only by this WFH rule are labelled *App rule* with the reason; the KPI says
  how many employees differ only by an app rule (October: 74 identical + 17 rule-only of 97).
- **Rule changes recalculate draft months automatically** once after an update (`RULES_VERSION`); finalised months
  never change.
- **Fix — features not appearing after an update:** the browser kept its cached `app.css`/`app.js`, so v1.6.3's Top N
  lists showed every row and the search/selector stacked. CSS, JS and icon URLs now carry a content fingerprint
  (`?v=…`), so every update is picked up on the next page load.

## 1.6.3 — 2026-11-18 — ranked lists for everyone; heatmap Total
Owner: "the data should be for all employees … show top 5, 10 etc and also a search … employee name wise"; "in the
attendance heatmap … one more column 'total' … based on the user selected filters".
- **Overview ranked lists** (late, regularisations, missing punches, absence, WFH over limit, hours shortfall, excess
  hours, comp-offs) now hold **every** employee who qualifies (before: first 8). Each list has **Top 5 / 10 / 20 / All**
  (the choice applies to all lists and is remembered in the browser) and a **search by name or employee no.**; the rank
  shown is always the real rank; footer "10 of 57 people".
- **Insights → Attendance heatmap:** new **Total** column = days in the states highlighted in the legend (e.g. Late +
  WFH; all marked days when nothing is highlighted), sortable; a footer row totals the people currently shown (search /
  filter / group) for Pres., Late, Abs., Leave and Total, and counts each day's highlighted cells.
- Fix: hovering a heatmap row no longer blanks its coloured cells.

## 1.6.2 — 2026-11-18 — place names only from the built-in map
Owner: "remove Locations from Rules & calendars, since we are using locations from the package".
- *Settings → Rules & calendars → Locations* tab removed (named places / geofences, suggested places, *Name it*). Every
  sign-in is named from the built-in offline map of India — no setup.
- With it went the check that needed named offices: *Tagged 'Office' but not at an office* (`OFF_SITE_OFFICE`), the
  Locations page's office-check column and chip, and the office-check columns in the Excel downloads. The Locations KPI
  is now *Tagged 'Office' from a phone*. The shared-phone check (`SHARED_DEVICE`) stays.
- Places that had a name typed by an admin are renamed once from the map (`geo.NAMES_VERSION` 3; draft months
  recalculated in the background). The old `geofences` table is left untouched in the database (nothing deleted).

## 1.6.1 — 2026-11-18 — data safety; exports show every correction and review
**Exports** are always built at the moment of download from the app's current figures — every day correction, month
adjustment, setting and review is already in them. New: the *Flags* sheet shows each exception's review status,
reviewer, date and note; a new *Corrections* sheet lists every day and month correction with what changed, the reason,
who and when; the payroll sheet's footer says so.

**Data safety**
- *Automatic daily backup by the app itself* (works on every install, with or without the server's scheduled task):
  an online SQLite snapshot checked with `PRAGMA integrity_check` before it is kept; the last 14 days in full, then one
  per month for 2 years. The encryption key (`secret.key`, needed for two-step sign-in) is copied next to the backups.
- *Settings → Security & system → Backups & data safety:* start-up health check, last backup, key copy, the backups,
  **Back up now**. Restore: stop the app, `python -m app.cli restore <file>` (the current database is kept as a
  safety copy; a backup that fails its check is refused).
- *Updates:* the supervisor now refuses any package that would replace `data`, `.venv`, `.git`, `certs`, `backups`
  or the data folder itself (in addition to the download check); the pre-update backup is verified and the update is
  cancelled — code untouched — if it isn't sound; pre-update backups are pruned to the last 10.
- SQLite commits are fully synchronous (`synchronous=FULL`): a confirmed save survives a power cut.

## 1.6.0 — 2026-11-18 — punch locations in Excel; HTML board pack removed
- **Punches & locations workbook** (`/exports/<month>/locations.xlsx`): every punch of the month — office biometric
  and mobile — with time, punch type, tag, **place name**, the employee's remark, office check, permission reason,
  phone, status and coordinates; plus *Places* and *By person* sheets. Available for any month with punches,
  including one not calculated yet. Download it from Locations (*Download Excel*), Downloads, or a not-calculated
  month's page.
- Payroll Excel → *Daily Detail* gains **Places** (each punch: time · place · remark) next to the tag.
- Employee page: **Sign-ins & places** button opens Locations filtered to that person.
- **HTML board pack removed** on the owner's decision — the app (Overview, Insights, Locations, drill-downs) does the
  job better. Its route, generator, template, buttons and tests are gone; old audit entries still read "Downloaded
  board pack (old)".
- Why punch places were not visible: the October swipe export has no GPS coordinates; the November
  *Employee Swipe Details* export (with coordinates) has to be imported for place names to appear.

## 1.5.2 — 2026-11-18 — Bradford factor removed
- Removed on the owner's request: the Bradford factor (S² × D), its bands chart, warning counts and every column/label
  in Insights, the employee Scorecard, Excel and the board pack.
- Absence is now shown as plain numbers: unplanned absence (absent + loss of pay) this month, and days + separate
  spells over the last 12 months. Insights → *Absence* lists the most unplanned absence this month and over 12 months.
- Attention score: its absence part (25 points) is now unplanned-absence days in the last 12 months (10 days or more =
  the full 25); the other parts are unchanged. Groups compared show absence days (12 m) instead of average Bradford.

## 1.5.1 — 2026-11-18 — firm logo
- **Settings → Security & system → Logo:** an admin uploads the firm's logo (SVG best; PNG/JPEG/WebP; ≤ 1 MB). It is
  stored with the data in `DATA_DIR/branding` — never in the code or the public repository — and shown in the
  sidebar, on the sign-in pages (desktop and phone), as the browser-tab icon (the logo's left mark, e.g. the red
  "HSCo" block) and in the board pack header (on a white backing; embedded so the file still works offline).
- SVGs are sanitised: shapes, gradients, clip paths and text only; scripts, event handlers, external links,
  embedded documents/images and entity declarations are refused. The logo is served with a sandboxing CSP.
- The firm's logo is pre-installed on this PC's installation and the local development data.
- Not in Excel: embedding images in .xlsx needs an extra library (Pillow) that isn't part of the install.

## 1.5.0 — 2026-11-18 — sign-ins with coordinates: place names, location checks, Locations page
Built and tested on greytHR's newer *Employee Swipe Details* export (November 2026, with GPS).
- **Importer:** the new 17-column swipe export is recognised (it was being mistaken for a muster: its approval
  "Status" passed as an attendance status and the "Swipe Date" timestamp as a date — both value checks are now
  strict). Longitude/Latitude are read in any column order (and swapped back if labelled the wrong way round). New
  per-punch fields are kept: the employee's remark, permission reason, approval status, phone model and phone id
  ("Signed In/Out By" no longer passes as an IN/OUT direction).
- **Database:** new swipe columns; existing databases are upgraded in place at start-up (`add_missing_columns` —
  additive only).
- **Place names** for every sign-in with GPS (offline gazetteer; named places first) — 445 points named in 0.3 s.
- **Location checks** (new flags): *Tagged 'Office' but not at an office* (once offices are named; 150 m GPS slack)
  and *Phone used to sign in for several people* (buddy-punching risk). Rejected sign-ins are ignored by the
  calculation and noted on the day.
- **Suggested places** (Settings → Locations): busiest unnamed spots with their usual tag, the remarks typed there,
  the people, a suggested name and type and a fitting radius — "Name it" fills the form. Offices are suggested as
  "Mumbai office" / "Delhi office" from the Locations list; a spot only one person tags "Office" is not offered as
  an office ("probably not an office"); one-off remarks and times typed as remarks don't become names.
- **Locations page** (new): every mobile sign-in with place, remark, phone, office check; chips (Office-but-
  elsewhere, client, WFH, on duty, permission, shared phone, no GPS, rejected); by-place and by-person tables; works
  for a month that has punches but no muster yet.
- **Month not calculated yet:** overview, payroll sheet, Insights and reconciliation for such a month (e.g. the
  month in progress) now say so and show what is imported and the sign-ins so far — instead of zeros, misleading
  "vs last month" changes and download buttons that led nowhere.
- Employee day view, board pack and Excel ("Sign-ins & Places", "Places" sheets; board-pack *Locations* tab) show
  places with remarks. Review queue loads faster (no day detail).

## 1.4.1 — 2026-11-17 — full re-test: speed, safety and the review's fixes
Tested end to end again: every page crawled as admin and as viewer (no errors), a fresh install importing the real
exports in a browser (2.8 s for all five), awkward uploads, a synthetic firm 3× the size with 12 months of history,
two people importing at once while others browse, and an independent code review.

**Faster** (300 people × 12 months; before → after): overview 2.3 s → 0.5 s · Insights first load 4.9 s → 0.7 s ·
an employee page after a correction 4.7 s → 0.14 s · Settings 1.4 s → 0.5 s · board pack 4.7 s → 0.8 s ·
Excel 8.2 s → 4.3 s · muster + swipes upload 15–22 s → 5.5–8.4 s · one month's calculation 1.2 s → 0.75 s.
How: results store a compact per-person month summary (`attendance_stats`), so history, trends and Bradford factors
never decode the day-by-day detail; lists that don't need day detail skip it; rows are inserted in bulk; employees
and the place-name cache are loaded once per import; place lookups search a 3 km window first; an upload is
recalculated once for all its files; the unnamed-places list is grouped in the database; Excel borders are registered
once; shift texts are parsed once.

**Safer**
- A small file no longer wipes a month: a file covering fewer than 80 % of the people already in that month (e.g. a
  correction for one person) replaces only those people's days/punches and says so; a full export still replaces
  the month.
- Files dated more than a month ahead of today are refused ("often a wrong year") instead of creating a phantom
  month that would become everyone's default view. Admins can now **delete a mistaken draft month** (Payroll leave
  sheet → bin icon; reason and typing the month name required; audit-logged; later months recalculated).
- Importing an earlier month recalculates every later draft month (leave and comp-off balances carry forward).
- Recalculations commit month by month, and the start-up maintenance (gazetteer index, one-time place renaming,
  summary backfill) runs in a background thread with short transactions — measured: 50k swipes renamed and 12
  months recalculated in the background with no failed request and a 0.3 s median response meanwhile.
- The file type is decided by content, not extension (a .xls saved as .xlsx now opens).

**Fixed (independent review)**
- Attention score treated 0 % on-time / 0 % hours as 100 % — the worst cases scored best. Same falsy-zero colouring in
  Insights and the board pack.
- Remembered layouts store column index + header and are checked on replay (moved/duplicate/blank headers or a
  missing required column → recognised afresh or sent for review, never imported blindly).
- Several unsure sheets in one workbook can each be mapped (the upload is kept until all are done).
- The month of a day-level file comes from its dates; a title month is used only without dates (and a disagreeing
  title is reported). "from … to …" wins over a bare month name; print/run dates are ignored.
- Numeric employee codes (e.g. 50123) are no longer taken for Excel dates.
- Recognition caches are per thread (two simultaneous uploads can't interfere).
- Joining/leaving lists apply the Date of Leaving (marks the person inactive).
- Status words: "Half Day Leave/CL" → leave + present (not absent), "Half Day Absent" → A/P, "P(HD)", "PL (HD)";
  short codes are kept exactly as written so admins can map their own (PH, AB…).
- `[h]:mm` durations over 24 h and "9.5"-style decimals are read correctly; warnings quote real spreadsheet rows.
- The place-name upgrade no longer holds the write lock, skips finalised months and never calls the online service.
- Board-pack data is fully escaped inside its script; imported text can't become an Excel formula.
- Small: "1 rows", missing values show "—", bare `/periods` opens the latest month, negative tenure hidden.

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
