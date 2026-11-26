"""DB → engine → MonthResult (docs/BUSINESS_RULES.md). Leave balances are kept in greytHR, not here (v1.9)."""
from __future__ import annotations

from dataclasses import fields as dc_fields
from datetime import date, datetime
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.engine import model as M
from app.engine.compute import compute_month
from app.models import (
    DayOverrideRow, Employee, HolidayCalendar, MonthOverrideRow, MonthResult, MusterRow, Period, SwipeRow,
)
from app.services.attendance_stats import month_stats
from app.services.settings import get_policy

SCALARS = [f.name for f in dc_fields(M.EmployeeMonth) if f.name not in ("days", "flags", "overrides")]


def _ints(s: str) -> tuple:
    return tuple(int(x) for x in (s or "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit())


def engine_employee(e: Employee, holidays: Optional[dict], eff=None, optional: Optional[dict] = None) -> M.Employee:
    """eff = categories.Effective (what applies after the category rules); without it the employee's own settings."""
    pat = eff.weekly_off if eff else (e.weekly_off if e.weekly_off in M.PATTERNS else M.P_MUSTER)
    offs = eff.off_weekdays if eff else e.off_weekdays
    sats = eff.working_saturdays if eff else e.working_saturdays
    hpd = eff.hours_per_day if eff else e.hours_per_day
    return M.Employee(
        emp_no=e.emp_no, name=e.name, group=e.group or "", location=e.location or "", doj=e.doj, dol=e.dol,
        weekly_off=pat or M.P_MUSTER, off_weekdays=_ints(offs) or (5, 6),
        working_saturdays=_ints(sats), hours_per_day=hpd, exempt=bool(e.exempt),
        exempt_reason=e.exempt_reason or "", holidays=holidays,
        optional_holidays=optional or {}, category=e.category or "",
    )


def calendars_for_month(db: Session, year: int, month: int) -> tuple[dict, Optional[int], dict, set]:
    """({calendar id: {date: name}} of regular holidays, default calendar id, {calendar id: {date: name}} optional,
    ids of calendars that have any holiday in `year` — a list not loaded for that year yet is skipped, so greytHR's
    own holiday marks apply instead of turning every holiday into a working day)."""
    cals = db.scalars(select(HolidayCalendar)).all()
    out, opt, default_id, loaded = {}, {}, None, set()
    for c in cals:
        hs = [h for h in c.holidays if h.day.year == year and h.day.month == month]
        out[c.id] = {h.day: h.name for h in hs if not h.optional}
        opt[c.id] = {h.day: h.name for h in hs if h.optional}
        if any(h.day.year == year for h in c.holidays):
            loaded.add(c.id)
        if c.is_default:
            default_id = c.id
    return out, default_id, opt, loaded


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
        "late_sit": dd.is_late_sitting, "regularised": dd.is_regularised,
        "wfh": dd.is_wfh, "partial_wfh": dd.is_partial_wfh, "wfh_closed": dd.wfh_office_closed, "rh": dd.is_rh,
        "extra": dd.is_extra_work, "co_earned": dd.compoff_earned,
        "sandwich": dd.is_sandwich, "mobile_office": dd.mobile_office_swipes, "overridden": dd.overridden,
        "notes": dd.notes,
    }


def _weekly_off_history(db: Session, period: Period, employees: dict, policy) -> None:
    """Weekly-off evidence from the 3 months before (greytHR muster), for weekday slots a month cannot tell by itself
    — a holiday on the only 1st Saturday of the month (v1.14: a holiday on a weekly off is a weekly off)."""
    from app.engine.compute import greythr_weekly_offs
    keys = [((period.year * 12 + period.month - 1) - i) for i in (1, 2, 3)]
    ids = [p.id for p in db.scalars(select(Period)) if (p.year * 12 + p.month - 1) in keys]
    if not ids:
        return
    by_emp: dict = {}
    for r in db.execute(select(MusterRow.emp_no, MusterRow.day, MusterRow.s1, MusterRow.s2)
                        .where(MusterRow.period_id.in_(ids))):
        if r[0] in employees:
            by_emp.setdefault(r[0], {})[r[1]] = M.MusterDay(r[0], r[1], r[2] or "", r[3] or "")
    for emp_no, days in by_emp.items():
        offs, seen = greythr_weekly_offs(days, policy, set())
        employees[emp_no].off_slots_hint = {k: k in offs for k in seen}


def compute_period(db: Session, period: Period) -> dict:
    """Calculate one month. Months are independent (no balance carries forward), so nothing else is touched."""
    policy = get_policy(db)
    y, m = period.year, period.month
    from app.services.scope import ignored_emp_nos
    skip = ignored_emp_nos(db, policy)                 # ignored groups (OTHERS): never calculated (v1.13)
    emps_db = {e.emp_no: e for e in db.scalars(select(Employee)).all() if e.emp_no not in skip}
    cal_days, default_cal, cal_opt, cal_loaded = calendars_for_month(db, y, m)
    from app.services import categories as CAT
    cats = CAT.by_key(db)
    from app.services.hours_policies import assignments
    hp = assignments(db)                               # minimum-hours policies (v1.14)
    employees, effs = {}, {}
    for k, e in emps_db.items():
        eff = effs[k] = CAT.effective(e, cats, default_cal, cal_loaded, hp)
        cal_id = eff.calendar_id
        employees[k] = engine_employee(e, cal_days.get(cal_id) if cal_id else None, eff,
                                       cal_opt.get(cal_id) if cal_id else None)

    # plain rows, not ORM objects (thousands per month)
    muster = [M.MusterDay(*r) for r in db.execute(
        select(MusterRow.emp_no, MusterRow.day, MusterRow.s1, MusterRow.s2, MusterRow.in_time, MusterRow.out_time,
               MusterRow.work_min, MusterRow.shift, MusterRow.name).where(MusterRow.period_id == period.id))
              if r[0] not in skip]
    swipes = [M.Swipe(*r[:8], *(x or "" for x in r[8:])) for r in db.execute(
        select(SwipeRow.emp_no, SwipeRow.ts, SwipeRow.door, SwipeRow.location_type, SwipeRow.swipe_type, SwipeRow.lat,
               SwipeRow.lon, SwipeRow.place, SwipeRow.note, SwipeRow.reason, SwipeRow.approval, SwipeRow.device,
               SwipeRow.device_id).where(SwipeRow.period_id == period.id)) if r[0] not in skip]
    d_ovr = [M.DayOverride(o.emp_no, o.day, o.day_type or None, o.s1 or None, o.s2 or None, o.work_min, o.reason)
             for o in db.scalars(select(DayOverrideRow).where(DayOverrideRow.period_id == period.id))]
    m_ovr: dict = {}
    for o in db.scalars(select(MonthOverrideRow).where(MonthOverrideRow.period_id == period.id)):
        v = o.value
        if o.field not in ("remark", "exempt", "shortfall_waived"):
            try:
                v = float(v)
            except ValueError:
                continue
        m_ovr.setdefault(o.emp_no, {})[o.field] = v

    _weekly_off_history(db, period, employees, policy)
    results = compute_month(y, m, employees, muster, swipes, policy, d_ovr, m_ovr)
    from app.services.location_checks import flags_by_employee
    loc_flags = flags_by_employee(swipes, {k: e.name for k, e in emps_db.items()})

    db.execute(delete(MonthResult).where(MonthResult.period_id == period.id))
    for emp_no, r in results.items():
        data = {k: getattr(r, k) for k in SCALARS}
        data["category"] = (emps_db[emp_no].category or "") if emp_no in emps_db else ""
        data["additional_category"] = (emps_db[emp_no].additional_category or "") if emp_no in emps_db else ""
        data["calendar_from"] = effs[emp_no].calendar_from if emp_no in effs else ""
        data["hours_from"] = effs[emp_no].hours_from if emp_no in effs else ""
        for k, v in list(data.items()):
            if isinstance(v, float):
                data[k] = round(v, 4)
        data["overrides"] = r.overrides
        days = [_serialize_day(d) for d in r.days]
        data["_ins"] = month_stats(days)          # compact summary for Insights history/trends
        flags = [{"code": c, "severity": s, "message": msg} for c, s, msg in r.flags]
        flags.extend(loc_flags.get(emp_no, []))
        db.add(MonthResult(period_id=period.id, emp_no=emp_no, name=r.name, group=r.group, location=r.location,
                           category=(emps_db[emp_no].category or "") if emp_no in emps_db else "",
                           deduction_days=r.deduction_days, balance_hours=round(r.balance_hours, 4),
                           flag_count=sum(1 for f in flags if f["severity"] in ("critical", "high", "medium")),
                           data=data, flags=flags, days=days))
    period.computed_at = datetime.now()
    db.flush()
    summary = {"employees": len(results), "deductions": sum(1 for r in results.values() if r.deduction_days)}
    return summary
