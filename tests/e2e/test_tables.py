"""Browser tests for the table toolkit (app/static/table.js) on a live server with synthetic data.

Fixture employees (tests/fixtures/make_fixtures.py): T001 BSS (0.5 d deduction), T002 ATA (1 d), T003 Partner (exempt),
T004 Article (WFH 4 → 1 d deduction for WFH over the limit, very late), T005 BSS (1 absent, LOP), T006 Offshore (joined 13-Oct-2026).
"""
import csv
import io
import re
import socket
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    import os
    import uvicorn
    port = _port()
    d = tmp_path_factory.mktemp("tables")
    os.environ.update({"HSCO_DATA_DIR": str(d / "data"), "HSCO_INSECURE_COOKIES": "1"})
    from app.config import reset_config
    from app.db import reset_engine, session_scope
    reset_config()
    reset_engine()
    from app.main import create_app
    app = create_app()
    from app.services.imports import import_file
    from tests.conftest import make_user
    from tests.fixtures.make_fixtures import write_all
    fx = write_all(d / "fx")
    for k in ("master", "doj", "swipes", "muster"):
        with session_scope() as db:
            import_file(db, fx[k].read_bytes(), fx[k].name, None)
    make_user()
    from sqlalchemy import select
    from app.models import User
    from app.security import core as sec
    with session_scope() as db:
        tok = sec.create_session(db, db.scalar(select(User)), "127.0.0.1", "t")
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://localhost:{port}", tok
    srv.should_exit = True
    th.join(timeout=5)
    reset_engine()
    reset_config()


@pytest.fixture()
def page(live):
    base, tok = live
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        ctx.add_cookies([{"name": "hsco_session", "value": tok, "url": base}])
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.base = base
        yield pg
        assert not pg.errors, pg.errors
        b.close()


def visible_ids(pg, table="#res"):
    return pg.evaluate(f"""Array.from(document.querySelectorAll('{table} tbody tr:not(.tt-grp)'))
        .filter(r => r.style.display !== 'none').map(r => (r.dataset.href || '').split('/').pop() || r.dataset.emp || r.cells[0].innerText.trim())""")


def bar(pg, table="#res"):
    return f"[data-table-for='{table}']"


def add_filter(pg, column, table="#res"):
    pg.click(f"{bar(pg, table)} .tt-btn[data-a=filter]")
    pg.click(f".tt-pop .tt-list button:has-text('{column}')")
    pg.wait_for_selector(".tt-pop .tt-op")


def test_payroll_sheet_search_filter_sort_group_columns_views_csv(page):
    pg = page
    pg.goto(pg.base + "/periods/2026-10")
    pg.wait_for_selector(f"{bar(pg)} .tt-count")
    assert pg.inner_text(f"{bar(pg)} .tt-count") == "6 rows"

    # search
    pg.fill(f"{bar(pg)} .tt-search input", "mehta")
    pg.wait_for_timeout(250)
    assert visible_ids(pg) == ["T004"] and "Showing 1 of 6" in pg.inner_text(f"{bar(pg)} .tt-count")
    pg.fill(f"{bar(pg)} .tt-search input", "")
    pg.wait_for_timeout(250)

    # enum filter (faceted checkboxes with counts) on a column that is hidden by default
    add_filter(pg, "Group")
    assert pg.inner_text(".tt-pop .tt-vals label:has-text('BSS') .muted") == "2"
    pg.check(".tt-pop .tt-vals input[value='BSS']")
    pg.click(".tt-pop [data-done]")
    assert sorted(visible_ids(pg)) == ["T001", "T005"]
    assert "Group" in pg.inner_text(f"{bar(pg)} .tt-active") and "BSS" in pg.inner_text(f"{bar(pg)} .tt-active")

    # numeric filter stacks (AND)
    add_filter(pg, "Absent")
    pg.select_option(".tt-pop .tt-op", "gte")
    pg.fill(".tt-pop .tt-v", "1")
    pg.click(".tt-pop [data-done]")
    assert visible_ids(pg) == ["T005"]
    pg.screenshot(path="test-results/tables_filters.png")

    # remove one chip, then the URL keeps state across a reload
    pg.click(f"{bar(pg)} .tt-chip:has-text('Absent') .tt-x")
    assert sorted(visible_ids(pg)) == ["T001", "T005"]
    assert "t_res=" in pg.url
    pg.reload()
    pg.wait_for_selector(f"{bar(pg)} .tt-count")
    assert sorted(visible_ids(pg)) == ["T001", "T005"]

    # saved view: save, clear, re-apply
    pg.click(f"{bar(pg)} .tt-btn[data-a=views]")
    pg.fill(".tt-pop .tt-vname", "BSS only")
    pg.click(".tt-pop [data-save]")
    pg.keyboard.press("Escape")
    pg.click(f"{bar(pg)} .tt-clear")
    assert len(visible_ids(pg)) == 6
    pg.click(f"{bar(pg)} .tt-btn[data-a=views]")
    pg.click(".tt-pop [data-apply='0']")
    assert sorted(visible_ids(pg)) == ["T001", "T005"]
    pg.click(f"{bar(pg)} .tt-clear")

    # sort: header click (numbers default to high → low), shift-click adds a 2nd level
    # T002 (1 day, hours) and T004 (1 day, WFH over the limit — v1.6.6) share the largest deduction
    pg.click("#res th[data-key=deduction]")
    assert visible_ids(pg)[:2] == ["T002", "T004"]
    pg.click("#res th[data-key=deduction]")
    assert visible_ids(pg)[-2:] == ["T002", "T004"]
    pg.click("#res th[data-key=emp]", modifiers=["Shift"])           # 2nd level breaks the tie: employee A→Z
    assert pg.get_attribute("#res th[data-key=emp]", "data-order") == "2"
    assert visible_ids(pg)[-2:] == ["T002", "T004"]
    pg.click(f"{bar(pg)} .tt-btn[data-a=sort]")
    assert len(pg.query_selector_all(".tt-pop [data-i]")) == 2
    pg.keyboard.press("Escape")
    pg.click(f"{bar(pg)} .tt-clear")

    # group by with counts + subtotals, collapse
    pg.click(f"{bar(pg)} .tt-btn[data-a=group]")
    pg.click(".tt-pop .tt-list button[data-k=group]")
    groups = pg.eval_on_selector_all("#res tr.tt-grp td:first-child", "els => els.map(e => e.innerText)")
    assert any("BSS" in g and "2" in g for g in groups) and len(groups) == 5
    pg.screenshot(path="test-results/tables_grouped.png", full_page=True)
    pg.click("#res tr.tt-grp:has-text('BSS') .tt-tog")
    assert "T001" not in visible_ids(pg)
    pg.click(f"{bar(pg)} .tt-clear")

    # columns: hide Remark, show all
    pg.click(f"{bar(pg)} .tt-btn[data-a=columns]")
    pg.uncheck(".tt-pop .tt-vals input[value=remark]")
    assert not pg.is_visible("#res th[data-key=remark]")
    pg.click(".tt-pop [data-p=all]")
    assert pg.is_visible("#res th[data-key=group]") and pg.is_visible("#res th[data-key=remark]")
    pg.click(".tt-pop [data-p=key]")
    pg.keyboard.press("Escape")

    # CSV of exactly what is shown
    pg.click("[data-chips] .chip[data-chip=deduction]")
    def download(which):                      # v1.8: hidden columns → choose "Columns shown" / "All columns"
        pg.click(f"{bar(pg)} .tt-btn[data-a=csv]")
        with pg.expect_download() as dl:
            pg.click(f'.tt-pop [data-all="{which}"]')
        return list(csv.reader(io.StringIO(open(dl.value.path(), encoding="utf-8-sig").read())))
    shown, every = download("0"), download("1")
    for rows in (shown, every):
        assert rows[0][:2] == ["Employee No", "Name"] and "Deduction days" in rows[0]   # number and name split
        assert [r[0] for r in rows[1:]] == visible_ids(pg)                              # rows as on screen, in order
        assert len(rows) - 1 == 3                                # T001, T002, T004 (WFH, v1.6.6)
    assert set(shown[0]) < set(every[0]) and "Remark" in every[0]
    assert all(not v.endswith(" d") for r in shown[1:] for v in r)                       # raw numbers, no units
    # footer total follows the filter
    assert pg.inner_text("#res tfoot td[data-sum][data-unit]") == "2.5 d"


def test_review_queue_employees_days_and_audit(page):
    pg = page
    # review queue: severity filter + severity sort order
    pg.goto(pg.base + "/flags?show=all")
    pg.wait_for_selector(f"{bar(pg, '#fl')} .tt-count")
    add_filter(pg, "Severity", "#fl")
    pg.check(".tt-pop .tt-vals input[value='High']")
    pg.click(".tt-pop [data-done]")
    sev = pg.eval_on_selector_all("#fl tbody tr", "rs => rs.filter(r => r.style.display !== 'none').map(r => r.cells[2].innerText.trim())")
    assert sev and set(sev) == {"High"}
    pg.click(f"{bar(pg, '#fl')} .tt-clear")
    pg.click("#fl th[data-key=sev]")
    sev = pg.eval_on_selector_all("#fl tbody tr", "rs => rs.map(r => r.cells[2].innerText.trim())")
    order = {"Critical": 0, "High": 1, "Medium": 2, "Info": 3}
    assert [order[s] for s in sev] == sorted(order[s] for s in sev)

    # employees: date filter (joined on/after) and enum on location
    pg.goto(pg.base + "/employees")
    pg.wait_for_selector(f"{bar(pg, '#emps')} .tt-count")
    add_filter(pg, "Joined", "#emps")
    pg.select_option(".tt-pop .tt-op", "after")
    pg.fill(".tt-pop .tt-v", "2026-01-01")
    pg.click(".tt-pop [data-done]")
    joined = pg.eval_on_selector_all("#emps tbody tr", "rs => rs.filter(r => r.style.display !== 'none').map(r => r.dataset.href.split('/').pop())")
    assert sorted(joined) == ["T006", "T007"]

    # employee page: day chips (tags) + toolkit
    pg.goto(pg.base + "/periods/2026-10/employee/T004#days")
    pg.click("#emp-tabs [data-tab=days]")
    pg.click("[data-chips='#days'] .chip[data-chip=wfh]")
    n = pg.eval_on_selector_all("#days tbody tr", "rs => rs.filter(r => r.style.display !== 'none').length")
    assert n == 4
    pg.click("[data-chips='#days'] .chip[data-chip=late]")
    n_late = pg.eval_on_selector_all("#days tbody tr", "rs => rs.filter(r => r.style.display !== 'none').length")
    assert n_late >= 15
    pg.click("#days th[data-key=late]")
    lates = pg.eval_on_selector_all("#days tbody tr", "rs => rs.filter(r => r.style.display !== 'none').map(r => +r.cells[6].innerText)")
    assert lates == sorted(lates, reverse=True)

    # audit log: server-side filters
    pg.goto(pg.base + "/audit?cat=Imports")
    whats = pg.eval_on_selector_all("#aud tbody tr", "rs => rs.map(r => r.cells[2].innerText.trim())")
    assert whats and set(whats) == {"Imported file"}
    assert re.search(r"\d+ entr", pg.inner_text("#audit-total"))
    pg.goto(pg.base + "/audit?user=nobody")
    assert "No entries" in pg.content()


def test_bulk_review_legend_filter_and_flag_links(page):
    pg = page
    # ---- employee page: legend filters the calendar (multi-select = any of)
    pg.goto(pg.base + "/periods/2026-10/employee/T004")
    pg.wait_for_selector("#cal-legend")
    total_wfh = pg.eval_on_selector_all(".cal .day[data-tags~='wfh']", "e => e.length")
    total_late = pg.eval_on_selector_all(".cal .day[data-tags~='late']", "e => e.length")
    assert total_wfh == 4 and total_late >= 15
    pg.click("#cal-legend button[data-lg=wfh]")
    assert pg.inner_text("#lg-n") == "4"
    assert pg.eval_on_selector_all(".cal .day.match", "e => e.length") == 4
    pg.click("#cal-legend button[data-lg=late]")
    union = pg.eval_on_selector_all(".cal .day[data-day]",
                                    "ds => ds.filter(d => { const t = d.dataset.tags.split(' '); return t.includes('wfh') || t.includes('late'); }).length")
    assert pg.inner_text("#lg-n") == str(union)
    assert pg.get_attribute("#cal-legend button[data-lg=late]", "aria-pressed") == "true"
    pg.screenshot(path="test-results/legend_filter.png")
    pg.click("#lg-clear")
    assert pg.eval_on_selector_all(".cal.filtering", "e => e.length") == 0
    # disabled when a mark never occurs (T004 has no holidays worked? at least one legend item may be disabled)
    assert pg.eval_on_selector_all("#cal-legend button.lg", "bs => bs.every(b => b.disabled === (b.querySelector('.muted').textContent.trim() === '0'))")

    # ---- flags card → review queue filtered to this employee
    pg.click("#emp-queue")
    pg.wait_for_selector("#emp-filter")
    assert "Dev Mehta" in pg.inner_text("#emp-filter")
    emps = pg.eval_on_selector_all("#fl tbody tr", "rs => [...new Set(rs.map(r => r.dataset.emp))]")
    assert emps == ["T004"]
    pg.go_back()
    pg.click(".flag-link >> nth=0")
    pg.wait_for_selector("#emp-filter")
    assert "code=" in pg.url and "emp=T004" in pg.url

    # ---- review queue (v1.12: only WFH above the limit and Group not set): three people without a group + T004's WFH
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Employee, Period
    from app.services.compute import compute_period
    with session_scope() as db:
        for e in ("T003", "T005", "T006"):
            db.get(Employee, e).group = ""
        compute_period(db, db.scalar(select(Period)))
    pg.goto(pg.base + "/flags?period=2026-10&show=open")
    pg.wait_for_selector(f"{bar(pg, '#fl')} .tt-count")
    open_before = len(pg.query_selector_all("#fl tbody tr"))
    assert open_before == 4
    assert pg.is_hidden("#bulk")
    pg.check("#fl tbody tr:nth-child(2) input.sel")                # two of the Group-not-set rows
    pg.click("#fl tbody tr:nth-child(3) td:nth-child(5)")         # clicking the row toggles too
    assert pg.inner_text("#bulk-n") == "2" and pg.is_visible("#bulk")
    pg.screenshot(path="test-results/bulk_bar.png")
    pg.click("#bulk-review")
    pg.wait_for_selector("#rev.open")
    assert "Mark 2 items reviewed" in pg.inner_text("#rv-title")
    pg.click("#rev [data-note='No action needed']")
    pg.click("#rv-save")
    pg.wait_for_selector(".toast:has-text('2 items marked reviewed')")
    assert len(pg.query_selector_all("#fl tbody tr")) == open_before - 2

    # select-all only takes rows the filters show
    add_filter(pg, "Severity", "#fl")
    pg.check(".tt-pop .tt-vals input[value='High']")
    pg.click(".tt-pop [data-done]")
    shown = pg.eval_on_selector_all("#fl tbody tr", "rs => rs.filter(r => r.style.display !== 'none').length")
    pg.check("#sel-all")
    assert pg.inner_text("#bulk-n") == str(shown)
    pg.click(f"{bar(pg, '#fl')} .tt-clear")                     # filter removed → hidden rows were never selected
    assert pg.inner_text("#bulk-n") == str(shown)
    assert pg.eval_on_selector("#sel-all", "e => e.indeterminate") is True
    pg.click("#bulk-clear")
    assert pg.is_hidden("#bulk")

    # bulk re-open from the Reviewed tab
    pg.goto(pg.base + "/flags?period=2026-10&show=reviewed")
    n_rev = len(pg.query_selector_all("#fl tbody tr"))
    pg.check("#sel-all")
    pg.on("dialog", lambda d: d.accept())
    pg.click("#bulk-reopen")
    pg.wait_for_selector(".toast:has-text('re-opened')")
    assert len(pg.query_selector_all("#fl tbody tr")) == 0 and n_rev >= 2
