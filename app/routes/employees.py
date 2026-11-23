"""Employee master: list, profile (history across months), create / edit with audit."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.engine.model import PATTERNS, WEEKDAY_NAMES
from app.models import Category, Employee, HolidayCalendar, MonthResult, Period
from app.services import categories as CAT
from app.services import metrics as MX
from app.services.audit import audit
from app.services.compute import compute_period
from app.services.settings import get_general, get_policy
from app.web import Ctx, hr, redirect, render, viewer

router = APIRouter()

FIELDS = ("name", "group", "location", "category", "additional_category", "department", "designation", "email",
          "weekly_off", "off_weekdays",
          "working_saturdays", "hours_per_day", "exempt", "exempt_reason", "doj", "dol",
          "is_active", "notes")


@router.get("/employees", response_class=HTMLResponse)
def employees(ignored: int = 0, c: Ctx = Depends(viewer)):
    from app.services.scope import ignored_emp_nos
    skip = ignored_emp_nos(c.db)
    allx = c.db.scalars(select(Employee).order_by(Employee.emp_no)).all()
    # ignored groups (OTHERS, v1.13) are not part of the app; ?ignored=1 lists only them (to check / correct a group)
    emps = [e for e in allx if (e.emp_no in skip) == bool(ignored)]
    last = {}
    latest = c.db.scalar(select(Period).where(Period.computed_at.is_not(None)).order_by(Period.year.desc(), Period.month.desc()))
    if latest:
        for r in c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id)):
            last[r.emp_no] = r
    cals = {cal.id: cal.name for cal in c.db.scalars(select(HolidayCalendar))}
    cats = CAT.by_key(c.db)
    dcal = CAT.default_calendar_id(c.db)
    eff = {e.emp_no: CAT.effective(e, cats, dcal) for e in emps}
    return render(c, "employees.html", emps=emps, last=last, latest=latest, patterns=PATTERNS, cals=cals, eff=eff,
                  n_ignored=len(skip), showing_ignored=bool(ignored), ignored_groups=get_policy(c.db).ignored_groups)


@router.get("/employees/new", response_class=HTMLResponse)
def new_employee(c: Ctx = Depends(hr)):
    return _form(c, Employee(emp_no="", weekly_off=CAT.FOLLOW, off_weekdays="5,6", is_active=True), new=True)


def _form(c: Ctx, e: Employee, new: bool = False, history=None):
    g = get_general(c.db)
    cals = c.db.scalars(select(HolidayCalendar).order_by(HolidayCalendar.name)).all()
    cats = CAT.by_key(c.db)
    # what the category would give (shown on the "Follow category" choices)
    probe = Employee(emp_no=e.emp_no, category=e.category, additional_category=e.additional_category,
                     weekly_off=CAT.FOLLOW, hours_per_day=None)
    via = CAT.effective(probe, cats, CAT.default_calendar_id(c.db))
    cal_names = {x.id: x.name for x in cals}
    return render(c, "employee_form.html", e=e, new=new, patterns=PATTERNS, cals=cals, groups=g.get("groups", []),
                  locations=g.get("locations", []), weekday_names=WEEKDAY_NAMES, history=history or [],
                  categories=sorted(x.name for x in c.db.scalars(select(Category))), via=via,
                  via_cal=cal_names.get(via.calendar_id, "greytHR's holiday marks"), follow=CAT.FOLLOW)


@router.get("/employees/{emp_no}", response_class=HTMLResponse)
def employee(emp_no: str, c: Ctx = Depends(viewer)):
    e = c.db.get(Employee, emp_no)
    if e is None:
        raise HTTPException(404, "Employee not found")
    return _form(c, e, history=MX.employee_history(c.db, emp_no))


def _parse_date(s: str):
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


@router.post("/employees/save")
def save_employee(emp_no: str = Form(...), name: str = Form(""), group: str = Form(""), location: str = Form(""),
                        category: str = Form(""), additional_category: str = Form(""), cat_fields: str = Form(""),
                        department: str = Form(""), designation: str = Form(""), email: str = Form(""),
                        weekly_off: str = Form("MUSTER"), off_weekdays: list[str] = Form([]),
                        working_saturdays: str = Form(""), hours_per_day: str = Form(""), exempt: str = Form(""),
                        exempt_reason: str = Form(""), doj: str = Form(""),
                        dol: str = Form(""), is_active: str = Form(""), notes: str = Form(""), new: str = Form(""),
                        c: Ctx = Depends(hr)):
    emp_no = emp_no.strip().upper()
    if not emp_no:
        return redirect("/employees/new", "Employee number is required.")
    e = c.db.get(Employee, emp_no)
    if new and e is not None:
        return redirect(f"/employees/{emp_no}", "That employee number already exists.")
    if e is None:
        e = Employee(emp_no=emp_no, source="manual")
        c.db.add(e)
    before = {f: str(getattr(e, f, "")) for f in FIELDS}
    try:
        e.hours_per_day = float(hours_per_day) if hours_per_day.strip() else None
        e.doj, e.dol = _parse_date(doj), _parse_date(dol)
    except ValueError:
        return redirect(f"/employees/{emp_no}", "Invalid number or date.")
    if weekly_off not in PATTERNS and weekly_off != CAT.FOLLOW:
        weekly_off = CAT.FOLLOW
    e.name, e.group, e.location = name.strip(), group.strip(), location.strip()
    cache = CAT.by_key(c.db)
    for fld, val in (("category", category), ("additional_category", additional_category)):
        # the employee form says it carries both fields (cat_fields=1), so a blank clears; anything else posting
        # without them keeps what the employee has
        if not cat_fields and not val.strip():
            continue
        cat = CAT.ensure(c.db, val, cache) if val.strip() else None      # loosely matched; new names are added
        setattr(e, fld, cat.name if cat else "")
    e.department, e.designation, e.email = department.strip(), designation.strip(), email.strip()
    e.weekly_off = weekly_off
    e.off_weekdays = ",".join(sorted({x for x in off_weekdays if x.isdigit() and 0 <= int(x) <= 6})) or "5,6"
    e.working_saturdays = ",".join(x for x in working_saturdays.replace(" ", "").split(",") if x in "12345" and x)
    e.exempt = bool(exempt)
    e.exempt_reason = exempt_reason.strip()
    e.is_active = bool(is_active) and e.dol is None
    e.notes = notes
    after = {f: str(getattr(e, f, "")) for f in FIELDS}
    changes = {k: [before[k], after[k]] for k in FIELDS if before[k] != after[k]}
    audit(c.db, c.user, "employee.create" if new else "employee.update", "employee", emp_no, changes, c.ip)
    c.db.flush()
    for p in c.db.scalars(select(Period).where(Period.status == "DRAFT", Period.computed_at.is_not(None))
                          .order_by(Period.year, Period.month)):
        compute_period(c.db, p)
        c.db.commit()          # one month per transaction
    return redirect(f"/employees/{emp_no}", "Employee saved and draft months recomputed.")
