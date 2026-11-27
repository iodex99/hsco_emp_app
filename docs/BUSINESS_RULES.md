# Business Rules — calculation specification

Source: `reference/HR Policy_Leave and Attendance.pdf` (Work Schedules, Festival Holidays, Leave Policy — all "Revised")
+ the manual MIS working (see DATA_ANALYSIS.md). Every number below is an **admin setting** (Settings → Policy)
with the default shown. Engine code: `app/engine/compute.py` (pure functions, unit‑tested).

## 1. Policy facts (from the PDF)
- Working days Mon–Fri; Sat & Sun off **except the 1st Saturday of each month** (working).
- Requirement is **monthly**: total hours = **8.5 h × working days**; excess on some days offsets shortfall on others
  within the same month. Minimum 7 h/day is *encouraged* only.
- Office timing 10:00–19:00 (shift in greytHR is 10:00–18:30).
- Shortfall is adjusted against leave **in multiples of 4 hours = half day** (3 h short ⇒ ½ day).
- "Late coming" concept discontinued (so late arrival is a *board metric*, never a deduction).
- Comp‑off: working on a non‑working day / declared holiday with **≥ 5 hours** earns a comp‑off.
- Late sitting discouraged (except seasonal deadlines).
- 14 festival holidays per calendar year (incl. Janmashtami).
- Leave: **15 paid days per financial year (Apr–Mar)**, pro‑rata for joiners/leavers; unapproved leave = unauthorised
  absence, adjusted against leave balance, then salary. No carry‑forward; unavailed leave **encashed** after 31‑Mar at
  (monthly gross ÷ 26) × days; leavers forfeit unavailed leave. **Leave balances are kept in greytHR, not in this app
  (owner, v1.9):** the app gives the leave deduction to apply; it keeps no opening, ledger or closing balance.
- WFH: max **2 days per month** (owner instruction) — excess is flagged.

## 2. Day classification (per employee, per date within employment)
1. **Day type**
   - Weekly‑off pattern (employee setting, default `MUSTER`):
     `MUSTER` (use greytHR OFF), `STD_FIRST_SAT` (Sun off, Sat off except 1st), `ALL_SAT_OFF`, `SUN_ONLY`,
     `CUSTOM` (choose the weekly-off weekdays, e.g. Wednesday only for HS068, plus which Saturdays of the month are
     working when Saturday is an off day, e.g. `1,3`).
   - **Category (v1.7, §2b) decides the defaults**: weekly-off pattern and hours per day come from the employee's own
     settings, else their category, else their additional category, else greytHR / policy. **Holidays go by category
     only** (owner, v1.9) — there is no per-employee holiday calendar.
   - Holiday: the date is in the person's **category list** (category's → additional category's → default list) →
     HOLIDAY; no list loaded for the year → greytHR `H`. A holiday on a weekly‑off day stays a weekly off.
   - **Optional holiday** (list flag *Is Optional*): a HOLIDAY only when greytHR marks the person's day `H` (they took
     it, named "… (optional)"); otherwise a working day with the note "Optional holiday (…) — not taken".
   - **A holiday for some categories only** (e.g. a Delhi festival; owner, v1.9 — replaces the v1.6.4 "targeted
     holidays" by location / group / person): it is a date in each of those categories' lists — Rules & calendars →
     *Holiday for several categories*, or a holiday-file row whose Category cell names several categories.
   - Result: `WORKDAY`, `WEEKLY_OFF` or `HOLIDAY`.
2. **Status** per session half (0.5 day each) from greytHR code via a mapping table (Settings → Status codes):
   `P→present`, `A→absent` (= unapplied leave), `PL/CL/SL/EL/ML→leave`, `LOP/LWP→leave` (v1.13), `COF→compoff`, `OFF/H→off`, `WFH/OD→present`.
   Unknown codes: present if work minutes > 0, else absent (flagged `UNKNOWN_STATUS`).
   If a pattern/calendar override makes a muster `OFF`/`H` day a `WORKDAY`: present if worked, else absent (flag).
3. **Day override** (manual, with reason) can set day type, either session status and/or work minutes.

## 3. Monthly figures (MIS columns reproduced)
| Key | MIS col | Rule |
|---|---|---|
| `total_week_days` | E | days with type WORKDAY or HOLIDAY |
| `holidays` | F | days with type HOLIDAY |
| `leave_days` | G | leave (incl. LOP / LWP) + compoff halves on WORKDAYs (split: `pl_days`, `cof_days`) |
| `absent_days` | H | absent (`A`) halves on WORKDAYs = **unapplied leave (absence)** (`unauth_absent_days`). v1.13: no LOP bucket / setting any more |
| `days_present` | I = M | E − F − G − H |
| `wfh_days` | J | see §6 |
| `total_hours` | K/O | Σ work minutes (all days) ÷ 60 |
| `extra_days` | L | WEEKLY_OFF/HOLIDAY days with **any** attendance evidence (work > 0, an in-time or a swipe) — matches HR's pivot |
| `extra_hours` | P | Σ work on those days ÷ 60 (**excluded** — owner: holiday work is not counted) |
| `required_hours` | N | I × 8.5 |
| `actual_hours` | Q | K − P |
| `balance_hours` | R | Q − N (negative = shortfall) |
| `deduction_days` | — | `0` if exempt or shortfall ≤ grace (0 h); else **ceil(shortfall ÷ 4) × 0.5** |

Month overrides (manual, with reason) may replace E, F, G, H, J, K, L, P, N, `deduction_days`, `exempt`, `remark`;
dependent figures are recomputed in the order E,F,G,H → I → N → Q → R → deduction.

**Exempt from the hours-shortfall deduction** (default): groups `Partner`, `SS`, `Article`, `Trainee`; employees flagged
*exempt* in master (e.g. HS014, HS055 — "Partner" remark); per‑month exception override. Auto remark explains every
result. This exemption does not cover WFH over the limit (§6 — only `wfh_deduction_exempt_groups`, default Partner,
and a month exception).

## 4. Leave balances — not calculated (owner, v1.9)
Leave and comp-off balances are kept in greytHR. The app does not take opening balances, credit leave, carry
balances forward, compute closing balances, salary-deduction days for exhausted leave or "payroll LOP". What it gives
per month: **leave deduction days** (hours shortfall + WFH over the limit, §3 and §6 — what to take from leave), PL /
COF / LOP / unauthorised-absence days as marked in greytHR. *Annual leave used* (Insights) compares paid leave taken in
the financial year with `annual_leave_days`. (Until v1.8 a ledger did this — `leave_openings` is kept in the database,
unused.)

## 5. Comp‑off
Earned = days of type WEEKLY_OFF/HOLIDAY with work ≥ **5 h** (1 day each). Taken = COF halves (greytHR). Shown per
month as earned and taken (Excel *Comp-off Ledger*); no balance.

## 6. WFH
Swipes whose Location Type ∈ {`Work from Home`, `WFH`}, **on the employee's own WORKDAY only, when present at least
half the day** (v1.6.7: a WFH sign-in on a day of full leave / absence is not WFH — `leaveday_wfh_days`, day note) (v1.6.4, owner): the
day type after their weekly-off pattern, their category's holiday list and day corrections. A WFH sign-in on
their weekly off / holiday is *off-day work* (extra day, hours, comp-off ≥ 5 h as usual), not WFH; the day gets the
note "Work from home on a weekly off/holiday — not counted as WFH" and the month stores `offday_wfh_days`.
Method `distinct_days` (default): each working date with ≥1 WFH swipe counts 1 (partial‑WFH days reported
separately). Method `swipes_half` reproduces HR's legacy count ÷ 2 (working-day swipes only).
**Closed Saturdays (v1.7.4, owner):** the branches open only on the Saturdays in `office_open_saturdays` (default
1st). WFH on a working Saturday that is not one of them is forced: still a WFH day (`wfh_office_closed`, note), but
not counted in `wfh_days` (nor in `swipes_half`), so not towards the limit or the deduction; the month stores
`office_closed_wfh_days`. `()` = every Saturday open.
**Approved extra WFH (v1.7.5, owner):** month correction `wfh_approved_days` (from the review queue or the employee
page, with a reason): `wfh_deduction_days = max(0, wfh_excess_days − wfh_approved_days) × wfh_excess_deduction_days`.
By default nothing is approved (the system deducts).
**Waived hours deduction (v1.8.0):** month correction `shortfall_waived` = true (review queue *Waive* or the
employee page, with a reason) → `shortfall_deduction_days = 0` for that month; the WFH-over-limit part still
applies; flag `SHORTFALL_EXEMPT` "… deduction waived this month" instead of `SHORTFALL_DEDUCTION`. Excel column U
(*Exempt (hours)*) = `Waived`; the deduction formula takes the hours part only when U = "No". RULES_VERSION 9.
Flag `WFH_OVER_LIMIT` when > **2**. HR's manual sheet counted off-day WFH too (DATA_ANALYSIS §2).
**Deduction (v1.6.5, owner):** `wfh_excess_days = ceil(max(0, wfh_days − wfh_limit_days))`;
`wfh_deduction_days = wfh_excess_days × wfh_excess_deduction_days` (default **0.5**; 0 = flag only), 0 only when the
employee's group is in `wfh_deduction_exempt_groups` (default Partner) or a month exception is granted. The hours
exemption (employee master flag, `exempt_groups` Partner/SS/Article/Trainee) does **not** exempt from the WFH
deduction (owner, v1.6.6: Article, SS and Trainee also lose ½ day).
`deduction_days = shortfall_deduction_days + wfh_deduction_days` (a month correction of deduction days overrides the
total) — the leave to deduct (in greytHR); Payroll Input shows the two parts separately. The WFH days beyond the
limit (in date order) get a note.

## 7. Behaviour metrics (never affect pay)
Computed on WORKDAYs with a present first session; groups in `metrics_exclude_groups` (default `Partner`) skipped.
- **Late arrival**: first‑in > shift start + **15 min**; *very late* > 60 min. Shift start/end parsed from shift name
  (`10:am-18:30pm` → 10:00–18:30, `9 am to 6pm (Carlyle)` → 09:00–18:00), editable in Settings → Shifts.
  Persistent late (flag): **> 60 min late on ≥ 10 days** (`persistent_late_basis = very_late`). Chosen after the October
  data showed median arrival 10:28 — a 15-minute rule flagged 66 of 94 people (noise); the 1-hour rule flags 23.
  Basis `late` (> grace) is available in Settings.
- **Early exit**: last‑out < shift end − 15 min on full present days.
- **Short day**: work < **7 h** on full present days. **Late sitting**: out after **21:00**.
- ~~Missing punch~~ / ~~present without any punch~~: removed in v1.13 (owner) — not counted, flagged or shown.
- **Regularisation**: day with a `Permission` swipe or with muster in/out times that match no swipe.
  Persistent ≥ **3** per month.
- **Mobile sign‑in at "Office"**: possible biometric bypass (count).
- **Sandwich absence**: leave/absence on a WORKDAY adjacent to a weekly off/holiday.
- **Present without punch**: a present working day with in/out times in greytHR but no swipe at all, for someone who
  does punch on other days (Oct: HS158 18 days, HS207 15 days). HR disallowed such a day for HS197.
- **Auto attendance**: present all month with no punches (partners) — only evaluated when a swipe file was imported.
- **No attendance** all month; unapplied leave (absence) ≥ 1; leave balance negative.
- Data quality: group not set, employee in muster but not master, DOJ missing, unknown status codes.

## 8. People movement
New joinee = DOJ within the month. Exit = DOL within month, or (suggested) employee in the previous month's muster
but absent from the latest DOJ (active list). Work anniversaries listed. Headcount by group/location.

## 9. Location names for coordinates
Swipe importer captures coordinates from `Latitude/Longitude` (any similar header) or a `lat, long` pair in any text
cell. Naming order (v1.6.2 — admin-named places removed): (1) **offline gazetteer** bundled in `app/data/in_places.csv.gz` (GeoNames, CC-BY 4.0: 566,055 Indian towns, villages,
neighbourhoods, localities, stations and landmarks with district and state), indexed on first start into
`DATA_DIR/geo/places-*.sqlite` (≈ 0.05° grid). The nearest place within `geocode_max_km` (15 km) is used; a named
neighbourhood is preferred when it is within max(1.5 km, 1.6 × the nearest place's distance); a landmark within 600 m is
appended. Label: `Place, District, State`, prefixed "near … (x km)" when ≥ 1 km away, suffixed "· by <landmark>".
No user input and nothing leaves the server. (2) optional **online OSM Nominatim** lookup — off by default because it
sends coordinates outside the server. Results are cached (`geo_cache`). When the gazetteer version changes
(`geo.NAMES_VERSION`, now 3), cached offline names are dropped once, stored swipes renamed and draft months
recalculated.

## 9a. Checks on mobile sign-ins (v1.5)
- **Rejected** sign-ins (approval Rejected/Declined/Cancelled/Denied) never count as attendance; the day notes them.
- **SHARED_DEVICE (high):** the same phone id used to sign in for two or more people in the month (each is flagged
  with the others' names).
- *Removed in v1.6.2 with the named places:* OFF_SITE_OFFICE ("tagged Office but not at an office") and suggested
  places. Biometric (Astra) punches are never checked.
- A month with punches but no muster is **not calculated**: its pages say so; Locations works from the punches.

## 10. Period lifecycle
`DRAFT` (imports, auto‑compute) → `FINALISED` (locked: no imports/overrides; exports stamped). Months are
independent (nothing carries forward), so changing one month never recalculates another.
Re‑open = admin only, reason mandatory, audit‑logged.
**Delete a month** (v1.4.1) = admin only, DRAFT only, reason + typing the month name, audit‑logged; removes the month's
imports, results, corrections and reviews; later draft months are recalculated.

## 11. Import rules (v1.4.1; v1.7: employee numbers must be ≤ 20 characters, without spaces, with a digit — report
footers such as "Total number of records : 101" are skipped)
- **Month of a file:** day-level files (muster, swipes) belong to the month most of their dates fall in; a title month
  is only used when there are no dates (and a disagreement is reported). Files more than one month ahead of today
  are refused (usually a wrong year).
- **Full vs partial files:** a muster/swipe file containing at least 80 % of the people already in that month is a
  full export and replaces the month; a smaller file replaces only the people it contains (others kept, message
  shown). Summary/MIS files always replace the month's copy.
- **Recalculation:** after an upload, the earliest affected draft month and every later draft month are recalculated
  once, oldest first (balances carry forward); finalised months are never touched.
- **Status words** are translated (Present, Absent, Week Off, Holiday, Leave, Half Day…; "Half Day Leave" = leave +
  present, "Half Day" = present + absent); **codes** (P, A, PL, WO, PH, AB…) are kept exactly as in the file and mapped
  by the status map in Settings (unknown codes are flagged).

## 12. Calculation-rule changes (v1.6.4)
`engine.compute.RULES_VERSION` is bumped whenever a rule changes; on the next start the app recalculates every
calculated **draft** month once (`maintenance.recalc_on_rules_change`). Finalised months keep their figures.

## 2b. Employee categories (v1.7)
greytHR "Category" (HSCO (Mum), HSCO (Carlyle), HSCO (CDTIPL), HSCO (CDTIPL) Boutique, HSCO (Delhi), HSCO (MDS),
HSCO (CBV), Article). `Employee.category` + `additional_category` (e.g. Article + HSCO (Mum)). `Category`: holiday list
(calendar), weekly-off pattern ('' = not set), off weekdays / working Saturdays (CUSTOM), hours per day (blank = not set).
**What applies** (first match): weekly offs — employee's own pattern (anything but *Follow category*) → category →
additional category → greytHR muster; hours/day — employee → category → additional → policy; holidays — category's
list → additional's → default list → greytHR `H` (never per employee, v1.9).
**Names match loosely** (`categories.key`: letters/digits only, lower case, firm prefix "HSCO" dropped).
**Imports:** *Employee Category Report* (Employee Number, Name, Curr.Location, Curr.Category, Additional Category —
cached values of VLOOKUP cells are used) sets category / additional category / location; categories are created as
needed; a category first created by a holiday list takes the report's spelling. **Holiday lists (v1.9, owner):** columns **Date, Day, Holiday, Category, Optional** — nothing else is needed
(greytHR's Holiday List Importer files work; their Location column is ignored). Each row goes to its Category's own
calendar (created and linked on first use, never the default); several categories in the cell (comma-separated) → the
holiday goes into each list; no Category column → the category in the file name ("… HSCO (Carlyle) 2026.xlsx"); a
blank category → the **default list** ("Standard", created as the default when there is none) for people whose
category has no list. All rows of one file are applied together; for each list and year with ≥ 5 holidays the file
**replaces** that year *within the span of dates it covers* (moved dates removed; a file of optional holidays only
removes only optional ones; fewer than 5 = added only). A category pointing at a shared calendar (the default or
another category's) gets a list of its own instead of rewriting it. **A list with no holidays in the year being
calculated is skipped** (next in the chain, finally greytHR's marks); the month stores the list used
(`calendar_from`). A Day column is checked; a date whose year differs from the list's main year is corrected when the
same day/month in that year falls on the stated weekday, otherwise reported. Pasted lists follow the same rules.
*Holiday for several categories* (Rules & calendars) adds one date to each ticked category's list and warns when a
list had no holiday yet in that year (it then counts as loaded for the year).
**Migration (v1.9):** targeted holidays for categories were moved into those categories' lists; ones for locations /
groups / named people are listed in the audit log (`migration.holidays_by_category`) and no longer apply; employees'
own holiday calendars were cleared.
**Check these dates:** in the latest calculated draft month, a (category, date) where ≥ 50 % (and ≥ 2) of the
category's people have "Holiday calendar differs from greytHR".

## Restricted holiday — status RH (v1.12, owner)
A muster day with **RH** in either session is the person's earned holiday: day type HOLIDAY ("Restricted holiday
(RH)"), counted in Holidays, never an absence, no hours required. Work on that day is off-day work (extra day / hours
excluded as for any holiday) but **no comp-off** is earned (note "Worked on a restricted holiday (RH) — no comp-off").
Engine `RH_CODES`; independent of the editable status-code list (no "unknown status" flag, no "calendar differs").

## What needs a review (v1.12, owner)
Only **WFH above monthly limit** and **Group not set** (`workflow.REVIEW_CODES`). Every other flag is reviewed
automatically: it is still raised and shown (employee page, Overview, Excel *Flags* = "Reviewed automatically") but
never waits in the review queue and is not counted as open.

## v1.13 (owner, 23-Nov-2026)
- **No LOP.** greytHR's LOP / LWP days count as leave taken (MIS column G, as HR's manual sheet did). There is no LOP
  column, KPI, flag, chip or setting any more; a status list saved earlier with `=lop` reads as `leave`. Figures that
  were already shown under G are unchanged (the default "LOP under Leaves" setting was in use).
- **Unapplied leave (absence)** is the name for absence without leave applied (greytHR `A`) everywhere — pages, flags,
  Excel (payroll sheet column Y, Board Summary, Flags), Insights (formerly "unplanned absence").
- **Ignored groups** (`policy.ignored_groups`, default `OTHERS`, Settings → Groups ignored by the app, compared without
  regard to case): employees in these groups are not calculated and appear in no page, list, count, search, chart, PDF
  or Excel (`services/scope.py`). Uploads may contain them — their rows are stored but never used; changing the
  employee's group (or the setting) brings them back at the next calculation. Employees page: "N ignored · Show them".
- **Missing punches** and **present without any punch** are removed everywhere (counts, flags, chips, lists, calendar
  marks, Excel columns, attention score). Calculation rules version 12 (draft months recalculated once).

## v1.14 (owner, 26-Nov-2026)
**Holiday on a weekly off.** A holiday (category list, optional holiday, greytHR H or a restricted holiday) on a day
that is the person's weekly off counts as a **weekly off**, not a holiday: it is not in Total Week Days (E) or Holidays
(F), needs no hours, and work done that day is off-day work as on any weekly off. Whose weekly off: the person's own
pattern if they have one; following greytHR (pattern MUSTER, greytHR shows H on such a day), the weekday's marks for
that person in the month (most of the same weekday OFF → weekly off; Saturdays the office is open and closed are
separate), else the 3 months before, else the firm's — Sundays and the Saturdays the office is closed
(`office_open_saturdays`). Engine: `greythr_weekly_offs`, `firm_weekly_off`, `Employee.off_slots_hint`.

**Hours per day** = the person's **minimum-hours policy** (Rules & calendars → Minimum hours; `services/hours_policies.py`)
→ their own hours (employee page) → their category's → the firm's (`hours_per_day`, 8.5). Required hours = days present
× that value; the shortfall, deduction and every report follow.

## Locking a month (v1.15 wording; was "finalise / re-open")
HR locks a month once the MIS is done (*Lock month* on the Overview or the payroll sheet). A locked month: no imports,
corrections, recalculation or upload deletion; its figures never change (rule changes skip it). An admin can unlock
it with a reason (audit-logged). Exports of a locked month say "(locked)" in the titles and "- Locked" in the file
name; an open month's exports carry no status word (no "DRAFT"). The database still stores `DRAFT` / `FINALISED`.

## Thresholds for the people counts (v1.15)
- Late sitting: more than **10** late-sitting days in the month. A late-sitting day (v1.16, owner) = a present working
  day with more than the person's required hours per day + **1 h** (`late_sitting_extra_min`, default 60) — the
  required hours come from their minimum-hours policy, own setting, category or the firm (8.5 h). Rules version 14.
- Hours shortfall / excess: monthly balance below **−20 h** / above **+20 h** (before any exemption).
- Present all days: days present ≥ working days (week days − holidays), working days > 0.

## Date of leaving (v1.16)
The greytHR *Date Of Leaving* report is the whole list since the start. Only employees already in the app are used:
their date of leaving is set (or corrected) and they are marked left when the date has passed; everyone else is left
out (counted in the import note, never created). The calculation stops at the leaving date (days after it are not
working days for the person), so absence after leaving is not unapplied leave.

## Permission Report (v1.16)
Rows: employee, date, type (WFH / On Duty / Regularization), from / to time, reason, workflow status. Counted:
Approved, Applied (pending); not counted: Withdraw, Rejected. Used for the Overview's Permission → WFH / On Duty and
the Excel *Permissions* sheet; it does not change the leave calculation (WFH days for the limit still come from the
muster and sign-ins).

## Month-end checklist (v1.16)
1 Import (muster + swipes) · 2 Review exceptions (all reviewed) · 3 Corrections — done when HR confirms they are made
in greytHR · 4 Download & share — done when the Excel or PDF is downloaded from the checklist itself (downloads
elsewhere don't count) · 5 Lock month. Stored on the month (`Period.checklist`), audit-logged (`period.checklist`).
