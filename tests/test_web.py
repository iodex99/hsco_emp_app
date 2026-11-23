"""HTTP-level integration tests: authentication & 2FA, CSRF, roles, imports, calculations, overrides, locking,
exports, admin. Uses synthetic fixtures (no real data needed)."""
import io
import re
import time

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


def test_password_sign_in_goes_straight_in(client):
    """v1.10 (owner): no two-step verification — the right password opens the app, wrong ones lock the account."""
    make_user()
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/employees"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/employees"
    assert client.get("/employees").status_code == 200
    assert "Two-step" not in client.get("/").text and "Change password" in client.get("/").text
    client.post("/logout", data={"csrf": csrf_of(client)})
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "//evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"                                   # no open redirect
    from app.db import session_scope
    from app.models import User
    from sqlalchemy import select
    with session_scope() as db:
        assert db.scalar(select(User.last_login).where(User.username == "admin")) is not None


def test_two_step_verification_is_gone(client):
    make_user()
    login_full(client)
    csrf = csrf_of(client)
    for path in ("/mfa", "/security/enrol"):
        assert client.get(path).status_code == 404, path
    for path in ("/mfa/totp", "/mfa/recovery", "/mfa/email", "/mfa/email/send", "/mfa/webauthn/options",
                 "/security/totp/confirm", "/security/webauthn/options", "/security/recovery/regenerate",
                 "/security/email/enable", "/users/1/reset-mfa"):
        assert client.post(path, data={"csrf": csrf, "code": "123456"}).status_code in (404, 405), path
    st = client.get("/settings").text
    assert 'name="mfa_required"' not in st and 'name="smtp_host"' not in st and "Email sign-in codes" not in st
    us = client.get("/users").text
    assert "Two-step" not in us and "two-step" not in us and "reset-mfa" not in us
    sec_page = client.get("/security").text
    assert "Change password" in sec_page and "Recovery codes" not in sec_page and "Authenticator" not in sec_page
    assert "Two-step verification" not in client.get("/login").text


def test_migration_removes_stored_two_step_data(data_dir):
    """remove_2fa_v1: secrets, recovery codes and emailed codes deleted; 2FA / email-server settings dropped; once."""
    from sqlalchemy import select, text
    from app.db import init_db, migrate_data, session_scope
    from app.models import AuditLog, Setting
    init_db()
    with session_scope() as db:                       # what an older version left behind
        db.execute(text("CREATE TABLE IF NOT EXISTS mfa_methods (id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, secret TEXT)"))
        db.execute(text("CREATE TABLE IF NOT EXISTS recovery_codes (id INTEGER PRIMARY KEY, user_id INTEGER, code_hash TEXT)"))
        db.execute(text("INSERT INTO mfa_methods (user_id, kind, secret) VALUES (1, 'totp', 'x'), (1, 'email', '')"))
        db.execute(text("INSERT INTO recovery_codes (user_id, code_hash) VALUES (1, 'h')"))
        g = db.get(Setting, "general")
        g.value = {**g.value, "mfa_required": True, "allowed_mfa": ["totp"], "smtp_host": "smtp.x",
                   "smtp_password": "secret!", "org_name": "Kept"}
        st = db.get(Setting, "migrations")
        st.value = {"done": [x for x in st.value["done"] if x != "remove_2fa_v1"]}
    migrate_data()
    with session_scope() as db:
        assert db.execute(text("SELECT COUNT(*) FROM mfa_methods")).scalar() == 0
        assert db.execute(text("SELECT COUNT(*) FROM recovery_codes")).scalar() == 0
        g = db.get(Setting, "general").value
        assert g["org_name"] == "Kept" and not any(k.startswith("smtp_") or "mfa" in k for k in g)
        a = db.scalar(select(AuditLog).where(AuditLog.action == "migration.remove_2fa"))
        assert a.details["deleted_rows"] == {"mfa_methods": 2, "recovery_codes": 1} and "smtp_password" in a.details["settings_dropped"]
    migrate_data()
    with session_scope() as db:
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "migration.remove_2fa")))) == 1


def test_csrf_enforced(client):
    make_user()
    login_full(client)
    r = client.post("/users", data={"username": "x1", "role": "viewer"})
    assert r.status_code == 400
    r = client.post("/users", data={"username": "x1", "role": "viewer", "csrf": "forged"})
    assert r.status_code == 400


def test_roles_and_user_admin(client):
    make_user()
    login_full(client)
    r = client.post("/users", data={"csrf": csrf_of(client, "/users"), "username": "board1", "role": "viewer",
                                    "full_name": "Board Member"})
    temp = re.search(r'id="temp-pw"[^>]*>([^<]+)<', r.text).group(1)
    client.post("/logout", data={"csrf": csrf_of(client)})
    # new user: temporary password -> straight to "set your password"; nothing else opens until it is set
    r = client.post("/login", data={"username": "board1", "password": temp}, follow_redirects=False)
    assert r.headers["location"] == "/security?must_change=1"
    assert "Set your password" in client.get(r.headers["location"]).text
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/security")
    weak = client.post("/security/password", data={"csrf": csrf_of(client), "current": temp, "new": "short", "confirm": "short"},
                       follow_redirects=True)
    assert "Password needs" in weak.text
    r = client.post("/security/password", data={"csrf": csrf_of(client), "current": temp, "new": "Board!Member#2026",
                                                "confirm": "Board!Member#2026"}, follow_redirects=True)
    assert r.status_code == 200 and "Password set — welcome." in r.text   # a non-latin-1 flash (cookie) works
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
             "/security", "/insights", "/insights?period=2026-10"]
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
    # v1.9: leave balances are kept in greytHR — no opening balance, ledger, salary deduction or payroll LOP
    assert c.post(base + "/opening", data={"csrf": csrf, "leave_balance": "0.5"}).status_code in (404, 405)
    d, flags = _result("T001")
    for k in ("leave_opening", "leave_closing", "leave_credit", "salary_deduction_days", "payroll_lop_days",
              "compoff_opening", "compoff_closing"):
        assert k not in d, k
    assert not {"NO_OPENING_BALANCE", "LEAVE_EXHAUSTED", "COMPOFF_OVERDRAWN"} & {f["code"] for f in flags}
    page = c.get(base).text
    assert "Leave this month" in page and "Opening balance" not in page and "Leave balance" not in page
    audit = c.get("/audit").text
    for a in ("override.month", "override.day", "import"):
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
    # v1.7.4: which Saturdays the branches open (default the 1st) — shown and saved
    c.post("/settings/policy", data={**form, "office_open_saturdays": "1, 3, 9"})
    from app.db import session_scope as _ss
    from app.services.settings import get_policy
    with _ss() as db:
        assert get_policy(db).office_open_saturdays == (1, 3)
    assert 'name="office_open_saturdays" value="1, 3"' in c.get("/settings").text
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
    assert wb.sheetnames[:6] == ["Contents", "Payroll Leave Sheet", "Payroll Input", "Comp-off Ledger",
                                 "Corrections", "Flags"]                                       # v1.8 order (v1.9 names)
    cs = wb["Contents"]
    links = {cs.cell(row=i, column=1).value: cs.cell(row=i, column=1).hyperlink for i in range(1, cs.max_row + 1)
             if cs.cell(row=i, column=1).hyperlink}
    assert links["Payroll Leave Sheet"].location == "'Payroll Leave Sheet'!A1" or "Payroll Leave Sheet" in str(links["Payroll Leave Sheet"].target or links["Payroll Leave Sheet"].location)
    assert any(cs.cell(row=i, column=1).value == "Leave deduction (days)" for i in range(1, 20))
    pws = wb["Payroll Leave Sheet"]
    assert pws.page_setup.orientation == "landscape" and pws.print_title_rows == "$5:$5" and pws.page_setup.fitToWidth == 1
    assert pws.sheet_properties.tabColor is not None
    pin = wb["Payroll Input"]
    # v1.9 (owner): the deduction split into hours shortfall and WFH over the limit, in columns and in the remark;
    # no LOP, salary deduction or leave balance
    assert [c_.value for c_ in pin[5]] == ["Employee No", "Name", "Group", "Category", "Location",
                                           "Hours shortfall deduction (days)", "WFH over-limit deduction (days)",
                                           "Total leave deduction (days)", "Remark"]
    pin_rows = {pin.cell(row=i, column=1).value: i for i in range(6, pin.max_row + 1)
                if str(pin.cell(row=i, column=1).value or "").startswith("T0")}
    assert set(pin_rows) == {e for e in ("T001", "T002", "T003", "T004", "T005", "T006") if _result(e)[0]["deduction_days"]}
    assert "T003" not in pin_rows                           # partner: no deduction → not listed
    for s in ("Late & Punctuality", "WFH & Locations", "Regularisation & Punches", "Comp-off Ledger",
              "People Movement", "Daily Detail", "Rules Applied", "Scorecard", "Groups Compared"):
        assert s in wb.sheetnames
    ws = wb["Payroll Leave Sheet"]
    hdr = [c_.value for c_ in ws[5]]
    assert hdr[:4] == ["Employee No", "Name", "Group", "Location"]
    assert hdr[25:] == ["LOP (approved)", "Comp-off Earned", "Flags", "Category"]       # no balance columns (v1.9)
    assert not any("Leave Opening" in h or "Closing" in h or "Salary" in h or "Payroll LOP" in h for h in hdr)
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
    led = [c_.value for c_ in wb["Comp-off Ledger"][5]]
    assert led == ["Employee No", "Name", "Group", "Category", "Comp-off earned", "Comp-off taken"]
    # Payroll Input: the formulas replicate the payroll sheet — hours part = the app's shortfall deduction, the rest of
    # column V = the WFH part; the remark says both in words
    P = "'Payroll Leave Sheet'!"
    for emp, k in pin_rows.items():
        d, i = _result(emp)[0], rows[emp]
        f, g, h = (pin.cell(row=k, column=x).value for x in (6, 7, 8))
        assert f.startswith(f'=IF({P}U{i}<>"No",0,') and g == f"={P}V{i}-F{k}" and h == f"={P}V{i}"
        o, p_, t, e_, f_, g_, h_ = (ws.cell(row=i, column=x).value for x in (15, 16, 20, 5, 6, 7, 8))
        bal = round((o - p_) - (e_ - f_ - g_ - h_) * t, 2)
        hours = 0 if bal >= 0 or ws.cell(row=i, column=21).value != "No" else math.ceil(round(-bal, 4) / 4) * 0.5
        assert hours == d["shortfall_deduction_days"] and d["deduction_days"] - hours == d["wfh_deduction_days"], emp
        remark = pin.cell(row=k, column=9).value
        assert ("Hours shortfall:" in remark) == bool(d["shortfall_deduction_days"]), (emp, remark)
        assert ("WFH over the limit:" in remark) == bool(d["wfh_deduction_days"]), (emp, remark)
    assert "WFH over the limit: 4 WFH days, limit 2 → 2 extra × 0.5 = 1 day" in pin.cell(row=pin_rows["T004"], column=9).value
    # the HTML board pack was removed in v1.6 (the app itself is the board view)
    assert c.get("/exports/2026-10.html").status_code == 404
    assert "/exports/2026-10.html" not in c.get("/exports").text and "Board pack" not in c.get("/").text
    # templates
    for t in ("employee_master", "holidays"):
        r = c.get(f"/exports/templates/{t}.xlsx")
        assert r.status_code == 200
        load_workbook(io.BytesIO(r.content))
    assert c.get("/exports/templates/leave_opening.xlsx").status_code == 404       # leave balances: greytHR (v1.9)
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
    assert page.count("bulk-ok") == 4                     # detail + the (hidden) review-note column, 2 rows
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


def test_holidays_by_category_settings_import_and_wfh(loaded):
    """v1.9 (owner): holidays are targeted only by category. Rules & calendars → "Holiday for several categories" adds
    the holiday to each ticked category's list; a holiday file has Date, Day, Holiday, Category, Optional (several
    categories comma-separated; blank = the default list). WFH on the person's own holiday is not WFH."""
    import io
    from datetime import date
    import openpyxl
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import AuditLog, Category, Employee
    c = loaded
    with session_scope() as db:
        for nm in ("HSCO (Art)", "HSCO (Mum)"):
            db.add(Category(name=nm))
        for no, cat in (("T001", "HSCO (Mum)"), ("T002", "HSCO (Mum)"), ("T004", "HSCO (Art)")):
            db.get(Employee, no).category = cat
    day_of = lambda emp, iso: next(d for d in _result_days(emp) if d["date"] == iso)  # noqa: E731
    wfh_dates = [d["date"] for d in _result_days("T004") if d["wfh"]]
    before, _ = _result("T004")
    assert before["wfh_days"] == len(wfh_dates) == 4 and before.get("offday_wfh_days", 0) == 0
    st = c.get("/settings").text
    assert 'id="multi"' in st and 'name="categories" value="HSCO (Art)"' in st
    assert 'type="checkbox" name="locations"' not in st and 'type="checkbox" name="groups"' not in st
    assert "Targeted holidays" not in st and 'id="th-pick"' not in st
    assert "Date, Day, Holiday, Category, Optional" in st
    csrf = csrf_of(c, "/settings")
    r = c.post("/settings/holidays/categories", data={"csrf": csrf, "day": "2026-10-07", "name": "X"},
               follow_redirects=True)
    assert "Tick the categories" in r.text
    # a holiday for the Article category on one of T004's WFH days
    wd = wfh_dates[0]
    r = c.post("/settings/holidays/categories", data={"csrf": csrf, "day": wd, "name": "Article day off",
                                                      "categories": ["HSCO (Art)"]}, follow_redirects=True)
    assert "Article day off" in r.text and "added to 1 list: HSCO (Art)" in r.text
    assert "had no 2026 holidays yet" in r.text                     # the list now counts as loaded for 2026
    d4 = day_of("T004", wd)
    assert d4["type"] == "HOLIDAY" and d4["holiday"] == "Article day off" and not d4["wfh"]
    assert any("not counted as WFH" in n for n in d4["notes"])
    after, _ = _result("T004")
    assert after["wfh_days"] == 3 and after["offday_wfh_days"] == 1
    assert day_of("T001", wd)["type"] == "WORKDAY"                       # other category: not given it
    with session_scope() as db:
        assert "holiday.add" in {a.action for a in db.scalars(select(AuditLog))}
    # the old targeting routes are gone
    assert c.post("/settings/targeted-holidays", data={"csrf": csrf, "day": wd}).status_code in (404, 405)
    # holiday list import: Date, Day, Holiday, Category, Optional
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Day", "Holiday", "Category", "Optional"])
    ws.append([date(2026, 10, 14), "Wed", "Navratri", "HSCO (Art), HSCO (Mum)", "No"])     # two categories
    ws.append([date(2026, 10, 16), "Fri", "Firm day", "", "No"])                          # no category → default
    ws.append([date(2026, 10, 15), "Mon", "Wrong weekday", "HSCO (Mum)", "Yes"])          # 15 Oct 2026 is a Thursday
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post("/imports", data={"csrf": csrf_of(c, "/imports")},
               files=[("files", ("holidays.xlsx", buf.getvalue(), "application/octet-stream"))])
    assert r.status_code == 200 and "Holiday list" in r.text and "Unexpected" not in r.text
    assert "Thu" in r.text                                              # the Day column is checked
    for e in ("T001", "T002", "T004"):
        assert day_of(e, "2026-10-14")["type"] == "HOLIDAY", e
    assert day_of("T005", "2026-10-16")["type"] == "HOLIDAY"           # no category → default list
    assert day_of("T001", "2026-10-16")["type"] == "WORKDAY"           # has its own list
    assert day_of("T001", "2026-10-15")["type"] == "WORKDAY"           # optional, not taken
    # the template: only these columns, pre-filled with the lists
    t = openpyxl.load_workbook(io.BytesIO(c.get("/exports/templates/holidays.xlsx").content))["Data"]
    assert [x.value for x in t[1]] == ["Date", "Day", "Holiday", "Category", "Optional"]
    got = {(r_[2].value, r_[3].value) for r_ in t.iter_rows(min_row=2) if r_[0].value}
    assert {("Article day off", "HSCO (Art)"), ("Navratri", "HSCO (Art)"), ("Navratri", "HSCO (Mum)"),
            ("Firm day", "")} <= {(a_, b_ or "") for a_, b_ in got}
    # employee form: holidays follow the category (no calendar of their own)
    page = c.get("/employees/T004").text
    assert 'name="holiday_calendar_id"' not in page and "Holidays follow the category" in page


def test_migration_moves_targeted_holidays_into_category_lists(data_dir):
    """v1.9 migration: a targeted holiday for categories becomes a holiday in each of their lists; location / group /
    person targeting is listed in the audit log; employees' own calendars are cleared."""
    from datetime import date
    from sqlalchemy import select
    from app.db import init_db, migrate_data, session_scope
    from app.models import AuditLog, Category, Employee, Holiday, HolidayCalendar, Setting, TargetedHoliday
    init_db()
    with session_scope() as db:
        own = HolidayCalendar(name="Own list")
        db.add(own)
        db.flush()
        db.add(Category(name="HSCO (Delhi)"))
        db.add(Employee(emp_no="E1", name="A", category="HSCO (Delhi)", holiday_calendar_id=own.id))
        db.add(TargetedHoliday(day=date(2026, 11, 7), name="Chhath", categories="HSCO (Delhi)", locations="",
                               groups="", emp_nos=""))
        db.add(TargetedHoliday(day=date(2026, 11, 9), name="Pune fest", categories="", locations="Pune",
                               groups="", emp_nos=""))
        st = db.get(Setting, "migrations")
        st.value = {"done": [x for x in st.value["done"] if x != "holidays_by_category_v1"]}
    migrate_data()
    with session_scope() as db:
        cat = db.scalar(select(Category).where(Category.name == "HSCO (Delhi)"))
        assert cat.holiday_calendar_id
        hs = {(h.day, h.name) for h in db.scalars(select(Holiday).where(Holiday.calendar_id == cat.holiday_calendar_id))}
        assert hs == {(date(2026, 11, 7), "Chhath")}
        assert db.scalar(select(TargetedHoliday)) is None
        assert db.get(Employee, "E1").holiday_calendar_id is None
        a = db.scalar(select(AuditLog).where(AuditLog.action == "migration.holidays_by_category"))
        assert a and "Pune fest" in str(a.details["no_longer_targeted"]) and a.details["own_calendar_cleared"] == ["E1"]
    migrate_data()                                                    # runs once
    with session_scope() as db:
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "migration.holidays_by_category")))) == 1


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
    assert [x.value for x in ws[5]][:7] == ["Date", "Day", "Employee No", "Name", "Group", "Category", "Issues"]
    assert ws.max_row - 5 == len(X["rows"])
    assert "Exceptions by Employee" in wb.sheetnames and wb["Exceptions by Employee"].max_row - 5 == len(X["people"])
    # viewers can see it too
    assert c.get("/exceptions").status_code == 200


def _xlsx(rows, sheet="Sheet0"):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post_files(c, files):
    return c.post("/imports", data={"csrf": csrf_of(c, "/imports")},
                  files=[("files", (n, b, "application/octet-stream")) for n, b in files])


def test_categories_end_to_end(loaded):
    """v1.7: employee categories from greytHR's category report; each category's holiday list (incl. optional
    holidays, a Day column with a wrong year, a list without a Category column named in the file name); replace-by-
    year; category weekly offs / hours; employee override; rename / delete; a holiday for a category; paste a list;
    disagreements with greytHR; categories everywhere (pages + Excel)."""
    from datetime import date
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Category, Employee, HolidayCalendar
    c = loaded
    day = lambda emp, d: next(x for x in _result_days(emp) if x["date"] == d)  # noqa: E731
    # 1) category report (as greytHR exports it: title row, then the header)
    rep = _xlsx([["Employee Category Report"],
                 ["Employee Number", "Employee Name", "Curr.Location", "Curr.Category", "Additional Category"],
                 ["T001", "Asha Rao", "Mumbai", "HSCO (Mum)", None], ["T002", "Bilal Khan", "Mumbai", "HSCO (Mum)", None],
                 ["T004", "Dev Mehta", "Mumbai", "Article", "Hsco (Mum)"], ["T006", "Farhan Ali", "Delhi", "HSCO (Carlyle)", None],
                 ["Total number of records : 4"]])
    # 2) the Mum list without 20-Oct (greytHR marks it H) + an optional holiday; 3) a printed list with a Day column
    #    and a wrong year; 4) a Carlyle list with no Category column (category from the file name)
    mum = _xlsx([["Holiday Date", "Description", "Is Optional", "Location", "Category"],
                 [date(2026, 1, 26), "Republic Day", "No", "Mumbai", "HSCO (Mum)"],
                 [date(2026, 3, 3), "Holi", "No", "Mumbai", "HSCO (Mum)"],
                 [date(2026, 10, 7), "Mum festival", "No", "Mumbai", "HSCO (Mum)"],
                 [date(2026, 10, 9), "Optional day", "Optional", "Mumbai", "HSCO (Mum)"],
                 [date(2026, 11, 10), "Diwali", "No", "Mumbai", "HSCO (Mum)"],
                 [date(2026, 12, 25), "Christmas", "No", "Mumbai", "HSCO (Mum)"]])
    carlyle = _xlsx([["Sr. No.", "Holiday Day", "Date"], [1, "New Year", date(2026, 1, 1)],
                     [2, "Carlyle day", date(2026, 10, 15)], [3, "Dussehra", date(2026, 10, 20)]], "Sheet1")
    r = _post_files(c, [("Holiday List Importer HSCO (Mum).xlsx", mum), ("EmployeeCategoryReport.xlsx", rep),
                        ("Holiday List Importer HSCO (Carlyle) 2026.xlsx", carlyle)])
    assert r.status_code == 200 and "Employee category report" in r.text and "HSCO (Mum) 2026: 6 holidays (1 optional) loaded" in r.text
    with session_scope() as db:
        emps = {e.emp_no: e for e in db.scalars(select(Employee))}
        cats = {x.name: x for x in db.scalars(select(Category))}
        assert (emps["T001"].category, emps["T004"].category, emps["T004"].additional_category) == \
            ("HSCO (Mum)", "Article", "HSCO (Mum)")                       # "Hsco (Mum)" matched to the same category
        assert emps["T006"].location == "Delhi" and emps["T003"].category == ""
        assert set(cats) == {"HSCO (Mum)", "Article", "HSCO (Carlyle)"}
        cal_mum = db.get(HolidayCalendar, cats["HSCO (Mum)"].holiday_calendar_id)
        assert cal_mum.name == "HSCO (Mum)" and not cal_mum.is_default and len(cal_mum.holidays) == 6
        assert db.get(HolidayCalendar, cats["HSCO (Carlyle)"].holiday_calendar_id).name == "HSCO (Carlyle)"
        assert cats["Article"].holiday_calendar_id is None
    # each category's own list; Article follows its additional category (Mum); no category → greytHR
    assert day("T001", "2026-10-07")["type"] == "HOLIDAY" and day("T001", "2026-10-07")["holiday"] == "Mum festival"
    assert day("T004", "2026-10-07")["type"] == "HOLIDAY"
    assert day("T006", "2026-10-14")["type"] == "WORKDAY" and day("T006", "2026-10-15")["type"] == "HOLIDAY"
    assert day("T001", "2026-10-15")["type"] == "WORKDAY"                         # Carlyle's day is not Mum's
    assert day("T003", "2026-10-07")["type"] == "WORKDAY" and day("T005", "2026-10-20")["type"] == "HOLIDAY"
    # optional holiday not taken (greytHR shows P) → a working day with a note
    d9 = day("T001", "2026-10-09")
    assert d9["type"] == "WORKDAY" and any("Optional holiday (Optional day) — not taken" in n for n in d9["notes"])
    # the Mum list has no 20-Oct but greytHR marks it H → listed under "Check these dates"
    assert day("T001", "2026-10-20")["type"] == "WORKDAY"
    st = c.get("/settings").text
    assert 'data-tab="categories"' in st and 'id="cat-conf"' in st and "2026-10-20" in st and "HSCO (Mum)" in st
    assert "No holiday list yet" not in st                    # Article people get the Mum list via their additional category
    # 5) a revised Mum list (20-Oct added, 7-Oct moved to 12-Oct) replaces 2026: moved date removed, conflict gone
    mum2 = _xlsx([["Holiday Date", "Day", "Description", "Is Optional", "Category"],
                  [date(2026, 1, 26), "Monday", "Republic Day", "No", "HSCO (Mum)"],
                  [date(2026, 3, 3), "Tuesday", "Holi", "No", "HSCO (Mum)"],
                  [date(2026, 10, 12), "Monday", "Mum festival", "No", "HSCO (Mum)"],
                  [date(2026, 10, 20), "Tuesday", "Dussehra", "No", "HSCO (Mum)"],
                  [date(2025, 11, 10), "Tuesday", "Diwali", "No", "HSCO (Mum)"],         # wrong year, Day column fixes it
                  [date(2026, 12, 25), "Friday", "Christmas", "No", "HSCO (Mum)"]])
    r = _post_files(c, [("Holiday List Importer HSCO (Mum) revised.xlsx", mum2)])
    assert "removed Mum festival 07-Oct, Optional day 09-Oct" in r.text and "taken as 10-Nov-2026" in r.text
    assert day("T001", "2026-10-07")["type"] == "WORKDAY" and day("T001", "2026-10-12")["type"] == "HOLIDAY"
    assert day("T001", "2026-10-20")["type"] == "HOLIDAY"
    conf = c.get("/settings").text.split('id="cat-conf"')[1].split("</table>")[0]
    assert "2026-10-20" not in conf                                      # resolved
    assert "2026-10-12" in conf and "Holiday — Mum festival" in conf      # the list's new date: greytHR still says working
    # 6) category settings: weekly offs + hours; an employee's own pattern still wins
    with session_scope() as db:
        mum_id = db.scalar(select(Category.id).where(Category.name == "HSCO (Mum)"))
        cal_id = db.scalar(select(Category.holiday_calendar_id).where(Category.id == mum_id))
    csrf = csrf_of(c, "/settings")
    r = c.post("/settings/categories/save", data={"csrf": csrf, "cid": str(mum_id), "name": "HSCO (Mum)",
                                                  "holiday_calendar_id": str(cal_id), "weekly_off": "ALL_SAT_OFF",
                                                  "hours_per_day": "8"}, follow_redirects=True)
    assert "saved" in r.text
    # (the fixture master's MUSTER became "follow the category" on import — v1.7.1)
    c.post("/employees/save", data={"csrf": csrf_of(c, "/employees/T001"), "emp_no": "T001", "name": "Asha Rao",
                                    "group": "BSS", "category": "HSCO (Mum)", "weekly_off": "CATEGORY",
                                    "is_active": "1", "doj": "2023-04-03"})
    assert day("T001", "2026-10-03")["type"] == "WEEKLY_OFF"                      # follows the category now
    assert _result("T001")[0]["hours_per_day"] == 8.0
    c.post("/employees/save", data={"csrf": csrf_of(c, "/employees/T002"), "emp_no": "T002", "name": "Bilal Khan",
                                    "group": "ATA", "category": "HSCO (Mum)", "weekly_off": "MUSTER",
                                    "is_active": "1", "doj": "2024-07-01"})
    assert day("T002", "2026-10-03")["type"] == "WORKDAY"                         # own pattern (greytHR) wins
    page = c.get("/employees/T001").text
    assert "Follow category — All Saturdays" in page and "category: 8" in page and 'list="cats"' in page
    # 7) rename keeps the people; deleting a category with people is refused
    c.post("/settings/categories/save", data={"csrf": csrf, "cid": str(mum_id), "name": "HSCO (Mumbai)",
                                              "holiday_calendar_id": str(cal_id), "weekly_off": "ALL_SAT_OFF",
                                              "hours_per_day": "8"})
    with session_scope() as db:
        assert db.get(Employee, "T001").category == "HSCO (Mumbai)" and db.get(Employee, "T004").additional_category == "HSCO (Mumbai)"
    r = c.post(f"/settings/categories/{mum_id}/delete", data={"csrf": csrf}, follow_redirects=True)
    assert "still has employees" in r.text
    # 8) a holiday for one category (Carlyle) only — added to its list (v1.9)
    c.post("/settings/holidays/categories", data={"csrf": csrf, "day": "2026-10-13", "name": "Carlyle offsite",
                                                  "categories": ["HSCO (Carlyle)"]})
    assert day("T006", "2026-10-13")["type"] == "HOLIDAY" and day("T001", "2026-10-13")["type"] == "WORKDAY"
    # 9) paste a list (as copied from the New Delhi PDF) into a new calendar
    c.post("/settings/calendars", data={"csrf": csrf, "name": "Delhi 2026"})
    with session_scope() as db:
        delhi = db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == "Delhi 2026"))
    r = c.post(f"/settings/calendars/{delhi.id}/paste", follow_redirects=True, data={"csrf": csrf, "text": (
        "India List of Holidays Calendar year 2026 – NEW DELHI:\nS.No Holiday Date\n1 Republic Day 26 January 2026\n"
        "2 Holi 04 March 2026\n3 Mahavir Jayanti 31 March 2026\n4 Good Friday 03 April 2026\n"
        "8 Mahatma Gandhi's Birthday 02 October 2026\n12 Christmas Day 25 December 2026\n")})
    assert "Delhi 2026 2026: 6 holidays loaded" in r.text
    with session_scope() as db:
        hs = {h.day: h.name for h in db.scalars(select(HolidayCalendar).where(HolidayCalendar.id == delhi.id)).one().holidays}
    assert hs[date(2026, 4, 3)] == "Good Friday" and hs[date(2026, 10, 2)] == "Mahatma Gandhi's Birthday"
    # 10) categories everywhere
    assert "HSCO (Mumbai)" in c.get("/employees").text and "No category" in c.get("/employees").text
    assert "By category" in c.get("/?period=2026-10").text and "HSCO (Carlyle)" in c.get("/?period=2026-10").text
    ins = c.get("/insights?period=2026-10").text
    assert "Categories compared" in ins and 'id="cc"' in ins and 'data-key="category"' in ins
    assert 'data-key="category"' in c.get("/periods/2026-10").text
    assert "HSCO (Carlyle)" in c.get("/periods/2026-10/employee/T006").text
    assert 'data-key="cat"' in c.get("/exceptions?period=2026-10").text
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    assert "Categories Compared" in wb.sheetnames
    cc = {x[0].value for x in wb["Categories Compared"].iter_rows(min_row=6) if x[0].value}
    assert {"HSCO (Mumbai)", "Article", "HSCO (Carlyle)"} <= cc
    ws = wb["Payroll Leave Sheet"]
    assert ws.cell(row=5, column=29).value == "Category"
    rows = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    assert ws.cell(row=rows["T004"], column=29).value == "Article + HSCO (Mumbai)"
    assert "Category" in [x.value for x in wb["Scorecard"][6]]
    tpl = load_workbook(io.BytesIO(c.get("/exports/templates/employee_master.xlsx").content)).active
    assert [x.value for x in tpl[1]][3:5] == ["Category", "Additional Category"]
    # audit
    with session_scope() as db:
        from app.models import AuditLog
        acts = {a.action for a in db.scalars(select(AuditLog))}
    assert {"category.update", "holiday.paste", "holidays.list"} <= acts


def test_category_review_fixes(loaded):
    """v1.7.1 review: a list not loaded for the year falls back to greytHR; a shared / default calendar is never
    rewritten; a year split over two files or an optional-only file never wipes the rest; '(1)' in a file name is no
    category; older masters (MUSTER, groups in a Category column); blanks clear the additional category; rename /
    delete keep the category's holidays; a list's Location column is ignored; formula-safe category in Excel."""
    from datetime import date
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Category, Employee, Holiday, HolidayCalendar
    c = loaded
    day = lambda emp, d: next(x for x in _result_days(emp) if x["date"] == d)  # noqa: E731
    csrf = csrf_of(c, "/settings")
    # MUSTER in an older master → follows the category (the fixture master says MUSTER for T001–T005)
    with session_scope() as db:
        assert db.get(Employee, "T001").weekly_off == "CATEGORY"
    # 1) a category list with holidays only for another year: greytHR's H (20-Oct) still counts
    c.post("/settings/categories/save", data={"csrf": csrf, "name": "HSCO (Old)", "holiday_calendar_id": "new"})
    with session_scope() as db:
        cat = db.scalar(select(Category).where(Category.name == "HSCO (Old)"))
        db.add(Holiday(calendar_id=cat.holiday_calendar_id, day=date(2025, 10, 2), name="Old year"))
    c.post("/employees/save", data={"csrf": csrf_of(c, "/employees/T001"), "emp_no": "T001", "name": "Asha Rao",
                                    "group": "BSS", "category": "HSCO (Old)", "weekly_off": "CATEGORY",
                                    "is_active": "1", "doj": "2023-04-03"})
    assert day("T001", "2026-10-20")["type"] == "HOLIDAY"
    assert "not loaded" in _result("T001")[0]["calendar_from"]
    # 2) a category pointing at the default calendar gets a list of its own; the default stays as it was
    c.post("/settings/calendars", data={"csrf": csrf, "name": "Standard", "is_default": "1"})
    with session_scope() as db:
        std = db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == "Standard"))
        for d_ in (date(2026, 1, 26), date(2026, 8, 15), date(2026, 10, 2), date(2026, 10, 20), date(2026, 12, 25)):
            db.add(Holiday(calendar_id=std.id, day=d_, name="Std"))
        cat = db.scalar(select(Category).where(Category.name == "HSCO (Old)"))
        cat.holiday_calendar_id = std.id
    rows = [["Holiday Date", "Description", "Category"]] + [[date(2026, m_, 3), f"H{m_}", "HSCO (Old)"] for m_ in range(1, 7)]
    r = _post_files(c, [("Holiday List Importer HSCO (Old).xlsx", _xlsx(rows))])
    assert "HSCO (Old) now has its own holiday list" in r.text
    with session_scope() as db:
        assert len(db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == "Standard")).holidays) == 5
        cat = db.scalar(select(Category).where(Category.name == "HSCO (Old)"))
        own = db.get(HolidayCalendar, cat.holiday_calendar_id)
        assert own.name != "Standard" and len([h for h in own.holidays if h.day.year == 2026]) == 6
    # 3) the second half of the year in another file adds to it; an optional-only file never removes fixed days
    rows = [["Holiday Date", "Description", "Category"]] + [[date(2026, m_, 5), f"H{m_}", "HSCO (Old)"] for m_ in range(7, 13)]
    _post_files(c, [("Holiday List Importer HSCO (Old) 2.xlsx", _xlsx(rows))])
    rows = [["Holiday Date", "Description", "Is Optional", "Category"]] + \
        [[date(2026, m_, 9), f"O{m_}", "Optional", "HSCO (Old)"] for m_ in range(1, 7)]
    _post_files(c, [("Holiday List Importer HSCO (Old) optional.xlsx", _xlsx(rows))])
    with session_scope() as db:
        hs = db.get(HolidayCalendar, db.scalar(select(Category).where(Category.name == "HSCO (Old)")).holiday_calendar_id).holidays
        hs = [h for h in hs if h.day.year == 2026]
        assert len([h for h in hs if not h.optional]) == 12 and len([h for h in hs if h.optional]) == 6
    # 4) "(1)" in a file name is not a category
    r = _post_files(c, [("Holiday list 2027 (1).xlsx", _xlsx([["Date", "Holiday"], [date(2027, 1, 26), "Republic Day"]]))])
    with session_scope() as db:
        assert db.scalar(select(Category).where(Category.name == "1")) is None
    # 5) an older master whose Category column holds groups → group; a DOJ-style file with Category keeps the joining date
    r = _post_files(c, [("employee_master_old.xlsx", _xlsx([["Employee No", "Name", "Category", "Hours Per Day"],
                                                             ["T005", "Esha Patel", "SS", 8]]))])
    assert "imported as the group" in r.text
    with session_scope() as db:
        e5 = db.get(Employee, "T005")
        assert e5.group == "SS" and e5.category == ""
    _post_files(c, [("people.xlsx", _xlsx([["Employee No", "Employee Name", "Joined On", "Location", "Category"],
                                            ["T005", "Esha Patel", date(2022, 10, 18), "Mumbai", "HSCO (Old)"],
                                            ["T003", "Chitra Iyer", date(2001, 1, 2), "Mumbai", "HSCO (Old)"],
                                            ["T002", "Bilal Khan", date(2024, 7, 1), "Mumbai", "HSCO (Old)"]]))])
    with session_scope() as db:
        assert db.get(Employee, "T005").doj == date(2022, 10, 18) and db.get(Employee, "T005").category == "HSCO (Old)"
    # 8) the category report is the full truth: a blank additional category clears it
    c.post("/employees/save", data={"csrf": csrf_of(c, "/employees/T002"), "emp_no": "T002", "name": "Bilal Khan",
                                    "group": "ATA", "category": "HSCO (Old)", "additional_category": "Article",
                                    "weekly_off": "CATEGORY", "is_active": "1", "doj": "2024-07-01"})
    _post_files(c, [("EmployeeCategoryReport.xlsx", _xlsx([["Employee Number", "Employee Name", "Curr.Category",
                                                            "Additional Category"], ["T002", "Bilal Khan", "HSCO (Old)", None]]))])
    with session_scope() as db:
        assert db.get(Employee, "T002").additional_category == ""
    # 9) greytHR's Holiday List Importer files carry a Location column: holidays go by category only (v1.9), so it
    # is ignored — the rows land in the category's list, nothing is said about locations
    rows = [["Holiday Date", "Description", "Location", "Category"]] + \
        [[date(2026, m_, 11), f"D{m_}", "Delhi", "HSCO (Old)"] for m_ in range(1, 7)]
    r = _post_files(c, [("Holiday List Importer HSCO (Old) Delhi.xlsx", _xlsx(rows))])
    assert "the list is for Delhi" not in r.text and "HSCO (Old) 2026: 6 holidays" in r.text
    # 7) a one-off holiday for the category goes into its list; renaming the category keeps it
    c.post("/settings/holidays/categories", data={"csrf": csrf, "day": "2026-10-14", "name": "Old offsite",
                                                  "categories": ["HSCO (Old)"]})
    with session_scope() as db:
        cat = db.scalar(select(Category).where(Category.name == "HSCO (Old)"))
        cid, cal = cat.id, cat.holiday_calendar_id
        assert db.scalar(select(Holiday).where(Holiday.calendar_id == cal, Holiday.name == "Old offsite"))
    c.post("/settings/categories/save", data={"csrf": csrf, "cid": str(cid), "name": "HSCO (Older)",
                                              "holiday_calendar_id": str(cal)})
    assert day("T001", "2026-10-14")["type"] == "HOLIDAY"
    # 12) category text is never an Excel formula
    with session_scope() as db:
        db.get(Employee, "T006").category = "=SUM(1,1)"
    c.post("/settings/categories/save", data={"csrf": csrf, "name": "Spare"})            # (recalculates)
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    ws = wb["Payroll Leave Sheet"]
    rows_ = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    cell = ws.cell(row=rows_["T006"], column=29)
    assert cell.value == "=SUM(1,1)" and cell.data_type == "s"
    # 15) delete: refused while anyone (also leavers) has it; its holiday list is kept
    for e in ("T001", "T002", "T003", "T005"):
        c.post("/employees/save", data={"csrf": csrf_of(c, f"/employees/{e}"), "emp_no": e, "name": e, "category": "",
                                        "cat_fields": "1", "weekly_off": "CATEGORY", "is_active": "1"})
    with session_scope() as db:
        left = [(e.emp_no, e.category, e.additional_category) for e in db.scalars(select(Employee))
                if "Old" in (e.category or "") + (e.additional_category or "")]
    assert not left, left
    r = c.post(f"/settings/categories/{cid}/delete", data={"csrf": csrf}, follow_redirects=True)
    assert "deleted" in r.text and "its holiday calendar is kept" in r.text
    with session_scope() as db:
        assert db.scalar(select(Holiday).where(Holiday.calendar_id == cal, Holiday.name == "Old offsite"))


def test_approve_extra_wfh_from_review_queue_and_excel_follows(loaded):
    """v1.7.5: Review queue → Approve extra WFH (manager / partner discretion) → only unapproved extra days are
    deducted; the flag is marked reviewed; the downloaded Excel (built from the app at that moment) shows the new
    deduction, a formula that allows for the approved days, and the correction; 0 removes the approval."""
    import math
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import AuditLog, FlagReview
    c = loaded
    d4, _ = _result("T004")                                     # Article: 4 WFH days → 2 over the limit → 1 day
    assert d4["wfh_excess_days"] == 2 and d4["wfh_deduction_days"] == 1.0
    page = c.get("/flags?period=2026-10&code=WFH_OVER_LIMIT").text
    assert 'data-wfh="T004"' in page and "Approve WFH" in page and 'action="/flags/wfh-approve"' in page
    csrf = csrf_of(c, "/flags?period=2026-10")
    r = c.post("/flags/wfh-approve", data={"csrf": csrf, "period": "2026-10", "emp_no": "T004", "days": "1"},
               follow_redirects=True)
    assert "reason is required" in r.text                       # who approved it is mandatory
    r = c.post("/flags/wfh-approve", data={"csrf": csrf, "period": "2026-10", "emp_no": "T004", "days": "1",
                                           "reason": "Approved by partner"}, follow_redirects=True)
    assert "1 extra WFH day(s) approved" in r.text
    assert "Edit WFH approval" in c.get("/flags?period=2026-10&code=WFH_OVER_LIMIT&show=all").text   # now reviewed
    d4, f4 = _result("T004")
    assert d4["wfh_approved_days"] == 1 and d4["wfh_deduction_days"] == 0.5
    assert any("1 extra approved by manager / partner" in f["message"] for f in f4 if f["code"] == "WFH_OVER_LIMIT")
    with session_scope() as db:
        rv = db.scalar(select(FlagReview).where(FlagReview.emp_no == "T004", FlagReview.code == "WFH_OVER_LIMIT"))
        assert rv and "Extra WFH approved: 1 day(s) — Approved by partner" in rv.note
        assert any(a.action == "override.month" and "wfh_approved_days" in str(a.details) for a in db.scalars(select(AuditLog)))
    # the Excel is generated from the app's current figures
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    ws = wb["Payroll Leave Sheet"]
    rows = {ws.cell(row=i, column=1).value: i for i in range(6, ws.max_row + 1)}
    i = rows["T004"]
    f = ws.cell(row=i, column=22).value
    assert f.endswith(f"+CEILING(MAX(0,J{i}-2-1),1)*0.5")
    j = ws.cell(row=i, column=10).value
    assert math.ceil(max(0, j - 2 - 1)) * 0.5 == d4["deduction_days"] == 0.5     # T004 is hours-exempt: WFH part only
    corr = [[x.value for x in row] for row in wb["Corrections"].iter_rows(min_row=6) if row[0].value]
    assert any("Extra WFH approved" in str(r_) and "Approved by partner" in str(r_) for r_ in corr), corr
    # approve all → no deduction; 0 → approval removed, deduction back
    c.post("/flags/wfh-approve", data={"csrf": csrf, "period": "2026-10", "emp_no": "T004", "days": "2",
                                       "reason": "Approved by manager"})
    assert _result("T004")[0]["wfh_deduction_days"] == 0
    c.post("/flags/wfh-approve", data={"csrf": csrf, "period": "2026-10", "emp_no": "T004", "days": "0",
                                       "reason": "Withdrawn"})
    assert _result("T004")[0]["wfh_deduction_days"] == 1.0 and not _result("T004")[0].get("wfh_approved_days")
    # also available as a month correction on the employee page
    assert "Extra WFH approved (days)" in c.get("/periods/2026-10/employee/T004").text


def _result_days(emp):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    with session_scope() as db:
        return list(db.scalar(select(MonthResult).where(MonthResult.emp_no == emp)).days)


# ---------------------------------------------------------------- v1.4.1 review & performance regressions

def test_attention_counts_zero_percent_as_zero(data_dir):
    from app.db import init_db
    init_db()                      # its own empty database (never the developer's data folder)
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
        compute_period(db, get_period(db, 2026, 10))
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
    assert any(r[3] == "Biometric" and r[4] == "Office (biometric)" and str(r[5]).startswith("Office biometric")
               for r in rows)                                                                 # biometric punches too (v1.8 labels)
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



def test_attention_absence_part_uses_12_month_days(data_dir):
    from app.db import init_db
    init_db()                      # its own empty database (never the developer's data folder)
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


# ---------------------------------------------------------------- v1.8.0 whole-app review

def test_number_plural_and_audit_filters():
    from app.web import _fmt_num, _plural, _readable
    assert _fmt_num(0.5) == "0.5" and _fmt_num(34.50) == "34.5" and _fmt_num(2.25) == "2.25" and _fmt_num(3.0) == "3"
    assert _plural(1, "day") == "day" and _plural(0.5, "day") == "day" and _plural(2, "day") == "days"
    assert _plural(3, "person", "people") == "people"
    assert _readable(["a", "b"]) == "a, b" and _readable({"x": 1, "y": ["p", "q"]}) == "x: 1; y: p, q"
    assert _readable("Approved — partner") == "Approved — partner" and _readable(None) == ""


def test_waive_hours_deduction_from_review_queue(loaded):
    """v1.8.0: Review queue → Waive (a partner waived the hours-shortfall deduction): month correction
    shortfall_waived with the reason, deduction gone, info note instead, Excel follows; undo on the employee page."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import AuditLog, MonthOverrideRow
    c = loaded
    d1, f1 = _result("T001")
    assert d1["shortfall_deduction_days"] > 0 and "SHORTFALL_DEDUCTION" in {f["code"] for f in f1}
    page = c.get("/flags?period=2026-10&code=SHORTFALL_DEDUCTION").text
    assert 'data-waive="T001"' in page and 'action="/flags/waive-shortfall"' in page
    assert "does not change any figure" in page
    csrf = csrf_of(c, "/flags?period=2026-10")
    r = c.post("/flags/waive-shortfall", data={"csrf": csrf, "period": "2026-10", "emp_no": "T001", "reason": " "},
               follow_redirects=True)
    assert "reason is required" in r.text
    r = c.post("/flags/waive-shortfall", data={"csrf": csrf, "period": "2026-10", "emp_no": "t001",
                                               "reason": "Waived by partner"}, follow_redirects=True)
    assert "hours deduction waived" in r.text
    d1, f1 = _result("T001")
    codes = {f["code"] for f in f1}
    assert d1["shortfall_deduction_days"] == 0 and "SHORTFALL_DEDUCTION" not in codes and "SHORTFALL_EXEMPT" in codes
    assert any("waived this month" in f["message"] for f in f1)
    with session_scope() as db:
        o = db.scalar(select(MonthOverrideRow).where(MonthOverrideRow.emp_no == "T001",
                                                     MonthOverrideRow.field == "shortfall_waived"))
        assert o and o.value == "true" and o.reason == "Waived by partner"
        assert any(a.action == "override.month" and "shortfall_waived" in str(a.details) for a in db.scalars(select(AuditLog)))
    assert "Hours deduction waived" in c.get("/periods/2026-10/employee/T001").text
    wb = load_workbook(io.BytesIO(c.get("/exports/2026-10.xlsx").content))
    ws = wb["Payroll Leave Sheet"]
    i = {ws.cell(row=k, column=1).value: k for k in range(6, ws.max_row + 1)}["T001"]
    assert ws.cell(row=i, column=21).value == "Waived" and ws.cell(row=i, column=22).value.startswith(f'=IF(U{i}<>"No",0,')
    corr = [[x.value for x in row] for row in wb["Corrections"].iter_rows(min_row=6) if row[0].value]
    assert any("Waived by partner" in str(r_) for r_ in corr), corr
    # finalised months cannot be changed from here
    with session_scope() as db:
        from app.models import Period
        db.scalar(select(Period).where(Period.year == 2026, Period.month == 10)).status = "FINALISED"
    r = c.post("/flags/waive-shortfall", data={"csrf": csrf, "period": "2026-10", "emp_no": "T002",
                                               "reason": "x"}, follow_redirects=True)
    assert "finalised" in r.text


def test_day_drawer_explains_codes_and_warns_about_fixed_figures(loaded):
    c = loaded
    base = "/periods/2026-10/employee/T001"
    page = c.get(base).text
    assert '<optgroup label="Leave">' in page and "— absent</option>" in page
    csrf = csrf_of(c, base)
    c.post(base + "/month-override", data={"csrf": csrf, "field": "absent_days", "value": "0", "reason": "Fixed by HR"})
    r = c.post(base + "/day-override", data={"csrf": csrf, "day": "2026-10-05", "s1": "A", "s2": "A",
                                             "work_hm": "00:00", "reason": "No punch"}, follow_redirects=True)
    assert "also has fixed figures" in r.text
    assert "they override what the days add up to" in c.get(base).text


def test_no_leave_balances_anywhere(loaded):
    """v1.9 (owner): leave balances are kept in greytHR — the app neither asks for nor shows them."""
    c = loaded
    page = c.get("/").text
    assert "opening balance" not in page.lower() and "LOP days" in page and "Payroll LOP" not in page
    sheet = c.get("/periods/2026-10").text
    assert "Leave bal." not in sheet and "Salary ded." not in sheet and "LOP days (greytHR)" in sheet
    ins = c.get("/insights?period=2026-10").text
    assert "Leave liability" not in ins and "Comp-off bal." not in ins and "Annual leave used" in ins
    imp = c.get("/imports").text
    assert "leave_opening" not in imp and "Leave opening" not in imp
    assert "leave_opening" not in c.get("/exports").text
    st = c.get("/settings").text
    assert 'name="leave_credit_mode"' not in st and 'name="absent_debits_leave"' not in st and "kept in greytHR" in st


def test_import_templates_round_trip(loaded, tmp_path):
    """v1.8.0 templates: drop-downs, frozen header, dd-mm-yyyy dates, explicit Yes/No; the leave opening template is
    pre-filled for the latest month and rows left blank never clear a stored balance."""
    c = loaded
    wb = load_workbook(io.BytesIO(c.get("/exports/templates/employee_master.xlsx").content))
    ws = wb["Data"]
    assert wb.sheetnames[:2] == ["Data", "Instructions"] and wb["Lists"].sheet_state == "hidden"
    assert ws.freeze_panes == "C2" and ws.data_validations.dataValidation
    heads = [x.value for x in ws[1]]
    ex = heads.index("Exempt From Deduction") + 1
    assert {ws.cell(row=r, column=ex).value for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=1).value} <= {"Yes", "No"}  # never blank
    assert ws.cell(row=2, column=heads.index("Date of Joining") + 1).number_format == "dd-mm-yyyy"
    # re-importing the master unchanged changes nothing (the Lists sheet is not data)
    f = tmp_path / "employee_master_template.xlsx"
    f.write_bytes(c.get("/exports/templates/employee_master.xlsx").content)
    before = {e: _result(e)[0]["deduction_days"] for e in ("T001", "T003", "T004")}
    r = _upload(c, [f])
    assert r.status_code == 200 and "Unexpected" not in r.text
    assert {e: _result(e)[0]["deduction_days"] for e in before} == before
    hw = load_workbook(io.BytesIO(c.get("/exports/templates/holidays.xlsx").content))
    assert "Examples" in " ".join(str(x.value) for x in hw["Instructions"]["A"])
    assert "Holiday Calendar" not in heads                  # holidays follow the category (v1.9)
    # holiday template (v1.9): Date, Day, Holiday, Category, Optional, pre-filled; re-importing it changes nothing
    from app.db import session_scope
    from app.models import Category
    with session_scope() as db:
        db.add(Category(name="HSCO (Mum)"))
    tw = __import__("openpyxl").Workbook()
    tw.active.append(["Date", "Day", "Holiday", "Category", "Optional"])
    from datetime import date as _date
    for d_, n_ in ((_date(2026, 1, 26), "Republic Day"), (_date(2026, 3, 4), "Holi"), (_date(2026, 8, 15), "Independence Day"),
                   (_date(2026, 10, 2), "Gandhi Jayanti"), (_date(2026, 10, 20), "Dussehra"), (_date(2026, 12, 25), "Christmas")):
        tw.active.append([d_, d_.strftime("%a"), n_, "HSCO (Mum)", "No"])
    f = tmp_path / "Holidays 2026.xlsx"
    tw.save(f)
    assert "6 holidays loaded" in _upload(c, [f]).text
    hw = load_workbook(io.BytesIO(c.get("/exports/templates/holidays.xlsx").content))
    assert [x.value for x in hw["Data"][1]] == ["Date", "Day", "Holiday", "Category", "Optional"]
    pre = [[x.value for x in r_] for r_ in hw["Data"].iter_rows(min_row=2) if r_[0].value]
    assert any(r_[2] == "Dussehra" and r_[3] == "HSCO (Mum)" and r_[1] == "Tue" and r_[4] == "No" for r_ in pre), pre
    f = tmp_path / "holidays_template.xlsx"
    f.write_bytes(c.get("/exports/templates/holidays.xlsx").content)
    r = _upload(c, [f])
    assert "HSCO (Mum) 2026: 6 holidays — no changes." in r.text and "Unexpected" not in r.text
    # a leave-balance file is no longer imported as balances
    assert c.get("/exports/templates/leave_opening.xlsx").status_code == 404


# ---------------------------------------------------------------- v1.11 delete an upload together with its data

def _batch(kind):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import ImportBatch
    with session_scope() as db:
        b = db.scalar(select(ImportBatch).where(ImportBatch.kind == kind).order_by(ImportBatch.id.desc()))
        return b.id if b else None


def _count(model, **kw):
    from sqlalchemy import func, select
    from app.db import session_scope
    with session_scope() as db:
        q = select(func.count()).select_from(model)
        for k, v in kw.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


def test_delete_attendance_upload_removes_its_days_and_resets_the_month(loaded, fixtures):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import AuditLog, MonthResult, MusterRow, Period, SwipeRow
    c = loaded
    bid = _batch("muster")
    n = _count(MusterRow, batch_id=bid)
    assert n > 100 and n == _count(MusterRow)                     # every attendance day knows its upload
    page = c.get("/imports").text
    assert f'data-del="{bid}"' in page and f"Removes {n:,} attendance days of October 2026" in page
    csrf = csrf_of(c, "/imports")
    r = c.post(f"/imports/{bid}/delete", data={"csrf": csrf, "reason": " "}, follow_redirects=True)
    assert "reason is required" in r.text and _count(MusterRow) == n
    r = c.post(f"/imports/{bid}/delete", data={"csrf": csrf, "reason": "Wrong file uploaded"}, follow_redirects=True)
    assert f"removed {n} attendance days" in r.text
    assert _count(MusterRow) == 0 and _count(MonthResult) == 0 and _count(SwipeRow) > 0     # punches: another file
    with session_scope() as db:
        assert db.scalar(select(Period.computed_at).where(Period.year == 2026, Period.month == 10)) is None
        a = db.scalar(select(AuditLog).where(AuditLog.action == "import.delete"))
        assert a.details["reason"] == "Wrong file uploaded" and "attendance days" in a.details["removed"][0]
    assert c.get("/periods/2026-10").status_code == 200                   # "waiting for files", not an error
    assert f'data-del="{bid}"' not in c.get("/imports").text
    _upload(c, [fixtures["muster"]])                                      # uploading it again brings the month back
    assert _count(MusterRow) == n and _count(MonthResult) > 0


def test_delete_punch_upload_recalculates_without_its_punches(loaded):
    from app.models import MonthResult, SwipeRow
    c = loaded
    wfh_before = _result("T004")[0]["wfh_days"]
    bid = _batch("swipes")
    r = c.post(f"/imports/{bid}/delete", data={"csrf": csrf_of(c, "/imports"), "reason": "Duplicate upload"},
               follow_redirects=True)
    assert "punches" in r.text and _count(SwipeRow) == 0 and _count(MonthResult) > 0
    assert wfh_before == 4 and _result("T004")[0]["wfh_days"] == 0                # WFH came from the punches


def test_delete_a_later_partial_file_only_takes_its_own_people(loaded, fixtures, tmp_path):
    """A small correction file replaced one person's days; deleting it removes that person's days only."""
    import openpyxl
    from app.models import MusterRow
    c = loaded
    full = _batch("muster")
    total = _count(MusterRow)
    wb = openpyxl.load_workbook(fixtures["muster"])
    ws = wb.active
    keys = ("employee no", "emp no", "employee number", "employee no.")
    norm = lambda v: " ".join(str(v or "").split()).lower()  # noqa: E731 - greytHR writes "Employee  No"
    hdr_row = next(i for i in range(1, 30) if any(norm(v.value) in keys for v in ws[i]))
    emp_col = next(j for j, v in enumerate(ws[hdr_row]) if norm(v.value) in keys)
    for i in range(ws.max_row, hdr_row, -1):
        v = ws.cell(row=i, column=emp_col + 1).value
        if v and str(v).strip() != "T001":
            ws.delete_rows(i)
    f = tmp_path / "Attendance Muster Report T001 fix.xlsx"
    wb.save(f)
    _upload(c, [f])
    part = _batch("muster")
    t001 = _count(MusterRow, emp_no="T001")
    assert part != full and t001 > 0 and _count(MusterRow, batch_id=part) == t001
    assert _count(MusterRow) == total and _count(MusterRow, batch_id=full) == total - t001
    c.post(f"/imports/{part}/delete", data={"csrf": csrf_of(c, "/imports"), "reason": "Wrong file uploaded"})
    assert _count(MusterRow, emp_no="T001") == 0
    assert _count(MusterRow) == _count(MusterRow, batch_id=full) == total - t001    # the others are untouched


def test_delete_holiday_and_employee_uploads_undo_their_changes(loaded):
    from datetime import date
    from app.db import session_scope
    from app.models import Category, Employee, Holiday
    c = loaded
    with session_scope() as db:
        db.add(Category(name="HSCO (Mum)"))
        db.get(Employee, "T001").category = "HSCO (Mum)"
    hol = _xlsx([["Date", "Day", "Holiday", "Category", "Optional"],
                 [date(2026, 10, 7), "Wed", "Mum day", "HSCO (Mum)", "No"],
                 [date(2026, 10, 9), "Fri", "Second day", "HSCO (Mum)", "No"]])
    _post_files(c, [("Holidays.xlsx", hol)])
    hb = _batch("holidays")
    assert _count(Holiday, batch_id=hb) == 2
    day = lambda emp, iso: next(d for d in _result_days(emp) if d["date"] == iso)  # noqa: E731
    assert day("T001", "2026-10-07")["type"] == "HOLIDAY"
    assert "Removes 2 holidays it put in the lists" in c.get("/imports").text
    c.post(f"/imports/{hb}/delete", data={"csrf": csrf_of(c, "/imports"), "reason": "Wrong year"})
    assert _count(Holiday, batch_id=hb) == 0 and day("T001", "2026-10-07")["type"] == "WORKDAY"
    # employee master: a changed group and a new person — undone; a later manual edit wins
    m = _xlsx([["Employee No", "Name", "Group"], ["T001", "Asha Rao", "ATA"], ["T002", "Bilal Khan", "GST"],
               ["T099", "New Person", "BSS"]])
    _post_files(c, [("employee_master.xlsx", m)])
    mb = _batch("employee_master")
    with session_scope() as db:
        assert db.get(Employee, "T001").group == "ATA" and db.get(Employee, "T099") is not None
        db.get(Employee, "T002").group = "Offshore"                     # edited by hand after the import
    page = c.get("/imports").text
    assert "the details it changed for 2 employees" in page and "1 employee it added" in page
    r = c.post(f"/imports/{mb}/delete", data={"csrf": csrf_of(c, "/imports"), "reason": "Wrong file uploaded"},
               follow_redirects=True)
    assert "employee detail" in r.text and "1 employee added by it" in r.text
    with session_scope() as db:
        assert db.get(Employee, "T001").group == "BSS"                  # put back
        assert db.get(Employee, "T002").group == "Offshore"             # edited since: kept
        assert db.get(Employee, "T099") is None                         # added by the file, no other data


def test_delete_upload_refused_for_a_finalised_month(loaded):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MusterRow, Period
    c = loaded
    bid = _batch("muster")
    with session_scope() as db:
        db.scalar(select(Period).where(Period.year == 2026, Period.month == 10)).status = "FINALISED"
    assert f'data-del="{bid}"' not in c.get("/imports").text
    r = c.post(f"/imports/{bid}/delete", data={"csrf": csrf_of(c, "/imports"), "reason": "x"}, follow_redirects=True)
    assert "finalised" in r.text and _count(MusterRow) > 0


def test_migration_links_older_rows_to_their_latest_upload(data_dir):
    from datetime import date
    from sqlalchemy import select, text
    from app.db import _link_rows_to_uploads, init_db, session_scope
    from app.models import ImportBatch, MusterRow, Period
    init_db()
    with session_scope() as db:
        p = Period(year=2026, month=9)
        db.add(p)
        db.flush()
        old = ImportBatch(period_id=p.id, kind="muster", filename="a.xlsx", sha256="x")
        new = ImportBatch(period_id=p.id, kind="muster", filename="b.xlsx", sha256="y")
        db.add_all([old, new])
        db.flush()
        db.add(MusterRow(period_id=p.id, emp_no="E1", day=date(2026, 9, 1)))
        new_id = new.id
    with session_scope() as db:
        db.execute(text("UPDATE muster_rows SET batch_id = NULL"))
        _link_rows_to_uploads(db)
    with session_scope() as db:
        assert db.scalar(select(MusterRow.batch_id)) == new_id
