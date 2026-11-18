"""HTTP-level integration tests: authentication & 2FA, CSRF, roles, imports, calculations, overrides, locking,
exports, admin. Uses synthetic fixtures (no real data needed)."""
import io
import re
import time

import pyotp
import pytest
from openpyxl import load_workbook

from tests.conftest import ADMIN_PW, csrf_of, login_full, make_user


def _csrf(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html) or re.search(r'name="csrf" content="([^"]+)"', html)
    return m.group(1)


def _upload(client, paths, period=""):
    csrf = csrf_of(client, "/imports")
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post("/imports", data={"csrf": csrf, "period": period}, files=files)


def _result(emp):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    with session_scope() as db:
        r = db.scalar(select(MonthResult).where(MonthResult.emp_no == emp))
        return dict(r.data), list(r.flags)


# ------------------------------------------------------------------------------------------------ auth

def test_requires_login(client):
    for path in ("/", "/periods/2026-10", "/employees", "/imports", "/settings", "/users", "/audit", "/exports"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert client.get("/login").status_code == 200
    assert "create-admin" in client.get("/login").text  # first-run hint


def test_password_lockout(client):
    make_user()
    for _ in range(5):
        r = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 401 and "locked" in r.text.lower()


def test_forced_totp_enrolment_then_challenge(client):
    make_user()
    secret = login_full(client)
    assert client.get("/").status_code == 200
    # logout -> login again requires TOTP
    client.post("/logout", data={"csrf": csrf_of(client)})
    assert client.get("/", follow_redirects=False).status_code == 303
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/"}, follow_redirects=True)
    assert "Two-step verification" in r.text
    csrf = _csrf(r.text)
    # MFA not passed yet -> protected pages bounce to /mfa
    assert client.get("/employees", follow_redirects=False).headers["location"] == "/mfa"
    bad = client.post("/mfa/totp", data={"code": "000000", "csrf": csrf, "next": "/"}, follow_redirects=True)
    assert "not accepted" in bad.text
    code = pyotp.TOTP(secret).at(time.time() + 30)
    ok = client.post("/mfa/totp", data={"code": code, "csrf": csrf, "next": "/employees"}, follow_redirects=False)
    assert ok.headers["location"] == "/employees"
    # replay of the same code is rejected
    client.post("/logout", data={"csrf": csrf_of(client)})
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
    replay = client.post("/mfa/totp", data={"code": code, "csrf": _csrf(r.text), "next": "/"}, follow_redirects=True)
    assert "not accepted" in replay.text


def test_recovery_codes_single_use(client):
    make_user()
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
    secret = re.search(r'id="totp-secret">([A-Z2-7]+)<', r.text).group(1)
    r2 = client.post("/security/totp/confirm", data={"code": pyotp.TOTP(secret).now(), "csrf": _csrf(r.text)})
    codes = re.findall(r"<div>([0-9A-F]{6}-[0-9A-F]{6})</div>", r2.text)
    assert len(codes) == 10
    client.post("/logout", data={"csrf": csrf_of(client)})
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
    ok = client.post("/mfa/recovery", data={"code": codes[0].lower(), "csrf": _csrf(r.text), "next": "/"}, follow_redirects=False)
    assert ok.headers["location"] == "/"
    client.post("/logout", data={"csrf": csrf_of(client)})
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
    again = client.post("/mfa/recovery", data={"code": codes[0], "csrf": _csrf(r.text), "next": "/"}, follow_redirects=True)
    assert "not accepted" in again.text


def test_csrf_enforced(client):
    make_user()
    login_full(client)
    r = client.post("/users", data={"username": "x1", "role": "viewer"})
    assert r.status_code == 400
    r = client.post("/users", data={"username": "x1", "role": "viewer", "csrf": "forged"})
    assert r.status_code == 400


def test_email_otp_and_webauthn_options(client):
    from app.security import core as sec
    sent = {}
    sec.send_email_otp._test_sink = lambda to, code: sent.update(to=to, code=code)
    try:
        make_user(email="admin@example.test")
        login_full(client)
        csrf = csrf_of(client, "/settings")
        client.post("/settings/general#general", data={"csrf": csrf, "smtp_host": "smtp.local", "smtp_from": "app@example.test",
                                                       "smtp_port": "25", "mfa_required": "1", "org_name": "Test Firm"})
        client.post("/security/email/enable", data={"csrf": csrf_of(client)})
        assert sent["to"] == "admin@example.test"
        client.post("/security/email/confirm", data={"csrf": csrf_of(client), "code": sent["code"]})
        assert "Email code" in client.get("/security").text
        # sign in again with an email code
        client.post("/logout", data={"csrf": csrf_of(client)})
        r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
        assert "Email me a code" in r.text
        client.post("/mfa/email/send", data={"csrf": _csrf(r.text), "next": "/"})
        ok = client.post("/mfa/email", data={"csrf": _csrf(r.text), "code": sent["code"], "next": "/"}, follow_redirects=False)
        assert ok.headers["location"] == "/"
        # passkey registration options are produced (browser ceremony itself is exercised in e2e)
        j = client.post("/security/webauthn/options", headers={"X-CSRF-Token": csrf_of(client)}).json()
        assert j["rp"]["id"] == "testserver" and j["challenge"]
    finally:
        del sec.send_email_otp._test_sink


def test_roles_and_user_admin(client):
    make_user()
    login_full(client)
    r = client.post("/users", data={"csrf": csrf_of(client, "/users"), "username": "board1", "role": "viewer",
                                    "full_name": "Board Member"})
    temp = re.search(r'id="temp-pw"[^>]*>([^<]+)<', r.text).group(1)
    client.post("/logout", data={"csrf": csrf_of(client)})
    # new user: password ok -> forced enrolment -> forced password change
    r = client.post("/login", data={"username": "board1", "password": temp}, follow_redirects=True)
    secret = re.search(r'id="totp-secret">([A-Z2-7]+)<', r.text).group(1)
    client.post("/security/totp/confirm", data={"code": pyotp.TOTP(secret).now(), "csrf": _csrf(r.text)})
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/security")
    weak = client.post("/security/password", data={"csrf": csrf_of(client), "current": temp, "new": "short", "confirm": "short"},
                       follow_redirects=True)
    assert "Password needs" in weak.text
    client.post("/security/password", data={"csrf": csrf_of(client), "current": temp, "new": "Board!Member#2026",
                                            "confirm": "Board!Member#2026"})
    assert client.get("/").status_code == 200
    for path in ("/imports", "/settings", "/users", "/audit"):
        assert client.get(path).status_code == 403, path


# ------------------------------------------------------------------------------------------------ data flow

@pytest.fixture()
def loaded(client, fixtures):
    make_user()
    login_full(client)
    r = _upload(client, [fixtures["master"], fixtures["doj"], fixtures["swipes"], fixtures["muster"]])
    assert r.status_code == 200 and "Import result" in r.text and "Unexpected" not in r.text, r.text[-3000:]
    return client


def test_import_and_calculations(loaded):
    c = loaded
    d1, _ = _result("T001")
    assert d1["total_week_days"] == 23 and d1["holidays"] == 1 and d1["days_present"] == 22
    assert d1["deduction_days"] == 0.5
    d2, _ = _result("T002")
    assert d2["extra_days"] == 1 and d2["compoff_earned"] == 1 and d2["extra_hours"] == 6
    assert d2["deduction_days"] == 1.0
    d3, _ = _result("T003")
    assert d3["exempt"] and d3["deduction_days"] == 0 and d3["shortfall_hours"] > 0
    d4, f4 = _result("T004")
    assert d4["wfh_days"] == 4 and {"WFH_OVER_LIMIT", "PERSISTENT_LATE"} <= {f["code"] for f in f4}
    d5, f5 = _result("T005")
    assert d5["pl_days"] == 1.5 and d5["lop_days"] == 1 and d5["unauth_absent_days"] == 1
    d6, f6 = _result("T006")
    assert d6["is_new_joinee"] and d6["total_week_days"] == 14   # 13-Oct onwards, all Saturdays off, 20-Oct holiday
    assert d6["missing_punch_days"] == 2 and d6["permission_swipes"] == 3
    assert "NEW_JOINEE" in {f["code"] for f in f6}
    # T007 (future joiner from DOJ) exists in master but has no attendance this month
    assert "T007" in c.get("/employees").text


def test_all_pages_render(loaded):
    c = loaded
    pages = ["/", "/?period=2026-10", "/periods/2026-10", "/employees", "/employees/T001", "/employees/new", "/flags",
             "/flags?code=WFH_OVER_LIMIT", "/reconciliation", "/imports", "/settings", "/users", "/audit", "/exports",
             "/security", "/security/enrol", "/insights", "/insights?period=2026-10"]
    pages += [f"/periods/2026-10/employee/T00{i}" for i in range(1, 7)]
    for p in pages:
        r = c.get(p)
        assert r.status_code == 200, (p, r.text[:300])
        assert "Traceback" not in r.text
    assert c.get("/periods/2099-01").status_code == 404
    assert c.get("/periods/2026-10/employee/NOPE").status_code == 404
    html = c.get("/").text
    assert "<h1>October 2026</h1>" in html and "Over WFH limit" in html and "Month-end checklist" in html


def test_overrides_ledger_and_audit(loaded):
    c = loaded
    base = "/periods/2026-10/employee/T001"
    csrf = csrf_of(c, base)
    # month override: exception -> no deduction
    r = c.post(base + "/month-override", data={"csrf": csrf, "field": "exempt", "value": "yes", "reason": "Partner approval"},
               follow_redirects=True)
    assert "overridden" in r.text
    d, _ = _result("T001")
    assert d["deduction_days"] == 0 and d["exempt"]
    # reason is mandatory
    r = c.post(base + "/month-override", data={"csrf": csrf, "field": "required_hours", "value": "150", "reason": " "},
               follow_redirects=True)
    assert "reason is required" in r.text.lower()
    # day override: mark 5-Oct absent
    c.post(base + "/day-override", data={"csrf": csrf, "day": "2026-10-05", "s1": "A", "s2": "A", "work_hm": "00:00",
                                         "reason": "No punch, not approved"})
    d, _ = _result("T001")
    assert d["unauth_absent_days"] == 1 and d["days_present"] == 21
    # opening balance -> ledger
    c.post(base + "/opening", data={"csrf": csrf, "leave_balance": "0.5", "compoff_balance": "0"})
    d, flags = _result("T001")
    assert d["leave_opening"] == 0.5 and d["leave_debit_absent"] == 1 and d["leave_closing"] == 0
    assert d["salary_deduction_days"] == 0.5 and d["payroll_lop_days"] == 0.5
    assert "LEAVE_EXHAUSTED" in {f["code"] for f in flags}
    audit = c.get("/audit").text
    for a in ("override.month", "override.day", "leave.opening", "import"):
        assert a in audit


def test_settings_changes_recompute(loaded):
    c = loaded
    csrf = csrf_of(c, "/settings")
    form = {"csrf": csrf, "wfh_limit_days": "5", "exempt_groups": "Partner, SS, Article, Trainee",
            "metrics_exclude_groups": "Partner", "wfh_location_types": "work from home, wfh", "lop_bucket": "leave",
            "wfh_method": "distinct_days", "leave_credit_mode": "annual", "absent_debits_leave": "1",
            "cof_overdraft_to_leave": "1", "late_sitting_after": "21:00", "default_shift_start": "10:00",
            "default_shift_end": "18:30", "status_map": "P=present\nA=absent\nPL=leave\nCOF=compoff\nLOP=lop\nOFF=off\nH=off",
            "shift_overrides": ""}
    c.post("/settings/policy", data=form)
    _, f4 = _result("T004")
    assert "WFH_OVER_LIMIT" not in {f["code"] for f in f4}
    # holiday calendar: default calendar with 2-Oct and 20-Oct -> everyone gets 2 holidays
    c.post("/settings/calendars", data={"csrf": csrf, "name": "Mumbai 2026", "is_default": "1"})
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import HolidayCalendar
    with session_scope() as db:
        cid = db.scalar(select(HolidayCalendar.id))
    c.post(f"/settings/calendars/{cid}/holiday", data={"csrf": csrf, "day": "2026-10-02", "name": "Gandhi Jayanti"})
    c.post(f"/settings/calendars/{cid}/holiday", data={"csrf": csrf, "day": "2026-10-20", "name": "Dussehra"})
    d1, _ = _result("T001")
    assert d1["holidays"] == 2 and d1["extra_days"] == 1   # worked on 2-Oct -> holiday work
    # named places were removed (v1.6.2): the Settings Locations tab and its routes are gone
    assert c.post("/settings/geofences", data={"csrf": csrf, "name": "HO", "category": "Office", "lat": "18.94",
                                               "lon": "72.82", "radius_m": "150"}).status_code in (404, 405)
    st = c.get("/settings").text
    assert 'data-tab="locations"' not in st and 'data-pane="locations"' not in st and "Suggested places" not in st
    # employee master edit -> audit + recompute
    c.post("/employees/save", data={"csrf": csrf, "emp_no": "T002", "name": "Bilal Khan", "group": "ATA", "location": "Mumbai",
                                    "weekly_off": "MUSTER", "hours_per_day": "8", "is_active": "1"})
    d2, _ = _result("T002")
    assert d2["hours_per_day"] == 8 and d2["deduction_days"] == 0
    assert "employee.update" in c.get("/audit").text


def test_finalise_locks_and_reopen(loaded):
    c = loaded
    csrf = csrf_of(c, "/periods/2026-10")
    r = c.post("/periods/2026-10/finalise", data={"csrf": csrf}, follow_redirects=True)
    assert "finalised" in r.text.lower()
    r = c.post("/periods/2026-10/employee/T001/month-override",
               data={"csrf": csrf, "field": "remark", "value": "x", "reason": "y"})
    assert r.status_code == 409
    r = c.post("/periods/2026-10/recompute", data={"csrf": csrf})
    assert r.status_code == 409
    from tests.fixtures.make_fixtures import write_all
    import tempfile, pathlib
    fx = write_all(pathlib.Path(tempfile.mkdtemp()))
    r = _upload(c, [fx["muster"]])
    assert "finalised" in r.text.lower()
    r = c.post("/periods/2026-10/reopen", data={"csrf": csrf, "reason": "Late regularisation approved"}, follow_redirects=True)
    assert "re-opened" in r.text
    assert "period.reopen" in c.get("/audit").text


def test_exports(loaded):
    c = loaded
    r = c.get("/exports/2026-10.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[:3] == ["Payroll Leave Sheet", "Board Summary", "Flags"]
    for s in ("Late & Punctuality", "WFH & Locations", "Regularisation & Punches", "Leave & Comp-off Ledger",
              "People Movement", "Daily Detail", "Rules Applied", "Scorecard", "Groups Compared"):
        assert s in wb.sheetnames
    ws = wb["Payroll Leave Sheet"]
    hdr = [c_.value for c_ in ws[5]]
    assert hdr[:4] == ["Employee No", "Name", "Group", "Location"]
    rows = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    i = rows["T001"]
    assert ws.cell(row=i, column=9).value == f"=E{i}-F{i}-G{i}-H{i}"
    assert ws.cell(row=i, column=22).value.startswith("=IF(U")
    assert ws.cell(row=i, column=22).value.endswith(f"))+CEILING(MAX(0,J{i}-2),1)*0.5")   # WFH over the limit (v1.6.5)
    # Excel formula logic replicated: balance + WFH -> deduction must equal the app's value
    import math
    for emp in ("T001", "T004"):
        i = rows[emp]
        e, f, g, h, j = (ws.cell(row=i, column=k).value for k in (5, 6, 7, 8, 10))
        o, p, t = (ws.cell(row=i, column=k).value for k in (15, 16, 20))
        bal = round((o - p) - (e - f - g - h) * t, 2)
        hours = 0 if bal >= 0 or ws.cell(row=i, column=21).value == "Yes" else math.ceil(round(-bal, 4) / 4) * 0.5
        ded = hours + math.ceil(max(0, j - 2)) * 0.5
        assert ded == _result(emp)[0]["deduction_days"], emp
    d4, f4 = _result("T004")             # Article: exempt for hours, but deducted for WFH over the limit (v1.6.6)
    assert d4["wfh_days"] == 4 and d4["wfh_excess_days"] == 2 and d4["wfh_deduction_days"] == 1.0 and d4["exempt"]
    assert any("1 day(s) deducted from leave" in x["message"] for x in f4 if x["code"] == "WFH_OVER_LIMIT")
    i3 = rows["T003"]                    # Partner: the WFH term is not in the formula at all
    assert "CEILING(MAX(0,J" not in ws.cell(row=i3, column=22).value
    led = [c_.value for c_ in wb["Leave & Comp-off Ledger"][5]]
    assert led[6:9] == ["Shortfall deduction", "WFH over-limit deduction", "Unauthorised absence"]
    # the HTML board pack was removed in v1.6 (the app itself is the board view)
    assert c.get("/exports/2026-10.html").status_code == 404
    assert "/exports/2026-10.html" not in c.get("/exports").text and "Board pack" not in c.get("/").text
    # templates
    for t in ("employee_master", "holidays", "leave_opening"):
        r = c.get(f"/exports/templates/{t}.xlsx")
        assert r.status_code == 200
        load_workbook(io.BytesIO(r.content))
    # round-trip the pre-filled employee master template through the importer
    r = c.get("/exports/templates/employee_master.xlsx")
    tmp = io.BytesIO(r.content)
    csrf = csrf_of(c, "/imports")
    res = c.post("/imports", data={"csrf": csrf}, files=[("files", ("employee_master.xlsx", tmp.getvalue(), "application/octet-stream"))])
    assert "Employee master" in res.text and "Unexpected" not in res.text


def test_bad_upload_reported(loaded):
    c = loaded
    csrf = csrf_of(c, "/imports")
    r = c.post("/imports", data={"csrf": csrf}, files=[("files", ("notes.xlsx", b"not an excel file", "application/octet-stream"))])
    assert r.status_code == 200 and "Could not read" in r.text


def test_enrol_without_pending_secret_and_unicode_flash(client):
    """Regression: 'Enrolment expired — …' (em dash) crashed the latin-1 cookie header with HTTP 500."""
    make_user()
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=True)
    csrf = _csrf(r.text)
    from app.db import session_scope
    from app.models import UserSession
    from sqlalchemy import select
    with session_scope() as db:                      # simulate a lost/expired enrolment secret
        for s in db.scalars(select(UserSession)):
            s.pending = {}
    r = client.post("/security/totp/confirm", data={"code": "123456", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "Enrolment expired — scan the new QR code." in page.text


def test_db_dependency_commits_before_response():
    """Regression: a request's write transaction must be committed before its response is sent, otherwise the
    browser's next request hits 'database is locked'."""
    import inspect
    from app import web
    from app.routes import auth
    src = inspect.getsource(web) + inspect.getsource(auth)
    assert "Depends(get_db)" not in src and src.count('Depends(get_db, scope="function")') >= 4


def test_review_queue_search_and_navigation(loaded):
    c = loaded
    # review queue lists actionable flags; mark one reviewed, then a whole type
    page = c.get("/flags?period=2026-10")
    assert page.status_code == 200 and "Review queue" in page.text and "Mark reviewed" in page.text
    tok = csrf_of(c, "/flags")
    j = c.post("/flags/review", json={"period": "2026-10", "emp_no": "T004", "code": "WFH_OVER_LIMIT", "action": "review",
                                      "note": "OK-T004-note"}, headers={"X-CSRF-Token": tok}).json()
    assert j["ok"] and j["reviewed"] == 1 and j["total"] > 1
    reviewed = c.get("/flags?period=2026-10&code=WFH_OVER_LIMIT&show=reviewed").text
    assert "OK-T004-note" in reviewed and "Re-open" in reviewed
    assert "OK-T004-note" not in c.get("/flags?period=2026-10&show=open").text
    j = c.post("/flags/review", json={"period": "2026-10", "emp_no": "*", "code": "PERSISTENT_LATE", "action": "review"},
               headers={"X-CSRF-Token": tok}).json()
    assert j["count"] >= 1
    j2 = c.post("/flags/review", json={"period": "2026-10", "emp_no": "T004", "code": "WFH_OVER_LIMIT", "action": "reopen"},
                headers={"X-CSRF-Token": tok}).json()
    assert j2["reviewed"] == j["reviewed"] - 1
    # reviews survive recomputation
    c.post("/periods/2026-10/recompute", data={"csrf": tok})
    assert "flag.review" in c.get("/audit").text
    assert c.post("/flags/review", json={"period": "2026-10", "emp_no": "T001", "code": "X"}).status_code == 400  # no CSRF
    # overview shows the month-end checklist with review progress
    html = c.get("/").text
    assert "Month-end checklist" in html and re.search(r"\d+ of \d+ reviewed", html)
    # quick search index
    people = c.get("/api/employees").json()
    t4 = next(p for p in people if p["emp_no"] == "T004")
    assert t4["name"] == "Dev Mehta" and t4["href"] == "/periods/2026-10/employee/T004" and t4["initials"] == "DM"
    # previous / next employee on the drill-down
    html = c.get("/periods/2026-10/employee/T003").text
    assert 'href="/periods/2026-10/employee/T002"' in html and 'href="/periods/2026-10/employee/T004"' in html
    # sidebar badge counts open exceptions
    assert re.search(r'Review queue<span class="count">\d+</span>', c.get("/employees").text)


def test_viewer_cannot_review(loaded):
    c = loaded
    from tests.conftest import make_user as mk
    mk("viewer1", "viewer", "View!Only#2026")
    c.post("/logout", data={"csrf": csrf_of(c)})
    login_full(c, "viewer1", "View!Only#2026")
    page = c.get("/flags?period=2026-10")
    assert page.status_code == 200 and "Mark reviewed" not in page.text
    r = c.post("/flags/review", json={"period": "2026-10", "emp_no": "T004", "code": "WFH_OVER_LIMIT"},
               headers={"X-CSRF-Token": csrf_of(c)})
    assert r.status_code == 403


def test_bulk_review_and_employee_filter(loaded):
    c = loaded
    tok = csrf_of(c, "/flags")
    page = c.get("/flags?period=2026-10&emp=T004&show=all").text
    assert "Showing only <b>Dev Mehta</b>" in page and "Show everyone" in page
    emps = set(re.findall(r'<tr data-emp="([^"]+)"', page))
    assert emps == {"T004"}
    codes = re.findall(r'<tr data-emp="T004" data-code="([^"]+)"', page)
    assert len(codes) >= 2
    items = [{"emp_no": "T004", "code": cd} for cd in codes[:2]] + [{"emp_no": "NOPE", "code": "X"}]   # bogus item ignored
    j = c.post("/flags/review", json={"period": "2026-10", "items": items, "action": "review", "note": "bulk-ok"},
               headers={"X-CSRF-Token": tok}).json()
    assert j["count"] == 2
    page = c.get("/flags?period=2026-10&emp=T004&show=reviewed").text
    assert page.count("bulk-ok") == 2
    j = c.post("/flags/review", json={"period": "2026-10", "items": items[:2], "action": "reopen"},
               headers={"X-CSRF-Token": tok}).json()
    assert j["count"] == 2 and "bulk-ok" not in c.get("/flags?period=2026-10&emp=T004&show=reviewed").text
    # the employee page links into the filtered queue
    html = c.get("/periods/2026-10/employee/T004").text
    assert 'href="/flags?period=2026-10&amp;emp=T004&amp;show=all"' in html or 'href="/flags?period=2026-10&emp=T004&show=all"' in html
    assert 'data-tags="' in html and 'data-lg="late"' in html


def test_unrecognised_file_mapped_by_hand_and_remembered(loaded):
    """A report the app cannot place is not imported blindly: HR maps the columns once, the import runs with that
    mapping, and the next file with the same columns is recognised automatically."""
    from openpyxl import Workbook
    c = loaded
    wb = Workbook()
    ws = wb.active
    ws.append(["Ref", "Dt", "AM", "PM", "Came", "Went"])          # meaningless headers
    for d in range(1, 4):
        ws.append(["T001", f"0{d}-10-2026", "P", "P", f"0{d}-10-2026 09:30", f"0{d}-10-2026 19:00"])
    buf = io.BytesIO()
    wb.save(buf)
    csrf = csrf_of(c, "/imports")
    r = c.post("/imports", data={"csrf": csrf}, files=[("files", ("odd_report.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert r.status_code == 200 and "not imported yet" in r.text and "Map columns" in r.text
    link = re.search(r'href="(/imports/map/[0-9a-f]{32}[^"]*)"', r.text).group(1).replace("&amp;", "&")
    token = link.split("/")[3].split("?")[0]
    page = c.get(link + ("&" if "?" in link else "?") + "kind=muster")
    assert page.status_code == 200 and "Tell the app what each column means" in page.text and "Came" in page.text
    # missing required fields → shown again with a message, nothing imported
    r = c.post(f"/imports/map/{token}", data={"csrf": _csrf(page.text), "sheet": "Sheet", "kind": "muster", "row": "1",
                                              "f_emp_no": "0"})
    assert "Still needed" in r.text
    # same column twice → rejected
    r = c.post(f"/imports/map/{token}", data={"csrf": _csrf(page.text), "sheet": "Sheet", "kind": "muster", "row": "1",
                                              "f_emp_no": "0", "f_date": "1", "f_s1": "1"})
    assert "same column" in r.text
    r = c.post(f"/imports/map/{token}", data={"csrf": _csrf(page.text), "sheet": "Sheet", "kind": "muster", "row": "1",
                                              "f_emp_no": "0", "f_date": "1", "f_s1": "2", "f_s2": "3", "f_in_time": "4",
                                              "f_out_time": "5", "remember": "1"})
    assert r.status_code == 200 and "your mapping" in r.text and "3 rows" in r.text, r.text[-1500:]
    assert "Remembered file layouts" in r.text
    # second upload of the same layout → recognised from memory, imported without asking
    csrf = csrf_of(c, "/imports")
    r = c.post("/imports", data={"csrf": csrf}, files=[("files", ("odd_report_nov.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert "remembered layout" in r.text and "not imported yet" not in r.text
    # expired / bogus tokens are handled
    assert c.get("/imports/map/" + "0" * 32, follow_redirects=False).status_code == 303
    assert c.get("/imports/map/../../etc", follow_redirects=False).status_code in (303, 404)
    # forget the layout
    from app.db import session_scope
    from app.services.imports import get_layouts
    with session_scope() as db:
        sig = get_layouts(db)[0]["signature"]
    r = c.post(f"/imports/layouts/{sig}/delete", data={"csrf": csrf_of(c, "/imports")}, follow_redirects=True)
    assert "Remembered file layouts" not in r.text


def test_place_names_upgraded_once(loaded):
    """Swipes named by an older gazetteer are renamed once (draft months recalculated); later runs do nothing."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Period, SwipeRow
    from app.services import geo
    with session_scope() as db:
        pid = db.scalar(select(Period.id))
        sw = SwipeRow(period_id=pid, emp_no="T001", ts=__import__("datetime").datetime(2026, 10, 1, 10), lat=19.1145,
                      lon=72.8712, place="Mumbai (old name)")
        db.add(sw)
    assert geo.upgrade_place_names() >= 1
    with session_scope() as db:
        places = {s.place for s in db.scalars(select(SwipeRow).where(SwipeRow.lat == 19.1145))}
    assert places and all("Andheri" in p for p in places), places
    assert geo.upgrade_place_names() == 0


def test_insights_kpis_and_scorecard(loaded):
    """Insights numbers follow the definitions in METRICS.md on the synthetic month."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Period
    from app.services import insights as INS
    with session_scope() as db:
        p = db.scalar(select(Period).where(Period.year == 2026, Period.month == 10))
        I = INS.insights(db, p)
        assert INS.insights(db, p) is I                       # cached until something is recalculated
        rows = {r["emp_no"]: r for r in I["rows"]}
        k = I["kpis"]
        assert k["headcount"] == len(rows) and 0 <= k["absenteeism_pct"] <= 100 and 0 <= k["punctuality_pct"] <= 100
        for r in rows.values():
            assert 0 <= r["attention"] <= 100
            assert r["absence_12m_days"] >= r["unplanned_days"] and r["absence_12m_spells"] >= r["unplanned_spells"]
            assert "bradford" not in r                                  # removed on request (v1.5.2)
        assert "bradford_bands" not in I and not any("bradford" in k for k in I["kpis"])
        assert len(I["matrix"]["rows"]) == len(rows) and all(len(m["s"]) == len(I["matrix"]["dates"]) for m in I["matrix"]["rows"])
        sc = INS.employee_scorecard(I, "T001")
        assert sc["row"]["emp_no"] == "T001" and 1 <= sc["rank"] <= sc["of"] and len(sc["metrics"]) == 10
    # spells: Fri absent + Mon absent with a weekend between = 1 spell, 2 days; a present day breaks the spell
    days = [{"date": "2026-10-02", "type": "WORKDAY", "b1": "absent", "b2": "absent"},
            {"date": "2026-10-03", "type": "WEEKLY_OFF", "b1": "off", "b2": "off"},
            {"date": "2026-10-05", "type": "WORKDAY", "b1": "lop", "b2": "lop"},
            {"date": "2026-10-06", "type": "WORKDAY", "b1": "present", "b2": "present"},
            {"date": "2026-10-07", "type": "WORKDAY", "b1": "present", "b2": "absent"}]
    assert INS._spells(days) == (2, 2.5)
    html = loaded.get("/insights").text
    assert "Employee scorecard" in html and "Bradford" not in html and "Absence 12 m" in html and 'id="hm"' in html
    html = loaded.get("/periods/2026-10/employee/T001").text
    assert 'data-tab="score"' in html and "Compared with others" in html


def test_ranked_lists_cover_everyone_and_heatmap_total(loaded):
    """v1.6.3: Overview lists hold every qualifying employee (page shows top N + search); heatmap has a Total column."""
    from types import SimpleNamespace as NS
    from app.services.metrics import top_lists
    D = [{"emp_no": f"E{i:02d}", "name": f"Person {i:02d}", "group": "BSS", "very_late_days": i % 7, "regularised_days": 0,
          "missing_punch_days": 0, "wfh_days": 0, "unauth_absent_days": 0, "balance_hours": i - 30.0,
          "late_sitting_days": 0, "compoff_earned": 0, "sandwich_count": 0} for i in range(40)]
    top = top_lists(D, NS(metrics_exclude_groups=[], wfh_limit_days=2))
    assert len(top["late"]) == sum(1 for d in D if d["very_late_days"] >= 1) > 10           # no cut-off at 8/10
    assert [r["value"] for r in top["late"]] == sorted((r["value"] for r in top["late"]), reverse=True)
    assert len(top["shortfall"]) == 30 and top["shortfall"][0]["value"] == 30                # all who are short
    assert len(top["excess_hours"]) == 9
    html = loaded.get("/?period=2026-10").text
    from app.web import ASSET_V                 # v1.6.4: versioned asset URLs, so an update never runs on cached JS/CSS
    assert len(ASSET_V) == 10 and f'/static/app.css?v={ASSET_V}"' in html and f'/static/app.js?v={ASSET_V}"' in html
    assert f'/static/table.js?v={ASSET_V}"' in html and f'/static/icons.svg?v={ASSET_V}#i-' in html
    assert 'href="/static/app.css"' not in html and 'src="/static/app.js"' not in html
    assert html.count("data-toplist") == 8                                     # every ranked list on the Overview
    page = loaded.get("/insights?period=2026-10").text
    assert 'data-key="tot"' in page and 'id="hm-tot-sub"' in page and "<tfoot>" in page and 'class="hm-fd"' in page
    assert page.count('class="num hm-tot" data-v=') == page.count('class="hm-name"') > 0


def test_targeted_holidays_settings_import_and_wfh(loaded):
    """v1.6.4: targeted holidays (Rules & calendars and holiday-list import) apply only to the chosen people; WFH on
    the person's own holiday is not WFH."""
    import io
    from datetime import date
    import openpyxl
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import AuditLog, TargetedHoliday
    c = loaded
    day_of = lambda emp, iso: next(d for d in _result_days(emp) if d["date"] == iso)  # noqa: E731
    wfh_dates = [d["date"] for d in _result_days("T004") if d["wfh"]]
    before, _ = _result("T004")
    assert before["wfh_days"] == len(wfh_dates) == 4 and before.get("offday_wfh_days", 0) == 0
    st = c.get("/settings").text
    assert 'id="targeted"' in st and 'name="groups" value="Article"' in st and 'name="locations" value="Mumbai"' in st
    assert 'name="wfh_excess_deduction_days"' in st and "0 = flag only" in st         # v1.6.5 setting
    csrf = csrf_of(c, "/settings")
    r = c.post("/settings/targeted-holidays", data={"csrf": csrf, "day": "2026-10-07", "name": "X"}, follow_redirects=True)
    assert "Choose who the holiday is for" in r.text
    r = c.post("/settings/targeted-holidays", data={"csrf": csrf, "day": "2026-10-07", "emp_nos": ["NOPE1"]},
               follow_redirects=True)
    assert "Unknown employee number" in r.text
    # a holiday for the Article group on one of T004's WFH days
    wd = wfh_dates[0]
    r = c.post("/settings/targeted-holidays", data={"csrf": csrf, "day": wd, "name": "Article day off",
                                                    "groups": ["Article"]}, follow_redirects=True)
    assert r.status_code == 200 and "Article day off" in r.text and "Targeted holiday" in r.text
    d4 = day_of("T004", wd)
    assert d4["type"] == "HOLIDAY" and d4["holiday"] == "Article day off" and not d4["wfh"]
    assert any("not counted as WFH" in n for n in d4["notes"])
    after, _ = _result("T004")
    assert after["wfh_days"] == 3 and after["offday_wfh_days"] == 1 and after["holidays"] == before["holidays"] + 1
    assert day_of("T001", wd)["type"] == "WORKDAY"                       # BSS: not targeted
    # named employee on top of a location filter
    c.post("/settings/targeted-holidays", data={"csrf": csrf, "day": "2026-10-09", "name": "Pune fest",
                                                "locations": ["Pune"], "emp_nos": ["T001"]})
    assert day_of("T001", "2026-10-09")["type"] == "HOLIDAY" and day_of("T002", "2026-10-09")["type"] == "WORKDAY"
    with session_scope() as db:
        hs = {h.name: h.id for h in db.scalars(select(TargetedHoliday))}
        assert {"holiday.targeted.add"} <= {a.action for a in db.scalars(select(AuditLog))}
    c.post(f"/settings/targeted-holidays/{hs['Article day off']}/delete", data={"csrf": csrf})
    assert day_of("T004", wd)["type"] == "WORKDAY" and _result("T004")[0]["wfh_days"] == 4
    # holiday list import with targeted columns (+ an unknown employee is reported, not guessed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Holiday", "Calendar", "For Locations", "For Groups", "For Employees"])
    ws.append([date(2026, 10, 14), "Navratri (Article)", "", "", "Article", ""])
    ws.append([date(2026, 10, 15), "Asha's day", "", "", "", "Asha Rao, Nobody Here"])
    ws.append([date(2026, 10, 16), "Firm day", "Standard", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")},
               files=[("files", ("holidays.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert r.status_code == 200 and "Holiday list" in r.text and "Nobody Here" in r.text
    assert day_of("T004", "2026-10-14")["type"] == "HOLIDAY" and day_of("T001", "2026-10-14")["type"] == "WORKDAY"
    assert day_of("T001", "2026-10-15")["holiday"] == "Asha's day" and day_of("T002", "2026-10-15")["type"] == "WORKDAY"
    assert day_of("T002", "2026-10-16")["type"] == "HOLIDAY"                 # calendar row: everyone
    t = c.get("/exports/templates/holidays.xlsx")
    hdr = [x.value for x in openpyxl.load_workbook(io.BytesIO(t.content)).active[1]]
    assert hdr[:6] == ["Date", "Holiday", "Calendar", "For Locations", "For Groups", "For Employees"]


def test_rule_change_recalculates_draft_months_once(loaded):
    """An update that changes a calculation rule (RULES_VERSION) recalculates draft months once at start-up."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.engine.compute import RULES_VERSION
    from app.models import MonthResult, Setting
    from app.services import maintenance
    with session_scope() as db:
        r = db.scalar(select(MonthResult).where(MonthResult.emp_no == "T004"))
        r.data = {**r.data, "wfh_days": 99}                       # a figure calculated under older rules
        st = db.get(Setting, "rules_version")
        if st is None:
            db.add(Setting(key="rules_version", value={"v": RULES_VERSION - 1}))
        else:
            st.value = {"v": RULES_VERSION - 1}
    assert maintenance.recalc_on_rules_change() >= 1
    assert _result("T004")[0]["wfh_days"] == 4
    with session_scope() as db:
        assert db.get(Setting, "rules_version").value == {"v": RULES_VERSION}
    assert maintenance.recalc_on_rules_change() == 0                # only once per rules version


def test_punch_exceptions_page_and_excel(loaded):
    """v1.6.7: missing / incorrect punches, regularisations, WFH etc. day by day and by employee, matching the days."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Period
    from app.services.exceptions import month_exceptions
    c = loaded
    with session_scope() as db:
        X = month_exceptions(db, db.scalar(select(Period).where(Period.year == 2026, Period.month == 10)))
    by = {(x["emp_no"], x["date"]): x["issues"] for x in X["rows"]}
    people = {p["emp_no"]: p for p in X["people"]}
    # every flag on a stored day shows up, and nothing else
    for emp in ("T001", "T002", "T004", "T005", "T006"):
        for d in _result_days(emp):
            iss = by.get((emp, d["date"]), [])
            assert ("missing" in iss) == bool(d["missing"]), (emp, d["date"])
            assert ("wfh" in iss) == bool(d["wfh"]), (emp, d["date"])
            assert ("very_late" in iss) == bool(d["very_late"]), (emp, d["date"])
    assert people["T006"]["missing"] >= 1 and (people["T006"]["permission"] + people["T006"]["edited"]) >= 1
    assert people["T004"]["wfh"] == 4 and people["T004"]["wfh_over"] == 2 and people["T004"]["wfh_deduction"] == 1.0
    assert sum(1 for (e, _d), i in by.items() if e == "T004" and "wfh_over" in i) == 2   # the 3rd and 4th WFH day
    assert people["T005"]["absent"] >= 2
    assert "T003" not in people or not people["T003"]["missing"]          # partner on automatic attendance: not checked
    html = c.get("/exceptions?period=2026-10").text
    assert "Punch exceptions" in html and 'id="ex"' in html and 'id="exp"' in html and 'data-chip="missing"' in html
    assert 'href="/exceptions?period=2026-10"' in html                     # sidebar
    assert "/periods/2026-10/employee/T006?day=2026-10-" in html           # row opens that day
    assert "openDay(qd)" in c.get("/periods/2026-10/employee/T006?day=2026-10-14").text
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    ws = wb["Punch Exceptions"]
    assert [x.value for x in ws[5]][:6] == ["Date", "Day", "Employee No", "Name", "Group", "Issues"]
    assert ws.max_row - 5 == len(X["rows"])
    assert "Exceptions by Employee" in wb.sheetnames and wb["Exceptions by Employee"].max_row - 5 == len(X["people"])
    # viewers can see it too
    assert c.get("/exceptions").status_code == 200


def _result_days(emp):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    with session_scope() as db:
        return list(db.scalar(select(MonthResult).where(MonthResult.emp_no == emp)).days)


# ---------------------------------------------------------------- v1.4.1 review & performance regressions

def test_attention_counts_zero_percent_as_zero():
    from app.services import insights as INS
    from app.services.settings import get_policy
    from app.db import session_scope
    with session_scope() as db:
        pol = get_policy(db)
    base = {"behaviour": True, "absence_12m_days": 0, "regularised_days": 0, "wfh_days": 0, "missing_punch_days": 0}
    worst = INS._attention({**base, "punctuality_pct": 0.0, "utilisation_pct": 0.0}, pol)
    best = INS._attention({**base, "punctuality_pct": 100.0, "utilisation_pct": 100.0}, pol)
    assert worst["attention"] == 40 and best["attention"] == 0


def test_stored_month_stats_match_day_detail(loaded):
    """Insights computed from the compact stored summaries equal the ones computed from the full day detail."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult, Period
    from app.services import attendance_stats as AS
    from app.services import insights as INS
    with session_scope() as db:
        p = db.scalar(select(Period).where(Period.year == 2026, Period.month == 10))
        res = list(db.scalars(select(MonthResult).where(MonthResult.period_id == p.id)))
        assert res and all(r.data.get("_ins") == AS.month_stats(r.days) for r in res)
        stored = INS._insights(db, p)
        for r in res:                                       # pretend the month was calculated by an old version
            d = dict(r.data); d.pop("_ins"); r.data = d
        db.flush()
        legacy = INS._insights(db, p)
        key = lambda I: [(x["emp_no"], x["absence_12m_days"], x["absence_12m_spells"], x["unplanned_days"],
                          x["punctuality_pct"]) for x in I["rows"]]
        assert key(stored) == key(legacy) and stored["trend"] == legacy["trend"]
        db.rollback()
    # a spell running from one month's last working day into the next month's first counts once
    a = {"sp": 1, "un": 1, "sa": False, "ea": True}
    b = {"sp": 2, "un": 3, "sa": True, "ea": False}
    assert AS.join_spells([((2026, 9), a), ((2026, 10), b)]) == (2, 4)
    assert AS.join_spells([((2026, 8), a), ((2026, 10), b)]) == (3, 4)     # not adjacent months → separate


def test_doj_list_with_leaving_dates(loaded):
    from openpyxl import Workbook
    from app.db import session_scope
    from app.models import Employee
    c = loaded
    wb = Workbook(); ws = wb.active
    ws.append(["Employee Number", "Employee Name", "Joined On", "Date of Leaving"])
    ws.append(["T001", "Asha Rao", "03 APR 2023", "28 OCT 2026"])
    ws.append(["T002", "Bilal Khan", "01 JUL 2024", None])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("joiners_leavers.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert r.status_code == 200 and "Unexpected" not in r.text
    with session_scope() as db:
        e = db.get(Employee, "T001")
        assert str(e.dol) == "2026-10-28" and e.is_active is False
        assert db.get(Employee, "T002").dol is None


def test_two_unsure_sheets_can_both_be_mapped(loaded):
    from openpyxl import Workbook
    c = loaded
    wb = Workbook(); a = wb.active; a.title = "A"; b = wb.create_sheet("B")
    for ws in (a, b):                                   # employee column has no usable header → inferred → review
        ws.append(["Col A", "Attendance Date", "Session1 Status", "Session2 Status"])
        for i, d in enumerate(range(1, 4)):
            ws.append([f"T00{i + 1}", f"0{d} Oct 2026", "P", "P"])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("two.xlsx", buf.getvalue(), "application/octet-stream"))])
    links = re.findall(r'href="(/imports/map/[0-9a-f]{32}\?sheet=[AB][^"]*)"', r.text)
    assert len(links) == 2, r.text[-800:]
    token = links[0].split("/")[3].split("?")[0]
    form = {"kind": "muster", "row": "1", "f_emp_no": "0", "f_date": "1", "f_s1": "2", "f_s2": "3"}
    page = c.get(links[0].replace("&amp;", "&"))
    r1 = c.post(f"/imports/map/{token}", data={"csrf": _csrf(page.text), "sheet": "A", **form})
    assert "your mapping" in r1.text
    page = c.get(f"/imports/map/{token}?sheet=B")
    assert page.status_code == 200 and "Tell the app" in page.text            # still available after mapping A
    r2 = c.post(f"/imports/map/{token}", data={"csrf": _csrf(page.text), "sheet": "B", **form})
    assert "your mapping" in r2.text
    assert c.get(f"/imports/map/{token}?sheet=B", follow_redirects=False).status_code == 303   # now cleaned up


def test_data_month_beats_wrong_title(loaded):
    from openpyxl import Workbook
    c = loaded
    wb = Workbook(); ws = wb.active
    ws.append(["Attendance Register November 2026"])
    ws.append(["Employee No", "Attendance Date", "Session1 Status", "Session2 Status"])
    for d in range(1, 6):
        ws.append(["T001", f"0{d} Oct 2026", "P", "P"])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("register.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert "October 2026" in r.text and "title mentions November 2026" in r.text


def test_one_recalculation_per_upload(loaded, fixtures, monkeypatch):
    from app.services import imports as IMP
    calls = []
    orig = IMP.compute_period
    monkeypatch.setattr(IMP, "compute_period", lambda db, p, cascade=True: calls.append(p.key) or orig(db, p, cascade=False))
    c = loaded
    files = [("files", (fixtures[k].name, fixtures[k].read_bytes(), "application/octet-stream")) for k in ("muster", "swipes")]
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=files)
    assert r.status_code == 200 and calls == ["2026-10"], calls


def test_excel_resists_injected_text(loaded):
    from openpyxl import load_workbook
    from app.db import session_scope
    from app.models import Employee
    from app.services.compute import compute_period, get_period
    with session_scope() as db:
        db.get(Employee, "T001").name = '</script><!--<script>alert(1)</script> =HYPERLINK("http://x")'
        compute_period(db, get_period(db, 2026, 10), cascade=False)
    page = loaded.get("/periods/2026-10").text
    assert "<script>alert(1)" not in page and "&lt;/script&gt;" in page       # escaped in the app
    wb = load_workbook(io.BytesIO(loaded.get("/exports/2026-10.xlsx").content))
    ws = wb["Payroll Leave Sheet"]
    vals = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and "HYPERLINK" in c.value]
    assert vals and all(not v.startswith("=") for v in vals)


def test_place_name_upgrade_skips_finalised_months(loaded):
    import datetime as dt
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Period, Setting, SwipeRow
    from app.services import geo
    with session_scope() as db:
        p = db.scalar(select(Period))
        p.status = "FINALISED"
        db.add(SwipeRow(period_id=p.id, emp_no="T001", ts=dt.datetime(2026, 10, 1, 3, 33), lat=19.1146, lon=72.8713, place="frozen"))
        s = db.get(Setting, "geo_names_version")
        if s:
            db.delete(s)
    geo.upgrade_place_names()
    with session_scope() as db:
        assert db.scalar(select(SwipeRow.place).where(SwipeRow.ts == dt.datetime(2026, 10, 1, 3, 33))) == "frozen"


def test_partial_file_updates_only_its_people(loaded, fixtures):
    """A correction file for one person must not wipe everyone else's month; a full export still replaces it."""
    from openpyxl import Workbook
    from sqlalchemy import func, select
    from app.db import session_scope
    from app.models import MusterRow
    c = loaded

    def counts():
        with session_scope() as db:
            return dict(db.execute(select(MusterRow.emp_no, func.count()).group_by(MusterRow.emp_no)).all())
    before = counts()
    wb = Workbook(); ws = wb.active
    ws.append(["Employee No", "Attendance Date", "Session1 Status", "Session2 Status"])
    ws.append(["T001", "05 Oct 2026", "PL", "PL"])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("fix.xlsx", buf.getvalue(), "x"))])
    assert "only their attendance days were replaced" in r.text
    after = counts()
    assert after["T001"] == 1 and all(after[e] == n for e, n in before.items() if e != "T001")
    # the full export again restores everything
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")},
               files=[("files", (fixtures["muster"].name, fixtures["muster"].read_bytes(), "x"))])
    assert counts() == before and "only their" not in r.text.split("Import history")[0]


def test_content_decides_file_type(tmp_path):
    from openpyxl import Workbook
    from app.importers.readers import read_workbook
    wb = Workbook(); wb.active.append(["a", "b"]); buf = io.BytesIO(); wb.save(buf)
    assert read_workbook(buf.getvalue(), "export.xls")["Sheet"][0] == ["a", "b"]       # xlsx named .xls
    assert read_workbook(buf.getvalue(), "export.csv")["Sheet"][0] == ["a", "b"]       # xlsx named .csv


def test_far_future_month_warns(loaded):
    from openpyxl import Workbook
    c = loaded
    wb = Workbook(); ws = wb.active
    ws.append(["Employee No", "Attendance Date", "Session1 Status", "Session2 Status"])
    ws.append(["T001", "01 Jan 2099", "P", "P"])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("typo.xlsx", buf.getvalue(), "x"))])
    assert "January 2099" in r.text and "months ahead of today" in r.text and "Check the dates" in r.text
    assert c.get("/periods/2099-01").status_code == 404              # no phantom month was created


def test_admin_can_delete_a_mistaken_month(loaded):
    from app.db import session_scope
    from app.models import AuditLog, MusterRow, Period
    from sqlalchemy import select
    c = loaded
    page = c.get("/periods/2026-10").text
    assert 'id="delmonth"' in page
    csrf = csrf_of(c, "/periods/2026-10")
    r = c.post("/periods/2026-10/delete", data={"csrf": csrf, "reason": "oops", "confirm": "Oct 2026"}, follow_redirects=True)
    assert "type the month name exactly" in r.text and c.get("/periods/2026-10").status_code == 200
    r = c.post("/periods/2026-10/delete", data={"csrf": csrf, "reason": "wrong file", "confirm": "October 2026"}, follow_redirects=True)
    assert "October 2026 deleted" in r.text
    assert c.get("/periods/2026-10").status_code == 404
    with session_scope() as db:
        assert db.scalar(select(MusterRow.id).limit(1)) is None and db.scalar(select(Period.id)) is None
        assert db.scalar(select(AuditLog).where(AuditLog.action == "period.delete")) is not None


# ---------------------------------------------------------------- v1.5: locations end to end

def test_locations_page_uses_built_in_place_names(loaded):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    c = loaded
    page = c.get("/locations").text
    assert "Locations" in page and "Acme Pharma" in page
    assert "Andheri East, Mumbai Suburban, Maharashtra" in page                       # built-in map name, T004's WFH
    assert "Name your offices" not in page and "Office check" not in page and "/settings#locations" not in page
    assert "Tagged &#39;Office&#39; from a phone" in page or "Tagged 'Office' from a phone" in page
    with session_scope() as db:
        codes = {f["code"] for r in db.scalars(select(MonthResult)) for f in r.flags}
    assert "OFF_SITE_OFFICE" not in codes
    # rejected sign-in ignored, remark visible in the day view
    emp = c.get("/periods/2026-10/employee/T006").text
    assert "rejected sign-in(s) ignored" in emp
    assert "Acme Pharma" in c.get("/periods/2026-10/employee/T001").text


def test_shared_phone_and_signins_only_month(loaded):
    """A phone used by two people is flagged; a month with punches but no muster still has a Locations page."""
    from openpyxl import Workbook
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    c = loaded
    head = ["Employee No", "Employee Name", "Swipe Date", "Door/Address", "Longitude", "Latitude", "Location Type",
            "Mobile Device Name", "Mobile Device Id", "Swipe Type", "Status", "Remarks"]
    wb = Workbook(); ws = wb.active; ws.append(head)
    ws.append(["T001", "Asha Rao", "02 Oct 2026 09:50:00", "Mobile Sign In", "72.8712", "19.1145", "Work from Home", "Pixel 8", "SAMEPHONE1", "Mobile Sign In", "Approved", ""])
    ws.append(["T002", "Bilal Khan", "02 Oct 2026 09:52:00", "Mobile Sign In", "72.8712", "19.1145", "Work from Home", "Pixel 8", "SAMEPHONE1", "Mobile Sign In", "Approved", ""])
    buf = io.BytesIO(); wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("two_people.xlsx", buf.getvalue(), "x"))])
    assert "only their punches were replaced" in r.text                              # partial file: others kept
    with session_scope() as db:
        f = {m.emp_no: m.flags for m in db.scalars(select(MonthResult))}
    assert any(x["code"] == "SHARED_DEVICE" and "Bilal Khan" in x["message"] for x in f["T001"])
    # November: punches only (month in progress, no muster yet)
    wb = Workbook(); ws = wb.active; ws.append(head)
    ws.append(["T004", "Dev Mehta", "03 Nov 2026 10:05:00", "Mobile Sign In", "72.8680", "19.0660", "Client Location", "iPhone", "X1", "Mobile Sign In", "Approved", "Maker properties"])
    ws.append(["T004", "Dev Mehta", "03 Nov 2026 18:05:00", "Mobile Sign In", "72.8680", "19.0660", "Client Location", "iPhone", "X1", "Mobile Sign In", "Approved", ""])
    ws.append(["T006", "Farhan Ali", "04 Nov 2026 10:00:00", "", "", "", "", None, None, "Permission", "", ""])
    ws.append(["T001", "Asha Rao", "04 Nov 2026 09:40:00", "hasmukhshah", "", "", "", None, None, "Astra", "Approved", ""])
    ws.append(["T001", "Asha Rao", "04 Nov 2026 18:40:00", "hasmukhshah", "", "", "", None, None, "Astra", "Approved", ""])
    buf = io.BytesIO(); wb.save(buf)
    import datetime as _dt
    if _dt.date.today() >= _dt.date(2026, 10, 1):
        c.post("/imports", data={"csrf": csrf_of(c, "/imports")}, files=[("files", ("nov.xlsx", buf.getvalue(), "x"))])
        page = c.get("/locations?period=2026-11").text
        assert "November 2026" in page and "Maker properties" in page and "Bandra Kurla Complex" in page
        assert c.get("/").status_code == 200                                         # overview unaffected
        # a month with punches but no muster: a clear "not calculated yet" page, correct counts, no dead downloads
        for path in ("/?period=2026-11", "/periods/2026-11", "/insights?period=2026-11", "/reconciliation?period=2026-11"):
            html = c.get(path).text
            assert "Not calculated yet" in html and "/exports/2026-11.xlsx" not in html, path   # no payroll download
            assert "/exports/2026-11/locations.xlsx" in html, path                                 # punches can be
        html = c.get("/?period=2026-11").text
        assert '3</div><div class="l">Mobile sign-ins (2 with GPS)' in html and '2</div><div class="l">Office biometric' in html


def test_location_exports(loaded):
    from openpyxl import load_workbook
    c = loaded
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    assert "Sign-ins & Places" in wb.sheetnames and "Places" in wb.sheetnames
    vals = [cell.value for row in wb["Sign-ins & Places"].iter_rows() for cell in row]
    assert "At Acme Pharma (BKC)" in vals and any(isinstance(v, str) and "Andheri East" in v for v in vals)
    # Daily Detail shows each punch's place and remark
    dd = [cell.value for row in wb["Daily Detail"].iter_rows() for cell in row if isinstance(cell.value, str)]
    assert any("Andheri East" in v and "Office biometric" not in v for v in dd) and any("“At Acme Pharma (BKC)”" in v for v in dd)
    # punches & locations workbook (any month with punches)
    r = c.get("/exports/2026-10/locations.xlsx")
    assert r.status_code == 200 and "Punches_and_Locations_2026-10.xlsx" in r.headers["content-disposition"]
    lw = load_workbook(io.BytesIO(r.content))
    assert lw.sheetnames == ["Punches", "Places", "By person"]
    rows = [[cell.value for cell in row] for row in lw["Punches"].iter_rows(min_row=6)]
    assert any(r[3] == "Astra" and str(r[5]).startswith("Office biometric") for r in rows)       # biometric punches too
    assert any(r[6] == "At Acme Pharma (BKC)" and r[10] == 19.066 for r in rows)                # remark + coordinates
    assert "Office check" not in [c.value for c in lw["Punches"][5]]
    assert c.get("/exports/2099-01/locations.xlsx").status_code == 404
    assert "/exports/2026-10/locations.xlsx" in c.get("/locations").text and "/exports/2026-10/locations.xlsx" in c.get("/exports").text
    assert "Sign-ins &amp; places" in c.get("/periods/2026-10/employee/T001").text


def test_schema_upgrade_adds_columns(tmp_path):
    """A database from an older version (swipes without the new columns) is upgraded in place, data kept."""
    import sqlite3
    from sqlalchemy import create_engine
    from app.db import add_missing_columns
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("create table swipes (id integer primary key, period_id int, emp_no varchar(32), ts datetime, door varchar(128),"
                " location_type varchar(64), swipe_type varchar(32), lat float, lon float, place varchar(256))")
    con.execute("insert into swipes (period_id, emp_no, ts, place) values (1, 'E1', '2026-10-01 10:00:00', 'Office')")
    con.commit(); con.close()
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    added = add_missing_columns(eng)
    assert {"swipes.note", "swipes.reason", "swipes.approval", "swipes.device", "swipes.device_id"} <= set(added)
    con = sqlite3.connect(db)
    assert con.execute("select emp_no, place, note, device_id from swipes").fetchall() == [("E1", "Office", "", "")]
    con.close()
    assert add_missing_columns(eng) == []                                            # idempotent


# ---------------------------------------------------------------- v1.5.1: firm logo

TEST_LOGO = (b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="50" viewBox="0 0 300 50" fill="none">'
             b'<defs><clipPath id="c"><rect width="300" height="50"/></clipPath></defs><g clip-path="url(#c)">'
             b'<rect width="102" height="50" fill="#DF1C23"/><path d="M10 10h20v30H10z" fill="white"/>'
             b'<text x="110" y="30" fill="#2B2A29">Test Firm LLP</text></g></svg>')


def test_logo_sanitiser():
    import pytest
    from app.services import branding
    assert "Test Firm LLP" in branding.sanitize_svg(TEST_LOGO)
    bad = {b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>': "script",
           b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>': "event handlers",
           b'<svg xmlns="http://www.w3.org/2000/svg"><use href="http://evil/x.svg#a"/></svg>': "links",
           b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>': "foreignobject",
           b'<!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="http://www.w3.org/2000/svg"/>': "DOCTYPE",
           b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(http://x)</style></svg>': "styles",
           b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://x/a)"/></svg>': "references",
           b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAA"/></svg>': "image"}
    for data, why in bad.items():
        with pytest.raises(ValueError) as ex:
            branding.sanitize_svg(data)
        assert why.lower() in str(ex.value).lower(), (why, ex.value)
    with pytest.raises(ValueError):
        branding.save(b"just text", "notes.txt")
    with pytest.raises(ValueError):
        branding.save(b"\x89PNG" + b"0" * branding.MAX_BYTES, "big.png")


def test_logo_upload_and_display(loaded):
    import re as _re
    c = loaded
    assert c.get("/branding/logo").status_code == 404 and 'class="brand-img"' not in c.get("/").text
    r = c.post("/settings/branding", data={"csrf": csrf_of(c, "/settings")},
               files={"file": ("logo.svg", TEST_LOGO, "image/svg+xml")}, follow_redirects=True)
    assert "Logo saved" in r.text
    lg = c.get("/branding/logo")
    assert lg.status_code == 200 and lg.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in lg.headers["content-security-policy"] and b"Test Firm LLP" in lg.content
    fav = c.get("/branding/favicon.svg")
    assert fav.status_code == 200 and b'viewBox="0 0 102 50"' in fav.content          # the left mark only
    page = c.get("/").text
    assert 'brand brand-img' in page and '/branding/logo?v=' in page and '/branding/favicon.svg?v=' in page
    # the sign-in page shows it to people who aren't signed in yet
    from starlette.testclient import TestClient
    from app.main import create_app
    anon = TestClient(create_app())
    assert "/branding/logo?v=" in anon.get("/login").text and anon.get("/branding/logo").status_code == 200
    # a malicious SVG is refused and the current logo kept
    r = c.post("/settings/branding", data={"csrf": csrf_of(c, "/settings")},
               files={"file": ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "image/svg+xml")},
               follow_redirects=True)
    assert "Logo not saved" in r.text and b"Test Firm LLP" in c.get("/branding/logo").content
    r = c.post("/settings/branding/delete", data={"csrf": csrf_of(c, "/settings")}, follow_redirects=True)
    assert "Logo removed" in r.text and c.get("/branding/logo").status_code == 404


def test_viewer_cannot_change_logo(client):
    make_user("viewer1", "viewer")
    login_full(client, "viewer1")
    r = client.post("/settings/branding", data={"csrf": csrf_of(client, "/")},
                    files={"file": ("logo.svg", TEST_LOGO, "image/svg+xml")}, follow_redirects=False)
    assert r.status_code == 403



def test_attention_absence_part_uses_12_month_days():
    from app.services import insights as INS
    from app.services.settings import get_policy
    from app.db import session_scope
    with session_scope() as db:
        pol = get_policy(db)
    base = {"behaviour": True, "punctuality_pct": 100.0, "utilisation_pct": 100.0, "regularised_days": 0, "wfh_days": 0,
            "missing_punch_days": 0}
    assert INS._attention({**base, "absence_12m_days": 5}, pol)["attention"] == 12          # 5 of 10 days → half of 25
    assert INS._attention({**base, "absence_12m_days": 30}, pol)["attention"] == 25         # capped at the full part
    assert "absence" in INS.WEIGHTS and "bradford" not in INS.WEIGHTS


def test_no_bradford_anywhere(loaded):
    for path in ("/insights", "/periods/2026-10/employee/T005"):
        assert "bradford" not in loaded.get(path).text.lower(), path
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(loaded.get("/exports/2026-10.xlsx").content))
    assert not any("bradford" in str(c.value).lower() for ws in wb for row in ws.iter_rows() for c in row if c.value)


# ---------------------------------------------------------------- v1.6.1: exports reflect corrections; backups

def test_excel_is_built_from_current_figures_with_corrections_and_reviews(loaded):
    from openpyxl import load_workbook
    c = loaded

    def wb():
        return load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    before = wb()
    assert before["Corrections"]["A6"].value == "No manual corrections this month."
    base = "/periods/2026-10/employee/T001"
    csrf = csrf_of(c, base)
    c.post(base + "/day-override", data={"csrf": csrf, "day": "2026-10-05", "s1": "A", "s2": "A", "work_hm": "00:00",
                                         "reason": "Was on unapproved leave"})
    c.post(base + "/month-override", data={"csrf": csrf, "field": "remark", "value": "Checked by HR",
                                           "reason": "Partner note"})
    tok = csrf_of(c, "/flags")
    c.post("/flags/review", json={"period": "2026-10", "emp_no": "T004", "code": "WFH_OVER_LIMIT", "action": "review",
                                  "note": "Approved by partner"}, headers={"X-CSRF-Token": tok})
    after = wb()                                                 # downloaded again: built fresh from the app
    corr = [[x.value for x in r] for r in after["Corrections"].iter_rows(min_row=6) if r[0].value]
    assert any(r[0] == "T001" and r[2] == "Day" and "session 1 A" in r[4] and r[5] == "Was on unapproved leave" for r in corr)
    assert any(r[0] == "T001" and r[2] == "Month figure" and r[5] == "Partner note" for r in corr)
    flags = [[x.value for x in r] for r in after["Flags"].iter_rows(min_row=6)]
    assert any(r[0] == "T004" and r[6] == "Reviewed" and r[9] == "Approved by partner" for r in flags)
    # the payroll sheet's figures changed with the correction (a working day marked absent; Days Present is a live
    # formula = week days − holidays − leave − absent, so it follows)
    def absent(w):
        ws = w["Payroll Leave Sheet"]
        hdr = next(r for r in ws.iter_rows(values_only=True) if r and "Days Absent" in r)
        col = hdr.index("Days Absent")
        return next(r[col] for r in ws.iter_rows(values_only=True) if r and r[0] == "T001")
    assert absent(after) == absent(before) + 1


def test_backups_daily_verified_and_restorable(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from app.services import backups as BK
    dd = tmp_path / "data"
    dd.mkdir()
    monkeypatch.setattr(BK, "get_config", lambda: SimpleNamespace(data_dir=dd))
    with sqlite3.connect(dd / "hsco.db") as con:
        con.execute("create table t(x)")
        con.execute("insert into t values ('v1')")
    (dd / "secret.key").write_text("KEY")
    assert BK.due()
    info = BK.make_backup("daily")
    assert info["check"] == "ok" and (dd / "backups" / info["file"]).exists()
    assert (dd / "backups" / "secret.key").read_text() == "KEY" and not BK.due()
    assert BK.check_health()["check"] == "ok" and BK.state()["last_backup"]["file"] == info["file"]
    # retention: 14 days in full, then one per month
    now = datetime(2026, 11, 18, 12)
    for d in range(1, 70):
        t = now - timedelta(days=d)
        (dd / "backups" / f"hsco_{t:%Y%m%d}_020000_daily.db").write_text("x")
    BK.prune(now)
    left = {BK._ts(p) for p in (dd / "backups").glob("hsco_2026*_020000_daily.db")}
    made = [now - timedelta(days=d) for d in range(1, 70)]
    made = [t.replace(hour=2, minute=0) for t in made]
    recent = {t for t in made if now - t <= timedelta(days=14)}
    older_newest_per_month = {max(t for t in made if now - t > timedelta(days=14) and (t.year, t.month) == m)
                              for m in {(t.year, t.month) for t in made if now - t > timedelta(days=14)}}
    assert left == recent | older_newest_per_month                    # every recent one + newest of each older month
    # restore: data comes back, the current database is kept as a safety copy
    with sqlite3.connect(dd / "hsco.db") as con:
        con.execute("update t set x='v2'")
    safety = BK.restore(dd / "backups" / info["file"])
    with sqlite3.connect(dd / "hsco.db") as con:
        assert con.execute("select x from t").fetchone() == ("v1",)
    with sqlite3.connect(safety) as con:
        assert con.execute("select x from t").fetchone() == ("v2",)
    bad = dd / "backups" / "hsco_20261118_000000_bad.db"
    bad.write_bytes(b"not a database")
    import pytest
    with pytest.raises(RuntimeError):
        BK.restore(bad)


def test_backup_now_button(loaded):
    c = loaded
    r = c.post("/settings/backup", data={"csrf": csrf_of(c, "/settings")}, follow_redirects=True)
    assert "Backup written and verified" in r.text and "Backups &amp; data safety" in r.text
    assert "manual" in c.get("/settings").text
