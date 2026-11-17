"""Browser end-to-end test (Playwright + Chromium) against a real uvicorn server.

Covers: first-login TOTP enrolment, recovery codes, real-file upload, dashboard charts, payroll sheet filtering,
employee drill-down, day correction, Excel + board-pack downloads, the offline board pack's drill-down modal,
passkey (WebAuthn) registration and sign-in via Chromium's virtual authenticator, and JS-error-free pages.
Screenshots go to test-results/ (git-ignored).
Run: .venv\\Scripts\\python -m pytest tests/e2e -q
"""
import os
import re
import socket
import threading
import time
from pathlib import Path

import pyotp
import pytest

pw = pytest.importorskip("playwright.sync_api")
from tests.conftest import ADMIN_PW, HAS_REF, REF  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "test-results"
REAL = ["MIS Report October 2026_Manual.xlsx", "DateOfJoiningReport.xlsx", "Attendance Summary Report.xls",
        "EmployeeSwipeDetails.xlsx", "Attendance Muster Report.xlsx"]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    port = _free_port()
    d = tmp_path_factory.mktemp("e2e") / "data"
    os.environ.update({"HSCO_DATA_DIR": str(d), "HSCO_INSECURE_COOKIES": "1", "HSCO_RP_ID": "localhost",
                       "HSCO_ORIGIN": f"http://localhost:{port}"})
    from app.config import reset_config
    from app.db import reset_engine
    reset_config()
    reset_engine()
    from app.main import create_app
    from tests.conftest import make_user
    app = create_app()
    make_user()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://localhost:{port}", tmp_path_factory.mktemp("fx")
    srv.should_exit = True
    th.join(timeout=5)
    reset_engine()
    reset_config()


def test_full_browser_journey(server):
    base, fxdir = server
    OUT.mkdir(exist_ok=True)
    if HAS_REF:
        files = [str(REF / f) for f in REAL]
        from tests.reference_setup import build
        emp = "PC006"
        emp_name = build()["mis"][emp]["name"]  # read at runtime: real names never live in the repo
    else:
        from tests.fixtures.make_fixtures import write_all
        fx = write_all(Path(fxdir))
        files = [str(fx[k]) for k in ("master", "doj", "swipes", "muster")]
        emp, emp_name = "T004", "Dev Mehta"
    errors = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
        page.on("console", lambda m: errors.append(f"{page.url}: console {m.text}") if m.type == "error" else None)

        # ---- sign in + forced TOTP enrolment
        page.goto(base + "/")
        assert "/login" in page.url
        page.fill("#u", "admin")
        page.fill("#p", ADMIN_PW)
        page.click("button:has-text('Continue')")
        page.wait_for_url(re.compile(r".*/security/enrol"))
        secret = page.inner_text("#totp-secret").strip()
        page.screenshot(path=str(OUT / "01_enrol.png"))
        page.fill("input[name=code]", pyotp.TOTP(secret).now())
        page.click("button:has-text('Verify')")
        page.wait_for_selector("#codes")
        assert len(page.query_selector_all("#codes div")) == 10
        page.click("a:has-text('Continue')")
        assert "set up your first month" in page.content()
        page.screenshot(path=str(OUT / "00_welcome.png"))

        # ---- import (drag-and-drop zone with file preview)
        page.goto(base + "/imports")
        page.set_input_files("input[name=files]", files)
        assert len(page.query_selector_all("#filelist li")) == len(files)
        page.click("#upbtn")
        page.wait_for_selector("text=Import result", timeout=120_000)
        assert "Unexpected" not in page.content()
        page.screenshot(path=str(OUT / "02_import.png"), full_page=True)

        # ---- overview: checklist, KPIs, tabbed charts
        page.goto(base + "/")
        page.wait_for_selector("canvas#cLoc", state="attached")
        n_charts = page.evaluate("Object.keys(Chart.instances || {}).length")
        assert n_charts >= 5, errors
        assert page.is_visible("text=Month-end checklist")
        page.screenshot(path=str(OUT / "03_dashboard.png"), full_page=True)
        page.click("#dash-tabs [data-tab=punct]")
        page.wait_for_selector("canvas#cIn", state="visible")
        page.screenshot(path=str(OUT / "03b_dashboard_punctuality.png"), full_page=True)

        # ---- quick search with the "/" shortcut
        page.keyboard.press("/")
        page.keyboard.type(emp_name[:6])
        page.wait_for_selector("#q-results.open a")
        page.screenshot(path=str(OUT / "03c_search.png"))
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r".*/employee/"))

        # ---- payroll sheet: search, chips, row click
        page.goto(base + "/periods/2026-10")
        page.screenshot(path=str(OUT / "04_sheet.png"), full_page=True)
        page.click("[data-chips] .chip[data-chip=deduction]")
        shown = page.evaluate("Array.from(document.querySelectorAll('#res tbody tr')).filter(r=>r.style.display!=='none').length")
        assert shown == page.evaluate("document.querySelectorAll('#res tbody tr[data-tags*=deduction]').length")
        page.click("[data-chips] .chip[data-chip='']")
        page.fill("[data-table-for='#res'] .tt-search input", emp_name)
        page.wait_for_timeout(300)
        visible = page.evaluate("Array.from(document.querySelectorAll('#res tbody tr')).filter(r=>r.style.display!=='none').length")
        assert visible == 1
        page.click("#res tbody tr:visible")
        page.wait_for_url(re.compile(rf".*/employee/{emp}"))
        assert page.query_selector(".cal .day") is not None
        page.screenshot(path=str(OUT / "05_drilldown.png"), full_page=True)

        # ---- correct a day straight from the calendar (side panel)
        page.click(".cal .day[data-day='2026-10-05']")
        page.wait_for_selector("#day.open")
        page.click("#quick [data-q^='A,A']")
        page.fill("#day-form input[name=reason]", "E2E test correction")
        page.screenshot(path=str(OUT / "05b_day_drawer.png"))
        page.click("button:has-text('Save day correction')")
        page.wait_for_selector("text=Day override saved")
        page.click("#emp-tabs [data-tab=corr]")
        assert page.is_visible("text=E2E test correction")

        # ---- review queue: mark an exception reviewed via the side panel
        page.goto(base + "/flags?period=2026-10")
        before = page.inner_text(".ph .split .strong")
        page.click("#fl button[data-act=review] >> nth=0")
        page.wait_for_selector("#rev.open")
        page.click("#rev [data-note='No action needed']")
        page.screenshot(path=str(OUT / "06a_review.png"))
        page.click("#rv-save")
        page.wait_for_selector(".toast:has-text('Marked reviewed')")
        assert page.inner_text(".ph .split .strong") != before

        # ---- downloads
        with page.expect_download() as dl:
            page.goto(base + "/exports")
            page.click("a:has-text('Payroll sheet + board metrics')")
        xlsx = OUT / "export.xlsx"
        dl.value.save_as(str(xlsx))
        assert xlsx.stat().st_size > 10_000
        with page.expect_download() as dl2:
            page.click("a:has-text('Board pack (.html)')")
        pack = OUT / "board_pack.html"
        dl2.value.save_as(str(pack))

        # ---- the offline board pack works on its own (file://, no server)
        bp = ctx.new_page()
        bp.on("pageerror", lambda e: errors.append(f"boardpack: {e}"))
        bp.goto(pack.as_uri())
        bp.wait_for_selector("#kpis .kpi")
        bp.screenshot(path=str(OUT / "06_boardpack.png"), full_page=True)
        bp.click("#tabs a[data-t=sheet]")
        bp.fill("#q", emp_name)
        bp.click(f"#tbl tbody tr:has-text('{emp_name}')")
        bp.wait_for_selector("#modal .cal")
        assert emp_name in bp.inner_text("#mbox")
        bp.screenshot(path=str(OUT / "07_boardpack_drill.png"), full_page=True)
        bp.close()

        # ---- passkey: register with a virtual authenticator, then sign in with it
        cdp = ctx.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True, "hasUserVerification": True,
            "isUserVerified": True}})
        page.goto(base + "/security")
        page.once("dialog", lambda d: d.accept("Virtual key"))
        page.click("#addkey")
        page.wait_for_selector("text=Virtual key", timeout=15_000)
        page.screenshot(path=str(OUT / "08_security.png"), full_page=True)
        page.click("#um-btn")
        page.click("#um-menu button:has-text('Sign out')")
        page.fill("#u", "admin")
        page.fill("#p", ADMIN_PW)
        page.click("button:has-text('Continue')")
        page.wait_for_selector("#pk")
        page.screenshot(path=str(OUT / "09_mfa_choice.png"))
        page.click("#pk")
        page.wait_for_url(re.compile(rf"^{re.escape(base)}/$"), timeout=15_000)
        assert "Month-end checklist" in page.content()

        # ---- every main page without JS errors
        for path in ("/flags", "/employees", "/reconciliation", "/settings", "/settings#holidays", "/users", "/audit",
                     f"/employees/{emp}"):
            page.goto(base + path)
            page.wait_for_load_state("networkidle")
        for name, path in (("10_reconciliation", "/reconciliation"), ("11_employees", "/employees"), ("12_settings", "/settings"),
                           ("13_users", "/users"), ("14_flags", "/flags"), ("15_exports", "/exports")):
            page.goto(base + path)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        browser.close()
    assert not errors, errors
