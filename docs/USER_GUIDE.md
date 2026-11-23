# User Guide — monthly workflow

## Roles
- **Viewer** (partners/board): dashboard, payroll sheet, flags, employees, reconciliation, downloads.
- **HR**: + import, corrections, employee master, holiday lists, finalise.
- **Admin**: + policy rules, security settings, users, re-open a finalised month.

## Signing in
User name + password — that's all (no second step since v1.10). A new user (or one whose password an admin reset)
signs in with the temporary password and is asked to set their own straight away. Change your password any time:
your initials (top right) → **Change password**. Five wrong passwords lock the account for 15 minutes (an admin can
unlock it on the Users page).

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
- On an employee's page, click **legend items** under the calendar (several at once) to show only those days, e.g. Late + Regularised.
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
- **CSV** downloads the rows you see (filter, search and sort as on screen). If columns are hidden it asks: *Columns
  shown* or *All columns*. Every CSV has **Employee No** and **Name** in separate columns, plain numbers (units in the
  header), dates as yyyy-mm-dd and opens directly in Excel.
- **Print** (Ctrl+P) prints the table only, headed with the firm, month (Draft / Final) and who printed it.

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
3. **Review queue**: work through the two kinds of exception that need a review (WFH above the monthly limit, Group
   not set). Mark each one reviewed; pick a reason or type a note.
4. Click an employee to open the **drill-down**: calendar, every punch with place, flags, leave this month. Correct if needed:
   - *Click the day* in the calendar: change day type, session status (P, A, PL, COF…) or hours.
   - *Corrections tab, Adjust a figure*: e.g. grant an exception (Exempt this month = yes), set required hours, override
     deduction days, or a remark. **A reason is mandatory**; everything is in the Audit log and highlighted in Excel.
5. **Payroll leave sheet** → *Finalise for payroll* (locks the month) → **Download Excel** for payroll.
   Columns E–S mirror the old MIS sheet; extra columns give deduction days, leave / comp-off / unapplied leave and comp-off
   earned. Leave balances stay in greytHR — apply the deduction days there.
6. Partners and the board use the app itself (give them the *viewer* role): Overview, Insights, Locations and every
   drill-down, with sorting, filters and CSV on every table.

## Permanent employee settings (Employees → name)
Group, location, DOJ/DOL, weekly-off pattern (greytHR / standard 1st-Saturday / all Saturdays off / Sunday only /
custom weekdays + working Saturdays), required hours per day (e.g. 8 instead of 8.5) (holidays follow the category),
exempt from the hours-shortfall deduction (+ reason; WFH over the limit is still deducted). Changes recompute all
draft months.

## Parallel run (first months)
Upload your manual MIS workbook too. **Reconciliation** lists every difference between your sheet and the app.
For October 2026 (v1.0 rules): 91 of 97 identical once the employee settings were entered; the 6 differences are
explained in `docs/DATA_ANALYSIS.md`. Rules added since (WFH on own off-days, closed Saturdays, ½-day WFH deduction,
categories) intentionally differ from the manual sheet; Reconciliation labels those rows *app rule*.

## Rules & calendars
Policy numbers (8.5 h/day, 4 h block = ½ day, WFH limit 2, comp-off ≥ 5 h, late thresholds, exempt groups),
greytHR status-code meanings, shift timings and holiday calendars (default + special). Changes apply to draft months
only. Place names need no setup: they come from the built-in map of India (v1.6.2).

## A holiday for some categories only
Rules & calendars → *Holiday calendars* → **Holiday for several categories**: pick the date and name, tick the
categories (and *Optional* if it is one). It is added to each ticked category's own list and draft months are
recalculated. Holidays go by category only — to give someone a holiday, their category's list must have it.

## Work from home on a day off
WFH is counted only on a person's own working days. If someone signs in "Work from Home" on their weekly off or a
holiday, the day is shown as off-day work (and can earn a comp-off) with a note — it does not count towards the WFH
limit.

## Extra WFH allowed by a manager / partner
Review queue → *WFH above monthly limit* → **Approve WFH**: enter how many extra days were approved and by whom.
Only the remaining extra days are deducted; the payroll Excel you download afterwards shows the new deduction and the
approval (Corrections sheet). *Edit WFH approval* changes it; 0 removes it.

## WFH on a closed Saturday
The branches open only on the 1st Saturday (Rules & calendars → Work from home → *Saturdays the office is open*).
Someone whose Saturday is a working day and who works from home on another Saturday has no choice: the day shows as
WFH with a note and in Punch exceptions (*WFH — office closed*), but it does not count towards the 2-day limit.

## WFH over the limit
Each WFH day above 2 in a month takes ½ day of leave (Rules & calendars → Work from home; 0 = only flag it). It
appears in the employee's *Leave deduction*, the review queue and the payroll Excel (*Payroll Input* shows it
separately from the hours part). It applies to
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
Every list (late, regularisations, unapplied leave, WFH, hours) includes everyone it applies to. Choose
**Top 5 / 10 / 20 / All** (remembered for all lists) or type a name or employee number in the list's search box; the
number on the left is the person's real rank. *All groups* ▾ keeps only the groups you tick; the footer totals the people
matching (v1.13).

## Punch exceptions
Sidebar → **Punch exceptions**: every day someone has an incorrect punch (times changed, rejected sign-in), a regularisation, WFH (over the
limit, on a day off), was more than an hour late, left early, had a short day, had unapplied leave or worked a day off — one
row per person and day with all issues, punches and reasons. Click a chip (e.g. *Regularisation request*) to see only those;
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
4. A one-off holiday for some categories (e.g. a Delhi festival): Rules & calendars → *Holiday for several
   categories* — tick the categories; or put several categories, comma-separated, in a holiday-file row.

## Waive the hours deduction (v1.8)
Review queue → *Hours shortfall → leave deduction* → **Waive**: when a partner lets someone off the hours shortfall
for the month. Say who approved it (required). The hours part of the deduction is removed (WFH over the limit is
still deducted), the month recalculates and the Excel shows *Waived* in the *Exempt (hours)* column. Undo it on the
employee's page → *Corrections*. *Mark reviewed* on its own never changes a figure.

## The payroll Excel (v1.8)
Opens on **Contents** (the month's key totals and what each sheet is for). Give payroll the **Payroll Input** sheet
(v1.9): only people with a leave deduction, with the deduction split into **hours shortfall** and **WFH over the
limit**, the total, and a remark that says why in words (e.g. "WFH over the limit: 4 WFH days, limit 2 → 2 extra ×
0.5 = 1 day"). *Comp-off Ledger*: comp-off earned and taken per person. *Payroll Leave Sheet* is the full MIS layout
with live formulas. Every sheet prints on landscape pages with the month and status in the footer.

## Import templates (v1.8)
Imports → templates: drop-downs list the values the app knows, dates are dd-mm-yyyy, the Instructions sheet explains
every column. The **holiday template** (v1.9) has only Date, Day, Holiday, Category, Optional and comes pre-filled
with the lists in the app from this year on — change the dates for next year and upload it. (The leave-opening
template was removed: leave balances are kept in greytHR.)

## Leave balances (v1.9)
The app does not keep leave or comp-off balances — greytHR does. Each month it gives the **leave deduction** to apply
(hours shortfall + WFH over the limit), and shows leave (incl. LOP), comp-off and unapplied leave as marked in greytHR. The employee page
→ *Leave & comp-off* shows this month's leave, the deduction split and comp-off earned / taken.

## Opening the app (v1.9.2)
Sign in to the server (Remote Desktop) and double-click **HSCO Attendance** on the desktop. The app is always running
in the background on the server — there is no window to keep open or close.

## Deleting an upload (v1.11)
Uploaded the wrong file? Import files → **Import history** → the bin icon on that upload. The panel says what will be
removed — the file's attendance days / punches / summary rows for that month, the holidays it put in the lists, or the
employee details it changed (put back as they were) — then give a reason and confirm. The month is recalculated at
once; with no attendance left it shows as waiting for files again. Upload the right file afterwards. Not possible in
a finalised month (re-open it first).

## Review queue (v1.12)
Only two kinds of exception wait for you: **WFH above monthly limit** (approve extra WFH or mark reviewed) and
**Group not set** (set the group on the employee's page, then mark reviewed). Everything else — hours shortfall,
lateness, regularisations, … — is reviewed automatically; you still see it on each employee's page and in the Excel.
To waive someone's hours-shortfall deduction: their page → *Leave & comp-off* → **Waive**.

## Restricted holidays (RH)
When greytHR's muster shows **RH** for a day, that day is the person's holiday: not absent, nothing deducted, and if
they worked anyway, no comp-off is earned for it.

## Moving the data folder (v1.12, admins)
Rules & calendars → *Security & system* → **Data folder** shows where everything is kept and how big it is. To move it
(e.g. to a bigger drive): type a new or empty folder such as `D:\HSCOData` → **Move the data here** → confirm. The app
copies and checks everything and restarts on the new folder within a few seconds — reload the page. The old folder is
left untouched with a MOVED.txt note; delete it once you are happy.

## Places show as numbers (coordinates)?
Rules & calendars → *Place names*: a red warning means the built-in map of India is missing on the server (installs
before v1.12.1). Install the update from the banner; after the restart every punch gets its place name — nothing to
re-import.

## What's new in v1.13 — find the people behind every number
- **Click a number to see who.** On the Overview click a *Needs attention* type, any bar of a chart (a day of *Daily
  attendance* and its colour, a half hour of *First punch*, a weekday, a tenure band), a row of *Where people punched*
  or of *By group*; on Insights a bar of *Length of the working day* or *Groups side by side*, a number of *When people
  arrive* or a row of *Groups compared*. A panel lists exactly those people with their figure — search it, Filter →
  Group, sort, download CSV; the total is at the bottom. *Needs attention* also offers "Show these people on the payroll
  leave sheet". Trend charts open the month you click.
- **Hover a bar** for the date with its weekday (e.g. "Thu 9 Oct 2026"), every series and the total.
- **"i"** next to a figure or title explains how it is calculated — hover it, or tap it on a touch screen.
- **Ranked lists** (late, regularisations, WFH, hours, comp-offs, unapplied leave): *All groups* ▾ to keep one or
  several groups; the footer shows how many match and their total.
- **PDF** next to *Excel* on the Overview, Insights, Payroll leave sheet, Punch exceptions and Locations: every tab of the
  page in one file (charts in colour, cards, lists and tables as shown — filters you set are kept), A4 landscape with
  page numbers. It is made in your browser; nothing is sent anywhere.
- **Work location**: where people punched as a ranked list (punches, share, people); the WFH card counts WFH days, the
  people who worked from home and who is over the limit — *Everyone* / *Over the limit*.
- **Wording**: absence without leave applied is now **Unapplied leave (absence)**. **LOP is gone** — greytHR's LOP / LWP
  days count as leave. **Missing punches** and **present without any punch** are no longer reported.
- **OTHERS**: employees whose group is OTHERS are left out of the app completely (Rules & calendars → *Groups ignored by
  the app* to change the list). Employees → "N employees in OTHERS are ignored · Show them".

## PDF (v1.13.1)
Click **PDF ▾** (next to Excel) on the Overview, Insights, Payroll leave sheet, Punch exceptions or Locations:
- **This page** — every tab of the page, with the filters / search you set on screen.
- **Full report** — everything for the month in one file: a cover with contents (click a line to jump there), then the
  Overview, Insights, Payroll leave sheet, Review queue, Punch exceptions, Locations and Reconciliation. It takes about
  10 seconds; the page shows "Preparing the PDF…" meanwhile. The PDF is made in your browser — nothing leaves the server.
