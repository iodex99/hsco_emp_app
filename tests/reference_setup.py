"""Builds engine inputs from the real reference files + the HR knowledge encoded in the manual MIS.



Used by tests/test_reference_reconciliation.py (skipped when reference/ is absent â€” it is git-ignored)."""

from datetime import date

from pathlib import Path



from app.engine.model import DayOverride, Employee, P_ALL_SAT_OFF, P_CUSTOM, Policy

from app.importers import parsers as P

from app.importers.readers import read_path



REF = Path(__file__).resolve().parent.parent / "reference"
SUMMARY_NAME = next((p.name for p in sorted(REF.glob("Attendance Summary Report*.xls*"))), "Attendance Summary Report.xls")



# Employee-level settings HR applies by hand (derived in docs/DATA_ANALYSIS.md "Manual corrections")

WEEKLY_OFF = {"HS053": P_ALL_SAT_OFF, "HS162": P_ALL_SAT_OFF, "HS192": P_ALL_SAT_OFF, "HS068": P_CUSTOM}

OFF_WEEKDAYS = {"HS068": (2,)}   # Wednesday off, works weekends

HOURS_PER_DAY = {"HS138": 8.0, "HS192": 8.0}

EXEMPT = {"HS014": "Treated as Partner", "HS055": "Treated as Partner"}

CALENDAR_2HOL = {"HS207"}   # greytHR calendar missing: both 02-Oct and 20-Oct holidays

# month-level decisions HR took that the data alone cannot justify (overrides in the app)

MONTH_OVERRIDES = {

    "HS158": {"exempt": True},                                  # "Exception"

    "HS134": {"leave_days": 2, "absent_days": 3},               # comp-off not earned

    "HS138": {"leave_days": 4, "absent_days": 1},               # leave balance exhausted

    "HS129": {"leave_days": 1, "absent_days": 12},              # LOP counted as absence here

}

# HR disallowed a 'present' day that had no punch at all

DAY_OVERRIDES = [DayOverride("HS197", date(2026, 10, 23), s1="A", s2="A", work_min=0, reason="No punch — disallowed")]





def load(name):

    sheets = read_path(REF / name)

    dets = P.detect(sheets)

    return sheets, dets





def build():

    out = {}

    s, d = load("Attendance Muster Report.xlsx")

    dm = next(x for x in d if x.kind == "muster")

    out["muster"], _ = P.parse_muster(s[dm.sheet], dm)

    out["period"] = P.detect_period(dm, s[dm.sheet])

    s, d = load("EmployeeSwipeDetails.xlsx")

    ds = next(x for x in d if x.kind == "swipes")

    out["swipes"], _, _ = P.parse_swipes(s[ds.sheet], ds)

    s, d = load("MIS Report October 2026_Manual.xlsx")

    dmis = next(x for x in d if x.kind == "mis_manual")

    out["mis"] = {r["emp_no"]: r for r in P.parse_mis_manual(s[dmis.sheet], dmis)}

    s, d = load("DateOfJoiningReport.xlsx")

    dd = next(x for x in d if x.kind == "doj")

    out["doj"] = {r["emp_no"]: r for r in P.parse_doj(s[dd.sheet], dd)}

    s, d = load(SUMMARY_NAME)

    dsu = next(x for x in d if x.kind == "summary")

    out["summary"] = {r["emp_no"]: r for r in P.parse_summary(s[dsu.sheet], dsu)}

    emps = {}

    for e, r in out["mis"].items():

        emps[e] = Employee(

            emp_no=e, name=r["name"], group=r["group"], location=r["location"],

            doj=out["doj"].get(e, {}).get("doj"),

            weekly_off=WEEKLY_OFF.get(e, "MUSTER"), off_weekdays=OFF_WEEKDAYS.get(e, (5, 6)), hours_per_day=HOURS_PER_DAY.get(e),

            exempt=e in EXEMPT, exempt_reason=EXEMPT.get(e, ""),

            holidays={date(2026, 10, 2): "Gandhi Jayanti", date(2026, 10, 20): "Dussehra"} if e in CALENDAR_2HOL else None,

        )

    out["employees"] = emps

    out["policy"] = Policy()

    return out

