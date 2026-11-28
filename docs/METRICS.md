# Metrics & Flags Catalogue

Code: `app/engine/compute.py` (per employee), `app/services/metrics.py` (organisation). Thresholds are settings.

## Board KPIs (dashboard tiles, Excel "Board Summary", HTML pack)
| KPI | Definition |
|---|---|
| Total head count | employees with ≥ 1 muster day in the month (ignored groups left out) |
| New joinees / Left employees | DOJ / DOL within the month (exits also suggested from the DOJ active list) |
| Total attendance | Σ days present ÷ Σ (week days − holidays) (v1.16: the present-days line removed) |
| Present all days (v1.15) | people with working days > 0 and days present ≥ working days (no leave, no unapplied leave, no half day) |
| Leave deductions | employees with shortfall deduction; total deduction days |
| Unapplied leave (absent) | how many people (the headline, red) and Σ `A` halves on working days (grey) (v1.13 — formerly "unauthorised absence"; v1.15 "absence" → "absent"; there is no LOP any more: LOP / LWP count as leave) |
| Over WFH limit | employees with WFH days > limit (2); WFH counts only on the person's own working days (v1.6.4); ½ day of leave per extra day (v1.6.5). Work location tab (v1.13): WFH days (instances), people who worked from home, people over the limit and days over, and every WFH person (over-limit badge, “Over the limit” chip) |
| Persistently late | > 60 min late on ≥ 10 days (non-partners) |
| Persistent regularisation | ≥ 3 regularised days |
| Avg hours / present day; avg in / out time | non-partners, present working days |
| Comp-offs earned (≥ 5 h) | people who earned one (red) and the comp-off days in total (v1.15) |
| Hours shortfall > 20 h / Excess hours > 20 h (v1.15) | people whose monthly balance is below −20 h / above +20 h (`metrics.HOURS_PEOPLE_LIMIT`) |
| Late sitting > 10 days (v1.15) | people with more than 10 late-sitting days (`metrics.LATE_SITTING_PEOPLE_DAYS`). v1.16: a late-sitting day = a present working day with worked minutes > required hours per day × 60 + `late_sitting_extra_min` (60) — the person's own required hours (policy / own / category / firm), not a clock time |

v1.15 removed from the Overview: leave days, comp-offs taken and "no attendance" tiles, off-day work days, excess /
shortfall hours sums (still in Excel *Board Summary*) and the Trend tab.

**Comparison with last month (v1.15, owner: "in all the reports there should be comparison with last month")**: every
headline figure shows ▲ / ▼ and the difference against the calendar month before, with last month's value, e.g.
"▼ 26 vs last month (34)"; "= same as last month" when equal; nothing when there is no calculated month before. Green
= better, red = worse; neutral figures (head count, joinees, sign-ins, leave taken) in grey. Percentages compare in
points. Unapplied leave compares the people. Pages: Overview, Insights, Payroll leave sheet, Punch exceptions,
Locations, employee page (the person's own previous month), PDFs; Excel *Board Summary* has *last month* and *Change*
columns (`ui.vs` / `ui.mini` in `_ui.html`).

## Charts
Where people punched (v1.13: a ranked bar list instead of a doughnut — punches, share of all punches, people; a Total
row) · arrival-time histogram (30-min buckets) · daily attendance mix (present / leave (incl. comp-off) / unapplied
leave / WFH; tooltip "Thu 9 Oct 2026" with every series and the total) · late arrivals by weekday · tenure profile ·
per-employee history chart (the Overview's 12-month trend was removed in v1.15; Insights keeps its trend tab). The daily
chart's axis shows the date in red and the day initial in grey (v1.15).

**Click-through (v1.13, owner: "when clicked on these bars, shall show the data of those exact filtered out employees … across
the app")**: every bar / segment / row of a chart or breakdown opens a panel listing exactly the people behind it with
their figure for it (searchable, Filter → Group, sortable, CSV, total row): daily attendance (date × present / leave /
unapplied leave / WFH; half days count ½), first-punch half hour, late arrivals by weekday, where people punched, tenure
bucket, Overview *Needs attention* type (with each person's detail and a link to the payroll sheet filtered to them,
`?f=fl-<CODE>`), By-group row, Insights day-length bucket, arrival cell (weekday × half hour), group bar / row. Trend
charts open the clicked month; the employee history chart opens that month's page. The lists behind the bars come from
`metrics.period_metrics()["drill"]` / `insights()["drill"]` and always add up to the bar.

## Top lists
Most often > 1 h late · WFH (everyone, over-limit marked) · most regularisations · unapplied leave (absence) ·
most excess hours · hours shortfall (with deduction) · comp-offs earned; Insights: most unapplied leave this month /
12 months. Each list holds every employee who qualifies (value ≥ 1 day, or above the WFH limit; ties by name); the page shows the
top 5/10/20/all and searches by name or employee no. (v1.6.3). v1.13: a **group filter** (several groups at once) on
every list, and a footer "x of y people · total z" for the people matching the search / groups.

## Heatmap Total (v1.6.3)
Insights → Attendance heatmap: *Total* = number of days in the legend states the user highlighted (several add up; a
half-leave day counts as one day); with nothing highlighted, all days with a state. Footer: sums for the rows shown
after search/filter, and per day the number of highlighted cells.

## Group table ("By group", v1.13: the category table was removed)
Headcount, present days, leave, comp-off, unapplied leave (absence), WFH, late days, regularised, deduction days,
actual ÷ required hours %; a Total row (sums; hours vs required = Σ actual ÷ Σ required).

## People movement
Joiners, exits, possible exits (in last month's muster, not this month's), work anniversaries, joined in last
6 months (probation watch), headcount by group, tenure buckets.

## Employee-level metrics (drill-down, Excel sheets)
MIS figures E–R; splits leave (incl. LOP / LWP) / COF / unapplied leave; WFH (+ partial); late days, very-late days, avg minutes late,
avg in/out, early exits, short days (< 7 h), late sitting, regularised days, permission swipes, auto attendance, client-location days, on-duty days, biometric vs mobile swipes, mobile
"Office" swipes, comp-off earned / taken, sandwich count, the deduction split (hours / WFH); day-by-day status, punches with
place names, notes where the app calendar differs from greytHR.

## Flags (severity)
| Code | Sev | Trigger |
|---|---|---|
| NO_ATTENDANCE | critical | no present day and no hours in month |
| SHORTFALL_DEDUCTION | high | deduction > 0 |
| UNAUTHORISED_ABSENCE | high | any `A` half-day — shown as **Unapplied leave (absence)** (v1.13) |
| WFH_OVER_LIMIT | high | WFH days > limit |
| LEAVE_EXHAUSTED, NO_OPENING_BALANCE, COMPOFF_OVERDRAWN | — | not raised since v1.9 (no balances); labels kept for finalised months |
| PERSISTENT_LATE | medium | "Often more than 1 h late" (see KPI) |
| PERSISTENT_REGULARISATION | medium | regularised days ≥ 3 |
| MISSING_PUNCHES, PRESENT_WITHOUT_PUNCH, LOP | — | removed in v1.13 (owner); never shown, even for months finalised earlier (`metrics.RETIRED_FLAGS`) |
| COMPOFF_OVERDRAWN | medium | COF taken > balance |
| GROUP_NOT_SET / NOT_IN_MASTER / UNKNOWN_STATUS | medium | data quality |
| SHORTFALL_EXEMPT, MOBILE_AT_OFFICE, LATE_SITTING (≥ 5), SANDWICH_PATTERN (≥ 2), AUTO_ATTENDANCE, DOJ_MISSING, NEW_JOINEE, EXIT, CALENDAR_MISMATCH, MANUAL_OVERRIDE, NO_OPENING_BALANCE | info | |

## Insights (v1.4 — `app/services/insights.py`; Insights page, employee Scorecard tab, Excel *Scorecard*)
Working days = days of type WORKDAY for the employee. *Behaviour* measures (punctuality, regularisation) exclude
`metrics_exclude_groups` (default Partner).

| Measure | Definition |
|---|---|
| Attendance % | days present ÷ working days |
| Unapplied leave (absence) % | absent (`A`) half-days ÷ 2 ÷ working days (v1.13: was "unplanned absence" = absent + LOP; LOP is leave now). Leave and comp-off are not counted |
| Absence spell | consecutive working days with unapplied leave; weekly-offs/holidays in between do not break a spell |
| On time % (punctuality) | present working days whose first punch is within the late grace (shift start + `late_grace_min`) ÷ present working days with a first punch |
| Hours vs required % (utilisation) | actual hours ÷ required hours |
| Overtime / shortfall hours | sum of positive / negative month balances |
| Long day | a day worked ≥ 2 h more than the person's hours per day |
| WFH share % | WFH days ÷ days present |
| Regularisation % | regularised days ÷ days present |
| Leave used FY % | paid leave taken in the financial year to date ÷ annual entitlement × people |
| Attrition (12 m) % | exits with Date of Leaving in the last 12 months ÷ average of headcount now and 12 months ago |
| Average tenure | mean years since joining, active employees with a DOJ |
| Median first punch | median of employees' average first-punch time (behaviour groups) |
| Arrival heatmap | first punches counted by weekday × half hour (`< 08:00` and `13:00 +` folded) |
| Working-day histogram | worked days by length bucket (< 4 h … 11 h +) |

**Attention score (0–100, higher = more to talk about)** = 30 × min(1, unapplied-leave days in the last 12 months ÷ 10) + 20 × (1 − on-time share)
+ 25 × min(1, (100 − hours-vs-required %) ÷ 20) + 15 × min(1, regularised days ÷ (2 × persistent-regularisation days))
+ 10 × min(1, (WFH days − limit) ÷ (2 × limit)). Behaviour parts are 0 for excluded groups. (v1.13: missing punches
removed; its 10 points went to unapplied leave +5 and hours +5.) "Main reasons" lists the parts worth ≥ 5 points. ≥ 50 counts as "needs a conversation".

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
| In/out changed (no matching punch) | regularised day without a Permission punch: greytHR in/out time matches no punch |
| Regularisation request | a `Permission` punch that day (reason shown) |
| Rejected sign-in | a sign-in with approval Rejected / Declined / Cancelled / Denied (ignored for attendance) |
| Phone sign-in tagged 'Office' | mobile sign-in with Location Type Office |
| WFH / WFH over the limit / WFH on a day off | WFH working day; the WFH days after the first *limit* (date order); WFH on own weekly off / holiday |
| WFH on a leave / absent day | WFH sign-in on a working day of full leave / absence (not counted — check the day) |
| WFH — office closed (Saturday) | WFH on a working Saturday the branches are closed (only the open Saturdays in the setting) — not counted (v1.7.4) |
| Late > 1 h / Early exit / Short day | as in Behaviour metrics (lateness under 1 h is on Insights / Overview) |
| Unapplied leave / Worked on a day off | absent (`A`) half on a working day; work on a weekly off / holiday (not WFH) |
People on automatic attendance are not checked for punch issues. "Incorrect punches" KPI = days with changed times
or a rejected sign-in (v1.13: missing punch and present-without-punch removed); "Regularisations" = requests + changed times.

## Categories (v1.7; v1.13: comparisons by group only)
The category comparison tables (Overview, Insights, Board Summary *By category*, *Categories Compared*) were removed
in v1.13 (owner: "only keep one table for group"). Payroll sheet, Punch exceptions, heatmap and scorecard keep a
Category column (hidden by default where space is tight).

## Totals, explanations, PDF (v1.13)
- **Totals** wherever they add up: tables with a footer that re-totals the rows shown (payroll sheet, scorecard,
  heatmap, Punch exceptions days / people, Locations sign-ins / places / people, By group, employee history), ranked
  lists (footer total), Where people punched (Total row), Insights arrival table (per weekday and per half hour), click-
  through panels; Insights *Groups compared* ends with the whole firm.
- **"i"** next to every KPI, card, tab and small figure: hover / focus / tap explains how it is calculated
  (`ui.info(text)`, `ui.kpi(…, tip=…)`).
- **PDF** (Overview, Insights, Payroll leave sheet, Punch exceptions, Locations): A4 landscape, every tab one after
  the other, KPI cards as boxes, charts as images, lists and tables as text tables with their colours (rows as filtered
  on screen), header (firm, page, time) and "Page x of y" on every page. Built in the browser (`static/pdf.js` with the
  vendored jsPDF + AutoTable) — nothing leaves the server.

## PDF (v1.13.1)
**PDF ▾** on the Overview, Insights, Payroll leave sheet, Punch exceptions and Locations: *This page* (every tab, rows
as filtered on screen) or *Full report* (cover + contents, then Overview, Insights, Payroll leave sheet, Review queue,
Punch exceptions, Locations, Reconciliation). Charts are drawn again at print size; ranked lists print everyone on them
with their total; short columns are sized to their content; screen-only hints are left out. The Overview's top row has
three cards (Leave deductions, Unapplied leave, Exceptions to review) — the attendance rate is under the Attendance tab.

## Overview changes (v1.16)
- **Glance strip** (5): head count (click → everyone with days present), new joinees, left employees (date of leaving),
  total attendance, present all days — each vs last month. Unapplied leave is a card in the Attendance tab.
- **First punch of the day**: people (each once) by their average first punch on days present: 09:30–10:00,
  10:00–10:30, 10:30–11:00, after 11:00 (`metrics.ARRIVAL_RANGES`); people before 09:30 are counted in the "i" note.
- **Late arrivals by weekday**: days with a first punch more than `very_late_min` (60) after shift start.
- **Daily attendance**: four stacked bars — present (not WFH), WFH, leave (incl. comp-off), unapplied leave; a WFH
  day's halves count as WFH, not present, so the bar adds up to the people with a working day.
- **Where people punched**: per place, people (distinct) then punches and share of punches; bar = people ÷ head
  count. Under **Permission** (a heading): WFH and On Duty — people and days (distinct person-days) from the
  Permission Report (status approved / applied / pending; withdrawn / rejected excluded), or, without a report, from
  sign-ins tagged Work from home / On-duty. Sign-ins tagged Permission are left out.

## Wording (v1.16.2, owner)
A number of employees is always written **headcount** ("8 headcount · 53 days", "1 headcount"), in the app, PDFs and
Excel. Column headers that counted employees are *Headcount*; the Excel sheet of joiners / exits is *Headcount
Movement*. "Person" is kept where it means one individual (per-person sheets, "each person's required hours").
