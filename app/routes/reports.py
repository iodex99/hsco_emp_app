"""Downloads: payroll Excel workbook, punches & locations workbook, import templates."""
from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select

from app.models import Employee, HolidayCalendar, MonthResult, Period
from app.reports.excel import build_locations_workbook, build_workbook
from app.routes.periods import get_p
from app.services.audit import audit
from app.web import Ctx, hr, render, viewer

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fname(p: Period, ext: str) -> str:
    return f"Attendance_Leave_{p.year}-{p.month:02d}{'_FINAL' if p.status == 'FINALISED' else '_DRAFT'}.{ext}"


@router.get("/exports", response_class=HTMLResponse)
def exports_page(c: Ctx = Depends(viewer)):
    from app.services.locations import months_with_signins
    signin_ids = {p.id for p in months_with_signins(c.db)}
    return render(c, "exports.html", signin_ids=signin_ids)


@router.get("/exports/{key}.xlsx")
def export_xlsx(key: str, c: Ctx = Depends(viewer)):
    p = get_p(c, key)
    if not p.computed_at:
        raise HTTPException(404, "Month not computed yet")
    data = build_workbook(c.db, p)
    audit(c.db, c.user, "export.xlsx", "period", p.key, ip=c.ip)
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{_fname(p, "xlsx")}"'})


@router.get("/exports/{key}/locations.xlsx")
def export_locations(key: str, c: Ctx = Depends(viewer)):
    """Every punch of the month with its place — available as soon as the swipe export is in (no muster needed)."""
    from app.models import SwipeRow
    p = get_p(c, key)
    if c.db.scalar(select(SwipeRow.id).where(SwipeRow.period_id == p.id).limit(1)) is None:
        raise HTTPException(404, "No punches imported for this month")
    data = build_locations_workbook(c.db, p)
    audit(c.db, c.user, "export.locations", "period", p.key, ip=c.ip)
    return Response(data, media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="Punches_and_Locations_{p.year}-{p.month:02d}.xlsx"'})


def _wb(headers: list[str], rows: list[list], note: str, lists: dict | None = None, dates: tuple = (),
        width: dict | None = None) -> bytes:
    """An import template: header row frozen + filter, dd-mm-yyyy date columns, drop-downs for known values (a hidden
    'Lists' sheet holds them, so long lists work; other values can still be typed) and an Instructions sheet."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        ws.column_dimensions[c.column_letter].width = (width or {}).get(h, max(14, len(h) + 2))
    for r in rows:
        ws.append(r)
    last = ws.max_row + 500                                  # room for new rows
    for h in dates:
        col = get_column_letter(headers.index(h) + 1)
        for r in range(2, last + 1):
            ws[f"{col}{r}"].number_format = "dd-mm-yyyy"
    if lists:
        lst = wb.create_sheet("Lists")
        for k, (h, values) in enumerate(lists.items(), start=1):
            if not values:
                continue
            lc = get_column_letter(k)
            lst.cell(row=1, column=k, value=h)
            for n, v in enumerate(values, start=2):
                lst.cell(row=n, column=k, value=v)
            dv = DataValidation(type="list", formula1=f"=Lists!${lc}$2:${lc}${len(values) + 1}", allow_blank=True,
                                showErrorMessage=False)
            col = get_column_letter(headers.index(h) + 1)
            dv.add(f"{col}2:{col}{last}")
            ws.add_data_validation(dv)
        lst.sheet_state = "hidden"
    ws.freeze_panes = "C2" if len(headers) > 4 else "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(ws.max_row, 1)}"
    ws2 = wb.create_sheet("Instructions", 1)
    for line in note.split("\n"):
        ws2.append([line])
    ws2["A1"].font = Font(bold=True, size=12)
    ws2.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/exports/templates/{name}.xlsx")
def template(name: str, c: Ctx = Depends(hr)):
    if name == "employee_master":
        from app.models import Category
        cals = {x.id: x.name for x in c.db.scalars(select(HolidayCalendar))}
        emps = list(c.db.scalars(select(Employee).order_by(Employee.emp_no)))
        cat_names = sorted({x.name for x in c.db.scalars(select(Category))} | {e.category for e in emps if e.category})
        rows = [[e.emp_no, e.name, e.group, e.category, e.additional_category, e.location, e.weekly_off,
                 e.working_saturdays, e.hours_per_day, "Yes" if e.exempt else "No", e.exempt_reason,
                 cals.get(e.holiday_calendar_id, ""), e.doj, e.dol, e.email, e.department, e.designation]
                for e in emps]
        data = _wb(["Employee No", "Name", "Group", "Category", "Additional Category", "Location", "Weekly Off Pattern",
                    "Working Saturdays", "Hours Per Day", "Exempt From Deduction", "Exempt Reason", "Holiday Calendar",
                    "Date of Joining", "Date of Leaving", "Email", "Department", "Designation"], rows,
                   "Employee master import.\nCategory: greytHR employee category (decides holiday list, weekly offs and "
                   "hours per day unless set on the employee).\n"
                   "Weekly Off Pattern: CATEGORY (follow the category), MUSTER (follow greytHR), STD_FIRST_SAT, "
                   "ALL_SAT_OFF, SUN_ONLY or CUSTOM (set off weekdays in the app).\nWorking Saturdays: e.g. 1 or 1,3 "
                   "(CUSTOM only).\n"
                   "Hours Per Day: blank = policy default (8.5).\nExempt From Deduction: Yes/No — the hours-shortfall "
                   "deduction only (WFH over the limit is still deducted).\n"
                   "Holiday Calendar: name of a calendar created in Rules & calendars.\nBlank cells leave existing values unchanged.\n"
                   "Dates: dd-mm-yyyy (e.g. 01-04-2024) or an Excel date. The drop-downs list the values the app knows; "
                   "a new category or group can still be typed in.",
                   lists={"Group": sorted({e.group for e in emps if e.group}), "Category": cat_names,
                          "Additional Category": cat_names, "Location": sorted({e.location for e in emps if e.location}),
                          "Weekly Off Pattern": ["CATEGORY", "MUSTER", "STD_FIRST_SAT", "ALL_SAT_OFF", "SUN_ONLY", "CUSTOM"],
                          "Exempt From Deduction": ["Yes", "No"], "Holiday Calendar": sorted(cals.values())},
                   dates=("Date of Joining", "Date of Leaving"), width={"Name": 28, "Email": 28, "Exempt Reason": 24})
    elif name == "holidays":
        from app.models import Category
        cat_names = sorted(x.name for x in c.db.scalars(select(Category)))
        data = _wb(["Date", "Day", "Holiday", "Category", "Is Optional", "Calendar", "For Categories", "For Locations",
                    "For Groups", "For Employees"], [],
                   "Holiday list import (greytHR's Holiday List Importer files work as they are). Date as dd-mm-yyyy or "
                   "an Excel date; Day (optional) is checked against the date.\n"
                   "Category = the employee category whose list this is (e.g. HSCO (Mum)) — each category keeps its own "
                   "list; a full year (5+ holidays) replaces that year, so next year's list is simply uploaded again.\n"
                   "Is Optional: Yes / Optional = an optional holiday (a working day unless greytHR marks it H).\n"
                   "Calendar = a named calendar instead of a category (blank both = 'Standard').\n"
                   "For Categories: a one-off holiday only for these categories (targeted).\n"
                   "Targeted holiday: fill For Locations and/or For Groups (comma-separated; both = people matching "
                   "both) and/or For Employees (employee numbers or exact names; always included). Calendar is then "
                   "ignored for that row.\n\nExamples (type into the Data sheet):\n"
                   "  15-08-2027 | Sun | Independence Day | HSCO (Mum) | No" "\n"
                   "  02-10-2027 |  | Gandhi Jayanti |  | No |  | HSCO (Mum), Carlyle   <- targeted to two categories" "\n"
                   "  10-09-2027 |  | Ganesh Chaturthi |  | Yes |  |  | Mumbai   <- optional, Mumbai only",
                   lists={"Category": cat_names, "Is Optional": ["Yes", "No"],
                          "Calendar": sorted(x.name for x in c.db.scalars(select(HolidayCalendar)))},
                   dates=("Date",), width={"Holiday": 28, "For Categories": 24, "For Employees": 24})
    elif name == "leave_opening":
        latest = c.db.scalar(select(Period).where(Period.computed_at.is_not(None)).order_by(Period.year.desc(), Period.month.desc()))
        rows = []
        if latest:
            for r in c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id).order_by(MonthResult.emp_no)):
                rows.append([r.emp_no, r.name, None, None, date(latest.year, latest.month, 1)])
        data = _wb(["Employee No", "Name", "Leave Balance", "Comp Off Balance", "As Of"], rows,
                   "Opening balances at the START of a month (go-live). 'As Of' = any date in that month, e.g. 01-10-2026; "
                   "or leave blank and choose the month on the Import page.\nLeave Balance = paid leave days available; "
                   "Comp Off Balance = comp-off days available.\nDays, half days allowed (12 or 12.5). "
                   "Rows with no balance are skipped, so fill in only the people you have balances for.",
                   dates=("As Of",), width={"Name": 28})
    else:
        raise HTTPException(404, "Unknown template")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}_template.xlsx"'})
