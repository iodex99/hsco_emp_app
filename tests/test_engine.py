"""Unit tests for the pure calculation engine (docs/BUSINESS_RULES.md)."""
from datetime import date, datetime, time

import pytest

from app.engine.compute import compute_employee_month, deduction_for_shortfall, parse_shift, pattern_day_type
from app.engine.model import (
    HOLIDAY, P_ALL_SAT_OFF, P_CUSTOM, P_STD_FIRST_SAT, P_SUN_ONLY, WEEKLY_OFF, WORKDAY, DayOverride, Employee, MusterDay,
    Policy, Swipe,
)

POL = Policy()


@pytest.mark.parametrize("short,expected", [
    (0, 0), (-5, 0), (0.0001, 0.5), (0.35, 0.5), (3.0, 0.5), (3.37, 0.5), (3.67, 0.5), (4.0, 0.5), (4.0001, 1.0),
    (6.0, 1.0), (8.0, 1.0), (8.5, 1.5), (13.97, 2.0), (16.0, 2.0), (17.6, 2.5),
])
def test_deduction_blocks(short, expected):
    assert deduction_for_shortfall(short, POL) == expected


def test_deduction_grace():
    p = Policy(shortfall_grace_hours=0.5)
    assert deduction_for_shortfall(0.4, p) == 0
    assert deduction_for_shortfall(0.6, p) == 0.5


@pytest.mark.parametrize("name,start,end", [
    ("10:am-18:30pm", time(10, 0), time(18, 30)),
    ("9 am to 6pm (Carlyle)", time(9, 0), time(18, 0)),
    ("09:30-18:30", time(9, 30), time(18, 30)),
    ("", time(10, 0), time(18, 30)),
    ("General", time(10, 0), time(18, 30)),
])
def test_parse_shift(name, start, end):
    assert parse_shift(name, POL) == (start, end)


def test_parse_shift_override():
    p = Policy(shift_overrides={"Night": (time(20, 0), time(23, 0))})
    assert parse_shift("Night", p) == (time(20, 0), time(23, 0))


def test_patterns_october_2026():
    oct3, oct10, oct4, oct7 = date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 4), date(2026, 10, 7)
    assert pattern_day_type(oct3, P_STD_FIRST_SAT) == WORKDAY
    assert pattern_day_type(oct10, P_STD_FIRST_SAT) == WEEKLY_OFF
    assert pattern_day_type(oct3, P_ALL_SAT_OFF) == WEEKLY_OFF
    assert pattern_day_type(oct10, P_SUN_ONLY) == WORKDAY
    assert pattern_day_type(oct4, P_SUN_ONLY) == WEEKLY_OFF
    # Wednesday-off employee who works weekends (HS068 in the real data)
    assert pattern_day_type(oct7, P_CUSTOM, (), (2,)) == WEEKLY_OFF
    assert pattern_day_type(oct4, P_CUSTOM, (), (2,)) == WORKDAY
    # Sat off except 1st and 3rd
    assert pattern_day_type(date(2026, 10, 17), P_CUSTOM, (1, 3), (5, 6)) == WORKDAY
    assert pattern_day_type(oct10, P_CUSTOM, (1, 3), (5, 6)) == WEEKLY_OFF
    # count working days in Oct-2026 for the standard pattern = 23
    days = [date(2026, 10, d) for d in range(1, 32)]
    assert sum(pattern_day_type(d, P_STD_FIRST_SAT) == WORKDAY for d in days) == 23
    assert sum(pattern_day_type(d, P_ALL_SAT_OFF) == WORKDAY for d in days) == 22
    assert sum(pattern_day_type(d, P_SUN_ONLY) == WORKDAY for d in days) == 27


def _md(emp, d, s="P", mins=510, in_hm=(10, 0), shift="10:am-18:30pm", s2=None):
    from datetime import timedelta
    i = datetime(d.year, d.month, d.day, *in_hm) if mins else None
    o = i + timedelta(minutes=mins) if mins else None
    return MusterDay(emp, d, s, s2 or s, i, o, mins, shift)


def month(emp_no="E1", hours=(8, 30), late=False, extra=None, statuses=None, very_late=False):
    """Standard-pattern month: Mon-Fri + 1st Sat working, 20-Oct holiday."""
    rows = []
    for d in range(1, 32):
        dt = date(2026, 10, d)
        if dt.weekday() == 6 or (dt.weekday() == 5 and d > 7):
            rows.append(MusterDay(emp_no, dt, "OFF", "OFF"))
        elif d == 20:
            rows.append(MusterDay(emp_no, dt, "H", "H"))
        else:
            st = (statuses or {}).get(d, "P")
            mins = hours[0] * 60 + hours[1] if st == "P" else 0
            rows.append(_md(emp_no, dt, st, mins, (11, 20) if very_late else (10, 30) if late else (9, 45)))
    for d, mins in (extra or {}).items():
        dt = date(2026, 10, d)
        rows = [r for r in rows if r.day != dt] + [MusterDay(emp_no, dt, "OFF", "OFF", datetime(2026, 10, d, 10), datetime(2026, 10, d, 10) , mins)]
    return rows


def test_exact_requirement_no_deduction():
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(), [], POL)
    assert (r.total_week_days, r.holidays, r.days_present) == (23, 1, 22)
    assert r.required_hours == pytest.approx(187)
    assert r.actual_hours == pytest.approx(187)
    assert r.deduction_days == 0


def test_shortfall_deduction_and_exemption():
    rows = month(hours=(8, 20))  # 10 min short x 22 = 3.67 h
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL)
    assert r.shortfall_hours == pytest.approx(22 * 10 / 60)
    assert r.deduction_days == 0.5
    assert any(f[0] == "SHORTFALL_DEDUCTION" for f in r.flags)
    for grp in ("Partner", "SS", "Article", "Trainee"):
        rr = compute_employee_month(Employee("E1", group=grp), 2026, 10, rows, [], POL)
        assert rr.deduction_days == 0 and rr.exempt
    rr = compute_employee_month(Employee("E1", group="BSS", exempt=True, exempt_reason="Treated as Partner"), 2026, 10, rows, [], POL)
    assert rr.deduction_days == 0 and "Treated as Partner" in rr.remark


def test_offday_hours_excluded_and_compoff():
    rows = month(hours=(8, 30), extra={11: 6 * 60, 18: 4 * 60})
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL)
    assert r.extra_days == 2
    assert r.extra_hours == pytest.approx(10)
    assert r.total_hours == pytest.approx(187 + 10)
    assert r.actual_hours == pytest.approx(187)
    assert r.compoff_earned == 1  # only the 6-hour day qualifies (>= 5 h)


def test_leave_absent_lop_buckets():
    rows = month(statuses={6: "PL", 7: "A", 8: "LOP", 9: "COF"})
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL)
    assert (r.pl_days, r.unauth_absent_days, r.lop_days, r.cof_days) == (1, 1, 1, 1)
    assert r.leave_days == 3          # PL + COF + LOP (default lop_bucket = leave)
    assert r.absent_days == 1
    assert r.days_present == 23 - 1 - 3 - 1
    r2 = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], Policy(lop_bucket="absent"))
    assert (r2.leave_days, r2.absent_days) == (2, 2)
    assert any(f[0] == "UNAUTHORISED_ABSENCE" for f in r.flags)


def test_half_day_leave():
    rows = month()
    rows = [r for r in rows if r.day != date(2026, 10, 14)] + [_md("E1", date(2026, 10, 14), "PL", 255, s2="P")]
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL)
    assert r.leave_days == 0.5 and r.days_present == 21.5
    assert r.required_hours == pytest.approx(21.5 * 8.5)


def test_hours_per_day_override_and_month_overrides():
    rows = month(hours=(8, 0))
    r = compute_employee_month(Employee("E1", group="BSS", hours_per_day=8.0), 2026, 10, rows, [], POL)
    assert r.required_hours == pytest.approx(176) and r.deduction_days == 0
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL,
                               month_overrides={"required_hours": 170, "remark": "agreed"})
    assert r.required_hours == 170 and r.balance_hours == pytest.approx(6) and r.remark == "agreed"
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL, month_overrides={"exempt": True})
    assert r.exempt and r.deduction_days == 0
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL, month_overrides={"deduction_days": 0})
    assert r.deduction_days == 0


def test_day_override():
    rows = month()
    ov = {date(2026, 10, 23): DayOverride("E1", date(2026, 10, 23), s1="A", s2="A", work_min=0, reason="no punch")}
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, [], POL, day_overrides=ov)
    assert r.absent_days == 1 and r.days_present == 21
    assert any(d.overridden for d in r.days)


def test_weekly_off_pattern_override_makes_saturday_off():
    rows = month()
    r = compute_employee_month(Employee("E1", group="BSS", weekly_off=P_ALL_SAT_OFF), 2026, 10, rows, [], POL)
    assert r.total_week_days == 22
    assert r.extra_days == 1  # worked the 1st Saturday which is now an off day
    assert any(f[0] == "CALENDAR_MISMATCH" for f in r.flags)


def test_holiday_calendar_overrides_muster():
    rows = month()
    emp = Employee("E1", group="BSS", holidays={date(2026, 10, 2): "Gandhi Jayanti", date(2026, 10, 20): "Dussehra"})
    r = compute_employee_month(emp, 2026, 10, rows, [], POL)
    assert r.holidays == 2
    assert r.extra_days == 1  # worked on 2-Oct per greytHR => holiday work


def test_wfh_methods_and_late():
    rows = month(late=True)
    sw = []
    for d in (5, 6, 7):
        sw += [Swipe("E1", datetime(2026, 10, d, 10, 30), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
               Swipe("E1", datetime(2026, 10, d, 19, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    sw += [Swipe("E1", datetime(2026, 10, 8, 10, 30), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
           Swipe("E1", datetime(2026, 10, 8, 19, 0), "hasmukhshah", "", "Astra")]
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL)
    assert r.wfh_days == 4 and r.partial_wfh_days == 1
    assert any(f[0] == "WFH_OVER_LIMIT" for f in r.flags)
    r2 = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, Policy(wfh_method="swipes_half"))
    assert r2.wfh_days == 3.5
    assert r.late_days == 22 and r.avg_late_min == 30 and r.very_late_days == 0
    assert not any(f[0] == "PERSISTENT_LATE" for f in r.flags)          # 30 min late is not "persistent"
    rl = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, Policy(persistent_late_basis="late"))
    assert any(f[0] == "PERSISTENT_LATE" for f in rl.flags)
    rv = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(very_late=True), [], POL)
    assert rv.very_late_days == 22 and any(f[0] == "PERSISTENT_LATE" for f in rv.flags)
    # partners are excluded from behaviour metrics
    rp = compute_employee_month(Employee("E1", group="Partner"), 2026, 10, rows, sw, POL)
    assert rp.late_days == 0


def test_wfh_counts_only_on_the_persons_own_working_days():
    """v1.6.4 (owner): WFH on a person's weekly off or holiday is not WFH — it is off-day work. Weekly offs and holidays
    are the person's own: weekly-off pattern, holiday calendar, targeted holidays, day corrections."""
    def wfh(d):
        return [Swipe("E1", datetime(2026, 10, d, 11, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
                Swipe("E1", datetime(2026, 10, d, 15, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    rows = month()                                        # 1st Sat working, Sat 10 off, Tue 20 holiday (greytHR)
    sw = wfh(3) + wfh(5) + wfh(6) + wfh(7) + wfh(10) + wfh(20)
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL)
    assert r.wfh_days == 4 and r.offday_wfh_days == 2                 # Sat 10 (weekly off) and Tue 20 (holiday) excluded
    d10 = next(d for d in r.days if d.day.day == 10)
    assert not d10.is_wfh and d10.is_extra_work and any("weekly off" in n and "not counted as WFH" in n for n in d10.notes)
    assert any("holiday" in n for n in next(d for d in r.days if d.day.day == 20).notes)
    assert Policy(wfh_method="swipes_half") and compute_employee_month(
        Employee("E1", group="BSS"), 2026, 10, rows, sw, Policy(wfh_method="swipes_half")).wfh_days == 4   # 8 swipes / 2
    # someone whose Saturdays are all off: Sat 3 is their weekly off → not WFH for them
    r = compute_employee_month(Employee("E1", group="BSS", weekly_off=P_ALL_SAT_OFF), 2026, 10, rows, sw, POL)
    assert r.wfh_days == 3 and r.offday_wfh_days == 3
    # their own holiday calendar (7 Oct) and a targeted holiday (6 Oct) also exclude WFH
    e = Employee("E1", group="BSS", holidays={date(2026, 10, 7): "Local holiday", date(2026, 10, 20): "Dussehra"},
                 targeted_holidays={date(2026, 10, 6): "Delhi festival"})
    r = compute_employee_month(e, 2026, 10, rows, sw, POL)
    d6 = next(d for d in r.days if d.day.day == 6)
    assert d6.day_type == HOLIDAY and d6.holiday_name == "Delhi festival" and not d6.is_wfh
    assert r.wfh_days == 2 and r.offday_wfh_days == 4                 # only Sat 3 and Mon 5 remain
    # a day corrected to a weekly off
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL,
                               day_overrides={date(2026, 10, 5): DayOverride("E1", date(2026, 10, 5), day_type=WEEKLY_OFF)})
    assert r.wfh_days == 3 and r.offday_wfh_days == 3


def test_targeted_holiday_without_calendar_and_matching():
    from types import SimpleNamespace as NS
    from app.services import holidays as TH
    # no calendar (greytHR marks used) + a targeted holiday on a working day
    r = compute_employee_month(Employee("E1", group="BSS", targeted_holidays={date(2026, 10, 9): "Chhath"}), 2026, 10,
                               month(), [], POL)
    d9 = next(d for d in r.days if d.day.day == 9)
    assert d9.day_type == HOLIDAY and d9.holiday_name == "Chhath" and "Holiday calendar differs from greytHR" in d9.notes
    assert r.holidays == 2 and next(d for d in r.days if d.day.day == 20).day_type == HOLIDAY   # greytHR's H still used
    # who it applies to: listed employees always; locations AND groups (blank = any); case/space-insensitive
    h = NS(locations="Delhi", groups=" article ,ATA", emp_nos="HS009")
    assert TH.applies(h, "E1", "delhi", "Article") and TH.applies(h, "E1", "Delhi", "ata")
    assert not TH.applies(h, "E1", "Mumbai", "Article") and not TH.applies(h, "E1", "Delhi", "BSS")
    assert TH.applies(h, "HS009", "Mumbai", "BSS")
    assert TH.applies(NS(locations="Delhi", groups="", emp_nos=""), "E1", "Delhi", "BSS")
    assert not TH.applies(NS(locations="", groups="", emp_nos=""), "E1", "Delhi", "BSS")   # must target someone
    assert TH.split("Mumbai, Delhi; mumbai|Pune") == ["Mumbai", "Delhi", "Pune"]
    assert TH.describe(h) == "Delhi & article / ATA · plus HS009"


def test_missing_punch_and_regularisation():
    rows = month()
    sw = []
    for r_ in rows:
        if r_.in_time:
            sw.append(Swipe("E1", r_.in_time, "hasmukhshah", "", "Astra"))
            if r_.day.day not in (5, 6):
                sw.append(Swipe("E1", r_.out_time, "hasmukhshah", "", "Astra"))
    sw.append(Swipe("E1", datetime(2026, 10, 9, 13, 0), "", "", "Permission"))
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL)
    assert r.missing_punch_days == 2
    assert r.permission_swipes == 1
    assert r.regularised_days >= 3  # 2 days whose out-time has no swipe + 1 permission day


def test_auto_attendance_detected():
    r = compute_employee_month(Employee("E1", group="Partner"), 2026, 10, month(), [], POL)
    assert r.auto_attendance  # present every day with in/out times but not a single punch
    r = compute_employee_month(Employee("E1", group="Partner"), 2026, 10, month(), [], POL, swipes_loaded=False)
    assert not r.auto_attendance  # swipe file not imported -> no evidence either way
    rows = month()
    sw = []
    for r_ in rows:  # punches on every present day except 2
        if r_.in_time and r_.day.day not in (12, 13):
            sw += [Swipe("E1", r_.in_time, "hasmukhshah", "", "Astra"), Swipe("E1", r_.out_time, "hasmukhshah", "", "Astra")]
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL)
    assert not r.auto_attendance
    assert r.no_punch_present_days == 2
    assert any(f[0] == "PRESENT_WITHOUT_PUNCH" for f in r.flags)


def test_new_joinee_and_exit():
    rows = [r for r in month() if r.day >= date(2026, 10, 19)]
    r = compute_employee_month(Employee("E1", group="BSS", doj=date(2026, 10, 19)), 2026, 10, rows, [], POL)
    assert r.is_new_joinee and r.total_week_days == 10 and r.required_hours == pytest.approx(9 * 8.5)
    r = compute_employee_month(Employee("E1", group="BSS", dol=date(2026, 10, 15)), 2026, 10, month(), [], POL)
    assert r.is_exit and all(d.day <= date(2026, 10, 15) for d in r.days)


def test_no_attendance_flag():
    rows = month(statuses={d: "A" for d in range(1, 32)})
    r = compute_employee_month(Employee("E1", group="ATA"), 2026, 10, rows, [], POL)
    assert r.days_present == 0 and r.required_hours == 0 and r.deduction_days == 0
    assert any(f[0] == "NO_ATTENDANCE" for f in r.flags)
