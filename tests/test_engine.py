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
    are the person's own: weekly-off pattern, their category's holiday list, day corrections."""
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
    # their category's holiday list (7 Oct, and 6 Oct added to it for a few categories) also excludes WFH
    e = Employee("E1", group="BSS", holidays={date(2026, 10, 6): "Delhi festival", date(2026, 10, 7): "Local holiday",
                                              date(2026, 10, 20): "Dussehra"})
    r = compute_employee_month(e, 2026, 10, rows, sw, POL)
    d6 = next(d for d in r.days if d.day.day == 6)
    assert d6.day_type == HOLIDAY and d6.holiday_name == "Delhi festival" and not d6.is_wfh
    assert r.wfh_days == 2 and r.offday_wfh_days == 4                 # only Sat 3 and Mon 5 remain
    # a day corrected to a weekly off
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL,
                               day_overrides={date(2026, 10, 5): DayOverride("E1", date(2026, 10, 5), day_type=WEEKLY_OFF)})
    assert r.wfh_days == 3 and r.offday_wfh_days == 3


def test_wfh_over_limit_deducts_half_day_per_extra_day():
    """v1.6.5 (owner): more than 2 WFH days in a month → ½ day of leave deducted for every additional WFH day."""
    def wfh(d):
        return [Swipe("E1", datetime(2026, 10, d, 11, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
                Swipe("E1", datetime(2026, 10, d, 19, 30), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    rows = month()                                                  # no hours shortfall
    for n, expect in ((1, 0), (2, 0), (3, 0.5), (4, 1.0), (5, 1.5)):
        sw = [s for d in (5, 6, 7, 8, 9)[:n] for s in wfh(d)]
        r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL)
        assert (r.wfh_days, r.wfh_deduction_days, r.deduction_days) == (n, expect, expect), n
    assert r.wfh_excess_days == 3 and r.shortfall_deduction_days == 0
    notes = {d.day.day: d.notes for d in r.days}
    assert not any("over the limit" in x for x in notes[6]) and any("WFH day 3 of the month" in x for x in notes[7])
    assert "1.5 day deduction (1.5 for 3 WFH day(s) over the limit)" in r.remark
    f = next(x for x in r.flags if x[0] == "WFH_OVER_LIMIT")
    assert "5 WFH days (limit 2) → 1.5 day(s) deducted from leave" in f[2]
    # together with an hours shortfall: both parts add up
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(hours=(8, 20)), sw[:6], POL)
    assert r.shortfall_deduction_days == 0.5 and r.wfh_deduction_days == 0.5 and r.deduction_days == 1.0
    assert "1 day deduction (0.5 for" in r.remark and "0.5 for 1 WFH day(s) over the limit" in r.remark
    # setting 0 = flag only; exempt employees are not deducted; off-day WFH does not count (v1.6.4)
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, Policy(wfh_excess_deduction_days=0))
    assert r.deduction_days == 0 and any(x[0] == "WFH_OVER_LIMIT" for x in r.flags)
    # v1.6.6 (owner): hours-exempt people (employee flag, Article / SS / Trainee groups) ARE deducted for WFH;
    # only wfh_deduction_exempt_groups (default Partner) and a month exception are not
    for e in (Employee("E1", group="BSS", exempt=True), Employee("E1", group="Article"), Employee("E1", group="SS"),
              Employee("E1", group="Trainee")):
        r = compute_employee_month(e, 2026, 10, rows, sw, POL)
        assert r.exempt and r.wfh_deduction_days == 1.5 and r.deduction_days == 1.5, e.group
    r = compute_employee_month(Employee("E1", group="Article"), 2026, 10, month(hours=(7, 0)), sw, POL)
    assert r.shortfall_deduction_days == 0 and r.deduction_days == 1.5 and "no hours deduction" in r.remark
    r = compute_employee_month(Employee("E1", group="Partner"), 2026, 10, rows, sw, POL)
    assert r.deduction_days == 0 and "exempt, no deduction" in next(x for x in r.flags if x[0] == "WFH_OVER_LIMIT")[2]
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, sw, POL, month_overrides={"exempt": "yes"})
    assert r.deduction_days == 0 and r.wfh_deduction_exempt
    r = compute_employee_month(Employee("E1", group="Partner"), 2026, 10, rows, sw, Policy(wfh_deduction_exempt_groups=()))
    assert r.wfh_deduction_days == 1.5
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, rows, wfh(5) + wfh(6) + wfh(10) + wfh(11), POL)
    assert r.wfh_days == 2 and r.deduction_days == 0                # Sat 10 / Sun 11 are weekly offs


def test_wfh_deduction_review_fixes():
    """v1.6.7 review: WFH on a full leave day is not WFH; flags follow the hours / WFH parts; manual corrections are
    reported as such; swipes ÷ 2 has no per-day over-limit notes."""
    def wfh(d):
        return [Swipe("E1", datetime(2026, 10, d, 11, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
                Swipe("E1", datetime(2026, 10, d, 19, 30), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    sw = [s for d in (5, 6, 7, 8) for s in wfh(d)]
    # 5 and 6 are full PL days, 7 is half PL: WFH days = 7 and 8 only → within the limit, no deduction
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(statuses={5: "PL", 6: "PL"}), sw, POL)
    assert r.pl_days == 2 and r.wfh_days == 2 and r.leaveday_wfh_days == 2 and r.deduction_days == 0
    assert any("day of leave / absence" in n for n in next(d for d in r.days if d.day.day == 5).notes)
    # WFH-only deduction: no "hours shortfall" flag
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(), sw, POL)
    codes = [f[0] for f in r.flags]
    assert r.deduction_days == 1.0 and "WFH_OVER_LIMIT" in codes and "SHORTFALL_DEDUCTION" not in codes
    # hours-exempt and short + WFH over the limit: SHORTFALL_EXEMPT (not a false "h short → deducted")
    r = compute_employee_month(Employee("E1", group="Article"), 2026, 10, month(hours=(7, 0)), sw, POL)
    codes = [f[0] for f in r.flags]
    assert "SHORTFALL_EXEMPT" in codes and "SHORTFALL_DEDUCTION" not in codes and r.deduction_days == 1.0
    # shortfall + WFH: the hours flag shows only the hours part
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(hours=(8, 20)), sw, POL)
    f = next(x for x in r.flags if x[0] == "SHORTFALL_DEDUCTION")
    assert r.deduction_days == 1.5 and "→ 0.5 day(s) deducted" in f[2]
    # a manual correction of the deduction is reported as such everywhere
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(), sw, POL,
                               month_overrides={"deduction_days": 0.5})
    assert r.deduction_days == 0.5 and "0.5 day deduction (set manually; calculated 1)" in r.remark
    assert "set manually to 0.5" in next(x for x in r.flags if x[0] == "WFH_OVER_LIMIT")[2]
    assert not any("deducted from leave" in n for d in r.days for n in d.notes)
    # swipes ÷ 2: deduction from the half-count, no misleading "WFH day n" notes
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(), sw, Policy(wfh_method="swipes_half"))
    assert r.wfh_days == 4 and r.wfh_deduction_days == 1.0
    assert not any("of the month — over the limit" in n for d in r.days for n in d.notes)


def test_excel_wfh_term_matches_what_was_charged():
    from types import SimpleNamespace as NS
    from app.reports.excel import _wfh_term
    pol = NS(wfh_limit_days=2, wfh_excess_deduction_days=0.5)
    assert _wfh_term({"wfh_days": 4}, 6, pol) == ""                                  # calculated before v1.6.5
    assert _wfh_term({"wfh_excess_days": 2, "wfh_deduction_days": 0}, 6, pol) == ""  # exempt under v1.6.5 rules
    assert _wfh_term({"wfh_excess_days": 2, "wfh_deduction_days": 1, "wfh_deduction_exempt": True}, 6, pol) == ""
    assert _wfh_term({"wfh_excess_days": 2, "wfh_deduction_days": 1}, 6, pol) == "+CEILING(MAX(0,J6-2),1)*0.5"
    assert _wfh_term({"wfh_excess_days": 2, "wfh_deduction_days": 2}, 7, pol) == "+CEILING(MAX(0,J7-2),1)*1"   # old rate kept
    assert _wfh_term({"wfh_excess_days": 0, "wfh_deduction_days": 0}, 8, pol) == "+CEILING(MAX(0,J8-2),1)*0.5"


def test_one_off_holiday_in_a_category_list():
    """v1.9 (owner): holidays are targeted only by category — a one-off holiday is a date in the category's list.
    It turns a working day into a holiday (noted where greytHR differs); without a list greytHR's marks are used."""
    from app.services import holidays as TH
    r = compute_employee_month(Employee("E1", group="BSS", holidays={date(2026, 10, 9): "Chhath",
                                                                    date(2026, 10, 20): "Dussehra"}),
                               2026, 10, month(), [], POL)
    d9 = next(d for d in r.days if d.day.day == 9)
    assert d9.day_type == HOLIDAY and d9.holiday_name == "Chhath" and "Holiday calendar differs from greytHR" in d9.notes
    assert r.holidays == 2
    r = compute_employee_month(Employee("E1", group="BSS"), 2026, 10, month(), [], POL)        # no list: greytHR's H
    assert r.holidays == 1 and next(d for d in r.days if d.day.day == 20).day_type == HOLIDAY
    assert TH.split("HSCO (Mum), HSCO (Delhi); hsco (mum)|HSCO (CBV)") == ["HSCO (Mum)", "HSCO (Delhi)", "HSCO (CBV)"]


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


def test_optional_holiday_taken_or_not():
    """v1.7: an optional holiday is a holiday only when greytHR marks the person's day H (they took it)."""
    rows = month()
    e = Employee("E1", group="BSS", holidays={}, optional_holidays={date(2026, 10, 7): "Gudi Padwa"})
    r = compute_employee_month(e, 2026, 10, rows, [], POL)
    d7 = next(d for d in r.days if d.day.day == 7)
    assert d7.day_type == WORKDAY and any("Optional holiday (Gudi Padwa) — not taken" in n for n in d7.notes)
    rows_h = [MusterDay("E1", m.day, "H", "H") if m.day.day == 7 else m for m in rows]
    r = compute_employee_month(e, 2026, 10, rows_h, [], POL)
    d7 = next(d for d in r.days if d.day.day == 7)
    assert d7.day_type == HOLIDAY and d7.holiday_name == "Gudi Padwa (optional)"
    assert not any("differs from greytHR" in n for n in d7.notes)


def test_wfh_on_a_closed_saturday_is_listed_not_counted():
    """v1.7.4 (owner): the branches open only on the 1st Saturday; someone whose Saturday is a working day works from
    home on the others — shown as WFH with a note, not counted towards the limit or its deduction."""
    def wfh(d):
        return [Swipe("E1", datetime(2026, 10, d, 10, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
                Swipe("E1", datetime(2026, 10, d, 18, 40), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    rows = [r if not (r.day.weekday() == 5 and r.day.day > 7) else _md("E1", r.day, "P", 520, (10, 0))
            for r in month()]                                           # works every Saturday (6-day week)
    e = Employee("E1", group="BSS", weekly_off=P_SUN_ONLY)
    sw = wfh(3) + wfh(10) + wfh(17) + wfh(24) + wfh(5) + wfh(6)
    r = compute_employee_month(e, 2026, 10, rows, sw, POL)
    closed = [d.day.day for d in r.days if d.wfh_office_closed]
    assert closed == [10, 17, 24] and r.office_closed_wfh_days == 3
    assert all(d.is_wfh for d in r.days if d.day.day in (10, 17, 24))                 # still shown as WFH
    assert any("office is closed" in n for n in next(d for d in r.days if d.day.day == 10).notes)
    assert r.wfh_days == 3 and r.wfh_deduction_days == 0.5                          # 1st Sat + Mon 5 + Tue 6 count
    r = compute_employee_month(e, 2026, 10, rows, sw, Policy(wfh_method="swipes_half"))
    assert r.wfh_days == 3                                                          # closed Saturdays' swipes left out
    r = compute_employee_month(e, 2026, 10, rows, sw, Policy(office_open_saturdays=()))
    assert r.office_closed_wfh_days == 0 and r.wfh_days == 6                        # every Saturday open: all count
    r = compute_employee_month(e, 2026, 10, rows, sw, Policy(office_open_saturdays=(1, 3)))
    assert [d.day.day for d in r.days if d.wfh_office_closed] == [10, 24]


def test_extra_wfh_approved_by_manager_is_not_deducted():
    """v1.7.5 (owner): more than 2 WFH days can be allowed at a manager's / partner's discretion (not in any export):
    HR records the approved extra days as a month correction — only the rest is deducted."""
    def wfh(d):
        return [Swipe("E1", datetime(2026, 10, d, 11, 0), "Mobile Sign In", "Work from Home", "Mobile Sign In"),
                Swipe("E1", datetime(2026, 10, d, 19, 30), "Mobile Sign In", "Work from Home", "Mobile Sign In")]
    sw = [s for d in (5, 6, 7, 8, 9) for s in wfh(d)]                   # 5 WFH days → 3 over the limit
    e = Employee("E1", group="BSS")
    r = compute_employee_month(e, 2026, 10, month(), sw, POL)
    assert (r.wfh_excess_days, r.wfh_deduction_days) == (3, 1.5)
    r = compute_employee_month(e, 2026, 10, month(), sw, POL, month_overrides={"wfh_approved_days": "2"})
    assert (r.wfh_approved_days, r.wfh_deduction_days, r.deduction_days) == (2, 0.5, 0.5)
    f = next(x for x in r.flags if x[0] == "WFH_OVER_LIMIT")[2]
    assert "2 extra approved by manager / partner" in f and "→ 0.5 day(s) deducted" in f
    notes = {d.day.day: " ".join(d.notes) for d in r.days}
    assert "approved by manager" in notes[7] and "approved by manager" in notes[8] and "deducted" in notes[9]
    assert "(2 more approved)" in r.remark
    r = compute_employee_month(e, 2026, 10, month(), sw, POL, month_overrides={"wfh_approved_days": "5"})
    assert r.wfh_deduction_days == 0 and r.deduction_days == 0
    assert "→ no deduction" in next(x for x in r.flags if x[0] == "WFH_OVER_LIMIT")[2]
