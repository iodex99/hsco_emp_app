# Metrics & Flags Catalogue

Code: `app/engine/compute.py` (per employee), `app/services/metrics.py` (organisation). Thresholds are settings.

## Board KPIs (dashboard tiles, Excel "Board Summary", HTML pack)
| KPI | Definition |
|---|---|
| Employees in muster | employees with ≥ 1 muster day in the month |
| New joinees / Exits | DOJ / DOL within the month (exits also suggested from the DOJ active list) |
| Attendance rate | Σ days present ÷ Σ (week days − holidays) |
| Leave deductions | employees with shortfall deduction; total deduction days |
| Payroll LOP days | LOP status days + days beyond leave balance (+ comp-off overdraft if configured) |
| Over WFH limit | employees with WFH days > limit (2) |
| Persistently late | > 60 min late on ≥ 10 days (non-partners) |
| Persistent regularisation | ≥ 3 regularised days |
| Unauthorised absent days | Σ `A` halves on working days |
| Avg hours / present day; avg in / out time | non-partners, present working days |
| Off-day / holiday work days; comp-offs earned (≥ 5 h) | |
| Excess / shortfall hours | Σ positive / negative monthly balances |
| Late-sitting days | out after 21:00 or next day |

## Charts
Location mix of punches (biometric office / client / office-mobile / WFH / on-duty / permission) · arrival-time
histogram (30-min buckets) · daily attendance mix (present / leave / absent+LOP / WFH) · late arrivals by weekday ·
tenure profile · per-employee excess/shortfall bar (HTML pack, click → drill-down) · 12-month trend (deductions, late,
WFH, absence, regularisation) once more than one month exists · per-employee history chart.

## Top lists
Most often > 1 h late · above WFH limit · most regularisations · missing punches · unauthorised absence ·
most excess hours · hours shortfall (with deduction) · comp-offs earned · leave adjacent to weekends/holidays.

## Group table
Headcount, present days, PL, comp-off, absent, LOP, WFH, late days, regularised, deduction days, employees deducted,
actual ÷ required hours %.

## People movement
Joiners, exits, possible exits (in last month's muster, not this month's), work anniversaries, joined in last
6 months (probation watch), headcount by group, tenure buckets.

## Employee-level metrics (drill-down, Excel sheets)
MIS figures E–R; splits PL / COF / unauthorised / LOP; WFH (+ partial); late days, very-late days, avg minutes late,
avg in/out, early exits, short days (< 7 h), late sitting, missing punches, regularised days, permission swipes,
present-without-punch days, auto attendance, client-location days, on-duty days, biometric vs mobile swipes, mobile
"Office" swipes, comp-off earned, sandwich count, leave & comp-off ledger, payroll LOP; day-by-day status, punches with
place names, notes where the app calendar differs from greytHR.

## Flags (severity)
| Code | Sev | Trigger |
|---|---|---|
| NO_ATTENDANCE | critical | no present day and no hours in month |
| SHORTFALL_DEDUCTION | high | deduction > 0 |
| UNAUTHORISED_ABSENCE | high | any `A` half-day |
| WFH_OVER_LIMIT | high | WFH days > limit |
| LEAVE_EXHAUSTED | high | leave closing would go negative → salary deduction |
| PERSISTENT_LATE | medium | "Often more than 1 h late" (see KPI) |
| PERSISTENT_REGULARISATION | medium | regularised days ≥ 3 |
| MISSING_PUNCHES | medium | ≥ 2 present days with one punch / no out |
| PRESENT_WITHOUT_PUNCH | medium | present days with times but zero swipes (employee punches on other days) |
| COMPOFF_OVERDRAWN | medium | COF taken > balance |
| GROUP_NOT_SET / NOT_IN_MASTER / UNKNOWN_STATUS | medium | data quality |
| SHORTFALL_EXEMPT, MOBILE_AT_OFFICE, LATE_SITTING (≥ 5), SANDWICH_PATTERN (≥ 2), LOP, AUTO_ATTENDANCE, DOJ_MISSING, NEW_JOINEE, EXIT, CALENDAR_MISMATCH, MANUAL_OVERRIDE, NO_OPENING_BALANCE | info | |
