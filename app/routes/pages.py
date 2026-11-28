"""Dashboard, imports, flags, reconciliation, audit log."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from datetime import datetime

import anyio

from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import delete, select

from app.models import AuditLog, Employee, FlagReview, ImportBatch, MonthResult, Period
from app.services import insights as INS
from app.services import metrics as MX
from app.services import workflow as WF
from app.services.audit import audit
from app.services.settings import get_policy
from app.importers import parsers as P
from app.importers.readers import read_workbook
from app.services import imports as IMP
from app.services.imports import ImportError_, import_file
from app.web import Ctx, hr, periods, redirect, render, viewer

router = APIRouter()
re_period = __import__('re').compile(r"^\d{4}-\d{2}$")


def pick_period(c: Ctx, key: Optional[str]) -> Optional[Period]:
    ps = periods(c.db)
    if key:
        for p in ps:
            if p.key == key:
                return p
    for p in ps:  # latest period with results
        if p.computed_at:
            return p
    return ps[0] if ps else None


def pending_month(c: Ctx, p: Period):
    """A month with imports but no calculation yet (no muster, e.g. the month in progress): say so plainly instead of
    showing zeros, list what's in and what's missing, and point to its sign-ins."""
    if p.computed_at is not None:
        return None
    from sqlalchemy import case, func
    from app.models import SwipeRow
    n_if = lambda cond: func.sum(case((cond, 1), else_=0))  # noqa: E731  (an integer count, not a boolean)
    agg = c.db.execute(select(func.count(), n_if(func.lower(SwipeRow.swipe_type) != "astra"), n_if(SwipeRow.lat.is_not(None)),
                              func.min(SwipeRow.ts), func.max(SwipeRow.ts), func.count(SwipeRow.emp_no.distinct()))
                       .where(SwipeRow.period_id == p.id)).one()
    total = agg[0] or 0
    signins = {"total": total, "mobile": int(agg[1] or 0), "biometric": total - int(agg[1] or 0), "gps": int(agg[2] or 0),
               "first": agg[3], "last": agg[4], "people": agg[5]}
    calculated = next((x for x in periods(c.db) if x.computed_at), None)
    return render(c, "month_pending.html", period=p, wf=WF.status(c.db, p), signins=signins, calculated=calculated)


@router.get("/", response_class=HTMLResponse)
def dashboard(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    if p.computed_at is None:
        return pending_month(c, p)
    data = MX.period_metrics(c.db, p)
    wf = WF.status(c.db, p)
    return render(c, "dashboard.html", period=p, m=data, labels=MX.FLAG_LABELS, wf=wf, policy=get_policy(c.db),
                  review_codes=WF.REVIEW_CODES)


@router.get("/insights", response_class=HTMLResponse)
def insights_page(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    if p.computed_at is None:
        return pending_month(c, p)
    data = INS.insights(c.db, p)
    chart = {"key": p.key, "label": p.label, "hours_hist": data["hours_hist"], "groups": data["groups"],
             "trend": data["trend"], "drill": data["drill"],
             "who": {x["emp_no"]: [x["name"], x["group"]] for x in data["rows"]},
             # per person, for "who is behind this group" (Groups chart): attendance, on time, hours vs required,
             # unapplied leave %
             "people": [[x["emp_no"], x["attendance_pct"], x["punctuality_pct"], x["utilisation_pct"], x["absence_pct"]]
                        for x in data["rows"]],
             "arrival": data["arrival"]}
    from app.services.compute import previous_period
    prev = previous_period(c.db, p)
    prev_kpis = INS.insights(c.db, prev)["kpis"] if prev and prev.computed_at else None      # last month (v1.15)
    return render(c, "insights.html", period=p, I=data, chart=chart, policy=get_policy(c.db), prev_kpis=prev_kpis)


@router.get("/locations", response_class=HTMLResponse)
def locations_page(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    """Where headcount signed in from (mobile sign-ins with place names) — available as soon as a swipe export with
    coordinates is imported, even before the month's muster."""
    from app.services import locations as LOC
    p = LOC.pick(c.db, period)
    months = LOC.months_with_signins(c.db)
    if p is None:
        return render(c, "locations.html", R=None, months=months)
    pm = [x for x in months if (x.year, x.month) < (p.year, p.month)]
    prev_kpis = LOC.report(c.db, pm[0])["kpis"] if pm and (pm[0].year * 12 + pm[0].month) == (p.year * 12 + p.month - 1) else None
    return render(c, "locations.html", R=LOC.report(c.db, p), months=months, period=p, prev_kpis=prev_kpis)


@router.get("/api/employees")
def api_employees(c: Ctx = Depends(viewer)):
    """Quick-search index: every employee, linking to the latest month's drill-down when one exists."""
    latest = pick_period(c, None)
    res = {}
    if latest is not None:
        res = {r.emp_no: r for r in c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id))}
    out = []
    from app.services.scope import employees as scoped
    for e in scoped(c.db, order=Employee.name):                    # ignored groups (OTHERS) are not searchable
        parts = (e.name or e.emp_no).split()
        r = res.get(e.emp_no)
        out.append({"emp_no": e.emp_no, "name": e.name or e.emp_no, "group": e.group,
                    "initials": (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper(),
                    "flags": r.flag_count if r else 0,
                    "href": f"/periods/{latest.key}/employee/{e.emp_no}" if r else f"/employees/{e.emp_no}"})
    return JSONResponse(out)


@router.get("/imports", response_class=HTMLResponse)
def imports_page(c: Ctx = Depends(hr)):
    batches = c.db.scalars(select(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(100)).all()
    pmap = {p.id: p for p in periods(c.db)}
    cur = pick_period(c, None)
    return render(c, "imports.html", batches=batches, pmap=pmap, results=None, wf=WF.status(c.db, cur) if cur else None,
                  layouts=IMP.get_layouts(c.db), kind_labels=P.KIND_LABELS, effects=_effects(c, batches, pmap))


def _effects(c: Ctx, batches, pmap) -> dict:
    """For the Delete button of each upload: what deleting it removes, in words (None = not allowed)."""
    out = {}
    for b in batches:
        p = pmap.get(b.period_id)
        if p is not None and p.status != "DRAFT":
            out[b.id] = None
            continue
        e = IMP.import_effects(c.db, b)
        bits = []
        if e["rows"]:
            bits.append(f"{e['rows']:,} {e['what']}" + (f" of {p.label}" if p else ""))
        if e["holidays"]:
            bits.append(f"{e['holidays']} holiday{'s' if e['holidays'] != 1 else ''} it put in the lists")
        if e["changed"]:
            bits.append(f"the details it changed for {e['changed']} employee{'s' if e['changed'] != 1 else ''} "
                        "(put back as they were, unless edited since)")
        if e["created"]:
            bits.append(f"{e['created']} employee{'s' if e['created'] != 1 else ''} it added (if no other file has "
                        "data for them)")
        out[b.id] = ("Removes " + "; ".join(bits) + ".") if bits else \
            "Nothing is linked to this upload any more (a later file replaced its data, or it was imported before " \
            "v1.11) — only the history entry goes."
    return out


@router.post("/imports/{bid}/delete")
def delete_upload(bid: int, reason: str = Form(""), c: Ctx = Depends(hr)):
    """Delete an upload and the data it imported (services/imports.delete_import); reason required, audit-logged."""
    b = c.db.get(ImportBatch, bid)
    if b is None:
        return redirect("/imports", "That upload was already deleted.")
    if not reason.strip():
        return redirect("/imports", "Say why the upload is deleted (a reason is required for every manual change).")
    try:
        msg = IMP.delete_import(c.db, b, c.user, reason, c.ip)
    except ValueError as ex:
        return redirect("/imports", str(ex))
    return redirect("/imports", msg)


@router.post("/imports", response_class=HTMLResponse)
async def do_import(request: Request, files: list[UploadFile] = File(...), period: str = Form(""), c: Ctx = Depends(hr)):
    hint = None
    if period:
        y, m = period.split("-")
        hint = (int(y), int(m))
    # order matters: master data before attendance so the first compute already has groups/calendars
    # (category report first: holiday lists find their categories by name; a list for an unknown category still
    # works — the category is created and matched loosely when the report comes later)
    order = {"categ": -1, "employee": 0, "holiday": 0, "date": 1, "doj": 1, "mis": 2, "summary": 3, "swipe": 4,
             "muster": 5}
    blobs = []
    for f in files:
        data = await f.read()
        rank = next((v for k, v in order.items() if k in (f.filename or "").lower()), 3)
        blobs.append((rank, f.filename or "upload.xlsx", data))
    return await anyio.to_thread.run_sync(_run_imports, c, blobs, hint)


def _run_imports(c: Ctx, blobs, hint, manual=None, remember=False, token=""):
    results, errors, touched = [], [], set()
    for _, name, data in sorted(blobs, key=lambda x: x[0]):
        if not data:
            continue
        try:
            t: set = set()
            res = import_file(c.db, data, name, c.user, hint, c.ip, manual=manual, recompute=False, touched_out=t)
            if manual and remember:
                rows = read_workbook(data, name)[manual["sheet"]]
                IMP.save_layout(c.db, P.manual_sheet(manual["sheet"], rows, manual["kind"], manual["header_row"],
                                                     manual["map"]), name)
                audit(c.db, c.user, "import_layout_saved", manual["kind"], name, {"sheet": manual["sheet"]}, c.ip)
            c.db.commit()
            touched |= t
            if token:
                IMP.finish_pending(token, manual["sheet"] if manual else "")
            results.append({"file": name, "sheets": res})
        except ImportError_ as ex:
            c.db.rollback()
            errors.append({"file": name, "error": str(ex)})
        except Exception as ex:  # noqa: BLE001
            c.db.rollback()
            errors.append({"file": name, "error": f"Unexpected error: {ex}"})
    if touched:            # all files of the upload are in: recalculate once (oldest affected month onwards)
        try:
            IMP.recompute_from(c.db, touched, commit_each=True)
            c.db.commit()
        except Exception as ex:  # noqa: BLE001
            c.db.rollback()
            errors.append({"file": "Recalculation", "error": f"The files were imported but recalculating failed: {ex}"})
    batches = c.db.scalars(select(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(100)).all()
    pmap = {p.id: p for p in periods(c.db)}
    cur = pick_period(c, None)
    return render(c, "imports.html", batches=batches, pmap=pmap, results=results, errors=errors,
                  wf=WF.status(c.db, cur) if cur else None, layouts=IMP.get_layouts(c.db), kind_labels=P.KIND_LABELS,
                  effects=_effects(c, batches, pmap))


def _map_context(c: Ctx, token: str, sheet: str, kind: str, hr_row: Optional[int], chosen: Optional[dict] = None,
                 error: str = ""):
    pend = IMP.load_pending(token)
    if not pend:
        return None
    data, filename = pend
    sheets = read_workbook(data, filename)
    names = [n for n, rows in sheets.items() if rows]
    if sheet not in sheets:
        found = P.detect(sheets, IMP.get_layouts(c.db))
        sheet = found[0].sheet if found else (names[0] if names else "")
    rows = sheets.get(sheet, [])
    auto = P.detect_sheet(sheet, rows) if rows else None
    if hr_row is None:
        hr_row = auto.header_row if auto else P.guess_header_row(rows)
    hr_row = max(0, min(hr_row, max(0, len(rows) - 1)))
    if kind not in P.SCHEMAS:
        kind = auto.kind if auto else "muster"
    mapping = chosen if chosen is not None else (
        dict(auto.map) if auto and auto.kind == kind and auto.header_row == hr_row else P.suggest(rows, hr_row, kind))
    header = rows[hr_row] if rows else []
    width = max([len(r or []) for r in rows[hr_row: hr_row + 8]] + [len(header)])
    cols = [{"i": i, "name": (str(header[i]).strip() if i < len(header) and header[i] not in (None, "") else f"Column {i + 1}")}
            for i in range(width)]
    req, opt, _ = P.SCHEMAS[kind]
    req_fields = [f for r in req for f in (r if isinstance(r, tuple) else (r,))]
    fields = [{"key": f, "label": P.FIELD_LABELS.get(f, f), "required": f in req_fields,
               "alt": next((" / ".join(P.FIELD_LABELS.get(a, a) for a in r) for r in req
                            if isinstance(r, tuple) and f in r), "")} for f in P.schema_fields(kind)]
    preview = [[("" if v is None else str(v))[:40] for v in (r or [])] + [""] * (width - len(r or [])) for r in rows[hr_row + 1: hr_row + 7]]
    return dict(token=token, filename=filename, sheets=names, sheet=sheet, kind=kind, hr_row=hr_row, cols=cols,
                fields=fields, mapping=mapping, preview=preview, kind_labels=P.KIND_LABELS, error=error,
                missing=P.missing_required(kind, mapping), auto=auto, title_rows=[[("" if v is None else str(v))[:60] for v in (r or []) if v not in (None, "")]
                                                                                for r in rows[:hr_row]][:6])


@router.get("/imports/map/{token}", response_class=HTMLResponse)
def map_page(token: str, sheet: str = "", kind: str = "", row: Optional[int] = None, c: Ctx = Depends(hr)):
    ctx = _map_context(c, token, sheet, kind, (row - 1) if row else None)
    if ctx is None:
        return redirect("/imports", "That upload has expired — please upload the file again.")
    return render(c, "import_map.html", **ctx)


@router.post("/imports/map/{token}", response_class=HTMLResponse)
async def map_submit(token: str, request: Request, c: Ctx = Depends(hr)):
    form = await request.form()
    sheet, kind = str(form.get("sheet", "")), str(form.get("kind", ""))
    try:
        hr_row = max(0, int(form.get("row", "1")) - 1)
    except ValueError:
        hr_row = 0
    mapping = {}
    for k, v in form.items():
        if k.startswith("f_") and str(v).strip().isdigit():
            mapping[k[2:]] = int(v)
    period = str(form.get("period", ""))
    remember = form.get("remember") == "1"

    def run():
        if kind not in P.SCHEMAS:
            return redirect(f"/imports/map/{token}", "Choose what kind of report this is.")
        clean = {f: i for f, i in mapping.items() if f in P.schema_fields(kind)}
        if len(set(clean.values())) != len(clean):
            ctx = _map_context(c, token, sheet, kind, hr_row, clean, "The same column is chosen for two different fields.")
            return render(c, "import_map.html", **ctx) if ctx else redirect("/imports", "Upload expired.")
        miss = P.missing_required(kind, clean)
        if miss:
            ctx = _map_context(c, token, sheet, kind, hr_row, clean, "Still needed: " + ", ".join(miss))
            return render(c, "import_map.html", **ctx) if ctx else redirect("/imports", "Upload expired.")
        pend = IMP.load_pending(token)
        if not pend:
            return redirect("/imports", "That upload has expired — please upload the file again.")
        data, filename = pend
        hint = None
        if re_period.match(period):
            y, m = period.split("-")
            hint = (int(y), int(m))
        return _run_imports(c, [(0, filename, data)], hint, manual={"sheet": sheet, "kind": kind, "header_row": hr_row,
                                                                    "map": clean}, remember=remember, token=token)
    return await anyio.to_thread.run_sync(run)


@router.post("/imports/layouts/{sig}/delete")
def delete_layout(sig: str, c: Ctx = Depends(hr)):
    if IMP.delete_layout(c.db, sig):
        audit(c.db, c.user, "import_layout_deleted", "layout", sig, {}, c.ip)
    return redirect("/imports", "Saved layout removed.")


@router.get("/flags", response_class=HTMLResponse)
def flags_page(period: Optional[str] = None, code: str = "", show: str = "open", emp: str = "",
               c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    all_rows = [f for f in WF.actionable_flags(c.db, p) if f["actionable"]]     # the two types that need a review
    emp = emp.strip().upper()
    rows = [f for f in all_rows if f["emp_no"] == emp] if emp else all_rows
    # WFH over the limit: what was deducted / approved (for the "Approve extra WFH" action)
    wfh_info = {}
    for r in p and c.db.scalars(select(MonthResult).where(MonthResult.period_id == p.id)) or []:
        d = r.data or {}
        if d.get("wfh_excess_days"):
            wfh_info[r.emp_no] = {"excess": d.get("wfh_excess_days") or 0, "approved": d.get("wfh_approved_days") or 0,
                                  "deduction": d.get("wfh_deduction_days") or 0}
    emp_name = next((f["name"] for f in rows), "") if emp else ""
    if emp and not emp_name:
        e = c.db.get(Employee, emp)
        emp_name = e.name if e else emp
    rows.sort(key=lambda f: (MX.SEV_ORDER.get(f["severity"], 9), f["code"], f["name"]))
    groups = {}
    for f in rows:
        g = groups.setdefault(f["code"], {"code": f["code"], "label": MX.FLAG_LABELS.get(f["code"], f["code"]),
                                          "severity": f["severity"], "total": 0, "open": 0})
        g["total"] += 1
        g["open"] += 0 if f["reviewed"] else 1
    summary = sorted(groups.values(), key=lambda g: (MX.SEV_ORDER.get(g["severity"], 9), -g["total"]))
    act = [f for f in all_rows if f["actionable"]]
    return render(c, "flags.html", period=p, rows=rows, summary=summary, code=code, show=show, emp=emp,
                  emp_name=emp_name, total=len(act), reviewed=sum(1 for f in act if f["reviewed"]),
                  labels=MX.FLAG_LABELS, wfh_info=wfh_info,
                  review_types=[MX.FLAG_LABELS.get(x, x) for x in WF.REVIEW_CODES])


@router.get("/exceptions", response_class=HTMLResponse)
def exceptions_page(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    """Missing / incorrect punches, regularisations, WFH and other day exceptions (services/exceptions.py)."""
    from app.services.exceptions import month_exceptions
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    if p.computed_at is None:
        return pending_month(c, p)
    from app.services.compute import previous_period
    prev = previous_period(c.db, p)
    prev_kpis = month_exceptions(c.db, prev)["kpis"] if prev and prev.computed_at else None      # last month (v1.15)
    return render(c, "exceptions.html", period=p, X=month_exceptions(c.db, p), prev_kpis=prev_kpis)


@router.post("/flags/wfh-approve")
def approve_extra_wfh(period: str = Form(...), emp_no: str = Form(...), days: str = Form(...), reason: str = Form(""),
                      c: Ctx = Depends(hr)):
    """Manager / partner allowed more WFH than the limit (not in any export): record the approved extra days as a
    month correction (audited, with the reason) and mark the WFH flag reviewed. Only unapproved extra days are
    deducted; the Excel follows (built from the app at download)."""
    from datetime import datetime
    from app.models import MonthOverrideRow
    from app.services.compute import compute_period
    p = pick_period(c, period)
    back = f"/flags?period={p.key}&code=WFH_OVER_LIMIT" if p else "/flags"
    if p is None or p.status != "DRAFT":
        return redirect(back, "This month is locked — unlock it to change a deduction.")
    if not reason.strip():
        return redirect(back, "Say who approved it (a reason is required for every manual change).")
    try:
        n = float(days)
    except ValueError:
        return redirect(back, "Days must be a number.")
    if n < 0 or n > 31:
        return redirect(back, "Days must be between 0 and 31.")
    emp_no = emp_no.strip().upper()
    o = c.db.scalar(select(MonthOverrideRow).where(MonthOverrideRow.period_id == p.id, MonthOverrideRow.emp_no == emp_no,
                                                   MonthOverrideRow.field == "wfh_approved_days"))
    if n == 0:
        if o is not None:
            c.db.delete(o)
    else:
        if o is None:
            o = MonthOverrideRow(period_id=p.id, emp_no=emp_no, field="wfh_approved_days", value="0", reason="")
            c.db.add(o)
        o.value, o.reason, o.created_by, o.created_at = f"{n:g}", reason.strip(), c.user.username, datetime.now()
    audit(c.db, c.user, "override.month", "employee", emp_no, {"period": p.key, "field": "wfh_approved_days",
                                                               "value": f"{n:g}", "reason": reason.strip()}, c.ip)
    rv = c.db.scalar(select(FlagReview).where(FlagReview.period_id == p.id, FlagReview.emp_no == emp_no,
                                              FlagReview.code == "WFH_OVER_LIMIT"))
    note = f"Extra WFH approved: {n:g} day(s) — {reason.strip()}" if n else ""
    if n and rv is None:
        c.db.add(FlagReview(period_id=p.id, emp_no=emp_no, code="WFH_OVER_LIMIT", reviewed_by=c.user.username,
                            note=note[:500]))
    elif n and rv is not None:
        rv.note, rv.reviewed_by = note[:500], c.user.username
    c.db.flush()
    compute_period(c.db, p)
    name = c.db.scalar(select(Employee.name).where(Employee.emp_no == emp_no)) or emp_no
    return redirect(back, f"{name}: {n:g} extra WFH day(s) approved — deduction recalculated." if n else
                    f"{name}: approval removed — the WFH deduction applies again.")


@router.post("/flags/waive-shortfall")
def waive_shortfall(period: str = Form(...), emp_no: str = Form(...), reason: str = Form(""), back: str = Form(""),
                    c: Ctx = Depends(hr)):
    """A partner waived the hours-shortfall deduction for this month: month correction "shortfall_waived" (audited,
    with the reason), month recalculated (the flag becomes an info note). Undo on the employee's page (Corrections)."""
    from datetime import datetime
    from app.models import FlagReview, MonthOverrideRow
    from app.services.compute import compute_period
    p = pick_period(c, period)
    if not (back.startswith("/periods/") and "//" not in back):           # (v1.12: Waive is on the employee's page)
        back = f"/periods/{p.key}/employee/{emp_no.strip().upper()}" if p else "/"
    if p is None or p.status != "DRAFT":
        return redirect(back, "This month is locked — unlock it to change a deduction.")
    if not reason.strip():
        return redirect(back, "Say who waived it (a reason is required for every manual change).")
    emp_no = emp_no.strip().upper()
    o = c.db.scalar(select(MonthOverrideRow).where(MonthOverrideRow.period_id == p.id, MonthOverrideRow.emp_no == emp_no,
                                                   MonthOverrideRow.field == "shortfall_waived"))
    if o is None:
        o = MonthOverrideRow(period_id=p.id, emp_no=emp_no, field="shortfall_waived", value="true", reason="")
        c.db.add(o)
    o.value, o.reason, o.created_by, o.created_at = "true", reason.strip(), c.user.username, datetime.now()
    audit(c.db, c.user, "override.month", "employee", emp_no, {"period": p.key, "field": "shortfall_waived",
                                                               "value": "true", "reason": reason.strip()}, c.ip)
    c.db.flush()
    compute_period(c.db, p)
    name = c.db.scalar(select(Employee.name).where(Employee.emp_no == emp_no)) or emp_no
    return redirect(back, f"{name}: hours deduction waived — recalculated.")


@router.post("/flags/review")
async def review_flag(request: Request, c: Ctx = Depends(hr)):
    """Mark flags reviewed / re-open them: one (emp_no + code), many (items=[{emp_no, code}, …]) or every open flag
    of a type (emp_no='*')."""
    data = await request.json()
    return await anyio.to_thread.run_sync(_review, c, data)


def _review(c: Ctx, data: dict):
    p = pick_period(c, data.get("period"))
    code, emp, action, note = data.get("code", ""), data.get("emp_no", ""), data.get("action", "review"), data.get("note", "")[:500]
    targets = [(emp, code)]
    items = data.get("items")
    if isinstance(items, list):
        valid = {(f["emp_no"], f["code"]) for f in WF.actionable_flags(c.db, p) if f["actionable"]}
        targets = [(str(i.get("emp_no", "")), str(i.get("code", ""))) for i in items[:2000] if isinstance(i, dict)]
        targets = [t for t in dict.fromkeys(targets) if t in valid]
        emp, code = ("*bulk*", ",".join(sorted({t[1] for t in targets}))[:200])
    elif emp == "*":
        targets = [(f["emp_no"], f["code"]) for f in WF.actionable_flags(c.db, p) if f["code"] == code and not f["reviewed"]]
    for e, cd in targets:
        c.db.execute(delete(FlagReview).where(FlagReview.period_id == p.id, FlagReview.emp_no == e, FlagReview.code == cd))
        if action == "review":
            c.db.add(FlagReview(period_id=p.id, emp_no=e, code=cd, note=note, reviewed_by=c.user.username,
                                reviewed_at=datetime.now()))
    audit(c.db, c.user, "flag." + action, "employee", emp, {"period": p.key, "code": code, "note": note, "count": len(targets)}, c.ip)
    c.db.flush()
    act = [f for f in WF.actionable_flags(c.db, p) if f["actionable"]]
    return JSONResponse({"ok": True, "count": len(targets), "reviewed": sum(1 for f in act if f["reviewed"]), "total": len(act),
                         "by": c.user.username})


@router.get("/reconciliation", response_class=HTMLResponse)
def recon_page(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    if p.computed_at is None:
        return pending_month(c, p)
    return render(c, "reconciliation.html", period=p, r=MX.reconciliation(c.db, p))


AUDIT_CATEGORIES = {
    "login": "Sign-in & 2FA", "logout": "Sign-in & 2FA", "mfa": "Sign-in & 2FA", "password": "Sign-in & 2FA",
    "import": "Imports", "override": "Corrections", "leave": "Corrections", "employee": "Employees",
    "period": "Months (lock / unlock / recompute)", "flag": "Review queue", "settings": "Settings", "calendar": "Settings",
    "holiday": "Settings", "geofence": "Settings", "user": "Users", "export": "Downloads", "update": "Updates",
}
AUDIT_PAGE = 200


@router.get("/audit", response_class=HTMLResponse)
def audit_page(q: str = "", user: str = "", cat: str = "", date_from: str = "", date_to: str = "", page: int = 1,
               c: Ctx = Depends(hr)):
    """Server-side filters (the log can be large) + paging; the table toolkit handles sorting within a page."""
    from datetime import datetime as _dt, timedelta
    from sqlalchemy import func, or_

    stmt = select(AuditLog)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.action.like(like), AuditLog.username.like(like), AuditLog.entity_id.like(like),
                              AuditLog.entity.like(like), AuditLog.ip.like(like)))
    if user:
        stmt = stmt.where(AuditLog.username == user)
    if cat:
        prefixes = [k for k, v in AUDIT_CATEGORIES.items() if v == cat]
        stmt = stmt.where(or_(*[AuditLog.action.like(f"{k}%") for k in prefixes])) if prefixes else stmt
    try:
        if date_from:
            stmt = stmt.where(AuditLog.ts >= _dt.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(AuditLog.ts < _dt.fromisoformat(date_to) + timedelta(days=1))
    except ValueError:
        pass
    total = c.db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = max(1, page)
    rows = c.db.scalars(stmt.order_by(AuditLog.ts.desc()).offset((page - 1) * AUDIT_PAGE).limit(AUDIT_PAGE)).all()
    users = [u for u in c.db.scalars(select(AuditLog.username).distinct().order_by(AuditLog.username)) if u]
    cats = sorted(set(AUDIT_CATEGORIES.values()))
    pages = max(1, (total + AUDIT_PAGE - 1) // AUDIT_PAGE)
    return render(c, "audit.html", rows=rows, q=q, user=user, cat=cat, date_from=date_from, date_to=date_to,
                  page=page, pages=pages, total=total, users=users, cats=cats, cat_of=AUDIT_CATEGORIES)
