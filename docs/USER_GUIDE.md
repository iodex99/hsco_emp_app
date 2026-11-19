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
- **Insights**: who needs a conversation and why — scorecard with an attention score (0–100) and its reasons, 12-month unplanned absence, on-time arrivals, hours vs required, an employee × day heatmap (click legend items to highlight; the *Total* column and footer count the highlighted days for the people shown), arrival times, groups compared.
  An employee's **Scorecard** tab compares them with their group and the firm.
- **Employees**: settings per person (group, working pattern, hours per day, exemption).
- **Locations**: every mobile sign-in with its place name (worked out from the GPS coordinates automatically),
  the employee's remark and phone; filter to client visits, WFH, "Office" sign-ins made elsewhere, shared
  phones; see it by place or by person. Available as soon as the swipe export is imported — no need to wait for
  month end.
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
2. **Import exports** → select all files → *Import & calculate*. The app recognises each file and the month, even if
   columns were moved or renamed (expand *Columns used* to see how each column was read).
   Warnings show possible exits (people missing from the active list) and missing coordinates.
   If a file says **not imported yet · Map columns**, the app wasn't sure what it is: choose the report type, check the
   column for each field (its guesses are pre-filled; the preview highlights the columns used) and *Import*. Leave
   *Remember this layout* ticked and the same kind of file imports automatically from then on (remembered layouts are
   listed at the bottom of Import files, with a bin to forget one).
   **Correction files:** a file with only a few people (e.g. a corrected muster for one person) updates just those
   people; everyone else's month is kept. The full export replaces the whole month.
   A file dated well in the future is refused — check the year in the file.
   **Imported the wrong month by mistake?** An admin can delete a draft month: Payroll leave sheet → bin icon → give a
   reason and type the month name.
3. **Review queue**: work through the exceptions (shortfall deductions, unauthorised absence, WFH over limit,
   present-without-punch, leave exhausted). Mark each one reviewed; pick a reason or type a note.
4. Click an employee to open the **drill-down**: calendar, every punch with place, flags, ledger. Correct if needed:
   - *Click the day* in the calendar: change day type, session status (P, A, PL, COF, LOP…) or hours.
   - *Corrections tab, Adjust a figure*: e.g. grant an exception (Exempt this month = yes), set required hours, override
     deduction days, or a remark. **A reason is mandatory**; everything is in the Audit log and highlighted in Excel.
   - *Opening balances* — first month only (or bulk via the Leave opening template).
5. **Payroll leave sheet** → *Finalise for payroll* (locks the month) → **Download Excel** for payroll.
   Columns E–S mirror the old MIS sheet; extra columns give deduction days, leave ledger and payroll LOP days.
6. Partners and the board use the app itself (give them the *viewer* role): Overview, Insights, Locations and every
   drill-down, with sorting, filters and CSV on every table.

## Permanent employee settings (Employees → name)
Group, location, DOJ/DOL, weekly-off pattern (greytHR / standard 1st-Saturday / all Saturdays off / Sunday only /
custom weekdays + working Saturdays), required hours per day (e.g. 8 instead of 8.5), holiday calendar,
exempt from the hours-shortfall deduction (+ reason; WFH over the limit is still deducted). Changes recompute all
draft months.

## Parallel run (first months)
Upload your manual MIS workbook too. **Reconciliation** lists every difference between your sheet and the app.
For October 2026: 91 of 97 identical once the employee settings were entered; the 6 differences are explained in
`docs/DATA_ANALYSIS.md`.

## Rules & calendars
Policy numbers (8.5 h/day, 4 h block = ½ day, WFH limit 2, comp-off ≥ 5 h, late thresholds, exempt groups),
greytHR status-code meanings, shift timings and holiday calendars (default + special). Changes apply to draft months
only. Place names need no setup: they come from the built-in map of India (v1.6.2).

## Targeted holidays
Rules & calendars → *Holiday calendars* → **Targeted holidays**: pick the date and name, tick the locations and/or
groups (ticking both means people in that location *and* group), and/or add people by name or number. The line above
the button shows who will get it. Saving recalculates draft months. For many at once, use the holiday-list template's
*For Locations / For Groups / For Employees* columns.

## Work from home on a day off
WFH is counted only on a person's own working days. If someone signs in "Work from Home" on their weekly off or a
holiday, the day is shown as off-day work (and can earn a comp-off) with a note — it does not count towards the WFH
limit.

## WFH over the limit
Each WFH day above 2 in a month takes ½ day of leave (Rules & calendars → Work from home; 0 = only flag it). It
appears in the employee's *Leave deduction*, the leave ledger, the review queue and the payroll Excel. It applies to
everyone — including Article, SS and Trainee — except the groups in *Groups not deducted for WFH* (default Partner)
or a month exception; a month correction can change the deduction.

## A month that isn't calculated yet
If you import the swipe export during the month, that month shows "Not calculated yet": what's been imported, the
sign-ins so far and a link to Locations. Everything else (leave, hours, Insights, downloads) appears once the
month's Attendance Muster Report is imported.

## Logo
Settings → Security & system → **Logo**: upload the firm's logo (SVG is sharpest). It appears in the sidebar, on the
sign-in page and in the browser tab. *Remove* goes back to the firm's initials.

## Where to see punch locations
- **Locations** (sidebar): every mobile sign-in with its place name, tag, remark and phone; *Download Excel*
  gives every punch of the month (biometric and mobile) with place and coordinates.
- An employee's month → **Sign-ins & places** (top right), or click a day in the calendar to see each punch and place.
- Payroll Excel → *Daily Detail* → **Places** column.
Place names need a swipe export **with GPS** (greytHR *Employee Swipe Details* with Latitude/Longitude — the October
export had none). Import it like any other file, at any time in the month.

## Are the downloads up to date?
Yes — every Excel is created the moment you click, from the app's current figures, so it always includes the
corrections and reviews made so far. *Corrections* lists each correction with reason, who and when; *Flags* shows
which exceptions were reviewed and the note. For a month you have finalised, the figures are frozen.

## Backups
The app backs itself up every day (Settings → Security & system → *Backups & data safety*; *Back up now* for an
extra copy, e.g. before month-end). Ask IT to also copy the app's data folder to another drive regularly.

## Ranked lists on the Overview
Every list (late, regularisations, missing punches, absence, WFH, hours) includes everyone it applies to. Choose
**Top 5 / 10 / 20 / All** (remembered for all lists) or type a name or employee number in the list's search box; the
number on the left is the person's real rank.

## Punch exceptions
Sidebar → **Punch exceptions**: every day someone has a missing or incorrect punch, a regularisation, WFH (over the
limit, on a day off), was more than an hour late, left early, had a short day, was absent or worked a day off — one
row per person and day with all issues, punches and reasons. Click a chip (e.g. *Missing punch*) to see only those;
click a row to open that person's day and correct it. *By employee* gives the counts per person. The same lists are
in the payroll Excel (*Punch Exceptions*, *Exceptions by Employee*).

## Categories & holiday lists (v1.7)
1. Import greytHR's **Employee Category Report** and each category's **Holiday List Importer** file (drop them all on
   the Import page together). Each category gets its own holiday list; the result lists what was loaded, added,
   changed or removed, and any date whose weekday didn't match.
2. **Rules & calendars → Categories:** pick each category's holiday list, weekly offs and hours per day. Employees
   follow their category (and, for articles, their additional category) unless their own page says otherwise.
   *Check these dates* lists days where a category's list and greytHR disagree — review them before payroll.
3. **Next year / revised list:** upload it again — the dates it covers are replaced (until a year's list is loaded,
   greytHR's own holiday marks are used for that year). No Excel file? Open the calendar and
   use **Paste a list** (copy the table from a PDF or email). Tick *Optional* for optional holidays.
4. A one-off holiday for a category: *Targeted holidays* → Categories.
