"""End-to-end over HTTP with the REAL October-2026 exports (skipped when reference/ is absent).

Mirrors what HR will do at go-live: import all exports + last manual MIS, set the few employee settings HR applies by
hand, record the month-specific decisions as overrides, then check the reconciliation page and the exports.
"""
import io
import re

import pytest
from openpyxl import load_workbook

from tests.conftest import HAS_REF, REF, csrf_of, login_full, make_user

pytestmark = pytest.mark.skipif(not HAS_REF, reason="reference/ (real data, git-ignored) not present")

FILES = ["MIS Report October 2026_Manual.xlsx", "DateOfJoiningReport.xlsx", "Attendance Summary Report.xls",
         "EmployeeSwipeDetails.xlsx", "Attendance Muster Report.xlsx"]


def _emp_form(c, emp_no, **changes):
    html = c.get(f"/employees/{emp_no}").text
    val = lambda n: (re.search(rf'name="{n}" value="([^"]*)"', html) or [None, ""])[1]  # noqa: E731
    data = {"csrf": csrf_of(c), "emp_no": emp_no, "name": val("name"), "group": val("group"), "location": val("location"),
            "weekly_off": "MUSTER", "doj": val("doj"), "is_active": "1"}
    data.update(changes)
    r = c.post("/employees/save", data=data, follow_redirects=True)
    assert "Employee saved" in r.text


def test_real_month_end_to_end(client):
    c = client
    make_user()
    login_full(c)
    csrf = csrf_of(c, "/imports")
    files = [("files", (f, (REF / f).read_bytes(), "application/octet-stream")) for f in FILES]
    r = c.post("/imports", data={"csrf": csrf}, files=files)
    assert r.status_code == 200 and "Unexpected" not in r.text and "Could not" not in r.text, r.text[-2000:]
    for label in ("Attendance Muster Report", "Employee Swipe Details", "Date of Joining Report",
                  "Attendance Summary Report", "Manual MIS working"):
        assert label in r.text
    assert "possible exits" in r.text  # HS098/HS102/HS104 not in active DOJ list

    # --- employee settings HR applies by hand (become permanent master data)
    for e in ("HS053", "HS162", "HS192"):
        _emp_form(c, e, weekly_off="ALL_SAT_OFF", **({"hours_per_day": "8"} if e == "HS192" else {}))
    _emp_form(c, "HS068", weekly_off="CUSTOM", off_weekdays="2")
    _emp_form(c, "HS138", hours_per_day="8")
    for e in ("HS014", "HS055"):
        _emp_form(c, e, exempt="1", exempt_reason="Treated as Partner")
    csrf = csrf_of(c, "/settings")
    c.post("/settings/calendars", data={"csrf": csrf, "name": "Gandhi Jayanti + Dussehra"})
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import HolidayCalendar
    with session_scope() as db:
        cid = db.scalar(select(HolidayCalendar.id))
    c.post(f"/settings/calendars/{cid}/holiday", data={"csrf": csrf, "day": "2026-10-02", "name": "Gandhi Jayanti"})
    c.post(f"/settings/calendars/{cid}/holiday", data={"csrf": csrf, "day": "2026-10-20", "name": "Dussehra"})
    _emp_form(c, "HS207", holiday_calendar_id=str(cid))

    # --- month decisions recorded as overrides
    def mo(emp, field, value, reason):
        r = c.post(f"/periods/2026-10/employee/{emp}/month-override",
                   data={"csrf": csrf_of(c), "field": field, "value": value, "reason": reason}, follow_redirects=True)
        assert "overridden" in r.text
    mo("HS158", "exempt", "yes", "Exception approved")
    mo("HS134", "leave_days", "2", "Comp-off not earned")
    mo("HS134", "absent_days", "3", "Comp-off not earned")
    mo("HS138", "leave_days", "4", "Leave balance exhausted")
    mo("HS138", "absent_days", "1", "Leave balance exhausted")
    mo("HS129", "leave_days", "1", "LOP treated as absence")
    mo("HS129", "absent_days", "12", "LOP treated as absence")
    r = c.post("/periods/2026-10/employee/HS197/day-override",
               data={"csrf": csrf_of(c), "day": "2026-10-23", "s1": "A", "s2": "A", "work_hm": "00:00",
                     "reason": "Present without punch - disallowed"}, follow_redirects=True)
    assert "Day override saved" in r.text

    # --- reconciliation page
    html = c.get("/reconciliation?period=2026-10").text
    m = re.search(r"Identical employees</div>\s*<div class=\"v\">(\d+) / (\d+)", html)
    assert m and (int(m.group(1)), int(m.group(2))) == (72, 97), m and m.groups()
    # the other 19 of the earlier 91 differ only by app rules: WFH on own off days (17, v1.6.4) and the
    # ½-day deduction per WFH day over the limit (HS016, HS195; v1.6.5) — HR applied neither in October
    assert "19 more differ only by an app rule" in html and "not counted under the app" in html

    # --- dashboard + flags + drill-down on real data
    html = c.get("/?period=2026-10").text
    assert "October 2026" in html and "Over WFH limit" in html
    assert c.get("/flags?period=2026-10&code=WFH_OVER_LIMIT").text.count("data-wfh=\"") == 2      # HS179, HS195 (v1.6.4 off days; v1.7.4 closed Saturdays: HS016)
    for e in ("HS001", "HS068", "HS207", "PC006"):
        assert c.get(f"/periods/2026-10/employee/{e}").status_code == 200

    # --- Excel: same deductions as the manual sheet (+ the HS207 finding)
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    ws = wb["Payroll Leave Sheet"]
    from sqlalchemy import select as s_
    from app.models import MonthResult
    with session_scope() as db:
        R = list(db.scalars(s_(MonthResult)))
    ded = {r.emp_no: r.data["shortfall_deduction_days"] for r in R if r.data.get("shortfall_deduction_days")}
    assert ded == {"HS112": 1.0, "HS138": 0.5, "HS205": 0.5, "HS207": 0.5}
    # + ½ day per WFH day over the limit (v1.6.5) — total in the payroll sheet's deduction column
    assert {r.emp_no: r.data["wfh_deduction_days"] for r in R if r.data.get("wfh_deduction_days")} ==         {"HS179": 1.0, "HS195": 0.5}
    rowof = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    assert "+CEILING(MAX(0,J" in ws.cell(row=rowof["HS179"], column=22).value
    assert ws.max_row >= 5 + 97
    assert "Reconciliation" in wb.sheetnames
    from tests.reference_setup import build
    assert build()["mis"]["PC003"]["name"] in c.get("/insights").text


@pytest.mark.skipif(not (REF / "Employee Swipe Details.xlsx").exists(), reason="Nov-2026 swipe export not present")
def test_real_signins_with_coordinates(client):
    """The real November punch log with GPS: imported with October's exports in one go, every sign-in gets a place,
    October is untouched, November (no muster yet) has a Locations page, suggestions and the office check work."""
    from sqlalchemy import func, select
    from app.db import session_scope
    from app.models import MonthResult, Period, SwipeRow
    c = client
    make_user()
    login_full(c)
    files = [("files", (f, (REF / f).read_bytes(), "x")) for f in FILES + ["Employee Swipe Details.xlsx"]]
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=files)
    assert r.status_code == 200 and "Unexpected" not in r.text and "not imported yet" not in r.text
    assert "Employee Swipe Details.xlsx" in r.text and "November 2026" in r.text
    with session_scope() as db:
        nov = db.scalar(select(Period).where(Period.year == 2026, Period.month == 11))
        gps = db.scalar(select(func.count()).where(SwipeRow.period_id == nov.id, SwipeRow.lat.is_not(None)))
        unnamed = db.scalar(select(func.count()).where(SwipeRow.period_id == nov.id, SwipeRow.lat.is_not(None),
                                                       (SwipeRow.place == "") | SwipeRow.place.is_(None)))
        assert gps > 100 and unnamed == 0
        assert all(6 <= la <= 37.5 and 68 <= lo <= 97.5 for la, lo in db.execute(
            select(SwipeRow.lat, SwipeRow.lon).where(SwipeRow.period_id == nov.id, SwipeRow.lat.is_not(None))))
        assert db.scalar(select(func.count()).where(MonthResult.period_id == nov.id)) == 0      # no muster yet
        oct_rows = db.scalar(select(func.count()).select_from(MonthResult).join(Period).where(Period.month == 10))
        assert oct_rows == 97
    page = c.get("/locations?period=2026-11")
    assert page.status_code == 200 and "November 2026" in page.text and "Bandra Kurla Complex" in page.text
    assert c.get("/").status_code == 200 and "October 2026" in c.get("/").text              # default view stays October
    page = c.get("/locations?period=2026-11").text
    assert "Ghatkopar, Mumbai Suburban, Maharashtra" in page and "Office check" not in page
    # November isn't calculated, but its punches & locations can be downloaded
    from openpyxl import load_workbook
    r = c.get("/exports/2026-11/locations.xlsx")
    assert r.status_code == 200
    rows = [[x.value for x in row] for row in load_workbook(io.BytesIO(r.content))["Punches"].iter_rows(min_row=6)]
    assert len(rows) > 1000 and sum(1 for x in rows if x[10] is not None) > 100
    assert all(x[5] for x in rows if x[10] is not None)                       # every GPS punch has a place name


CAT_DIR = REF / "employeecategoryreportandholidaylists"


@pytest.mark.skipif(not CAT_DIR.exists(), reason="category report / holiday lists not present")
def test_real_categories_and_holiday_lists(client):
    """v1.7 on the real files: greytHR's category report + every category's holiday list on top of October."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Category, Employee, HolidayCalendar, MonthResult
    c = client
    make_user()
    login_full(c)
    base = ["DateOfJoiningReport.xlsx", "EmployeeSwipeDetails (1).xlsx", "Attendance Muster Report.xlsx"]
    files = [("files", (f, (REF / f).read_bytes(), "application/octet-stream")) for f in base]
    files += [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in sorted(CAT_DIR.glob("*.xlsx"))]
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=files)
    assert r.status_code == 200 and "Unexpected" not in r.text and "Could not" not in r.text, r.text[-2000:]
    assert "Employee category report" in r.text and "HSCO (Carlyle) 2026: 16 holidays loaded" in r.text
    assert "taken as 01-May-2026" in r.text and "taken as 25-Dec-2026" in r.text      # printed list's year typos
    with session_scope() as db:
        emps = {e.emp_no: e for e in db.scalars(select(Employee))}
        cats = {x.name: x for x in db.scalars(select(Category))}
        cal = {x.id: x for x in db.scalars(select(HolidayCalendar))}
        assert sum(1 for e in emps.values() if e.category) == 96 and "TOTAL NUMBER OF RECORDS : 101" not in emps
        assert {k: sum(1 for e in emps.values() if e.category == k) for k in cats} == {
            "HSCO (Mum)": 66, "HSCO (Carlyle)": 14, "HSCO (CDTIPL)": 6, "HSCO (CDTIPL) Boutique": 3, "HSCO (Delhi)": 2,
            "HSCO (MDS)": 1, "Article": 4, "HSCO (CBV)": 0}
        assert emps["HS118"].category == "Article" and emps["HS118"].additional_category == "HSCO (Mum)"
        assert emps["HS126"].additional_category == "Article"                    # cached VLOOKUP value
        assert emps["HS068"].category == "HSCO (CDTIPL) Boutique"                # report spelling kept
        mds = cal[cats["HSCO (MDS)"].holiday_calendar_id]
        assert len(mds.holidays) == 12 and sum(h.optional for h in mds.holidays) == 2   # two MDS files → one list
        assert not any(x.is_default for x in cal.values())
        res = {r.emp_no: r for r in db.scalars(select(MonthResult))}
    d2 = lambda e: next(x for x in res[e].days if x["date"] == "2026-10-02")  # noqa: E731
    assert res["HS007"].category == "HSCO (Carlyle)" and d2("HS007")["type"] == "HOLIDAY"       # Carlyle list
    assert d2("HS002")["type"] == "WORKDAY"                                                      # Mum list: none
    st = c.get("/settings").text
    conf = st.split('id="cat-conf"')[1].split("</table>")[0]
    assert "2026-10-02" in conf and "HSCO (Carlyle)" in conf and "Mahatma Gandhi Jayanti" in conf
    assert "No holiday list yet" in st and "HSCO (CDTIPL) (6)" in st and "HSCO (Delhi) (2)" in st
    ins = c.get("/insights?period=2026-10").text
    assert "Categories compared" in ins and "HSCO (Carlyle)" in ins
