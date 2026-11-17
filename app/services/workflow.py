"""Month-end workflow status (Import → Review → Correct → Finalise → Share) shown on the home page."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DayOverrideRow, FlagReview, ImportBatch, MonthOverrideRow, MonthResult, Period

ACTIONABLE = ("critical", "high", "medium")


def actionable_flags(db: Session, period: Period) -> list[dict]:
    reviewed = {(r.emp_no, r.code): r for r in db.scalars(select(FlagReview).where(FlagReview.period_id == period.id))}
    out = []
    for r in db.scalars(select(MonthResult).where(MonthResult.period_id == period.id).order_by(MonthResult.emp_no)):
        for f in r.flags:
            rv = reviewed.get((r.emp_no, f["code"]))
            out.append({**f, "emp_no": r.emp_no, "name": r.name, "group": r.group,
                        "reviewed": bool(rv), "review_note": rv.note if rv else "",
                        "reviewed_by": rv.reviewed_by if rv else "", "actionable": f["severity"] in ACTIONABLE})
    return out


def status(db: Session, period: Period) -> dict:
    kinds = set(db.scalars(select(ImportBatch.kind).where(ImportBatch.period_id == period.id)))
    any_doj = db.scalar(select(func.count(ImportBatch.id)).where(ImportBatch.kind == "doj")) > 0
    flags = [f for f in actionable_flags(db, period) if f["actionable"]]
    done_reviews = sum(1 for f in flags if f["reviewed"])
    n_ovr = (db.scalar(select(func.count(DayOverrideRow.id)).where(DayOverrideRow.period_id == period.id)) or 0) + \
            (db.scalar(select(func.count(MonthOverrideRow.id)).where(MonthOverrideRow.period_id == period.id)) or 0)
    files = [("muster", "Attendance muster", True), ("swipes", "Swipe details", True), ("doj", "Date of joining", False),
             ("summary", "Attendance summary", False)]
    file_status = [{"kind": k, "label": l, "required": req, "done": (k in kinds) or (k == "doj" and any_doj)}
                   for k, l, req in files]
    imported = all(f["done"] for f in file_status if f["required"])
    final = period.status == "FINALISED"
    steps = [
        {"key": "import", "title": "Import exports", "done": imported,
         "detail": "All required files imported" if imported else "Muster and swipe files needed", "url": "/imports"},
        {"key": "review", "title": "Review exceptions", "done": bool(flags) and done_reviews == len(flags) or not flags,
         "detail": f"{done_reviews} of {len(flags)} reviewed", "url": f"/flags?period={period.key}",
         "progress": (done_reviews / len(flags)) if flags else 1},
        {"key": "correct", "title": "Corrections", "done": True, "optional": True,
         "detail": f"{n_ovr} correction{'s' if n_ovr != 1 else ''} recorded", "url": f"/periods/{period.key}"},
        {"key": "finalise", "title": "Finalise for payroll", "done": final,
         "detail": "Locked" if final else "Not finalised yet", "url": f"/periods/{period.key}"},
        {"key": "share", "title": "Download & share", "done": final, "detail": "Excel + board pack",
         "url": "/exports"},
    ]
    current = next((i for i, s in enumerate(steps) if not s["done"] and not s.get("optional")), len(steps) - 1)
    return {"steps": steps, "current": current, "files": file_status, "flags_total": len(flags),
            "flags_reviewed": done_reviews, "overrides": n_ovr, "final": final}
