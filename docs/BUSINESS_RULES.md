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
  (monthly gross ÷ 26) × days; leavers forfeit unavailed leave.
- WFH: max **2 days per month** (owner instruction) — excess is flagged.

## 2. Day classification (per employee, per date within employment)
1. **Day type**
   - Weekly‑off pattern (employee setting, default `MUSTER`):
     `MUSTER` (use greytHR OFF), `STD_FIRST_SAT` (Sun off, Sat off except 1st), `ALL_SAT_OFF`, `SUN_ONLY`,
     `CUSTOM` (choose the weekly-off weekdays, e.g. Wednesday only for HS068, plus which Saturdays of the month are
     working when Saturday is an off day, e.g. `1,3`).
   - Holiday: if the employee has a **holiday calendar** assigned (or a default calendar exists) → date in calendar;
     otherwise greytHR `H`. A holiday on a weekly‑off day stays a weekly off.
   - **Targeted holiday** (v1.6.4, `targeted_holidays`, `services/holidays.py`): a holiday for some people only, on top
     of their calendar. Applies to every employee listed by number, plus everyone whose location is in *locations*
     (blank = any) **and** whose group is in *groups* (blank = any); at least one of the three must be set. Matching
     ignores case/spaces. Also stays a weekly off when it falls on one. Bulk: holiday list with *For Locations /
     For Groups / For Employees* columns.
   - Result: `WORKDAY`, `WEEKLY_OFF` or `HOLIDAY`.
2. **Status** per session half (0.5 day each) from greytHR code via a mapping table (Settings → Status codes):
   `P→present`, `A→absent`, `PL/CL/SL/EL/ML→leave`, `COF→compoff`, `LOP→lop`, `OFF/H→off`, `WFH/OD→present`.
   Unknown codes: present if work minutes > 0, else absent (flagged `UNKNOWN_STATUS`).
   If a pattern/calendar override makes a muster `OFF`/`H` day a `WORKDAY`: present if worked, else absent (flag).
3. **Day override** (manual, with reason) can set day type, either session status and/or work minutes.

## 3. Monthly figures (MIS columns reproduced)
| Key | MIS col | Rule |
|---|---|---|
| `total_week_days` | E | days with type WORKDAY or HOLIDAY |
| `holidays` | F | days with type HOLIDAY |
| `leave_days` | G | leave + compoff (+ LOP, default) halves on WORKDAYs (split: `pl_days`, `cof_days`, `lop_days`) |
| `absent_days` | H | absent halves on WORKDAYs (`unauth_absent_days`); `LOP` bucket setting `leave` (default — HR did this 3 of 4 times) or `absent` |
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

**Exempt from deduction** (default): groups `Partner`, `SS`, `Article`, `Trainee`; employees flagged *exempt* in master
(e.g. HS014, HS055 — "Partner" remark); per‑month exception override. Auto remark explains every result.

## 4. Leave ledger (financial year Apr–Mar)
- Credit mode `annual` (default): 15 days credited on 1‑Apr; joiners pro‑rata = 15 × (months remaining incl. joining
  month) ÷ 12 rounded to nearest 0.5. Alternative `monthly`: 1.25/month. *(Assumption — confirm with HR.)*
- Opening balance: manual entry / Excel import "as at" a month (go‑live), else carried from previous computed month,
  else (April) the new entitlement. Missing opening ⇒ flag `NO_OPENING_BALANCE` and balance shown as "net".
- Monthly debit = PL days + shortfall deduction days + unauthorised absent days (`absent_debits_leave`, default **on**,
  per policy). COF days debit the comp‑off ledger instead.
- Closing < 0 ⇒ excess becomes **salary‑deduction days**, closing set to 0.
- **Payroll LOP days** = LOP status days + excess (+ comp‑off overdraft if `cof_overdraft_to_leave` off).
- Year end (March): unavailed balance reported for **encashment**; leavers forfeit.

## 5. Comp‑off ledger
Earned = days of type WEEKLY_OFF/HOLIDAY with work ≥ **5 h** (1 day each). Availed = COF halves.
Opening manual. Availed > balance ⇒ flag `COMPOFF_OVERDRAWN`; overdraft debited from leave (default) — this reproduces
HR's manual reclassification (e.g. HS134).

## 6. WFH
Swipes whose Location Type ∈ {`Work from Home`, `WFH`}, **on the employee's own WORKDAY only** (v1.6.4, owner): the
day type after their weekly-off pattern, holiday calendar, targeted holidays and day corrections. A WFH sign-in on
their weekly off / holiday is *off-day work* (extra day, hours, comp-off ≥ 5 h as usual), not WFH; the day gets the
note "Work from home on a weekly off/holiday — not counted as WFH" and the month stores `offday_wfh_days`.
Method `distinct_days` (default): each working date with ≥1 WFH swipe counts 1 (partial‑WFH days reported
separately). Method `swipes_half` reproduces HR's legacy count ÷ 2 (working-day swipes only).
Flag `WFH_OVER_LIMIT` when > **2**. HR's manual sheet counted off-day WFH too (DATA_ANALYSIS §2).

## 7. Behaviour metrics (never affect pay)
Computed on WORKDAYs with a present first session; groups in `metrics_exclude_groups` (default `Partner`) skipped.
- **Late arrival**: first‑in > shift start + **15 min**; *very late* > 60 min. Shift start/end parsed from shift name
  (`10:am-18:30pm` → 10:00–18:30, `9 am to 6pm (Carlyle)` → 09:00–18:00), editable in Settings → Shifts.
  Persistent late (flag): **> 60 min late on ≥ 10 days** (`persistent_late_basis = very_late`). Chosen after the October
  data showed median arrival 10:28 — a 15-minute rule flagged 66 of 94 people (noise); the 1-hour rule flags 23.
  Basis `late` (> grace) is available in Settings.
- **Early exit**: last‑out < shift end − 15 min on full present days.
- **Short day**: work < **7 h** on full present days. **Late sitting**: out after **21:00**.
- **Missing punch**: present day with one swipe only / in without out.
- **Regularisation**: day with a `Permission` swipe or with muster in/out times that match no swipe.
  Persistent ≥ **3** per month.
- **Mobile sign‑in at "Office"**: possible biometric bypass (count).
- **Sandwich absence**: leave/absence on a WORKDAY adjacent to a weekly off/holiday.
- **Present without punch**: a present working day with in/out times in greytHR but no swipe at all, for someone who
  does punch on other days (Oct: HS158 18 days, HS207 15 days). HR disallowed such a day for HS197.
- **Auto attendance**: present all month with no punches (partners) — only evaluated when a swipe file was imported.
- **No attendance** all month; unauthorised absence ≥ 1; leave balance negative.
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
`DRAFT` (imports, auto‑compute) → `FINALISED` (locked: no imports/overrides; ledgers posted; exports stamped).
Re‑open = admin only, reason mandatory, audit‑logged.
**Delete a month** (v1.4.1) = admin only, DRAFT only, reason + typing the month name, audit‑logged; removes the month's
imports, results, corrections and reviews; later draft months are recalculated.

## 11. Import rules (v1.4.1)
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
