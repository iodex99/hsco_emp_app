"""Downloads: payroll Excel workbook, punches & locations workbook, import templates."""
from __future__ import annotations

import io

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


def _wb(headers: list[str], rows: list[list], note: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        ws.column_dimensions[c.column_letter].width = max(14, len(h) + 2)
    for r in rows:
        ws.append(r)
    ws2 = wb.create_sheet("Instructions")
    for line in note.split("\n"):
        ws2.append([line])
    ws2.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/exports/templates/{name}.xlsx")
def template(name: str, c: Ctx = Depends(hr)):
    if name == "employee_master":
        cals = {x.id: x.name for x in c.db.scalars(select(HolidayCalendar))}
        rows = [[e.emp_no, e.name, e.group, e.location, e.weekly_off, e.working_saturdays, e.hours_per_day,
                 "Yes" if e.exempt else "", e.exempt_reason, cals.get(e.holiday_calendar_id, ""), e.doj, e.dol,
                 e.email, e.department, e.designation]
                for e in c.db.scalars(select(Employee).order_by(Employee.emp_no))]
        data = _wb(["Employee No", "Name", "Group", "Location", "Weekly Off Pattern", "Working Saturdays", "Hours Per Day",
                    "Exempt From Deduction", "Exempt Reason", "Holiday Calendar", "Date of Joining", "Date of Leaving",
                    "Email", "Department", "Designation"], rows,
                   "Employee master import.\nWeekly Off Pattern: MUSTER (follow greytHR), STD_FIRST_SAT, ALL_SAT_OFF, "
                   "SUN_ONLY or CUSTOM (set off weekdays in the app).\nWorking Saturdays: e.g. 1 or 1,3 (CUSTOM only).\n"
                   "Hours Per Day: blank = policy default (8.5).\nExempt From Deduction: Yes/No.\n"
                   "Holiday Calendar: name of a calendar created in Rules & calendars.\nBlank cells leave existing values unchanged.")
    elif name == "holidays":
        data = _wb(["Date", "Holiday", "Calendar", "For Locations", "For Groups", "For Employees"], [],
                   "Holiday list import. Date as dd-mm-yyyy or an Excel date.\n"
                   "Calendar = calendar name (created automatically; blank = 'Standard') — the holiday is for everyone "
                   "on that calendar.\n"
                   "Targeted holiday: fill For Locations and/or For Groups (comma-separated; both = people matching "
                   "both) and/or For Employees (employee numbers or exact names; always included). Calendar is then "
                   "ignored for that row.")
    elif name == "leave_opening":
        latest = c.db.scalar(select(Period).where(Period.computed_at.is_not(None)).order_by(Period.year.desc(), Period.month.desc()))
        rows = []
        if latest:
            for r in c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id).order_by(MonthResult.emp_no)):
                rows.append([r.emp_no, r.name, None, None, None])
        data = _wb(["Employee No", "Name", "Leave Balance", "Comp Off Balance", "As Of"], rows,
                   "Opening balances at the START of a month (go-live). 'As Of' = any date in that month, e.g. 01-10-2026; "
                   "or leave blank and choose the month on the Import page.\nLeave Balance = paid leave days available; "
                   "Comp Off Balance = comp-off days available.")
    else:
        raise HTTPException(404, "Unknown template")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}_template.xlsx"'})
