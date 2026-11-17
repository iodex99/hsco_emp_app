"""Dashboard, imports, flags, reconciliation, audit log."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.models import AuditLog, ImportBatch, Period
from app.services import metrics as MX
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
    return render(c, "dashboard.html", period=p, m=data, labels=MX.FLAG_LABELS)


@router.get("/imports", response_class=HTMLResponse)
def imports_page(c: Ctx = Depends(hr)):
    batches = c.db.scalars(select(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(100)).all()
    pmap = {p.id: p for p in periods(c.db)}
    return render(c, "imports.html", batches=batches, pmap=pmap, results=None)


@router.post("/imports", response_class=HTMLResponse)
async def do_import(request: Request, files: list[UploadFile] = File(...), period: str = Form(""), c: Ctx = Depends(hr)):
    hint = None
    if period:
        y, m = period.split("-")
        hint = (int(y), int(m))
    results, errors = [], []
    # order matters: master data before attendance so the first compute already has groups/calendars
    order = {"employee": 0, "holiday": 0, "date": 1, "doj": 1, "mis": 2, "summary": 3, "swipe": 4, "muster": 5}
    blobs = []
    for f in files:
        data = await f.read()
        rank = next((v for k, v in order.items() if k in (f.filename or "").lower()), 3)
        blobs.append((rank, f.filename or "upload.xlsx", data))
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
    return render(c, "imports.html", batches=batches, pmap=pmap, results=results, errors=errors)


@router.get("/flags", response_class=HTMLResponse)
def flags_page(period: Optional[str] = None, code: str = "", severity: str = "", c: Ctx = Depends(viewer)):
    p = pick_period(c, period)
    if p is None:
        return render(c, "empty.html")
    data = MX.period_metrics(c.db, p)
    rows = [f for f in data["flags"] if (not code or f["code"] == code) and (not severity or f["severity"] == severity)]
    return render(c, "flags.html", period=p, rows=rows, summary=data["flag_summary"], code=code, severity=severity,
                  labels=MX.FLAG_LABELS)


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
