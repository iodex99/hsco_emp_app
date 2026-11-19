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
| Over WFH limit | employees with WFH days > limit (2); WFH counts only on the person's own working days (v1.6.4); ½ day of leave per extra day (v1.6.5) |
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
Each list holds every employee who qualifies (value ≥ 1 day, or above the WFH limit; ties by name); the page shows the
top 5/10/20/all and searches by name or employee no. (v1.6.3).

## Heatmap Total (v1.6.3)
Insights → Attendance heatmap: *Total* = number of days in the legend states the user highlighted (several add up; a
half-leave day counts as one day); with nothing highlighted, all days with a state. Footer: sums for the rows shown
after search/filter, and per day the number of highlighted cells.

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

## Insights (v1.4 — `app/services/insights.py`; Insights page, employee Scorecard tab, Excel *Scorecard*)
Working days = days of type WORKDAY for the employee. *Behaviour* measures (punctuality, regularisation, missing
punches) exclude `metrics_exclude_groups` (default Partner).

| Measure | Definition |
|---|---|
| Attendance % | days present ÷ working days |
| Unplanned absence % (absenteeism) | (absent + loss-of-pay half-days ÷ 2) ÷ working days. Planned leave and comp-off are *not* unplanned |
| Absence spell | consecutive working days with any unplanned absence; weekly-offs/holidays in between do not break a spell |
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

**Attention score (0–100, higher = more to talk about)** = 25 × min(1, unplanned-absence days in the last 12 months ÷ 10) + 20 × (1 − on-time share)
+ 20 × min(1, (100 − hours-vs-required %) ÷ 20) + 15 × min(1, regularised days ÷ (2 × persistent-regularisation days))
+ 10 × min(1, (WFH days − limit) ÷ (2 × limit)) + 10 × min(1, missing-punch days ÷ 5). Behaviour parts are 0 for excluded
groups. "Main reasons" lists the parts worth ≥ 5 points. ≥ 50 counts as "needs a conversation".

**Employee comparison** (Scorecard tab): the person's value, their group's average and the firm's average for ten
measures; verdict "better / needs attention" when the gap to the firm exceeds 5 % points (percentages) or 10 % of the
firm value (others), otherwise "typical".

*v1.4.1:* the attention score uses 0 % as 0 % (previously a 0 % on-time or hours share was mistaken for "no data").
History-based measures (12-month absence, FY leave used, trend) are computed from the stored month summaries; a spell that runs
from one month's last working day into the next month's first working day counts once.

## Locations (v1.5 — `app/services/locations.py`; Locations page, Excel "Sign-ins & Places"/"Places", punches & locations workbook)
| Measure | Definition |
|---|---|
| Mobile sign-ins | punches that are not office-biometric (Astra); % with GPS |
| Client visits | distinct (person, day, place) with Location Type "Client Location" |
| WFH sign-ins | Location Type Work from Home (count and people) |
| Tagged 'Office' from a phone | mobile sign-ins tagged Office (count and people) |
| Shared phones | phone ids used by 2+ people in the month |
| By place | sign-ins, people, tags and top remarks per place name |
Flag: `SHARED_DEVICE` (high) "Phone used to sign in for several people" — see BUSINESS_RULES §9a.
(`OFF_SITE_OFFICE` was removed in v1.6.2 with the named places; its label is kept only for old history.)

*v1.5.2:* the Bradford factor was removed on request. 12-month absence is reported as **unplanned absence days** and
**spells** (a spell = consecutive working days with unplanned absence; offs/holidays between don't break it; a spell
crossing a month boundary counts once).

## Punch exceptions (v1.6.7 — `app/services/exceptions.py`; Punch exceptions page, Excel *Punch Exceptions* / *Exceptions by Employee*)
One row per employee-day with ≥ 1 issue; all from the stored day detail.
| Issue | Rule |
|---|---|
| Missing punch | present working day with one punch only, or an in-time without an out-time (`missing`) |
| Present, no punch | marked present in greytHR with no punch at all that day (people who punch at all) |
| In/out changed (no matching punch) | regularised day without a Permission punch: greytHR in/out time matches no punch |
| Regularisation request | a `Permission` punch that day (reason shown) |
| Rejected sign-in | a sign-in with approval Rejected / Declined / Cancelled / Denied (ignored for attendance) |
| Phone sign-in tagged 'Office' | mobile sign-in with Location Type Office |
| WFH / WFH over the limit / WFH on a day off | WFH working day; the WFH days after the first *limit* (date order); WFH on own weekly off / holiday |
| WFH on a leave / absent day | WFH sign-in on a working day of full leave / absence (not counted — check the day) |
| WFH — office closed (Saturday) | WFH on a working Saturday the branches are closed (only the open Saturdays in the setting) — not counted (v1.7.4) |
| Late > 1 h / Early exit / Short day | as in Behaviour metrics (lateness under 1 h is on Insights / Overview) |
| Absent / LOP / Worked on a day off | unauthorised absence or LOP half; work on a weekly off / holiday (not WFH) |
Partners on automatic attendance are not checked for punch issues. "Punch issues" KPI = days with missing punch, no
punch, changed times or a rejected sign-in; "Regularisations" = requests + changed times.

## Categories (v1.7)
Overview *By group & category* and Insights *Groups & categories* show the same measures per **category** (the
category stored with each month's result; "(not set)" for people without one) as per group; Excel: Board Summary *By
category*, *Categories Compared*, Scorecard column. Payroll sheet, Punch exceptions, heatmap and scorecard have a
Category column (hidden by default where space is tight).
