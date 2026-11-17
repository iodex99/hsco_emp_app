"""Workforce insights: per-employee scorecards and firm-level KPIs beyond the payroll sheet.

Every definition is documented in docs/METRICS.md ("Insights"). Pure read-only computation over MonthResult rows
(no web dependencies) so the Insights page, the employee page, the Excel workbook and the HTML board pack all show
identical numbers.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, MonthResult, Period
from app.services.compute import fy_of
from app.services.settings import get_policy

UNPLANNED = ("absent", "lop")
BRADFORD_BANDS = [(0, "None", "ok"), (1, "Low", "ok"), (51, "Monitor", "info"), (201, "Warning", "warn"),
                  (450, "Serious", "bad")]
HOURS_BUCKETS = [(0, 4, "< 4 h"), (4, 6, "4–6 h"), (6, 7, "6–7 h"), (7, 8, "7–8 h"), (8, 8.5, "8–8.5 h"),
                 (8.5, 9, "8.5–9 h"), (9, 10, "9–10 h"), (10, 11, "10–11 h"), (11, 99, "11 h +")]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# attention score weights (sum 100) — see METRICS.md
WEIGHTS = {"bradford": 25, "punctuality": 20, "utilisation": 20, "regularisation": 15, "wfh": 10, "missing": 10}


def _pct(a: float, b: float) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def _avg(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def bradford_band(score: float) -> tuple[str, str]:
    label, tone = "None", "ok"
    for lo, lab, t in BRADFORD_BANDS:
        if score >= lo:
            label, tone = lab, t
    return label, tone


def _spells(days: list[dict]) -> tuple[int, float]:
    """Unplanned-absence spells and days. Consecutive absent working days form one spell; weekly-offs and holidays
    between them do not break it (a Friday + Monday absence is one spell)."""
    spells, total, in_spell = 0, 0.0, False
    for dd in sorted(days, key=lambda d: d["date"]):
        if dd.get("type") != "WORKDAY":
            continue
        halves = sum(0.5 for b in (dd.get("b1"), dd.get("b2")) if b in UNPLANNED)
        if halves:
            if not in_spell:
                spells += 1
            in_spell = True
            total += halves
        else:
            in_spell = False
    return spells, total


def _hm(minutes) -> str:
    if minutes is None:
        return ""
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _months_back(period: Period, n: int) -> list[tuple[int, int]]:
    y, m, out = period.year, period.month, []
    for _ in range(n):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


_CACHE: dict = {}


def insights(db: Session, period: Period, with_matrix: bool = True) -> dict:
    """Cached per period until any month is recalculated or employee master data / settings change."""
    from sqlalchemy import func
    from app.models import Setting
    stamp = (period.id, with_matrix, db.scalar(select(func.max(Period.computed_at))),
             db.scalar(select(func.max(Setting.updated_at))), db.scalar(select(func.count(Employee.emp_no))),
             db.scalar(select(func.max(Employee.updated_at))) if hasattr(Employee, "updated_at") else None)
    hit = _CACHE.get(stamp)
    if hit is not None:
        return hit
    out = _insights(db, period, with_matrix)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[stamp] = out
    return out


def _insights(db: Session, period: Period, with_matrix: bool = True) -> dict:
    policy = get_policy(db)
    res = list(db.scalars(select(MonthResult).where(MonthResult.period_id == period.id).order_by(MonthResult.emp_no)))
    emps = {e.emp_no: e for e in db.scalars(select(Employee))}
    y, m = period.year, period.month
    month_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    excl = set(policy.metrics_exclude_groups)

    # ---------------------------------------------------------------- history (rolling 12 months, FY to date)
    window = _months_back(period, 12)
    hist_periods = [p for p in db.scalars(select(Period)) if (p.year, p.month) in set(window) and p.computed_at]
    hist_ids = {p.id: p for p in hist_periods}
    fy = fy_of(y, m, policy.fy_start_month)
    hist_days: dict[str, list] = defaultdict(list)
    fy_leave: Counter = Counter()
    fy_months: Counter = Counter()
    months_seen: Counter = Counter()
    if hist_ids:
        for emp_no, days, data, pid in db.execute(select(MonthResult.emp_no, MonthResult.days, MonthResult.data,
                                                         MonthResult.period_id)
                                                  .where(MonthResult.period_id.in_(list(hist_ids)))):
            p = hist_ids[pid]
            hist_days[emp_no].extend(days or [])
            months_seen[emp_no] += 1
            if fy_of(p.year, p.month, policy.fy_start_month) == fy and (p.year, p.month) <= (y, m):
                fy_leave[emp_no] += (data or {}).get("pl_days") or 0
                fy_months[emp_no] += 1

    # ---------------------------------------------------------------- per-employee scorecard
    rows = []
    arrivals = Counter()                       # (weekday, half-hour bucket) -> count
    hours_hist = Counter()
    for r in res:
        d = r.data
        e = emps.get(r.emp_no)
        days = r.days or []
        work = [x for x in days if x.get("type") == "WORKDAY"]
        workdays = len(work)
        present = [x for x in work if "present" in (x.get("b1"), x.get("b2"))]
        with_in = [x for x in present if x.get("in")]
        on_time = [x for x in with_in if not x.get("late")]
        long_days = [x for x in days if (x.get("work_min") or 0) >= (d.get("hours_per_day") or policy.hours_per_day) * 60 + 120]
        worked = [x for x in days if (x.get("work_min") or 0) > 0]
        unpl_spells_m, unpl_days_m = _spells(days)
        s12, d12 = _spells(hist_days.get(r.emp_no, days))
        bradford = round(s12 * s12 * d12)
        band, tone = bradford_band(bradford)
        tenure_m = None
        if e and e.doj:
            tenure_m = (month_end.year - e.doj.year) * 12 + (month_end.month - e.doj.month)
        req = d.get("required_hours") or 0
        act = d.get("actual_hours") or 0
        ent = policy.annual_leave_days
        row = {
            "emp_no": r.emp_no, "name": r.name, "group": r.group or "", "location": r.location or "",
            "behaviour": (r.group or "") not in excl,
            "tenure_m": tenure_m, "doj": e.doj.isoformat() if e and e.doj else "",
            "workdays": workdays, "present_days": d.get("days_present") or 0,
            "attendance_pct": _pct(d.get("days_present") or 0, workdays),
            "unplanned_days": unpl_days_m, "unplanned_spells": unpl_spells_m,
            "absence_pct": _pct(unpl_days_m, workdays),
            "leave_days": d.get("pl_days") or 0, "compoff_days": d.get("cof_days") or 0,
            "bradford": bradford, "bradford_band": band, "bradford_tone": tone, "bradford_spells": s12,
            "bradford_days": d12, "bradford_months": months_seen.get(r.emp_no, 1),
            "punctuality_pct": _pct(len(on_time), len(with_in)),
            "late_days": d.get("late_days") or 0, "very_late_days": d.get("very_late_days") or 0,
            "avg_late_min": d.get("avg_late_min") or 0,
            "avg_in": _hm(d.get("avg_in_min")), "avg_in_min": d.get("avg_in_min"),
            "avg_out": _hm(d.get("avg_out_min")), "avg_out_min": d.get("avg_out_min"),
            "avg_hours": d.get("avg_work_hours"),
            "utilisation_pct": _pct(act, req), "actual_hours": round(act, 1), "required_hours": round(req, 1),
            "balance_hours": round(d.get("balance_hours") or 0, 1),
            "long_days": len(long_days), "short_days": d.get("short_days") or 0,
            "late_sitting_days": d.get("late_sitting_days") or 0, "early_exit_days": d.get("early_exit_days") or 0,
            "wfh_days": d.get("wfh_days") or 0, "wfh_pct": _pct(d.get("wfh_days") or 0, len(present) or workdays),
            "regularised_days": d.get("regularised_days") or 0,
            "regularisation_pct": _pct(d.get("regularised_days") or 0, len(present)),
            "missing_punch_days": d.get("missing_punch_days") or 0,
            "extra_days": d.get("extra_days") or 0, "compoff_earned": d.get("compoff_earned") or 0,
            "deduction_days": d.get("deduction_days") or 0,
            "leave_closing": d.get("leave_closing"), "compoff_closing": d.get("compoff_closing"),
            "encashment_days": d.get("encashment_days"),
            "leave_used_fy": round(fy_leave.get(r.emp_no, d.get("pl_days") or 0), 1),
            "leave_used_fy_pct": _pct(fy_leave.get(r.emp_no, d.get("pl_days") or 0), ent),
            "flags": r.flag_count,
        }
        row.update(_attention(row, policy))
        rows.append(row)
        if row["behaviour"]:
            for x in with_in:
                h, mi = map(int, x["in"].split(":"))
                wd = x["wd"] if isinstance(x.get("wd"), int) else date.fromisoformat(x["date"]).weekday()
                arrivals[(wd, "< 08:00" if h < 8 else "13:00 +" if h >= 13 else f"{h:02d}:{(mi // 30) * 30:02d}")] += 1
            for x in worked:
                h = x["work_min"] / 60
                for lo, hi, lab in HOURS_BUCKETS:
                    if lo <= h < hi:
                        hours_hist[lab] += 1
                        break

    beh = [x for x in rows if x["behaviour"]]

    def tot(k, rs=rows):
        return round(sum((x.get(k) or 0) for x in rs), 1)

    workdays_all = tot("workdays")
    punct_num = punct_den = 0
    for r in res:
        if (r.group or "") in excl:
            continue
        for x in r.days or []:
            if x.get("type") == "WORKDAY" and x.get("in") and "present" in (x.get("b1"), x.get("b2")):
                punct_den += 1
                punct_num += 0 if x.get("late") else 1

    # attrition: exits in the last 12 months / average headcount
    start12 = date(*window[-1], 1)
    exits12 = [e for e in emps.values() if e.dol and start12 <= e.dol <= month_end]
    joins12 = [e for e in emps.values() if e.doj and start12 <= e.doj <= month_end]
    active_now = [e for e in emps.values() if not e.dol or e.dol > month_end]
    active_start = [e for e in emps.values() if (not e.doj or e.doj <= start12) and (not e.dol or e.dol >= start12)]
    avg_hc = (len(active_now) + len(active_start)) / 2 or 1
    tenures = [(month_end - e.doj).days / 365.25 for e in active_now if e.doj and e.doj <= month_end]
    ins = [x["avg_in_min"] for x in beh if x["avg_in_min"]]

    kpis = {
        "headcount": len(rows),
        "absenteeism_pct": _pct(tot("unplanned_days"), workdays_all),
        "attendance_pct": _pct(tot("present_days"), workdays_all),
        "unplanned_spells": int(tot("unplanned_spells")),
        "bradford_avg": _avg([x["bradford"] for x in rows]),
        "bradford_warning": sum(1 for x in rows if x["bradford"] >= 201),
        "bradford_serious": sum(1 for x in rows if x["bradford"] >= 450),
        "punctuality_pct": _pct(punct_num, punct_den),
        "utilisation_pct": _pct(tot("actual_hours"), tot("required_hours")),
        "overtime_hours": round(sum(max(0, x["balance_hours"]) for x in rows), 1),
        "shortfall_hours": round(sum(max(0, -x["balance_hours"]) for x in rows), 1),
        "long_days": int(tot("long_days", beh)),
        "late_sitting_days": int(tot("late_sitting_days", beh)),
        "wfh_pct": _pct(tot("wfh_days"), tot("present_days")),
        "regularisation_pct": _pct(tot("regularised_days", beh), tot("present_days", beh)),
        "missing_punch_pct": _pct(tot("missing_punch_days", beh), tot("present_days", beh)),
        "leave_liability_days": round(sum(x["leave_closing"] or 0 for x in rows), 1),
        "encashment_days": round(sum(x["encashment_days"] or 0 for x in rows), 1),
        "compoff_liability_days": round(sum(x["compoff_closing"] or 0 for x in rows), 1),
        "leave_used_fy_pct": _pct(tot("leave_used_fy"), policy.annual_leave_days * len(rows)),
        "attrition_pct": round(100 * len(exits12) / avg_hc, 1),
        "exits_12m": len(exits12), "joiners_12m": len(joins12),
        "avg_tenure_years": round(sum(tenures) / len(tenures), 1) if tenures else None,
        "median_in": _hm(median(ins)) if ins else "",
        "attention_high": sum(1 for x in rows if x["attention"] >= 50),
        "fy_label": f"FY {fy}-{str(fy + 1)[-2:]}",
    }

    # ---------------------------------------------------------------- group comparison
    groups = []
    by_g = defaultdict(list)
    for x in rows:
        by_g[x["group"] or "(not set)"].append(x)
    for g, rs in sorted(by_g.items()):
        wd = sum(x["workdays"] for x in rs)
        groups.append({
            "group": g, "headcount": len(rs),
            "attendance_pct": _pct(sum(x["present_days"] for x in rs), wd),
            "absenteeism_pct": _pct(sum(x["unplanned_days"] for x in rs), wd),
            "punctuality_pct": _avg([x["punctuality_pct"] for x in rs if x["behaviour"]]),
            "utilisation_pct": _pct(sum(x["actual_hours"] for x in rs), sum(x["required_hours"] for x in rs)),
            "avg_hours": _avg([x["avg_hours"] for x in rs]),
            "wfh_pct": _pct(sum(x["wfh_days"] for x in rs), sum(x["present_days"] for x in rs)),
            "regularisation_pct": _pct(sum(x["regularised_days"] for x in rs if x["behaviour"]),
                                       sum(x["present_days"] for x in rs if x["behaviour"])),
            "bradford_avg": _avg([x["bradford"] for x in rs]),
            "attention_avg": _avg([x["attention"] for x in rs]),
            "deduction_days": round(sum(x["deduction_days"] for x in rs), 1),
        })

    # ---------------------------------------------------------------- arrival heatmap & hours histogram
    buckets = sorted({b for _, b in arrivals}, key=lambda b: "00" if b.startswith("<") else "99" if "+" in b else b)
    arrival = {"buckets": buckets, "rows": [{"wd": WD[w], "counts": [arrivals.get((w, b), 0) for b in buckets]}
                                            for w in range(7) if any(arrivals.get((w, b)) for b in buckets)]}
    arrival["max"] = max([c for r in arrival["rows"] for c in r["counts"]] or [0])

    out = {
        "period": {"key": period.key, "label": period.label},
        "kpis": kpis, "rows": rows, "groups": groups, "arrival": arrival,
        "hours_hist": [{"bucket": lab, "count": hours_hist.get(lab, 0)} for _, _, lab in HOURS_BUCKETS],
        "bradford_bands": [{"band": lab, "tone": t, "from": lo,
                            "count": sum(1 for x in rows if x["bradford_band"] == lab)} for lo, lab, t in BRADFORD_BANDS],
        "weights": WEIGHTS,
        "trend": insight_trend(db, period),
    }
    if with_matrix:
        out["matrix"] = matrix(res)
    return out


def _attention(row: dict, policy) -> dict:
    """0–100 composite: higher = needs a conversation. Each part is 0..1 then weighted (see WEIGHTS)."""
    parts = {
        "bradford": min(1.0, row["bradford"] / 450),
        "punctuality": (1 - (row["punctuality_pct"] or 100) / 100) if row["behaviour"] else 0,
        "utilisation": max(0.0, min(1.0, (100 - (row["utilisation_pct"] or 100)) / 20)),
        "regularisation": min(1.0, (row["regularised_days"] or 0) / max(1, policy.persistent_regularisation_days * 2))
        if row["behaviour"] else 0,
        "wfh": min(1.0, max(0.0, (row["wfh_days"] - policy.wfh_limit_days) / max(1, policy.wfh_limit_days * 2))),
        "missing": min(1.0, (row["missing_punch_days"] or 0) / 5) if row["behaviour"] else 0,
    }
    score = round(sum(parts[k] * w for k, w in WEIGHTS.items()))
    top = sorted(((parts[k] * w, k) for k, w in WEIGHTS.items()), reverse=True)
    reasons = [k for v, k in top if v >= 5][:3]
    return {"attention": score, "attention_parts": {k: round(parts[k] * WEIGHTS[k], 1) for k in WEIGHTS},
            "attention_why": reasons}


STATE_ORDER = ["P", "WFH", "LATE", "HALF", "L", "CO", "A", "LOP", "OFF", "H", "XW", ""]


def _state(dd: dict) -> str:
    b = (dd.get("b1"), dd.get("b2"))
    if dd.get("type") == "HOLIDAY" and not dd.get("work_min"):
        return "H"
    if dd.get("type") != "WORKDAY":
        return "XW" if dd.get("extra") else "OFF"
    if "absent" in b:
        return "A"
    if "lop" in b:
        return "LOP"
    if b[0] == b[1] == "leave":
        return "L"
    if b[0] == b[1] == "compoff":
        return "CO"
    if "leave" in b or "compoff" in b:
        return "HALF"
    if dd.get("wfh"):
        return "WFH"
    if dd.get("late"):
        return "LATE"
    return "P"


def matrix(res: list[MonthResult]) -> dict:
    """Employee × day attendance states for the heatmap."""
    dates = sorted({dd["date"] for r in res for dd in (r.days or [])})
    out = []
    for r in res:
        by = {dd["date"]: dd for dd in (r.days or [])}
        cells = []
        for dt in dates:
            dd = by.get(dt)
            cells.append(_state(dd) if dd else "")
        out.append({"emp_no": r.emp_no, "name": r.name, "group": r.group or "", "s": "".join(
            (c[0] if c not in ("LOP", "LATE", "HALF", "WFH", "XW") else {"LOP": "X", "LATE": "T", "HALF": "½",
                                                                       "WFH": "W", "XW": "E"}[c]) if c else "."
            for c in cells)})
    return {"dates": dates, "weekdays": [WD[date.fromisoformat(d).weekday()] for d in dates], "rows": out,
            "legend": [("P", "Present", "p"), ("T", "Late", "t"), ("W", "WFH", "w"), ("½", "Half leave", "h"),
                       ("L", "Leave", "l"), ("C", "Comp-off", "c"), ("A", "Absent", "a"), ("X", "Loss of pay", "x"),
                       ("O", "Weekly off", "o"), ("H", "Holiday", "y"), ("E", "Worked off-day", "e")]}


def insight_trend(db: Session, period: Period, n: int = 12) -> list[dict]:
    excl = set(get_policy(db).metrics_exclude_groups)
    ps = [p for p in db.scalars(select(Period).order_by(Period.year, Period.month))
          if p.computed_at and (p.year, p.month) <= (period.year, period.month)][-n:]
    out = []
    for p in ps:
        wd = pres = unpl = act = req = pn = pd = wfh = 0.0
        hc = 0
        for grp, data, days in db.execute(select(MonthResult.group, MonthResult.data, MonthResult.days)
                                          .where(MonthResult.period_id == p.id)):
            hc += 1
            act += data.get("actual_hours") or 0
            req += data.get("required_hours") or 0
            pres += data.get("days_present") or 0
            wfh += data.get("wfh_days") or 0
            for x in days or []:
                if x.get("type") != "WORKDAY":
                    continue
                wd += 1
                unpl += sum(0.5 for b in (x.get("b1"), x.get("b2")) if b in UNPLANNED)
                if (grp or "") not in excl and x.get("in") and "present" in (x.get("b1"), x.get("b2")):
                    pd += 1
                    pn += 0 if x.get("late") else 1
        out.append({"key": p.key, "label": p.label, "headcount": hc, "absenteeism_pct": _pct(unpl, wd),
                    "attendance_pct": _pct(pres, wd), "punctuality_pct": _pct(pn, pd), "utilisation_pct": _pct(act, req),
                    "wfh_pct": _pct(wfh, pres)})
    return out


def employee_scorecard(ins: dict, emp_no: str) -> Optional[dict]:
    """One employee's scorecard next to their group's and the firm's averages."""
    me = next((x for x in ins["rows"] if x["emp_no"] == emp_no), None)
    if not me:
        return None
    grp = [x for x in ins["rows"] if x["group"] == me["group"]]
    keys = [("attendance_pct", "Attendance", "%", True), ("punctuality_pct", "On-time arrivals", "%", True),
            ("utilisation_pct", "Hours vs required", "%", True), ("avg_hours", "Average day (h)", "", True),
            ("absence_pct", "Unplanned absence", "%", False), ("bradford", "Bradford factor (12 m)", "", False),
            ("wfh_pct", "WFH share of days", "%", False), ("regularisation_pct", "Regularised days", "%", False),
            ("long_days", "Long days (≥ 2 h over)", "", None), ("attention", "Attention score", "", False)]
    out = []
    for k, label, unit, higher_better in keys:
        mv = me.get(k)
        ga = _avg([x.get(k) for x in grp])
        fa = _avg([x.get(k) for x in ins["rows"]])
        verdict = ""
        if mv is not None and fa is not None and higher_better is not None:
            diff = (mv - fa) if higher_better else (fa - mv)
            scale = max(abs(fa) * (0.05 if unit == "%" else 0.1), 1)
            verdict = "better" if diff > scale else "worse" if diff < -scale else "typical"
        out.append({"key": k, "label": label, "unit": unit, "me": mv, "group": ga, "firm": fa, "verdict": verdict})
    rank = sorted(ins["rows"], key=lambda x: -x["attention"]).index(me) + 1
    return {"row": me, "metrics": out, "rank": rank, "of": len(ins["rows"]), "weights": WEIGHTS}
