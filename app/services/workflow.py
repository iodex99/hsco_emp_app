"""Month-end checklist shown on the Overview (v1.16, owner): Import → Review exceptions → Corrections (confirmed made in
greytHR) → Download & share (downloaded from the checklist) → Lock month."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import defer, Session

from app.models import DayOverrideRow, FlagReview, ImportBatch, MonthOverrideRow, MonthResult, Period

# exception types someone has to review (owner, v1.12); every other flag is reviewed automatically — it stays on the
# employee's page and in the Excel, but never waits in the review queue
REVIEW_CODES = ("WFH_OVER_LIMIT", "GROUP_NOT_SET")
AUTO_NOTE = "Reviewed automatically"


def actionable_flags(db: Session, period: Period) -> list[dict]:
    reviewed = {(r.emp_no, r.code): r for r in db.scalars(select(FlagReview).where(FlagReview.period_id == period.id))}
    out = []
    for r in db.scalars(select(MonthResult).where(MonthResult.period_id == period.id).order_by(MonthResult.emp_no)
                        .options(defer(MonthResult.days), defer(MonthResult.data))):
        for f in r.flags:
            rv = reviewed.get((r.emp_no, f["code"]))
            needs = f["code"] in REVIEW_CODES
            out.append({**f, "emp_no": r.emp_no, "name": r.name, "group": r.group,
                        "reviewed": bool(rv) or not needs, "auto": not needs,
                        "review_note": rv.note if rv else ("" if needs else AUTO_NOTE),
                        "reviewed_by": rv.reviewed_by if rv else ("" if needs else "auto"), "actionable": needs})
    return out


def status(db: Session, period: Period) -> dict:
    kinds = set(db.scalars(select(ImportBatch.kind).where(ImportBatch.period_id == period.id)))
    # employee lists (joining / leaving dates) are not monthly: any upload counts
    anytime = set(db.scalars(select(ImportBatch.kind).where(ImportBatch.kind.in_(("doj", "leavers"))).distinct()))
    flags = [f for f in actionable_flags(db, period) if f["actionable"]]
    done_reviews = sum(1 for f in flags if f["reviewed"])
    n_ovr = (db.scalar(select(func.count(DayOverrideRow.id)).where(DayOverrideRow.period_id == period.id)) or 0) + \
            (db.scalar(select(func.count(MonthOverrideRow.id)).where(MonthOverrideRow.period_id == period.id)) or 0)
    files = [("muster", "Attendance muster", True), ("swipes", "Swipe details", True), ("doj", "Date of joining", False),
             ("leavers", "Date of leaving", False), ("permissions", "Permission report", False),
             ("summary", "Attendance summary", False)]
    file_status = [{"kind": k, "label": l, "required": req, "done": (k in kinds) or (k in anytime)}
                   for k, l, req in files]
    imported = all(f["done"] for f in file_status if f["required"])
    final = period.status == "FINALISED"
    cl = period.checklist or {}
    gt, dl = cl.get("greythr") or {}, cl.get("download") or {}

    def when(x):
        return x.get("at", "")[:10]
    steps = [
        {"key": "import", "title": "Import exports", "done": imported,
         "detail": "All required files imported" if imported else "Muster and swipe files needed", "url": "/imports"},
        {"key": "review", "title": "Review exceptions", "done": bool(flags) and done_reviews == len(flags) or not flags,
         "detail": f"{done_reviews} of {len(flags)} reviewed", "url": f"/flags?period={period.key}",
         "progress": (done_reviews / len(flags)) if flags else 1},
        {"key": "correct", "title": "Corrections", "done": bool(gt),
         "detail": f"{n_ovr} correction{'s' if n_ovr != 1 else ''} recorded"
                   + (f" · made in greytHR (confirmed by {gt.get('by')}, {when(gt)})" if gt else " — confirm once made in greytHR"),
         "url": f"/periods/{period.key}", "by": gt.get("by", ""), "at": when(gt)},
        {"key": "share", "title": "Download & share", "done": bool(dl),
         "detail": (f"Downloaded here ({dl.get('what') or 'file'}) by {dl.get('by')}, {when(dl)}" if dl
                    else "Download the Excel or PDF from here"), "url": "/exports"},
        {"key": "lock", "title": "Lock month", "done": final,
         "detail": "Locked" if final else "Not locked yet — after the MIS", "url": f"/periods/{period.key}"},
    ]
    current = next((i for i, s in enumerate(steps) if not s["done"]), len(steps) - 1)
    return {"steps": steps, "current": current, "files": file_status, "flags_total": len(flags),
            "flags_reviewed": done_reviews, "overrides": n_ovr, "final": final,
            "all_done": all(s["done"] for s in steps)}
