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
    temp = re.search(r'id="temp-pw">([^<]+)<', r.text).group(1)
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
             "/security", "/security/enrol"]
    pages += [f"/periods/2026-10/employee/T00{i}" for i in range(1, 7)]
    for p in pages:
        r = c.get(p)
        assert r.status_code == 200, (p, r.text[:300])
        assert "Traceback" not in r.text
    assert c.get("/periods/2099-01").status_code == 404
    assert c.get("/periods/2026-10/employee/NOPE").status_code == 404
    html = c.get("/").text
    assert "Board dashboard — October 2026" in html and "Over WFH limit" in html


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
    # geofence
    r = c.post("/settings/geofences", data={"csrf": csrf, "name": "HO", "category": "Office", "lat": "18.94", "lon": "72.82",
                                            "radius_m": "150"}, follow_redirects=True)
    assert "HO" in r.text
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
              "People Movement", "Daily Detail", "Rules Applied"):
        assert s in wb.sheetnames
    ws = wb["Payroll Leave Sheet"]
    hdr = [c_.value for c_ in ws[5]]
    assert hdr[:4] == ["Employee No", "Name", "Group", "Location"]
    rows = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    i = rows["T001"]
    assert ws.cell(row=i, column=9).value == f"=E{i}-F{i}-G{i}-H{i}"
    assert ws.cell(row=i, column=22).value.startswith("=IF(U")
    # Excel formula logic replicated: balance -> deduction must equal the app's value
    e, f, g, h = (ws.cell(row=i, column=k).value for k in (5, 6, 7, 8))
    o, p, t = (ws.cell(row=i, column=k).value for k in (15, 16, 20))
    bal = round((o - p) - (e - f - g - h) * t, 2)
    import math
    ded = 0 if bal >= 0 else math.ceil(round(-bal, 4) / 4) * 0.5
    assert ded == _result("T001")[0]["deduction_days"]
    # HTML board pack: self-contained, includes data and chart library, no external URLs
    r = c.get("/exports/2026-10.html")
    assert r.status_code == 200
    html = r.text
    assert "Board Pack" in html and "Dev Mehta" in html and "Chart" in html
    assert "cdn" not in html.lower() and "https://" not in html.split("<script>")[0]
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
