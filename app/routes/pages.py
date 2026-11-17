"""Dashboard, imports, flags, reconciliation, audit log."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from datetime import datetime

import anyio

from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import delete, select

from app.models import AuditLog, Employee, FlagReview, ImportBatch, MonthResult, Period
from app.services import metrics as MX
from app.services import workflow as WF
from app.services.audit import audit
from app.services.settings import get_policy
from app.services.imports import ImportError_, import_file
from app.web import Ctx, hr, periods, redirect, render, viewer

router = APIRouter()


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


@router.get("/", response_class=HTMLResponse)
def dashboard(period: Optional[str] = None, c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    data = MX.period_metrics(c.db, p)
    wf = WF.status(c.db, p)
    return render(c, "dashboard.html", period=p, m=data, labels=MX.FLAG_LABELS, wf=wf, policy=get_policy(c.db))


@router.get("/api/employees")
def api_employees(c: Ctx = Depends(viewer)):
    """Quick-search index: every employee, linking to the latest month's drill-down when one exists."""
    latest = pick_period(c, None)
    res = {}
    if latest is not None:
        res = {r.emp_no: r for r in c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id))}
    out = []
    for e in c.db.scalars(select(Employee).order_by(Employee.name)):
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
    return render(c, "imports.html", batches=batches, pmap=pmap, results=None, wf=WF.status(c.db, cur) if cur else None)


@router.post("/imports", response_class=HTMLResponse)
async def do_import(request: Request, files: list[UploadFile] = File(...), period: str = Form(""), c: Ctx = Depends(hr)):
    hint = None
    if period:
        y, m = period.split("-")
        hint = (int(y), int(m))
    # order matters: master data before attendance so the first compute already has groups/calendars
    order = {"employee": 0, "holiday": 0, "date": 1, "doj": 1, "mis": 2, "summary": 3, "swipe": 4, "muster": 5}
    blobs = []
    for f in files:
        data = await f.read()
        rank = next((v for k, v in order.items() if k in (f.filename or "").lower()), 3)
        blobs.append((rank, f.filename or "upload.xlsx", data))
    return await anyio.to_thread.run_sync(_run_imports, c, blobs, hint)


def _run_imports(c: Ctx, blobs, hint):
    results, errors = [], []
    for _, name, data in sorted(blobs, key=lambda x: x[0]):
        if not data:
            continue
        try:
            res = import_file(c.db, data, name, c.user, hint, c.ip)
            c.db.commit()
            results.append({"file": name, "sheets": res})
        except ImportError_ as ex:
            c.db.rollback()
            errors.append({"file": name, "error": str(ex)})
        except Exception as ex:  # noqa: BLE001
            c.db.rollback()
            errors.append({"file": name, "error": f"Unexpected error: {ex}"})
    batches = c.db.scalars(select(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(100)).all()
    pmap = {p.id: p for p in periods(c.db)}
    cur = pick_period(c, None)
    return render(c, "imports.html", batches=batches, pmap=pmap, results=results, errors=errors,
                  wf=WF.status(c.db, cur) if cur else None)


@router.get("/flags", response_class=HTMLResponse)
def flags_page(period: Optional[str] = None, code: str = "", show: str = "open", c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    rows = WF.actionable_flags(c.db, p)
    rows.sort(key=lambda f: (MX.SEV_ORDER.get(f["severity"], 9), f["code"], f["name"]))
    groups = {}
    for f in rows:
        g = groups.setdefault(f["code"], {"code": f["code"], "label": MX.FLAG_LABELS.get(f["code"], f["code"]),
                                          "severity": f["severity"], "total": 0, "open": 0})
        g["total"] += 1
        g["open"] += 0 if f["reviewed"] else 1
    summary = sorted(groups.values(), key=lambda g: (MX.SEV_ORDER.get(g["severity"], 9), -g["total"]))
    act = [f for f in rows if f["actionable"]]
    return render(c, "flags.html", period=p, rows=rows, summary=summary, code=code, show=show,
                  total=len(act), reviewed=sum(1 for f in act if f["reviewed"]), labels=MX.FLAG_LABELS)


@router.post("/flags/review")
async def review_flag(request: Request, c: Ctx = Depends(hr)):
    """Mark one flag (or every open flag of a type with emp_no='*') as reviewed, or re-open it."""
    data = await request.json()
    return await anyio.to_thread.run_sync(_review, c, data)


def _review(c: Ctx, data: dict):
    p = pick_period(c, data.get("period"))
    code, emp, action, note = data.get("code", ""), data.get("emp_no", ""), data.get("action", "review"), data.get("note", "")[:500]
    targets = [(emp, code)]
    if emp == "*":
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
    return render(c, "reconciliation.html", period=p, r=MX.reconciliation(c.db, p))


@router.get("/audit", response_class=HTMLResponse)
def audit_page(q: str = "", c: Ctx = Depends(hr)):
    stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(500)
    if q:
        like = f"%{q}%"
        stmt = select(AuditLog).where((AuditLog.action.like(like)) | (AuditLog.username.like(like)) |
                                      (AuditLog.entity_id.like(like)) | (AuditLog.entity.like(like))
                                      ).order_by(AuditLog.ts.desc()).limit(500)
    return render(c, "audit.html", rows=c.db.scalars(stmt).all(), q=q)
