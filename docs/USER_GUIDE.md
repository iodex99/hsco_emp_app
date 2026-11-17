# User Guide — monthly workflow

## Roles
- **Viewer** (partners/board): dashboard, payroll sheet, flags, employees, reconciliation, downloads.
- **HR**: + import, corrections, opening balances, employee master, calendars, finalise.
- **Admin**: + policy rules, security settings, users, re-open a finalised month.

## Signing in
Username + password, then a second step: authenticator-app code, security key / Windows Hello, an emailed code, or a
recovery code. First sign-in forces you to set up an authenticator app and shows 10 recovery codes — print/store them.
Add more methods under **My security** (several phones, keys).

## Finding your way
- **Overview**: the month at a glance, with a **month-end checklist** that tells you the next step.
- **Payroll leave sheet**: everyone's figures; chips filter to deductions, loss of pay, WFH over limit, flags and more.
- **Review queue**: every exception, grouped by type; mark each reviewed with a short note.
- **Employees**: settings per person (group, working pattern, hours per day, exemption).
- Press **/** anywhere to find an employee; use the **month switcher** (top right) to change month.
- On an employee's page, click **legend items** under the calendar (several at once) to show only those days, e.g. Late + Missing punch.
- In the **Review queue**, tick several rows (or the header box for all shown) and use the bar at the bottom to mark them reviewed in one go. *Review queue →* on an employee's Flags card shows only that person.
- On an employee's page, **click a calendar day** to see its punches and correct it; the left/right arrow keys move
  to the previous/next employee.

## Working with tables (every list in the app)
- **Search** box filters as you type. **Filter** → pick a column → choose values (lists show counts) or a condition
  (e.g. *Hours +/− < 0*, *Joined on or after 01-Jul-2026*). Filters appear as chips; click a chip to change it, × to
  remove, *Clear all* to reset. Quick-filter chips above the table combine with these.
- **Sort**: click a column header (again to reverse, a third time to clear); **Shift-click** another header to sort by
  a second column; or use the **Sort** menu for several levels.
- **Group** by a column (e.g. Group or Location) to see counts and subtotals per group; ▾ collapses a group.
- **Columns**: hide or show columns (the payroll sheet has *Key columns* / *All columns* presets).
- **Views**: save the current setup (e.g. "BSS with deductions") and reuse it next month; *Copy link* shares it.
- **CSV** downloads exactly what you see.

## Month end (≈ 10 minutes)
1. In greytHR export for the month: **Attendance Muster Report**, **Employee Swipe Details**,
   **Date Of Joining Report** (and optionally **Attendance Summary Report**). No need to remove filters or extra sheets.
2. **Import exports** → select all files → *Import & calculate*. The app recognises each file and the month.
   Warnings show possible exits (people missing from the active list) and missing coordinates.
3. **Review queue**: work through the exceptions (shortfall deductions, unauthorised absence, WFH over limit,
   present-without-punch, leave exhausted). Mark each one reviewed; pick a reason or type a note.
4. Click an employee to open the **drill-down**: calendar, every punch with place, flags, ledger. Correct if needed:
   - *Click the day* in the calendar: change day type, session status (P, A, PL, COF, LOP…) or hours.
   - *Corrections tab, Adjust a figure*: e.g. grant an exception (Exempt this month = yes), set required hours, override
     deduction days, or a remark. **A reason is mandatory**; everything is in the Audit log and highlighted in Excel.
   - *Opening balances* — first month only (or bulk via the Leave opening template).
5. **Payroll leave sheet** → *Finalise for payroll* (locks the month) → **Download Excel** for payroll.
   Columns E–S mirror the old MIS sheet; extra columns give deduction days, leave ledger and payroll LOP days.
6. Share the **Interactive board pack** (.html) with partners — opens offline in any browser.

## Permanent employee settings (Employees → name)
Group, location, DOJ/DOL, weekly-off pattern (greytHR / standard 1st-Saturday / all Saturdays off / Sunday only /
custom weekdays + working Saturdays), required hours per day (e.g. 8 instead of 8.5), holiday calendar,
exempt from deduction (+ reason). Changes recompute all draft months.

## Parallel run (first months)
Upload your manual MIS workbook too. **Reconciliation** lists every difference between your sheet and the app.
For October 2026: 91 of 97 identical once the employee settings were entered; the 6 differences are explained in
`docs/DATA_ANALYSIS.md`.

## Rules & calendars
Policy numbers (8.5 h/day, 4 h block = ½ day, WFH limit 2, comp-off ≥ 5 h, late thresholds, exempt groups),
greytHR status-code meanings, shift timings, holiday calendars (default + special), named locations (geofences) and
frequent unnamed sign-in spots. Changes apply to draft months only.
