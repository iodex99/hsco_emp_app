# Data Analysis — greytHR exports & the manual working (October 2026 sample)

All files live in `reference/` (git‑ignored, contains real employee data). Analysis done 2026‑11‑17.
No sheet had active AutoFilter criteria or hidden rows/columns (filters were present but empty — checked
programmatically). One sheet (`MIS…/Reference`) declares a used range of 1,048,550 rows but only 101 contain data.

Employee population in the sample: **97 employees** in the muster (IDs `HS001…HS208`, `PC001…PC006`),
Mumbai + Delhi, 2 shifts.

---

## 1. `Attendance Muster Report.xlsx`  ← **the core day‑level source**

### Sheet `OG` (greytHR export, rows 5‑2992, header row 4; rows 1‑3 = company name, address, title)
| Col | Header | Notes |
|---|---|---|
| A | SNO | export serial; manual sheet replaced with `=A5+1` |
| B | Name | |
| C | Employee  No | key (note double space in header) |
| D | Attendance Date | datetime |
| E | Day | weekday name |
| F | Session1  Status | first half status |
| G | Session2  Status | second half status |
| H | In Time  [Asia/Kolkata] | `"01 Oct 2026 10:00"` string, may be blank |
| I | Out Time  [Asia/Kolkata] | string, may be blank |
| J | Shift Name | `10:am-18:30pm` (2957 rows) or `9 am to 6pm (Carlyle)` (31 rows) |
| K | Total Work Hours | `"08 : 29"` string (HH : MM) = Out − In (computed from seconds, so ±1 min vs minute strings) |
| L‑N | **manual** | `=LEFT(K,2)`, `=RIGHT(K,2)`, `=M/60+L` → decimal hours, **only on rows where the employee worked on an OFF/H day** (168 rows) |

Status codes seen (per session): `P` 1914, `OFF` 780, `H` 108, `A` 79, `COF` 55, `PL` 35/34, `LOP` 17.
Half‑day exists: one row `PL`/`P`.
Only days within employment appear (2988 rows ≠ 97×31): mid‑month joiners/leavers have fewer rows.
Holidays in Oct‑2026: **20‑Oct (all 96 with H)** and **02‑Oct (only 12 employees)** → holiday lists differ per employee.
`HS207` has no `H` at all (worked/was marked P on both holidays) — greytHR calendar not assigned; HR manually gave 2 holidays.

### Sheet `Extra Working Days` (manual pivot)
Pivot of OG L‑N rows: *Count of Adjusted Extra Hours* per **Name** → number of OFF/holiday days worked (168 total).
### Sheet `Extra Working Hours` (manual pivot)
*Sum of Adjusted Extra Hours* per **Name** → hours worked on OFF/holidays (1230.02 h total).

## 2. `EmployeeSwipeDetails.xlsx`

### Sheet `Sheet0` (export, 4149 swipes, header row 1)
`Employee No | Employee Name | Shift | Swipe Date ("31 Oct 2026 21:57:28") | Door/Address | Location Type | Swipe Type | Received On | Count`

| Door/Address | Location Type | Swipe Type | Rows | Meaning |
|---|---|---|---|---|
| `hasmukhshah` | (blank) | `Astra` | 3267 | biometric device at office |
| `Mobile Sign In` | `Client Location` | `Mobile Sign In` | 526 | greytHR app at client |
| `Mobile Sign In` | `Office` | `Mobile Sign In` | 146 | app, claims office |
| `Mobile Sign In` | `Work from Home` | `Mobile Sign In` | 136 | WFH |
| `Mobile Sign In` | `On-Duty` | `Mobile Sign In` | 20 | on duty |
| (blank) | (blank) | `Permission` | 54 | swipes added via permission/regularisation |

`Count` = manual column, `1` on every WFH swipe.
Swipes per employee‑day: 2 → 1727 days, 3 → 154, **1 → 97 (missing punch)**, 4+ → 31. 52 days mix location types.
**No GPS coordinates are present in this export** (owner says some reports carry sign‑in/out coordinates — the importer
detects `lat/long` columns or `"19.07, 72.87"` text anywhere in the row; see BUSINESS_RULES §9).

Muster In/Out = first/last swipe of the day (1951/2085 match exactly; the rest are partners with synthetic 10:00‑18:30
times and regularised days).

### Sheet `Sheet1` (manual pivot)
Count of `Count` per Name → `Final = count/2` = **WFH days** (feeds MIS column J).
Flaw: a day with one WFH swipe + one office swipe becomes 0.5; 7 employees differ from a distinct‑day count
(e.g. HS179: 3 by HR method, 4 distinct WFH days).

## 3. `Attendance Summary Report.xls` (legacy .xls, `Sheet0`, header row 4)
`Employee No | Employee Name | Location | (3 blank) | Present days | Absent days | Leave days | On duty days | Holiday days |
Total Week Days | Total Hours | Hours Required | Leave Hours | Short Fall | Excess Hours`

- `Total Hours` == sum of muster `Total Work Hours` for all 97 (verified, 0 mismatches) → includes OFF/holiday work.
- `Hours Required` = 186:38 for almost everyone (22 × 8:29 shift length) — **not** the policy 8.5 h × days.
- `Short Fall`/`Excess` are per‑day sums (not netted) → unusable for the monthly policy. This is why HR works manually.
- `Total Week Days` is frequently wrong (e.g. 21 when the muster has 22 non‑OFF days); `Leave days` counts `LOP` as leave.
- `On duty days` always 0.

## 4. `DateOfJoiningReport.xlsx` (`Sheet0`, header row 2, footer "Total number of records : 101")
`Employee Number | Employee Name | Joined On ("10 SEP 2026")` — **active employees only**.
- 7 IDs not in October muster: `HS209, HS210 (06‑Nov‑26), HS211 (17‑Nov‑26), HS202, "2", "3", "8"` (numeric IDs = legacy/partners).
- 3 muster IDs missing here: `HS098, HS102, HS104` → they **left** (MIS remark "Article Left"). ⇒ exits can be detected.
- October joiners: HS205 (01‑Oct), HS206 (02‑Oct), HS208 (19‑Oct).

## 5. `MIS Report October 2026_Manual.xlsx`  ← the manual output we automate

### Sheet `Reference` (rows 5‑101; A‑L from Summary/exports, M‑S manual)
| Col | Header | Source / formula |
|---|---|---|
| A‑D | Employee No, Name, **Group**, Location | Group is **not in any export** (Partner, SS, BSS, ATA, GST, Offshore, Article, Trainee) — maintained by HR |
| E | Total Week Days | Summary col L, **manually corrected** to = non‑OFF days in muster (22 / 23 / 27 / 10 …) |
| F | Holiday | Summary, corrected |
| G | Days Leaves Taken | PL + COF (manually reclassified in some cases) |
| H | Days Absent | A + LOP (mostly) |
| I | Days Present | `=E-F-G-H` |
| J | WFH | `=VLOOKUP(B, [Swipe]Sheet1!D:F, 3, 0)` |
| K | Total Hours Present | Summary "HHH:MM" |
| L | Extra Working Days | `=VLOOKUP(B, [Muster]'Extra Working Days'!D:E, 2, 0)` |
| M | Total Days Worked | `=E-F-G-H` |
| N | Hours Required to be completed | `=M*8.5` |
| O | Adjusted Total Hours Present | `=VLOOKUP(B, Working!A4:E100, 5, 0)` (**bug: range not anchored, shifts each row**) |
| P | Working on Holiday to exclude | `=VLOOKUP(B, [Muster]'Extra Working Hours'!D:E, 2, 0)` |
| Q | Actual Hours Worked (excl. holidays) | `=O-P` |
| R | Shortfall / Excess | `=Q-N` |
| S | Remark | free text: `Partner`, `SS Ignore`, `Article`, `Article Left`, `Trainee`, `Exception`, `0.5 Days deduction`, `1 Day Deduction` |
| T | check | `=M-I` (always 0) |

### Sheet `Working`
Name | "HHH:MM" | `=LEFT(B,3)` | `=RIGHT(B,2)` | `=D/60+C` → decimal hours. (LEFT(…,3) breaks for < 100 h, e.g. "94:33" → "94:" — works only by luck.)

### Rules inferred from the remarks (confirmed against policy PDF)
- Deduction = **ceil(shortfall / 4) × 0.5 day**: −3.37 h → 0.5 day, −3.67 h → 0.5 day, −6.00 h → 1 day.
- **No deduction** for groups Partner, SS, Article, Trainee, for leavers, and for named exceptions
  (HS014 & HS055 carry remark "Partner"; HS158 −0.35 h = "Exception").
- Everyone else with shortfall > 0 gets a deduction (no grace observed).

### Manual corrections found (muster ⇒ MIS)
| Emp | What HR did | Why (inferred) | App handling |
|---|---|---|---|
| HS053, HS162, HS192 | E 23→22, I −1 | all‑Saturdays‑off staff, worked 1st Sat (muster marked P) | per‑employee weekly‑off pattern `ALL_SAT_OFF` |
| HS068 | E 26→27, I 21→22 | weekly off is **Wednesday**; works Sat & Sun; greytHR marked 31‑Oct OFF though she worked | pattern `CUSTOM`, off = Wed |
| HS207 | F 0→2, I 23→21 | holiday calendar missing in greytHR | holiday calendar assignment |
| HS129 | LOP 12 → Absent | LOP treated as absent | status mapping `LOP → absent` |
| HS149 | LOP 3 → Leave | inconsistent with HS129 | flagged; month override |
| HS134 | COF 5 → leave 2 + absent 3 | comp‑off not earned | comp‑off ledger validation |
| HS138 | PL 5 → leave 4 + absent 1 | leave balance exhausted (inferred) | leave ledger |
| HS179 | PL 1.5 → 1, P 18.5 → 19 | rounding half‑day | app keeps 0.5 precision |
| HS197 | P 20 → 19, A +1, hours −9:45 | a present day disallowed | day override |
| HS192 | N = 152 (not 19×8.5=161.5) | manual target override | month override |
| HS053 | day removed from E but its 8:55 h kept in O | inconsistent | app is consistent (hours on off‑days excluded) |

### Reconciliation result (engine vs manual MIS, Oct‑2026)
With the employee settings above + 7 recorded month overrides + 1 day override, **91 of 97 employees match every
column**; all HR deductions are reproduced (HS112 1 day, HS138 0.5, HS205 0.5). The other 6 differ only because the
manual sheet is internally inconsistent:
- HS053, HS162, HS192 — 1st Saturday removed from E but its hours kept in O (app excludes off‑day hours consistently).
- HS068 — 31‑Oct counted as a week day yet its 9:36 h excluded as "holiday work".
- HS179 — half‑day leave rounded (1.5 → 1); WFH 3 (swipes ÷ 2) vs 4 distinct days.
- **HS207 — real finding:** 16:58 h auto‑marked by greytHR on 02‑Oct & 20‑Oct (no punches) were counted although HR
  treated both days as holidays. Excluding them leaves him 0.35 h short ⇒ **0.5 day deduction** — HR to decide.

### Other findings worth the board's attention (Oct‑2026)
- Median first punch 10:28; 23 employees were > 1 h late on ≥ 10 days.
- 11 employees have "present" days with no punch at all (HS158 18 days, HS207 15, HS205 6, HS206 4 …).
- HS055 punches once a day while greytHR auto-fills 10:00–18:30 — his hours are not real.
- 6 employees exceeded the 2‑day WFH limit (HS016 6 days, HS045 4, HS179 4, HS020/HS093/HS195 3).
- greytHR's own "Total Week Days" is wrong for 18 employees (Summary vs muster).

## Importer robustness (v1.4)
The importer no longer depends on these exact layouts: reordered or renamed columns, title rows, extra columns, other
vendors' registers (single status column with words, time-only in/out, decimal hours) and biometric logs (separate date
and time, IN/OUT direction, latitude/longitude columns) are recognised — see ARCHITECTURE "Universal importer".
Measured on the October-2026 exports: all five recognised with confidence 1.0 (DOJ 0.996); detection ≈ 0.1–0.3 s each.
Synonyms for the greytHR headers above are in `parsers.F`. October's swipe export has **no GPS coordinates**
(only Location Type), so place names apply when the mobile-app export includes them.
