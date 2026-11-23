"""End-to-end self-update test — the full production path, locally:

release zip (scripts/build_release.py, same as CI) → installed copy (no .git) started by the supervisor
(run_server.py) → background check against a mock GitHub Releases API → banner → "Update now" in Chromium →
progress bar → download, checksum, staging → supervisor backs up DB, swaps code, restarts, health-checks →
page shows the new build. Then a deliberately broken release must be rolled back automatically.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import build_release  # noqa: E402
from test_updater import Releases  # noqa: E402

OUT = ROOT / "test-results" / "update"


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def _wait(fn, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.25)
    raise AssertionError(f"timeout waiting for {what}")


def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.kill()


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    z1, _ = build_release.build(1, dist, version="1.2.0")
    z2, _ = build_release.build(2, dist, version="9.9.9")
    z3, _ = build_release.build(3, dist, version="9.9.10",
                                mutate=lambda n, b: b + b"\nraise SystemExit(3)  # broken release\n" if n == "app/main.py" else b)
    install = tmp_path / "srv" / "hsco"
    install.mkdir(parents=True)
    with zipfile.ZipFile(z1) as z:
        z.extractall(install)
    assert not (install / ".git").exists()
    dd = tmp_path / "srv" / "data"
    # seed the server's data with the project code (same schema): user, signed-in session, a month of data
    monkeypatch.setenv("HSCO_DATA_DIR", str(dd))
    from app.config import reset_config
    from app.db import reset_engine, session_scope
    reset_config()
    reset_engine()
    from app.db import init_db
    init_db()
    from conftest import make_user
    make_user()
    from fixtures.make_fixtures import write_all
    from app.services.imports import import_file
    fx = write_all(tmp_path / "fx")
    for k in ("master", "doj", "swipes", "muster"):
        with session_scope() as db:
            import_file(db, fx[k].read_bytes(), fx[k].name, None)
    from sqlalchemy import select
    from app.models import User
    from app.security import core as sec
    with session_scope() as db:
        token = sec.create_session(db, db.scalar(select(User)), "127.0.0.1", "e2e")
    reset_engine()
    reset_config()
    rel = Releases(z2, 2, "9.9.9", delay=0.08)
    port = _port()
    env = {k: v for k, v in os.environ.items() if k not in ("HSCO_DISABLE_UPDATE_THREAD", "HSCO_SUPERVISED",
                                                            "HSCO_UPDATES_FORCE")}
    env.update(HSCO_DATA_DIR=str(dd), HSCO_UPDATE_URL=rel.base + "/latest", HSCO_UPDATE_FIRST_DELAY="1",
               PYTHONIOENCODING="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / "supervisor.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "run_server.py", "--dev", "--port", str(port)], cwd=str(install), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        _wait(lambda: (_health(port) or {}).get("build") == 1, 60, "build 1 to start")
        yield {"port": port, "token": token, "install": install, "dd": dd, "rel": rel, "z3": z3, "proc": proc}
    finally:
        _kill_tree(proc)
        rel.close()
        log.close()


def test_update_end_to_end(installed):
    port, base = installed["port"], f"http://localhost:{installed['port']}"
    install, dd = installed["install"], installed["dd"]
    errors = []
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies([{"name": "hsco_session", "value": installed["token"], "url": base}])
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1. the background check finds build 2 → banner
        def banner():
            page.goto(base + "/")
            return page.query_selector("#upd-banner")
        _wait(banner, 30, "update banner")
        assert "9.9.9 is available" in page.inner_text("#upd-banner")
        page.screenshot(path=str(OUT / "1_banner.png"))

        # 2. click → dialog with notes; start → progress bar moves
        page.click("#upd-banner button:has-text(\"What's new\")")
        page.wait_for_selector("#upd-modal.open")
        assert "Test release notes" in page.inner_text("#um-notes")
        page.screenshot(path=str(OUT / "2_dialog.png"))
        page.click("#um-go")
        samples, shot = [], False
        end = time.time() + 120
        while time.time() < end:
            try:
                txt = page.inner_text("#um-msg")
                pct = page.inner_text("#um-pct")
            except Exception:  # noqa: BLE001 - page reloading after success
                break
            samples.append((txt, pct))
            if not shot and "Downloading" in txt and pct not in ("0%", "100%", ""):
                page.screenshot(path=str(OUT / "3_progress.png"))
                shot = True
            if txt.startswith("Updated to v9.9.9"):
                page.screenshot(path=str(OUT / "4_done.png"))
                break
            time.sleep(0.1)
        pcts = sorted({int(p_.rstrip("%")) for _, p_ in samples if p_.endswith("%") and p_[:-1].isdigit()})
        assert any(0 < x < 100 for x in pcts), f"no intermediate progress seen: {samples[:20]}"
        assert any("Backing up, installing and restarting" in t for t, _ in samples), samples[-10:]

        # 3. server is on the new build; page reloads signed-in with a confirmation toast
        h = _wait(lambda: (_health(port) or {}).get("build") == 2 and _health(port), 60, "build 2")
        assert h["version"] == "9.9.9"
        page.wait_for_load_state("load")
        # the page reloads itself after a successful update and shows the toast once (remembered in localStorage);
        # if that reload won the race, forget it so the reload below must show the toast again
        page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('upd-seen-'))"
                      ".forEach(k => localStorage.removeItem(k))")
        page.goto(base + "/")
        page.wait_for_selector(".toast:has-text('Updated to v9.9.9')", timeout=10_000)
        assert page.query_selector("#upd-banner") is None
        page.screenshot(path=str(OUT / "5_updated.png"))
        page.goto(base + "/settings#updates")
        assert "build 2" in page.inner_text("[data-pane=updates]") and "installed" in page.inner_text("[data-pane=updates]")

        # files and data
        assert json.loads((install / "BUILD.json").read_text())["build"] == 2
        assert '__version__ = "9.9.9"' in (install / "app" / "__init__.py").read_text()
        assert list((dd / "backups").glob("pre-update-build2-*.db")), "DB backup before update"
        assert list((dd / "updates" / "rollback").glob("build1-*")), "previous version kept for rollback"
        assert json.loads((dd / "updates" / "last_result.json").read_text())["status"] == "applied"
        assert not (dd / "updates" / "staged").exists()
        page.goto(base + "/periods/2026-10")
        assert "Payroll leave sheet" in page.content() and "Dev Mehta" in page.content()   # data survived

        # 4. a broken release is rolled back automatically and the dialog says so
        rel = installed["rel"]
        rel.zpath, rel.build, rel.version = installed["z3"], 3, "9.9.10"
        import hashlib
        rel.sha = hashlib.sha256(installed["z3"].read_bytes()).hexdigest()
        page.goto(base + "/settings#updates")
        page.click("#chk-now")
        page.wait_for_selector("#upd-banner")
        page.click("#upd-now")
        page.wait_for_selector("#um-err:not(.hidden)", timeout=150_000)
        err = page.inner_text("#um-err")
        page.screenshot(path=str(OUT / "6_rolled_back.png"))
        assert "restored" in err or "rolled back" in err, err
        h = _wait(lambda: _health(port), 30, "server after rollback")
        assert h["build"] == 2 and h["version"] == "9.9.9"
        lr = json.loads((dd / "updates" / "last_result.json").read_text())
        assert lr["status"] == "rolled_back" and lr["to_build"] == 3
        assert json.loads((install / "BUILD.json").read_text())["build"] == 2
        b.close()
    assert not errors, errors
