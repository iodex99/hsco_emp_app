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
        assert "No data yet" in page.content()

        # ---- import
        page.goto(base + "/imports")
        page.set_input_files("input[name=files]", files)
        page.click("button:has-text('Import')")
        page.wait_for_selector("text=Import result", timeout=120_000)
        assert "Unexpected" not in page.content()
        page.screenshot(path=str(OUT / "02_import.png"), full_page=True)

        # ---- dashboard with charts
        page.goto(base + "/")
        page.wait_for_selector("canvas#cLoc")
        n_charts = page.evaluate("Object.keys(Chart.instances || {}).length")
        assert n_charts >= 5, errors
        page.screenshot(path=str(OUT / "03_dashboard.png"), full_page=True)

        # ---- payroll sheet, filter, drill-down
        page.goto(base + "/periods/2026-10")
        page.fill("input[type=search]", emp_name)
        visible = page.evaluate("Array.from(document.querySelectorAll('#res tbody tr')).filter(r=>r.style.display!=='none').length")
        assert visible == 1
        page.screenshot(path=str(OUT / "04_sheet.png"))
        page.click(f"a:has-text('{emp_name}')")
        page.wait_for_url(re.compile(rf".*/employee/{emp}"))
        assert page.query_selector(".cal .day") is not None
        page.screenshot(path=str(OUT / "05_drilldown.png"), full_page=True)

        # ---- day correction via form
        page.fill("input[name=day]", "2026-10-05")
        page.select_option("select[name=s1]", "A")
        page.select_option("select[name=s2]", "A")
        page.fill("input[name=work_hm]", "00:00")
        page.fill("form[action$=day-override] input[name=reason]", "E2E test correction")
        page.click("button:has-text('Save day correction')")
        page.wait_for_selector("text=Day override saved")
        assert "E2E test correction" in page.content()

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
        page.click("button:has-text('Sign out')")
        page.fill("#u", "admin")
        page.fill("#p", ADMIN_PW)
        page.click("button:has-text('Continue')")
        page.wait_for_selector("#pk")
        page.screenshot(path=str(OUT / "09_mfa_choice.png"))
        page.click("#pk")
        page.wait_for_url(re.compile(rf"^{re.escape(base)}/$"), timeout=15_000)
        assert "Board dashboard" in page.content()

        # ---- every main page without JS errors
        for path in ("/flags", "/employees", "/reconciliation", "/settings", "/settings#holidays", "/users", "/audit",
                     f"/employees/{emp}"):
            page.goto(base + path)
            page.wait_for_load_state("networkidle")
        page.goto(base + "/reconciliation")
        page.screenshot(path=str(OUT / "10_reconciliation.png"), full_page=True)
        browser.close()
    assert not errors, errors
