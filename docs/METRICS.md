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

## Insights (v1.4 — `app/services/insights.py`; Insights page, employee Scorecard tab, Excel *Scorecard*, board pack)
Working days = days of type WORKDAY for the employee. *Behaviour* measures (punctuality, regularisation, missing
punches) exclude `metrics_exclude_groups` (default Partner).

| Measure | Definition |
|---|---|
| Attendance % | days present ÷ working days |
| Unplanned absence % (absenteeism) | (absent + loss-of-pay half-days ÷ 2) ÷ working days. Planned leave and comp-off are *not* unplanned |
| Absence spell | consecutive working days with any unplanned absence; weekly-offs/holidays in between do not break a spell |
| Bradford factor | S² × D over the **last 12 computed months** (S = spells, D = unplanned-absence days). Bands: 0 none · 1–50 low · 51–200 monitor · 201–449 warning · 450+ serious |
| On time % (punctuality) | present working days whose first punch is within the late grace (shift start + `late_grace_min`) ÷ present working days with a first punch |
| Hours vs required % (utilisation) | actual hours ÷ required hours |
| Overtime / shortfall hours | sum of positive / negative month balances |
| Long day | a day worked ≥ 2 h more than the person's hours per day |
| WFH share % | WFH days ÷ days present |
| Regularisation % / missing-punch % | regularised (missing-punch) days ÷ days present |
| Leave used FY % | paid leave taken in the financial year to date ÷ annual entitlement × people |
| Leave / comp-off liability | sum of closing balances |
| Attrition (12 m) % | exits with Date of Leaving in the last 12 months ÷ average of headcount now and 12 months ago |
| Average tenure | mean years since joining, active employees with a DOJ |
| Median first punch | median of employees' average first-punch time (behaviour groups) |
| Arrival heatmap | first punches counted by weekday × half hour (`< 08:00` and `13:00 +` folded) |
| Working-day histogram | worked days by length bucket (< 4 h … 11 h +) |

**Attention score (0–100, higher = more to talk about)** = 25 × min(1, Bradford ÷ 450) + 20 × (1 − on-time share)
+ 20 × min(1, (100 − hours-vs-required %) ÷ 20) + 15 × min(1, regularised days ÷ (2 × persistent-regularisation days))
+ 10 × min(1, (WFH days − limit) ÷ (2 × limit)) + 10 × min(1, missing-punch days ÷ 5). Behaviour parts are 0 for excluded
groups. "Main reasons" lists the parts worth ≥ 5 points. ≥ 50 counts as "needs a conversation".

**Employee comparison** (Scorecard tab): the person's value, their group's average and the firm's average for ten
measures; verdict "better / needs attention" when the gap to the firm exceeds 5 % points (percentages) or 10 % of the
firm value (others), otherwise "typical".
