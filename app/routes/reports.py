"""Downloads: payroll Excel workbook, punches & locations workbook, import templates."""
from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select

from app.models import Employee, HolidayCalendar, Period
from app.reports.excel import build_locations_workbook, build_workbook
from app.routes.periods import get_p
from app.services.audit import audit
from app.web import Ctx, hr, render, viewer

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


PRODUCT = "Attendance MIS"      # every downloaded file starts with the product name (owner, v1.14)


def _fname(p: Period, ext: str) -> str:
    return f"{PRODUCT} - {p.label} - {'FINAL' if p.status == 'FINALISED' else 'DRAFT'}.{ext}"


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
        "Content-Disposition": f'attachment; filename="{PRODUCT} - Punches and locations - {p.label}.xlsx"'})


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
        emps = list(c.db.scalars(select(Employee).order_by(Employee.emp_no)))
        cat_names = sorted({x.name for x in c.db.scalars(select(Category))} | {e.category for e in emps if e.category})
        rows = [[e.emp_no, e.name, e.group, e.category, e.additional_category, e.location, e.weekly_off,
                 e.working_saturdays, e.hours_per_day, "Yes" if e.exempt else "No", e.exempt_reason,
                 e.doj, e.dol, e.email, e.department, e.designation]
                for e in emps]
        data = _wb(["Employee No", "Name", "Group", "Category", "Additional Category", "Location", "Weekly Off Pattern",
                    "Working Saturdays", "Hours Per Day", "Exempt From Deduction", "Exempt Reason",
                    "Date of Joining", "Date of Leaving", "Email", "Department", "Designation"], rows,
                   "Employee master import.\nCategory: greytHR employee category (decides holiday list, weekly offs and "
                   "hours per day unless set on the employee).\n"
                   "Weekly Off Pattern: CATEGORY (follow the category), MUSTER (follow greytHR), STD_FIRST_SAT, "
                   "ALL_SAT_OFF, SUN_ONLY or CUSTOM (set off weekdays in the app).\nWorking Saturdays: e.g. 1 or 1,3 "
                   "(CUSTOM only).\n"
                   "Hours Per Day: blank = policy default (8.5).\nExempt From Deduction: Yes/No — the hours-shortfall "
                   "deduction only (WFH over the limit is still deducted).\n"
                   "Holidays follow the Category (its holiday list in Rules & calendars).\nBlank cells leave existing values unchanged.\n"
                   "Dates: dd-mm-yyyy (e.g. 01-04-2024) or an Excel date. The drop-downs list the values the app knows; "
                   "a new category or group can still be typed in.",
                   lists={"Group": sorted({e.group for e in emps if e.group}), "Category": cat_names,
                          "Additional Category": cat_names, "Location": sorted({e.location for e in emps if e.location}),
                          "Weekly Off Pattern": ["CATEGORY", "MUSTER", "STD_FIRST_SAT", "ALL_SAT_OFF", "SUN_ONLY", "CUSTOM"],
                          "Exempt From Deduction": ["Yes", "No"]},
                   dates=("Date of Joining", "Date of Leaving"), width={"Name": 28, "Email": 28, "Exempt Reason": 24})
    elif name == "holidays":
        # owner (v1.9): Date, Day, Holiday, Category, Optional. Pre-filled with the lists in the app from this year on
        # (one row per holiday per list), so next year's list is the same file with new dates.
        from app.models import Category, Holiday
        cats = list(c.db.scalars(select(Category).order_by(Category.name)))
        by_cal: dict = {}
        for cat in cats:
            if cat.holiday_calendar_id:
                by_cal.setdefault(cat.holiday_calendar_id, []).append(cat.name)
        default = c.db.scalar(select(HolidayCalendar).where(HolidayCalendar.is_default.is_(True)))
        rows = []
        for h in c.db.scalars(select(Holiday).where(Holiday.day >= date(date.today().year, 1, 1)).order_by(Holiday.day)):
            names = by_cal.get(h.calendar_id) or ([""] if default and h.calendar_id == default.id else [])
            for cn in names:
                rows.append([h.day, h.day.strftime("%a"), h.name, cn, "Yes" if h.optional else "No"])
        rows.sort(key=lambda r: (r[3], r[0]))
        data = _wb(["Date", "Day", "Holiday", "Category", "Optional"], rows,
                   "Holiday list import — holidays go by category only.\n"
                   "Date: dd-mm-yyyy or an Excel date. Day (optional): checked against the date.\n"
                   "Holiday: the name. Category: whose list it is (e.g. HSCO (Mum)); several categories separated by commas put the holiday in each of their lists (e.g. a Delhi-only festival); blank = the default list (people whose category has no list of its own).\n"
                   "Optional: Yes = an optional holiday (a working day unless greytHR marks the person's day H), else No.\n"
                   "A full year for a category (5+ holidays) replaces that year of its list, so next year's list is simply this file with next year's dates. Fewer rows are added without removing anything.\n"
                   "This file is pre-filled with the lists in the app from this year on.\n"
                   "\n"
                   "Examples:\n"
                   "  15-08-2027 | Sun | Independence Day | HSCO (Mum) | No\n"
                   "  07-11-2027 | Sun | Chhath Puja | HSCO (Delhi), HSCO (CBV) | No   <- two categories\n"
                   "  10-09-2027 | Fri | Ganesh Chaturthi | HSCO (Mum) | Yes   <- optional",
                   lists={"Category": [x.name for x in cats], "Optional": ["Yes", "No"]},
                   dates=("Date",), width={"Holiday": 30, "Category": 30})
    else:
        raise HTTPException(404, "Unknown template")
    nice = {"employee_master": "Employee master", "holidays": "Holiday list"}.get(name, name.replace("_", " ").capitalize())
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{PRODUCT} - {nice} template.xlsx"'})
