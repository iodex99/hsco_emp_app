"""Monthly results (payroll leave sheet), employee drill-down, manual overrides, finalise / re-open."""
from __future__ import annotations

from datetime import date, datetime

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.engine.compute import MONTH_OVERRIDABLE
from app.engine.model import B_ABSENT, B_COMPOFF, B_LEAVE, B_PRESENT, HOLIDAY, WEEKLY_OFF, WORKDAY
from app.models import (
    DayOverrideRow, Employee, MonthOverrideRow, MonthResult, MusterRow, Period,
)
from app.services import metrics as MX
from app.services.audit import audit
from app.services.compute import previous_period, compute_period
from app.services import insights as INS
from app.services.settings import get_policy
from app.web import Ctx, admin, hr, redirect, render, viewer

router = APIRouter()

OVERRIDE_LABELS = {
    "total_week_days": "Total week days (E)", "holidays": "Holidays (F)", "leave_days": "Leave days (G)",
    "absent_days": "Absent days (H)", "wfh_days": "WFH days (J)", "total_hours": "Total hours present (K/O)",
    "extra_days": "Extra working days (L)", "extra_hours": "Holiday/off-day hours to exclude (P)",
    "required_hours": "Hours required (N)", "deduction_days": "Deduction days", "exempt": "Exempt this month (yes/no)",
    "remark": "Remark", "wfh_approved_days": "Extra WFH approved (days)",
    "shortfall_waived": "Hours deduction waived (yes/no)",
}


def get_p(c: Ctx, key: str) -> Period:
    try:
        y, m = (int(x) for x in key.split("-"))
    except ValueError:
        raise HTTPException(404, "Unknown period")
    p = c.db.scalar(select(Period).where(Period.year == y, Period.month == m))
    if p is None:
        raise HTTPException(404, "Period not found")
    return p


def _editable(p: Period):
    if p.status == "FINALISED":
        raise HTTPException(409, f"{p.label} is locked. An admin must unlock it before changes.")


@router.get("/periods")
def periods_index(c: Ctx = Depends(viewer)):
    """Bare /periods → the latest calculated month's payroll sheet (or the overview when there is none)."""
    from app.web import periods as all_periods
    ps = [p for p in all_periods(c.db) if p.computed_at]
    return redirect(f"/periods/{ps[0].key}" if ps else "/")


@router.get("/periods/{key}", response_class=HTMLResponse)
def period_results(key: str, f: str = "", c: Ctx = Depends(viewer)):
    """?f=<chip> pre-selects a quick filter; f=fl-<FLAG CODE> (from the Overview's "Needs attention") shows only the
    headcount with that exception (v1.13)."""
    p = get_p(c, key)
    if p.computed_at is None:
        from app.routes.pages import pending_month
        return pending_month(c, p)
    rows = MX.results_for(c.db, p, with_days=False)
    policy = get_policy(c.db)
    flag_emps: dict = {}
    for r in rows:
        for x in MX.live_flags(r.flags):            # (retired codes of older finalised months never shown)
            flag_emps.setdefault(x["code"], []).append(r.emp_no)
    from app.services.hours_policies import all_policies
    prev = previous_period(c.db, p)
    prev_kpis = MX.compute_kpis(c.db, prev, policy) if prev and prev.computed_at else None
    return render(c, "period.html", period=p, rows=rows, policy=policy, request_f=f, flag_emps=flag_emps, prev_kpis=prev_kpis,
                  flag_labels=MX.FLAG_LABELS, hp_items=all_policies(c.db))


@router.post("/periods/{key}/recompute")
def recompute(key: str, c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    s = compute_period(c.db, p)
    audit(c.db, c.user, "period.recompute", "period", p.key, s, c.ip)
    return redirect(f"/periods/{key}", f"Recomputed {s['employees']} employees.")


@router.post("/periods/{key}/checklist")
async def checklist(key: str, request: Request, c: Ctx = Depends(hr)):
    """Month-end checklist steps HR marks (v1.16, owner): step=greythr (corrections made in greytHR — confirm / undo)
    or step=download (the Excel / PDF downloaded from the checklist itself). JSON in, JSON out; audit-logged."""
    data = await request.json()
    return await anyio.to_thread.run_sync(_checklist, key, data, c)


def _checklist(key: str, data: dict, c: Ctx):
    p = get_p(c, key)
    step, action = str(data.get("step", "")), str(data.get("action", "done"))
    if step not in ("greythr", "download") or action not in ("done", "undo"):
        return JSONResponse({"error": "Unknown checklist step"}, status_code=400)
    cl = dict(p.checklist or {})
    if action == "undo":
        cl.pop(step, None)
    else:
        what = str(data.get("what", ""))[:10] if step == "download" else ""
        cl[step] = {"by": c.user.username, "at": datetime.now().isoformat(timespec="minutes"), **({"what": what} if what else {})}
    p.checklist = cl                         # a new dict, so the JSON column is saved
    audit(c.db, c.user, "period.checklist", "period", p.key, {"step": step, "action": action,
                                                              **({"what": cl[step].get("what")} if step in cl and step == "download" else {})}, c.ip)
    return {"ok": True, "checklist": cl}


@router.post("/periods/{key}/finalise")
def finalise(key: str, back: str = Form(""), c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    compute_period(c.db, p)
    p.status = "FINALISED"
    p.finalised_at = datetime.now()
    p.finalised_by = c.user.username
    audit(c.db, c.user, "period.finalise", "period", p.key, ip=c.ip)
    to = back if back.startswith("/") and not back.startswith(("//", "/\\")) else f"/periods/{key}"
    return redirect(to, f"{p.label} locked — imports and corrections are closed until an admin unlocks it.")


@router.post("/periods/{key}/reopen")
def reopen(key: str, reason: str = Form(...), c: Ctx = Depends(admin)):
    p = get_p(c, key)
    if not reason.strip():
        return redirect(f"/periods/{key}", "A reason is required to unlock the month.")
    p.status = "DRAFT"
    audit(c.db, c.user, "period.reopen", "period", p.key, {"reason": reason}, c.ip)
    return redirect(f"/periods/{key}", f"{p.label} unlocked — imports and corrections are open again.")


@router.post("/periods/{key}/delete")
def delete_month(key: str, reason: str = Form(""), confirm: str = Form(""), c: Ctx = Depends(admin)):
    """Remove a month imported by mistake (e.g. a typo'd date). Draft months only; reason required; audit-logged.
    Everything belonging to the month goes (imports, results, corrections, reviews). Other months are independent
    and are not touched."""
    from sqlalchemy import delete as sql_delete
    p = get_p(c, key)
    if p.status == "FINALISED":
        return redirect(f"/periods/{key}", "A locked month can't be deleted. Unlock it first.")
    if not reason.strip() or confirm.strip().lower() != p.label.lower():
        return redirect(f"/periods/{key}", f"To delete, give a reason and type the month name exactly ({p.label}).")
    label = p.label
    audit(c.db, c.user, "period.delete", "period", key, {"reason": reason.strip()}, c.ip)
    c.db.execute(sql_delete(Period).where(Period.id == p.id))      # rows of the month go with it (ON DELETE CASCADE)
    c.db.flush()
    return redirect("/", f"{label} deleted.")


@router.get("/periods/{key}/employee/{emp_no}", response_class=HTMLResponse)
def drill(key: str, emp_no: str, c: Ctx = Depends(viewer)):
    p = get_p(c, key)
    r = c.db.scalar(select(MonthResult).where(MonthResult.period_id == p.id, MonthResult.emp_no == emp_no))
    if r is None:
        raise HTTPException(404, "No result for this employee in this period")
    emp = c.db.get(Employee, emp_no)
    d_ovr = c.db.scalars(select(DayOverrideRow).where(DayOverrideRow.period_id == p.id, DayOverrideRow.emp_no == emp_no)
                         .order_by(DayOverrideRow.day)).all()
    m_ovr = c.db.scalars(select(MonthOverrideRow).where(MonthOverrideRow.period_id == p.id,
                                                        MonthOverrideRow.emp_no == emp_no)).all()
    history = MX.employee_history(c.db, emp_no)
    # calendar grid (Mon-first weeks)
    first = date(p.year, p.month, 1)
    pad = first.weekday()
    cells = [None] * pad + r.days
    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]
    if weeks and len(weeks[-1]) < 7:
        weeks[-1] += [None] * (7 - len(weeks[-1]))
    raw_status = {m.day.isoformat(): (m.s1, m.s2) for m in c.db.scalars(
        select(MusterRow).where(MusterRow.period_id == p.id, MusterRow.emp_no == emp_no))}
    order = list(c.db.scalars(select(MonthResult.emp_no).where(MonthResult.period_id == p.id).order_by(MonthResult.emp_no)))
    i = order.index(emp_no)
    prev_emp = order[i - 1] if i > 0 else None
    next_emp = order[i + 1] if i + 1 < len(order) else None
    return render(c, "employee_month.html", period=p, r=r, d=r.data, emp=emp, weeks=weeks, d_ovr=d_ovr, m_ovr=m_ovr,
                  prev_emp=prev_emp, next_emp=next_emp,
                  history=history, override_labels=OVERRIDE_LABELS, raw_status=raw_status,
                  day_types=[WORKDAY, WEEKLY_OFF, HOLIDAY], policy=get_policy(c.db),
                  status_codes=sorted(get_policy(c.db).status_map.keys()),
                  status_meaning=get_policy(c.db).status_map,
                  sc=INS.employee_scorecard(INS.insights(c.db, p, with_matrix=False), emp_no), retired=MX.RETIRED_FLAGS,
                  prev_key=f"{p.year - (p.month == 1)}-{(p.month - 2) % 12 + 1:02d}")


@router.post("/periods/{key}/employee/{emp_no}/day-override")
def add_day_override(key: str, emp_no: str, day: str = Form(...), day_type: str = Form(""), s1: str = Form(""),
                     s2: str = Form(""), work_hm: str = Form(""), reason: str = Form(...), c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    if not reason.strip():
        return redirect(f"/periods/{key}/employee/{emp_no}", "A reason is required for every manual change.")
    d = date.fromisoformat(day)
    if (d.year, d.month) != (p.year, p.month):
        return redirect(f"/periods/{key}/employee/{emp_no}", "Date is outside this period.")
    wm = None
    if work_hm.strip():
        try:
            h, m = work_hm.strip().split(":")
            wm = int(h) * 60 + int(m)
        except ValueError:
            return redirect(f"/periods/{key}/employee/{emp_no}", "Work hours must be HH:MM.")
    if day_type and day_type not in (WORKDAY, WEEKLY_OFF, HOLIDAY):
        return redirect(f"/periods/{key}/employee/{emp_no}", "Invalid day type.")
    o = c.db.scalar(select(DayOverrideRow).where(DayOverrideRow.period_id == p.id, DayOverrideRow.emp_no == emp_no,
                                                 DayOverrideRow.day == d))
    if o is None:
        o = DayOverrideRow(period_id=p.id, emp_no=emp_no, day=d, reason=reason)
        c.db.add(o)
    o.day_type, o.s1, o.s2, o.work_min, o.reason = day_type, s1.upper(), s2.upper(), wm, reason
    o.created_by = c.user.username
    o.created_at = datetime.now()
    c.db.flush()
    audit(c.db, c.user, "override.day", "employee", emp_no,
          {"period": p.key, "day": day, "day_type": day_type, "s1": s1, "s2": s2, "work": work_hm, "reason": reason}, c.ip)
    compute_period(c.db, p)
    pinned = [OVERRIDE_LABELS.get(o.field, o.field) for o in c.db.scalars(select(MonthOverrideRow).where(
        MonthOverrideRow.period_id == p.id, MonthOverrideRow.emp_no == emp_no))
        if o.field in ("total_week_days", "holidays", "leave_days", "absent_days", "wfh_days", "total_hours",
                       "extra_days", "extra_hours", "required_hours", "deduction_days")]
    msg = f"Day override saved for {d:%d-%b}."
    if pinned:
        msg += (" Note: this month also has fixed figures (" + ", ".join(pinned) + ") — they still override what the "
                "days add up to; remove them under Corrections if this day should count.")
    return redirect(f"/periods/{key}/employee/{emp_no}", msg)


@router.post("/periods/{key}/employee/{emp_no}/day-override/{oid}/delete")
def del_day_override(key: str, emp_no: str, oid: int, c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    o = c.db.get(DayOverrideRow, oid)
    if o and o.period_id == p.id:
        audit(c.db, c.user, "override.day_removed", "employee", emp_no, {"period": p.key, "day": o.day.isoformat()}, c.ip)
        c.db.delete(o)
        c.db.flush()
        compute_period(c.db, p)
    return redirect(f"/periods/{key}/employee/{emp_no}", "Day override removed.")


@router.post("/periods/{key}/employee/{emp_no}/month-override")
def add_month_override(key: str, emp_no: str, field: str = Form(...), value: str = Form(...), reason: str = Form(...),
                       c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    if field not in MONTH_OVERRIDABLE:
        return redirect(f"/periods/{key}/employee/{emp_no}", "Field cannot be overridden.")
    if not reason.strip():
        return redirect(f"/periods/{key}/employee/{emp_no}", "A reason is required for every manual change.")
    if field in ("exempt", "shortfall_waived"):
        value = "true" if value.strip().lower() in ("1", "y", "yes", "true") else "false"
    elif field != "remark":
        try:
            float(value)
        except ValueError:
            return redirect(f"/periods/{key}/employee/{emp_no}", "Value must be a number.")
    o = c.db.scalar(select(MonthOverrideRow).where(MonthOverrideRow.period_id == p.id, MonthOverrideRow.emp_no == emp_no,
                                                   MonthOverrideRow.field == field))
    if o is None:
        o = MonthOverrideRow(period_id=p.id, emp_no=emp_no, field=field, value=value, reason=reason)
        c.db.add(o)
    o.value, o.reason, o.created_by, o.created_at = value, reason, c.user.username, datetime.now()
    c.db.flush()
    audit(c.db, c.user, "override.month", "employee", emp_no, {"period": p.key, "field": field, "value": value,
                                                               "reason": reason}, c.ip)
    compute_period(c.db, p)
    return redirect(f"/periods/{key}/employee/{emp_no}", f"{OVERRIDE_LABELS.get(field, field)} overridden.")


@router.post("/periods/{key}/employee/{emp_no}/month-override/{oid}/delete")
def del_month_override(key: str, emp_no: str, oid: int, c: Ctx = Depends(hr)):
    p = get_p(c, key)
    _editable(p)
    o = c.db.get(MonthOverrideRow, oid)
    if o and o.period_id == p.id:
        audit(c.db, c.user, "override.month_removed", "employee", emp_no, {"period": p.key, "field": o.field}, c.ip)
        c.db.delete(o)
        c.db.flush()
        compute_period(c.db, p)
    return redirect(f"/periods/{key}/employee/{emp_no}", "Override removed.")


