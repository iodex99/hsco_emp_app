# User Guide — monthly workflow

## Roles
- **Viewer** (partners/board): dashboard, payroll sheet, flags, employees, reconciliation, downloads.
- **HR**: + import, corrections, opening balances, employee master, calendars, finalise.
- **Admin**: + policy rules, security settings, users, re-open a finalised month.

## Signing in
Username + password, then a second step: authenticator-app code, security key / Windows Hello, an emailed code, or a
recovery code. First sign-in forces you to set up an authenticator app and shows 10 recovery codes — print/store them.
Add more methods under **My security** (several phones, keys).

## Month end (≈ 10 minutes)
1. In greytHR export for the month: **Attendance Muster Report**, **Employee Swipe Details**,
   **Date Of Joining Report** (and optionally **Attendance Summary Report**). No need to remove filters or extra sheets.
2. **Import exports** → select all files → *Import & calculate*. The app recognises each file and the month.
   Warnings show possible exits (people missing from the active list) and missing coordinates.
3. **Exceptions & flags**: review high items — shortfall deductions, unauthorised absence, WFH over limit,
   present-without-punch, leave exhausted.
4. Click an employee to open the **drill-down**: calendar, every punch with place, flags, ledger. Correct if needed:
   - *Correct a day* — change day type (working/off/holiday), session status (P, A, PL, COF, LOP…) or hours.
   - *Adjust month figures* — e.g. grant an exception (Exempt this month = yes), set required hours, override
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
