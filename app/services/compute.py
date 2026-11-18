"""DB → engine → MonthResult, plus the leave & comp-off ledgers (docs/BUSINESS_RULES.md §4-5)."""
from __future__ import annotations

from dataclasses import fields as dc_fields
from datetime import date, datetime
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.engine import model as M
from app.engine.compute import SEV_HIGH, SEV_MEDIUM, compute_month
from app.models import (
    DayOverrideRow, Employee, HolidayCalendar, LeaveOpening, MonthOverrideRow, MonthResult, MusterRow, Period, SwipeRow,
)
from app.services.attendance_stats import month_stats
from app.services.settings import get_policy

SCALARS = [f.name for f in dc_fields(M.EmployeeMonth) if f.name not in ("days", "flags", "overrides")]


def _ints(s: str) -> tuple:
    return tuple(int(x) for x in (s or "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit())


def engine_employee(e: Employee, holidays: Optional[dict], targeted: Optional[dict] = None) -> M.Employee:
    return M.Employee(
        emp_no=e.emp_no, name=e.name, group=e.group or "", location=e.location or "", doj=e.doj, dol=e.dol,
        weekly_off=e.weekly_off or M.P_MUSTER, off_weekdays=_ints(e.off_weekdays) or (5, 6),
        working_saturdays=_ints(e.working_saturdays), hours_per_day=e.hours_per_day, exempt=bool(e.exempt),
        exempt_reason=e.exempt_reason or "", holidays=holidays, targeted_holidays=targeted or {},
    )


def calendars_for_month(db: Session, year: int, month: int) -> tuple[dict, Optional[int]]:
    cals = db.scalars(select(HolidayCalendar)).all()
    out, default_id = {}, None
    for c in cals:
        out[c.id] = {h.day: h.name for h in c.holidays if h.day.year == year and h.day.month == month}
        if c.is_default:
            default_id = c.id
    return out, default_id


def get_period(db: Session, year: int, month: int, create: bool = False) -> Optional[Period]:
    p = db.scalar(select(Period).where(Period.year == year, Period.month == month))
    if p is None and create:
        p = Period(year=year, month=month)
        db.add(p)
        db.flush()
    return p


def previous_period(db: Session, p: Period) -> Optional[Period]:
    y, m = (p.year, p.month - 1) if p.month > 1 else (p.year - 1, 12)
    return get_period(db, y, m)


def fy_of(year: int, month: int, fy_start: int) -> int:
    return year if month >= fy_start else year - 1


def prorata_entitlement(doj: Optional[date], year: int, month: int, policy: M.Policy) -> float:
    """Entitlement credited for the FY containing (year, month) for someone joining on doj."""
    fy = fy_of(year, month, policy.fy_start_month)
    fy_start = date(fy, policy.fy_start_month, 1)
    if doj is None or doj <= fy_start:
        return policy.annual_leave_days
    months_elapsed = (doj.year - fy_start.year) * 12 + (doj.month - fy_start.month)
    remaining = max(0, 12 - months_elapsed)
    return round(policy.annual_leave_days * remaining / 12 * 2) / 2


def _serialize_day(dd: M.DayDetail) -> dict:
    hm = lambda t: t.strftime("%H:%M") if t else ""  # noqa: E731
    return {
        "date": dd.day.isoformat(), "wd": dd.weekday, "type": dd.day_type, "s1": dd.s1, "s2": dd.s2,
        "b1": dd.b1, "b2": dd.b2, "in": hm(dd.in_time), "out": hm(dd.out_time),
        "out_next_day": bool(dd.out_time and dd.out_time.date() > dd.day),
        "work_min": dd.work_min, "shift": dd.shift, "holiday": dd.holiday_name,
        "swipes": [{"t": s.ts.strftime("%H:%M:%S"), "type": s.swipe_type, "loc": s.location_type, "door": s.door,
                    "place": s.place, "lat": s.lat, "lon": s.lon, "note": s.note, "reason": s.reason,
                    "device": s.device} for s in dd.swipes],
        "locs": dd.location_types, "late_min": dd.late_min,
        "late": dd.is_late, "very_late": dd.is_very_late, "early": dd.is_early_exit, "short": dd.is_short_day,
        "late_sit": dd.is_late_sitting, "missing": dd.is_missing_punch, "regularised": dd.is_regularised,
        "wfh": dd.is_wfh, "partial_wfh": dd.is_partial_wfh, "extra": dd.is_extra_work, "co_earned": dd.compoff_earned,
        "sandwich": dd.is_sandwich, "mobile_office": dd.mobile_office_swipes, "overridden": dd.overridden,
        "notes": dd.notes,
    }


def compute_period(db: Session, period: Period, cascade: bool = True) -> dict:
    policy = get_policy(db)
    y, m = period.year, period.month
    emps_db = {e.emp_no: e for e in db.scalars(select(Employee)).all()}
    cal_days, default_cal = calendars_for_month(db, y, m)
    from app.services import holidays as TH
    targeted = TH.by_employee(TH.for_month(db, y, m), emps_db.values())
    employees = {}
    for k, e in emps_db.items():
        cal_id = e.holiday_calendar_id or default_cal
        employees[k] = engine_employee(e, cal_days.get(cal_id) if cal_id else None, targeted.get(k))

    # plain rows, not ORM objects (thousands per month)
    muster = [M.MusterDay(*r) for r in db.execute(
        select(MusterRow.emp_no, MusterRow.day, MusterRow.s1, MusterRow.s2, MusterRow.in_time, MusterRow.out_time,
               MusterRow.work_min, MusterRow.shift, MusterRow.name).where(MusterRow.period_id == period.id))]
    swipes = [M.Swipe(*r[:8], *(x or "" for x in r[8:])) for r in db.execute(
        select(SwipeRow.emp_no, SwipeRow.ts, SwipeRow.door, SwipeRow.location_type, SwipeRow.swipe_type, SwipeRow.lat,
               SwipeRow.lon, SwipeRow.place, SwipeRow.note, SwipeRow.reason, SwipeRow.approval, SwipeRow.device,
               SwipeRow.device_id).where(SwipeRow.period_id == period.id))]
    d_ovr = [M.DayOverride(o.emp_no, o.day, o.day_type or None, o.s1 or None, o.s2 or None, o.work_min, o.reason)
             for o in db.scalars(select(DayOverrideRow).where(DayOverrideRow.period_id == period.id))]
    m_ovr: dict = {}
    for o in db.scalars(select(MonthOverrideRow).where(MonthOverrideRow.period_id == period.id)):
        v = o.value
        if o.field not in ("remark", "exempt"):
            try:
                v = float(v)
            except ValueError:
                continue
        m_ovr.setdefault(o.emp_no, {})[o.field] = v

    results = compute_month(y, m, employees, muster, swipes, policy, d_ovr, m_ovr)
    from app.services.location_checks import flags_by_employee
    loc_flags = flags_by_employee(swipes, {k: e.name for k, e in emps_db.items()})

    # ledgers
    prev = previous_period(db, period)
    prev_res = {}
    if prev:
        prev_res = {e: d for e, d in db.execute(select(MonthResult.emp_no, MonthResult.data)
                                                .where(MonthResult.period_id == prev.id))}
    openings = {o.emp_no: o for o in db.scalars(select(LeaveOpening).where(LeaveOpening.year == y, LeaveOpening.month == m))}

    db.execute(delete(MonthResult).where(MonthResult.period_id == period.id))
    for emp_no, r in results.items():
        data = {k: getattr(r, k) for k in SCALARS}
        for k, v in list(data.items()):
            if isinstance(v, float):
                data[k] = round(v, 4)
        e = employees.get(emp_no)
        ledger = _ledger(r, e, y, m, policy, openings.get(emp_no), prev_res.get(emp_no))
        data.update(ledger)
        data["overrides"] = r.overrides
        days = [_serialize_day(d) for d in r.days]
        data["_ins"] = month_stats(days)          # compact summary for Insights history/trends
        flags = [{"code": c, "severity": s, "message": msg} for c, s, msg in r.flags]
        if ledger.get("leave_opening") is None:
            flags.append({"code": "NO_OPENING_BALANCE", "severity": "info",
                          "message": "Leave opening balance not set — closing balance unknown"})
        if (ledger.get("salary_deduction_days") or 0) > 0:
            flags.append({"code": "LEAVE_EXHAUSTED", "severity": SEV_HIGH,
                          "message": f"Leave balance exhausted — {ledger['salary_deduction_days']:g} day(s) to be deducted from salary"})
        if ledger.get("compoff_overdraft"):
            flags.append({"code": "COMPOFF_OVERDRAWN", "severity": SEV_MEDIUM,
                          "message": f"Comp-off availed beyond balance by {ledger['compoff_overdraft']:g} day(s)"})
        flags.extend(loc_flags.get(emp_no, []))
        db.add(MonthResult(period_id=period.id, emp_no=emp_no, name=r.name, group=r.group, location=r.location,
                           deduction_days=r.deduction_days, balance_hours=round(r.balance_hours, 4),
                           flag_count=sum(1 for f in flags if f["severity"] in ("critical", "high", "medium")),
                           data=data, flags=flags, days=days))
    period.computed_at = datetime.now()
    db.flush()
    summary = {"employees": len(results), "deductions": sum(1 for r in results.values() if r.deduction_days)}
    if cascade:
        nxt = get_period(db, y + (m == 12), m % 12 + 1)
        if nxt and nxt.status == "DRAFT" and db.scalar(select(MusterRow.id).where(MusterRow.period_id == nxt.id).limit(1)):
            db.commit()        # this month is final; release the write lock before calculating the next one
            compute_period(db, nxt, cascade=True)
    return summary


def _ledger(r: M.EmployeeMonth, e: Optional[M.Employee], y: int, m: int, policy: M.Policy,
            opening: Optional[LeaveOpening], prev: Optional[dict]) -> dict:
    doj = e.doj if e else None
    fy_start = policy.fy_start_month
    credit = 0.0
    encash = None
    leave_open: Optional[float] = None
    if opening is not None and opening.leave_balance is not None:
        leave_open = float(opening.leave_balance)
    elif prev is not None and prev.get("leave_closing") is not None:
        leave_open = float(prev["leave_closing"])
        if m == fy_start:   # new FY: previous balance goes to encashment, no carry forward
            encash = leave_open
            leave_open = 0.0
    if opening is None or opening.leave_balance is None:
        if policy.leave_credit_mode == "monthly":
            if not doj or (doj.year, doj.month) <= (y, m):
                credit = round(policy.annual_leave_days / 12, 2)
                if leave_open is None and doj and (doj.year, doj.month) == (y, m):
                    leave_open = 0.0
        else:
            if m == fy_start and (not doj or doj <= date(y, m, 28)):
                credit = prorata_entitlement(doj, y, m, policy)
                if leave_open is None:
                    leave_open = 0.0
            elif doj and (doj.year, doj.month) == (y, m):
                credit = prorata_entitlement(doj, y, m, policy)
                leave_open = 0.0
    debit_pl = r.pl_days
    debit_short = r.deduction_days
    debit_absent = r.unauth_absent_days if policy.absent_debits_leave else 0.0

    # comp-off
    co_open: Optional[float] = None
    if opening is not None and opening.compoff_balance is not None:
        co_open = float(opening.compoff_balance)
    elif prev is not None and prev.get("compoff_closing") is not None:
        co_open = float(prev["compoff_closing"])
    co_overdraft = 0.0
    co_close = None
    if co_open is not None:
        avail = co_open + r.compoff_earned
        co_overdraft = max(0.0, r.cof_days - avail)
        co_close = max(0.0, avail - r.cof_days)
    debit_cof = co_overdraft if policy.cof_overdraft_to_leave else 0.0

    total_debit = debit_pl + debit_short + debit_absent + debit_cof
    salary_days = 0.0
    leave_close = None
    if leave_open is not None:
        bal = leave_open + credit - total_debit
        if bal < 0:
            salary_days = -bal
            bal = 0.0
        leave_close = round(bal, 2)
    lop_extra = co_overdraft if not policy.cof_overdraft_to_leave else 0.0
    return {
        "leave_opening": leave_open, "leave_credit": credit, "leave_debit_pl": debit_pl,
        "leave_debit_shortfall": debit_short, "leave_debit_absent": debit_absent, "leave_debit_compoff": debit_cof,
        "leave_debit_total": round(total_debit, 2), "leave_closing": leave_close,
        "salary_deduction_days": round(salary_days, 2), "encashment_days": encash,
        "compoff_opening": co_open, "compoff_closing": co_close, "compoff_overdraft": co_overdraft,
        "payroll_lop_days": round(r.lop_days + salary_days + lop_extra, 2),
    }
