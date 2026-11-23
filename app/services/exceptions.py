"""Punch & attendance exceptions for a month (docs/METRICS.md "Exceptions"): every employee-day with an incorrect
punch (times changed, rejected sign-in), a regularisation, WFH (over the limit / on a day off), late / early / short day, absence, off-day
work — one row per employee-day with all its issues, plus a per-employee summary.

Built only from the stored day detail (MonthResult.days) and the policy, so it always matches the payroll figures
(including corrections). No web code.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Period
from app.services.metrics import results_for
from app.services.settings import get_policy

# key, label, badge class, group ("punch" = incorrect punch, "reg", "wfh", "time", "att"). v1.13 (owner): "missing
# punch" and "present without any punch" removed everywhere.
TYPES = [
    ("edited", "In/out changed (no matching punch)", "b-warn", "punch"),
    ("rejected", "Rejected sign-in", "b-warn", "punch"),
    ("mobile_office", "Phone sign-in tagged 'Office'", "b-grey", "punch"),
    ("permission", "Regularisation request", "b-violet", "reg"),
    ("wfh", "WFH", "b-violet", "wfh"),
    ("wfh_over", "WFH over the limit", "b-bad", "wfh"),
    ("wfh_closed", "WFH — office closed (Saturday)", "b-grey", "wfh"),
    ("wfh_offday", "WFH on a day off", "b-grey", "wfh"),
    ("wfh_leave", "WFH on a leave / absent day", "b-warn", "wfh"),
    ("very_late", "Late > 1 h", "b-bad", "time"),      # lateness under an hour: Insights / Overview, not here
    ("early", "Early exit", "b-warn", "time"),
    ("short", "Short day", "b-warn", "time"),
    ("absent", "Unapplied leave (absence)", "b-bad", "att"),
    ("offday_work", "Worked on a day off", "b-grey", "att"),
]
LABELS = {k: lbl for k, lbl, _c, _g in TYPES}
CLASSES = {k: c for k, _l, c, _g in TYPES}


def _punches(dd: dict) -> str:
    out = []
    for s in dd.get("swipes") or []:
        where = s.get("loc") or ("biometric" if (s.get("type") or "").lower() == "astra" else s.get("type") or "")
        out.append(f"{(s.get('t') or '')[:5]} {where}".strip())
    return " · ".join(out)


def day_issues(dd: dict, wfh_types: set, auto_attendance: bool = False) -> list[str]:
    """Issue keys for one stored day (wfh_over is added by the caller, which knows the month's WFH order)."""
    keys = []
    sw = dd.get("swipes") or []
    notes = " ".join(dd.get("notes") or [])
    work = dd.get("type") == "WORKDAY"
    if not auto_attendance:
        perm = any((s.get("type") or "").lower() == "permission" for s in sw)
        if perm:
            keys.append("permission")
        elif dd.get("regularised"):
            keys.append("edited")
    if "rejected sign-in" in notes:
        keys.append("rejected")
    if dd.get("mobile_office"):
        keys.append("mobile_office")
    if dd.get("wfh") and dd.get("wfh_closed"):
        keys.append("wfh_closed")                 # forced: not counted towards the limit
    elif dd.get("wfh"):
        keys.append("wfh")
    elif any((s.get("loc") or "").strip().lower() in wfh_types for s in sw):
        keys.append("wfh_leave" if work else "wfh_offday")
    if dd.get("very_late"):
        keys.append("very_late")
    if dd.get("early"):
        keys.append("early")
    if dd.get("short"):
        keys.append("short")
    if work and "absent" in (dd.get("b1"), dd.get("b2")):
        keys.append("absent")
    if dd.get("extra") and "wfh_offday" not in keys:
        keys.append("offday_work")
    return keys


def month_exceptions(db: Session, period: Period) -> dict:
    pol = get_policy(db)
    wfh_types = {x.lower() for x in pol.wfh_location_types}
    limit = int(pol.wfh_limit_days)
    rows, people = [], []
    counts = {k: 0 for k in LABELS}
    for r in results_for(db, period, with_days=True):
        d = r.data or {}
        auto = bool(d.get("auto_attendance"))
        per = {k: 0 for k in LABELS}
        n_wfh = 0
        for dd in r.days or []:
            keys = day_issues(dd, wfh_types, auto)
            if "wfh" in keys:
                n_wfh += 1
                if n_wfh > limit:
                    keys.insert(keys.index("wfh") + 1, "wfh_over")
            if not keys:
                continue
            for k in keys:
                per[k] += 1
                counts[k] += 1
            reasons = sorted({s.get("reason") for s in dd.get("swipes") or [] if s.get("reason")})
            remarks = sorted({s.get("note") for s in dd.get("swipes") or [] if s.get("note")})
            rows.append({
                "date": dd["date"], "wd": dd.get("wd", ""), "emp_no": r.emp_no, "name": r.name, "group": r.group or "",
                "category": r.category or "",
                "issues": keys, "in": dd.get("in") or "", "out": dd.get("out") or "", "work_min": dd.get("work_min") or 0,
                "status": (dd.get("s1") or "") + ("" if dd.get("s2") == dd.get("s1") else " / " + (dd.get("s2") or "")),
                "type": dd.get("type"), "holiday": dd.get("holiday") or "", "punches": _punches(dd),
                "n_punches": len(dd.get("swipes") or []), "late_min": dd.get("late_min") or 0,
                "reason": "; ".join(reasons), "remark": "; ".join(remarks), "notes": "; ".join(dd.get("notes") or []),
                "overridden": bool(dd.get("overridden")),
                "href": f"/periods/{period.key}/employee/{r.emp_no}?day={dd['date']}",
            })
        if any(per.values()):
            people.append({"emp_no": r.emp_no, "name": r.name, "group": r.group or "", "category": r.category or "",
                           "auto": auto,
                           "wfh_days": d.get("wfh_days") or 0, "wfh_deduction": d.get("wfh_deduction_days") or 0,
                           "href": f"/periods/{period.key}/employee/{r.emp_no}", **per,
                           "punch_issues": per["edited"] + per["rejected"]})
    rows.sort(key=lambda x: (x["date"], x["name"]))
    people.sort(key=lambda p: (-p["punch_issues"], -p["permission"], p["name"]))
    return {"rows": rows, "people": people, "counts": counts, "types": TYPES, "labels": LABELS, "classes": CLASSES,
            "wfh_limit": pol.wfh_limit_days,
            "kpis": {"punch_days": sum(1 for x in rows if {"edited", "rejected"} & set(x["issues"])),
                     "punch_people": sum(1 for p in people if p["punch_issues"]),
                     "regularisations": counts["permission"] + counts["edited"],
                     "reg_people": sum(1 for p in people if p["permission"] or p["edited"]),
                     "wfh": counts["wfh"], "wfh_over": counts["wfh_over"],
                     "wfh_over_people": sum(1 for p in people if p["wfh_over"]),
                     "wfh_offday": counts["wfh_offday"]}}
