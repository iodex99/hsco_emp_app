"""Monthly attendance / leave calculation engine.

Spec: docs/BUSINESS_RULES.md.  Pure functions: feed employees, muster days, swipes, overrides and a Policy;
get back one EmployeeMonth per employee with MIS figures, behaviour metrics, flags and day details.
"""
from __future__ import annotations

import math
import functools
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from .model import (
    B_ABSENT, B_COMPOFF, B_LEAVE, B_LOP, B_OFF, B_PRESENT, HOLIDAY, P_ALL_SAT_OFF, P_CUSTOM, P_MUSTER,
    P_STD_FIRST_SAT, P_SUN_ONLY, WEEKLY_OFF, WORKDAY, DayDetail, DayOverride, Employee, EmployeeMonth,
    REJECTED_APPROVALS, MusterDay, Policy, Swipe,
)

SEV_CRITICAL, SEV_HIGH, SEV_MEDIUM, SEV_INFO = "critical", "high", "medium", "info"

MONTH_OVERRIDABLE = (
    "total_week_days", "holidays", "leave_days", "absent_days", "wfh_days", "total_hours",
    "extra_days", "extra_hours", "required_hours", "deduction_days", "exempt", "remark",
)

_TIME_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*:?\s*(am|pm)?", re.I)


def parse_shift(name: str, policy: Policy) -> tuple[time, time]:
    """'10:am-18:30pm' -> (10:00, 18:30); '9 am to 6pm (Carlyle)' -> (09:00, 18:00)."""
    if name and name in policy.shift_overrides:
        return policy.shift_overrides[name]
    if not name:
        return policy.default_shift_start, policy.default_shift_end
    hit = _shift_times(name)
    return hit if hit else (policy.default_shift_start, policy.default_shift_end)


@functools.lru_cache(maxsize=512)
def _shift_times(name: str) -> Optional[tuple[time, time]]:
    found = []
    for m in _TIME_RE.finditer(name):
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            found.append(time(h, mi))
        if len(found) == 2:
            break
    if len(found) == 2 and found[1] > found[0]:
        return found[0], found[1]
    return None


def nth_weekday_of_month(d: date) -> int:
    return (d.day - 1) // 7 + 1


def pattern_day_type(d: date, pattern: str, working_saturdays: Iterable[int] = (),
                     off_weekdays: Iterable[int] = (5, 6)) -> Optional[str]:
    """Return WORKDAY / WEEKLY_OFF according to the weekly-off pattern, or None for MUSTER."""
    wd = d.weekday()  # Mon=0 .. Sun=6
    if pattern == P_MUSTER:
        return None
    if pattern == P_CUSTOM:
        if wd in set(off_weekdays or ()):
            if wd == 5 and nth_weekday_of_month(d) in set(working_saturdays or ()):
                return WORKDAY
            return WEEKLY_OFF
        return WORKDAY
    if wd == 6:
        return WEEKLY_OFF
    if wd < 5:
        return WORKDAY
    # Saturday
    if pattern == P_SUN_ONLY:
        return WORKDAY
    if pattern == P_ALL_SAT_OFF:
        return WEEKLY_OFF
    if pattern == P_STD_FIRST_SAT:
        return WORKDAY if nth_weekday_of_month(d) == 1 else WEEKLY_OFF
    return None


def is_persistent_late(late_days: int, very_late_days: int, policy: Policy) -> bool:
    n = very_late_days if policy.persistent_late_basis == "very_late" else late_days
    return n >= policy.persistent_late_days


def deduction_for_shortfall(shortfall: float, policy: Policy) -> float:
    shortfall = round(shortfall, 4)
    if shortfall <= max(policy.shortfall_grace_hours, 0) or shortfall <= 0:
        return 0.0
    blocks = math.ceil(shortfall / policy.shortfall_block_hours - 1e-9)
    return blocks * policy.deduction_per_block_days


def _bucket(code: str, policy: Policy, worked: bool) -> str:
    code = (code or "").strip().upper()
    if not code:
        return B_PRESENT if worked else B_ABSENT
    b = policy.status_map.get(code)
    if b is None:
        return B_PRESENT if worked else B_ABSENT
    return b


def _mins(t: Optional[datetime]) -> Optional[int]:
    return None if t is None else t.hour * 60 + t.minute


# Bump when a calculation rule changes: on the next start the app recalculates every draft month once
# (services/maintenance.recalc_on_rules_change); finalised months never change.
# 2 = v1.6.4: WFH only on the person's own working days; targeted holidays.
# 3 = v1.6.5: half a day of leave deducted per WFH day above the monthly limit.
RULES_VERSION = 3


def compute_employee_month(
    emp: Employee,
    year: int,
    month: int,
    muster: list[MusterDay],
    swipes: list[Swipe],
    policy: Policy,
    day_overrides: Optional[dict] = None,
    month_overrides: Optional[dict] = None,
    swipes_loaded: bool = True,
) -> EmployeeMonth:
    day_overrides = day_overrides or {}
    month_overrides = month_overrides or {}
    res = EmployeeMonth(emp_no=emp.emp_no, name=emp.name or (muster[0].name if muster else ""),
                        group=emp.group or "", location=emp.location or "")
    hpd = emp.hours_per_day if emp.hours_per_day else policy.hours_per_day
    res.hours_per_day = hpd
    month_start = date(year, month, 1)
    month_end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    res.is_new_joinee = bool(emp.doj and month_start <= emp.doj <= month_end)
    res.is_exit = bool(emp.dol and month_start <= emp.dol <= month_end)

    swipes_by_day: dict[date, list[Swipe]] = defaultdict(list)
    rejected_by_day: dict[date, int] = defaultdict(int)
    for s in sorted(swipes, key=lambda x: x.ts):
        if (s.approval or "").strip().lower() in REJECTED_APPROVALS:
            rejected_by_day[s.ts.date()] += 1          # a rejected sign-in never counts as attendance
            continue
        swipes_by_day[s.ts.date()].append(s)

    behaviour = emp.group not in set(policy.metrics_exclude_groups)
    flags: list = []
    seen_unknown = set()

    # ---------- per-day classification ----------
    by_day = {m.day: m for m in muster}
    days_in_scope = sorted(by_day.keys() | {d for d in day_overrides.keys()})
    for d in days_in_scope:
        if emp.doj and d < emp.doj:
            continue
        if emp.dol and d > emp.dol:
            continue
        m = by_day.get(d) or MusterDay(emp.emp_no, d)
        ov: Optional[DayOverride] = day_overrides.get(d)
        s1, s2 = (m.s1 or "").strip().upper(), (m.s2 or "").strip().upper()
        work_min = m.work_min or 0
        if ov:
            if ov.s1:
                s1 = ov.s1.strip().upper()
            if ov.s2:
                s2 = ov.s2.strip().upper()
            if ov.work_min is not None:
                work_min = int(ov.work_min)
        worked = work_min > 0
        for code in (s1, s2):
            if code and code not in policy.status_map and code not in seen_unknown:
                seen_unknown.add(code)
                flags.append(("UNKNOWN_STATUS", SEV_MEDIUM, f"Unknown greytHR status code '{code}' (mapped by hours)"))

        muster_type = WEEKLY_OFF if (s1 == "OFF" and s2 == "OFF") else HOLIDAY if (s1 == "H" or s2 == "H") else WORKDAY
        ptype = pattern_day_type(d, emp.weekly_off, emp.working_saturdays, emp.off_weekdays)
        day_type = ptype or (WEEKLY_OFF if muster_type == WEEKLY_OFF else WORKDAY)
        holiday_name = ""
        if day_type == WORKDAY:
            if emp.holidays is not None:
                if d in emp.holidays:
                    day_type = HOLIDAY
                    holiday_name = emp.holidays[d] or "Holiday"
            elif muster_type == HOLIDAY:
                day_type = HOLIDAY
                holiday_name = "Holiday (greytHR)"
        if day_type == WORKDAY and d in emp.targeted_holidays:
            day_type = HOLIDAY
            holiday_name = emp.targeted_holidays[d] or "Holiday"
        if ov and ov.day_type:
            day_type = ov.day_type
        notes = []
        if (emp.holidays is not None or d in emp.targeted_holidays) and (muster_type == HOLIDAY) != (day_type == HOLIDAY) \
                and day_type != WEEKLY_OFF:
            notes.append("Holiday calendar differs from greytHR")
        if ptype and (ptype == WEEKLY_OFF) != (muster_type == WEEKLY_OFF) and muster_type != HOLIDAY:
            notes.append(f"Weekly-off pattern differs from greytHR ({'off' if ptype == WEEKLY_OFF else 'working'} per pattern)")

        if day_type == WORKDAY:
            b1 = _bucket(s1, policy, worked)
            b2 = _bucket(s2, policy, worked)
            if b1 == B_OFF:
                b1 = B_PRESENT if worked else B_ABSENT
                notes.append("greytHR shows OFF/H on a working day")
            if b2 == B_OFF:
                b2 = B_PRESENT if worked else B_ABSENT
        else:
            b1 = b2 = B_OFF
            if _bucket(s1, policy, worked) in (B_LEAVE, B_COMPOFF, B_LOP) or _bucket(s2, policy, worked) in (B_LEAVE, B_COMPOFF, B_LOP):
                notes.append("Leave recorded on a non-working day — ignored")

        sw = swipes_by_day.get(d, [])
        if rejected_by_day.get(d):
            notes.append(f"{rejected_by_day[d]} rejected sign-in(s) ignored")
        dd = DayDetail(day=d, weekday=d.strftime("%a"), day_type=day_type, s1=s1, s2=s2, b1=b1, b2=b2,
                       in_time=m.in_time, out_time=m.out_time, work_min=work_min, shift=m.shift,
                       swipes=sw, holiday_name=holiday_name, overridden=bool(ov), notes=notes)
        lt = []
        for s in sw:
            label = s.location_type or ("Office (biometric)" if s.swipe_type.lower() == "astra" else s.swipe_type or "Unknown")
            if label not in lt:
                lt.append(label)
            if s.swipe_type.lower().startswith("mobile") and s.location_type.lower() == "office":
                dd.mobile_office_swipes += 1
        dd.location_types = lt
        # WFH counts only on the person's own working days (their weekly-off pattern, holiday calendar, targeted
        # holidays and day corrections decide); WFH sign-ins on an off day / holiday are off-day work, not WFH.
        wfh_sw = [s for s in sw if s.location_type.strip().lower() in policy.wfh_location_types]
        if wfh_sw and day_type == WORKDAY:
            dd.is_wfh = True
            dd.is_partial_wfh = len(wfh_sw) < len(sw)
            res.wfh_swipes += len(wfh_sw)
        elif wfh_sw:
            res.offday_wfh_days += 1
            notes.append(f"Work from home on a {'holiday' if day_type == HOLIDAY else 'weekly off'} — "
                         "not counted as WFH (off-day work)")
        if day_type != WORKDAY and (worked or m.in_time or sw):
            dd.is_extra_work = True
            dd.compoff_earned = work_min >= policy.comp_off_min_hours * 60
        if day_type == WORKDAY and B_PRESENT in (b1, b2) and m.in_time and not sw and swipes:
            dd.notes.append("Marked present but no punch recorded this day")
        res.days.append(dd)

    # ---------- monthly aggregation ----------
    halves = defaultdict(float)
    lop_as_absent = policy.lop_bucket == "absent"
    for dd in res.days:
        if dd.day_type in (WORKDAY, HOLIDAY):
            res.total_week_days += 1
        if dd.day_type == HOLIDAY:
            res.holidays += 1
        if dd.day_type == WORKDAY:
            for b in (dd.b1, dd.b2):
                halves[b] += 0.5
        res.total_hours += dd.work_min / 60
        if dd.is_extra_work:
            res.extra_days += 1
            res.extra_hours += dd.work_min / 60
            if dd.compoff_earned:
                res.compoff_earned += 1
    res.pl_days = halves[B_LEAVE]
    res.cof_days = halves[B_COMPOFF]
    res.unauth_absent_days = halves[B_ABSENT]
    res.lop_days = halves[B_LOP]
    res.present_halves_days = halves[B_PRESENT]
    res.leave_days = res.pl_days + res.cof_days + (0 if lop_as_absent else res.lop_days)
    res.absent_days = res.unauth_absent_days + (res.lop_days if lop_as_absent else 0)

    # WFH
    if policy.wfh_method == "swipes_half":
        res.wfh_days = res.wfh_swipes / 2
    else:
        res.wfh_days = sum(1 for dd in res.days if dd.is_wfh)
    res.partial_wfh_days = sum(1 for dd in res.days if dd.is_wfh and dd.is_partial_wfh)

    # month overrides (inputs)
    res.overrides = dict(month_overrides)
    for k in ("total_week_days", "holidays", "leave_days", "absent_days", "wfh_days", "total_hours",
              "extra_days", "extra_hours"):
        if k in month_overrides and month_overrides[k] is not None:
            setattr(res, k, float(month_overrides[k]))
    res.days_present = res.total_week_days - res.holidays - res.leave_days - res.absent_days
    res.required_hours = res.days_present * hpd
    if month_overrides.get("required_hours") is not None:
        res.required_hours = float(month_overrides["required_hours"])
    res.actual_hours = res.total_hours - res.extra_hours
    res.balance_hours = res.actual_hours - res.required_hours
    res.shortfall_hours = max(0.0, -res.balance_hours)

    # exemption
    exempt_reason = ""
    if emp.exempt:
        exempt_reason = emp.exempt_reason or "Exempt (employee master)"
    elif emp.group and emp.group in set(policy.exempt_groups):
        exempt_reason = f"Exempt group ({emp.group})"
    if month_overrides.get("exempt") is not None:
        if str(month_overrides["exempt"]).lower() in ("1", "true", "yes"):
            exempt_reason = "Exception granted for this month"
        else:
            exempt_reason = ""
    res.exempt = bool(exempt_reason)
    res.shortfall_deduction_days = 0.0 if res.exempt else deduction_for_shortfall(res.shortfall_hours, policy)
    # WFH above the monthly limit: policy.wfh_excess_deduction_days of leave per extra WFH day (owner, v1.6.5)
    res.wfh_excess_days = float(math.ceil(max(0.0, res.wfh_days - policy.wfh_limit_days) - 1e-9))
    res.wfh_deduction_days = 0.0 if res.exempt else res.wfh_excess_days * policy.wfh_excess_deduction_days
    if res.wfh_excess_days:
        wfh_dates = [dd for dd in res.days if dd.is_wfh]
        for n, dd in enumerate(wfh_dates[int(policy.wfh_limit_days):], start=int(policy.wfh_limit_days) + 1):
            dd.notes.append(f"WFH day {n} of the month — over the limit of {policy.wfh_limit_days:g}"
                            + (f": {policy.wfh_excess_deduction_days:g} day deducted from leave"
                               if res.wfh_deduction_days else ""))
    res.deduction_days = res.shortfall_deduction_days + res.wfh_deduction_days
    if month_overrides.get("deduction_days") is not None:
        res.deduction_days = float(month_overrides["deduction_days"])

    # ---------- behaviour metrics ----------
    _behaviour(res, emp, policy, behaviour, swipes_loaded)

    # remark
    if month_overrides.get("remark"):
        res.remark = str(month_overrides["remark"])
    else:
        parts = []
        if res.exempt and (res.shortfall_hours > 0 or res.wfh_excess_days):
            parts.append(f"{exempt_reason} — no deduction")
        elif res.exempt:
            parts.append(exempt_reason)
        if res.deduction_days:
            why = []
            if res.shortfall_deduction_days:
                why.append(f"{res.shortfall_deduction_days:g} for {res.shortfall_hours:.2f} h short")
            if res.wfh_deduction_days:
                why.append(f"{res.wfh_deduction_days:g} for {res.wfh_excess_days:g} WFH day(s) over the limit")
            if len(why) == 1 and res.shortfall_deduction_days and "deduction_days" not in month_overrides:
                parts.append(f"{res.deduction_days:g} day deduction ({res.shortfall_hours:.2f} h short)")
            else:
                parts.append(f"{res.deduction_days:g} day deduction" + (f" ({'; '.join(why)})" if why else ""))
        if res.is_exit:
            parts.append(f"Left on {emp.dol:%d-%b-%Y}")
        if res.is_new_joinee:
            parts.append(f"Joined {emp.doj:%d-%b-%Y}")
        res.remark = "; ".join(parts)

    _flags(res, emp, policy, flags)
    res.flags = flags
    return res


def _behaviour(res: EmployeeMonth, emp: Employee, policy: Policy, enabled: bool, swipes_loaded: bool = True) -> None:
    in_mins, out_mins, work_hours, late_mins = [], [], [], []
    any_swipes = any(dd.swipes for dd in res.days)
    synthetic = 0
    present_days = 0
    for dd in res.days:
        res.total_swipes += len(dd.swipes)
        for s in dd.swipes:
            st = s.swipe_type.lower()
            if st == "astra":
                res.biometric_swipes += 1
            elif st.startswith("mobile"):
                res.mobile_swipes += 1
            elif st == "permission":
                res.permission_swipes += 1
        res.mobile_office_swipes += dd.mobile_office_swipes
        if any(s.location_type.lower() == "client location" for s in dd.swipes):
            res.client_days += 1
        if any(s.location_type.lower() == "on-duty" for s in dd.swipes):
            res.onduty_days += 1

        present1 = dd.day_type == WORKDAY and dd.b1 == B_PRESENT
        full = dd.day_type == WORKDAY and dd.b1 == B_PRESENT and dd.b2 == B_PRESENT
        if dd.day_type == WORKDAY and (dd.b1 == B_PRESENT or dd.b2 == B_PRESENT):
            present_days += 1
            if dd.in_time and not dd.swipes:
                synthetic += 1
        start, end = parse_shift(dd.shift, policy)
        in_t = dd.in_time or (dd.swipes[0].ts if dd.swipes else None)
        out_t = dd.out_time or (dd.swipes[-1].ts if len(dd.swipes) > 1 else None)
        # regularisation / missing punch (evidence based on swipes)
        perm = any(s.swipe_type.lower() == "permission" for s in dd.swipes)
        if any_swipes and dd.in_time:
            mins = {s.ts.replace(second=0, microsecond=0) for s in dd.swipes}
            unmatched = dd.in_time.replace(second=0) not in mins or (dd.out_time and dd.out_time.replace(second=0) not in mins)
        else:
            unmatched = False
        dd.is_regularised = perm or bool(unmatched)
        if (dd.b1 == B_PRESENT or dd.b2 == B_PRESENT) and dd.day_type == WORKDAY and any_swipes:
            if len(dd.swipes) == 1 or (dd.in_time and not dd.out_time):
                dd.is_missing_punch = True
        if not enabled:
            continue
        if present1 and in_t:
            late = _mins(in_t) - (start.hour * 60 + start.minute)
            if late > policy.late_grace_min:
                dd.is_late = True
                dd.late_min = late
                late_mins.append(late)
                if late > policy.very_late_min:
                    dd.is_very_late = True
        if full and out_t and dd.out_time:
            if out_t.date() == dd.day and _mins(out_t) < (end.hour * 60 + end.minute) - policy.early_exit_grace_min:
                dd.is_early_exit = True
            if dd.work_min < policy.min_daily_hours * 60:
                dd.is_short_day = True
        if out_t and (out_t.date() > dd.day or out_t.time() > policy.late_sitting_after):
            dd.is_late_sitting = True
        if dd.day_type == WORKDAY and (dd.b1 == B_PRESENT or dd.b2 == B_PRESENT):
            if in_t:
                in_mins.append(_mins(in_t))
            if out_t and out_t.date() == dd.day:
                out_mins.append(_mins(out_t))
            if dd.work_min:
                work_hours.append(dd.work_min / 60)

    # sandwich: leave/absence on a workday adjacent to an off/holiday
    by_date = {dd.day: dd for dd in res.days}
    for dd in res.days:
        if dd.day_type == WORKDAY and (dd.b1 in (B_LEAVE, B_ABSENT, B_LOP) or dd.b2 in (B_LEAVE, B_ABSENT, B_LOP)):
            prev_ = by_date.get(dd.day - timedelta(days=1))
            next_ = by_date.get(dd.day + timedelta(days=1))
            if (prev_ and prev_.day_type != WORKDAY) or (next_ and next_.day_type != WORKDAY):
                dd.is_sandwich = True
                res.sandwich_count += 1

    res.auto_attendance = swipes_loaded and present_days > 0 and synthetic == present_days
    res.no_punch_present_days = 0 if res.auto_attendance else synthetic
    res.late_days = sum(dd.is_late for dd in res.days)
    res.very_late_days = sum(dd.is_very_late for dd in res.days)
    res.avg_late_min = round(sum(late_mins) / len(late_mins), 1) if late_mins else 0
    res.early_exit_days = sum(dd.is_early_exit for dd in res.days)
    res.short_days = sum(dd.is_short_day for dd in res.days)
    res.late_sitting_days = sum(dd.is_late_sitting for dd in res.days)
    res.missing_punch_days = sum(dd.is_missing_punch for dd in res.days)
    res.regularised_days = 0 if res.auto_attendance else sum(dd.is_regularised for dd in res.days)
    res.avg_in_min = round(sum(in_mins) / len(in_mins), 1) if in_mins else None
    res.avg_out_min = round(sum(out_mins) / len(out_mins), 1) if out_mins else None
    res.avg_work_hours = round(sum(work_hours) / len(work_hours), 2) if work_hours else None


def _flags(res: EmployeeMonth, emp: Employee, policy: Policy, flags: list) -> None:
    add = lambda c, s, m: flags.append((c, s, m))  # noqa: E731
    if res.total_week_days and res.days_present <= 0 and res.total_hours == 0:
        add("NO_ATTENDANCE", SEV_CRITICAL, "No attendance for the entire month")
    if res.deduction_days:
        add("SHORTFALL_DEDUCTION", SEV_HIGH, f"{res.shortfall_hours:.2f} h short → {res.deduction_days:g} day(s) deducted from leave")
        # (kept for the total; the WFH part is explained on WFH_OVER_LIMIT)
    elif res.exempt and res.shortfall_hours > 0:
        add("SHORTFALL_EXEMPT", SEV_INFO, f"{res.shortfall_hours:.2f} h short but exempt")
    if res.unauth_absent_days:
        add("UNAUTHORISED_ABSENCE", SEV_HIGH, f"{res.unauth_absent_days:g} day(s) absent without approved leave")
    if res.wfh_days > policy.wfh_limit_days:
        add("WFH_OVER_LIMIT", SEV_HIGH, f"{res.wfh_days:g} WFH days (limit {policy.wfh_limit_days:g})"
            + (f" → {res.wfh_deduction_days:g} day(s) deducted from leave" if res.wfh_deduction_days
               else " — exempt, no deduction" if res.exempt and policy.wfh_excess_deduction_days else ""))
    if is_persistent_late(res.late_days, res.very_late_days, policy):
        add("PERSISTENT_LATE", SEV_MEDIUM, f"More than {policy.very_late_min} min late on {res.very_late_days} days "
            f"(late > {policy.late_grace_min} min on {res.late_days} days, avg {res.avg_late_min:.0f} min)")
    if res.regularised_days >= policy.persistent_regularisation_days:
        add("PERSISTENT_REGULARISATION", SEV_MEDIUM, f"{res.regularised_days} regularised days")
    if res.missing_punch_days >= 2:
        add("MISSING_PUNCHES", SEV_MEDIUM, f"{res.missing_punch_days} days with a missing punch")
    if res.mobile_office_swipes:
        add("MOBILE_AT_OFFICE", SEV_INFO, f"{res.mobile_office_swipes} mobile sign-ins tagged 'Office' (biometric bypass?)")
    if res.late_sitting_days >= 5:
        add("LATE_SITTING", SEV_INFO, f"Worked past {policy.late_sitting_after:%H:%M} on {res.late_sitting_days} days")
    if res.sandwich_count >= 2:
        add("SANDWICH_PATTERN", SEV_INFO, f"{res.sandwich_count} leave/absence days adjacent to weekends/holidays")
    if res.no_punch_present_days:
        add("PRESENT_WITHOUT_PUNCH", SEV_MEDIUM, f"{res.no_punch_present_days} day(s) marked present with no punch at all")
    if res.lop_days:
        add("LOP", SEV_INFO, f"{res.lop_days:g} loss-of-pay day(s)")
    if res.auto_attendance:
        add("AUTO_ATTENDANCE", SEV_INFO, "No punches — attendance auto-marked in greytHR")
    if not emp.group:
        add("GROUP_NOT_SET", SEV_MEDIUM, "Employee group not set in master (exemptions cannot apply)")
    if emp.doj is None:
        add("DOJ_MISSING", SEV_INFO, "Date of joining unknown")
    if res.is_new_joinee:
        add("NEW_JOINEE", SEV_INFO, f"Joined on {emp.doj:%d-%b-%Y}")
    if res.is_exit:
        add("EXIT", SEV_INFO, f"Left on {emp.dol:%d-%b-%Y}")
    mismatch = sum(1 for dd in res.days if dd.notes)
    if mismatch:
        add("CALENDAR_MISMATCH", SEV_INFO, f"{mismatch} day(s) where the app calendar/pattern differs from greytHR")
    for k in res.overrides:
        add("MANUAL_OVERRIDE", SEV_INFO, f"'{k}' manually set to {res.overrides[k]}")


def compute_month(
    year: int,
    month: int,
    employees: dict[str, Employee],
    muster: list[MusterDay],
    swipes: list[Swipe],
    policy: Policy,
    day_overrides: Optional[list[DayOverride]] = None,
    month_overrides: Optional[dict] = None,
) -> dict[str, EmployeeMonth]:
    """Compute every employee that appears in the muster (or has overrides)."""
    m_by = defaultdict(list)
    for m in muster:
        m_by[m.emp_no].append(m)
    s_by = defaultdict(list)
    for s in swipes:
        s_by[s.emp_no].append(s)
    do_by = defaultdict(dict)
    for o in day_overrides or []:
        do_by[o.emp_no][o.day] = o
    month_overrides = month_overrides or {}
    out = {}
    for emp_no in sorted(m_by.keys() | do_by.keys()):
        emp = employees.get(emp_no) or Employee(emp_no=emp_no, name=(m_by[emp_no][0].name if m_by[emp_no] else ""))
        out[emp_no] = compute_employee_month(emp, year, month, m_by[emp_no], s_by.get(emp_no, []), policy,
                                             do_by.get(emp_no), month_overrides.get(emp_no), swipes_loaded=bool(swipes))
        if emp_no not in employees:
            out[emp_no].flags.append(("NOT_IN_MASTER", SEV_MEDIUM, "Employee not in employee master"))
    return out
