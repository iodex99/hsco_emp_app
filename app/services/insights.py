"""Workforce insights: per-employee scorecards and firm-level KPIs beyond the payroll sheet.

Every definition is documented in docs/METRICS.md ("Insights"). Pure read-only computation over MonthResult rows
(no web dependencies) so the Insights page, the employee page, and the Excel workbook all show
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
from app.services import attendance_stats as AS
from app.services.compute import fy_of
from app.services.settings import get_policy

UNPLANNED = AS.UNPLANNED
HOURS_BUCKETS = [(0, 4, "< 4 h"), (4, 6, "4–6 h"), (6, 7, "6–7 h"), (7, 8, "7–8 h"), (8, 8.5, "8–8.5 h"),
                 (8.5, 9, "8.5–9 h"), (9, 10, "9–10 h"), (10, 11, "10–11 h"), (11, 99, "11 h +")]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# attention score weights (sum 100) — see METRICS.md. v1.13: missing punches removed (owner); its 10 points went to
# unapplied leave (+5) and hours (+5)
WEIGHTS = {"absence": 30, "punctuality": 20, "utilisation": 25, "regularisation": 15, "wfh": 10}
ABSENCE_FULL_DAYS = 10        # unplanned absence days in 12 months that give the full absence part of the score
PART_LABELS = {"absence": "Unapplied leave (absent), 12 months", "punctuality": "Late arrivals",
               "utilisation": "Hours below required", "regularisation": "Regularisations", "wfh": "WFH over the limit"}


def _pct(a: float, b: float) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def _avg(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _spells(days: list[dict]) -> tuple[int, float]:
    return AS.spells(days)


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


def _compare(rows: list[dict], by: str) -> list[dict]:
    """One line per group / category: the firm's comparison measures (Insights → Groups compared)."""
    out = []
    by_g = defaultdict(list)
    for x in rows:
        by_g[x.get(by) or "(not set)"].append(x)
    for g, rs in sorted(by_g.items()):
        wd = sum(x["workdays"] for x in rs)
        out.append({
            "group": g, "headcount": len(rs),
            "attendance_pct": _pct(sum(x["present_days"] for x in rs), wd),
            "absenteeism_pct": _pct(sum(x["unplanned_days"] for x in rs), wd),
            "punctuality_pct": _avg([x["punctuality_pct"] for x in rs if x["behaviour"]]),
            "utilisation_pct": _pct(sum(x["actual_hours"] for x in rs), sum(x["required_hours"] for x in rs)),
            "avg_hours": _avg([x["avg_hours"] for x in rs]),
            "wfh_pct": _pct(sum(x["wfh_days"] for x in rs), sum(x["present_days"] for x in rs)),
            "regularisation_pct": _pct(sum(x["regularised_days"] for x in rs if x["behaviour"]),
                                       sum(x["present_days"] for x in rs if x["behaviour"])),
            "absence_12m_days": round(sum(x["absence_12m_days"] for x in rs), 1),
            "attention_avg": _avg([x["attention"] for x in rs]),
            "deduction_days": round(sum(x["deduction_days"] for x in rs), 1),
        })
    return out


def insights(db: Session, period: Period, with_matrix: bool = True) -> dict:
    """Cached per period until any month is recalculated or employee master data / settings change."""
    from sqlalchemy import func
    from app.models import Setting
    stamp = (period.id, db.scalar(select(func.max(Period.computed_at))),
             db.scalar(select(func.max(Setting.updated_at))), db.scalar(select(func.count(Employee.emp_no))),
             db.scalar(select(func.max(Employee.updated_at))) if hasattr(Employee, "updated_at") else None)
    hit = _CACHE.get(stamp)
    if hit is not None:
        return hit
    out = _insights(db, period, True)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[stamp] = out
    return out


def _insights(db: Session, period: Period, with_matrix: bool = True) -> dict:
    policy = get_policy(db)
    res = list(db.scalars(select(MonthResult).where(MonthResult.period_id == period.id).order_by(MonthResult.emp_no)))
    from app.services.metrics import scoped_employees
    emps = scoped_employees(db, policy)                    # ignored groups (OTHERS) left out (v1.13)
    y, m = period.year, period.month
    month_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    excl = set(policy.metrics_exclude_groups)

    # ---------------------------------------------------------------- history (rolling 12 months, FY to date)
    window = _months_back(period, 12)
    hist_ids = {p.id: p for p in db.scalars(select(Period).where(Period.computed_at.is_not(None)))
                if (p.year, p.month) in set(window)}
    fy = fy_of(y, m, policy.fy_start_month)
    hist = history(db, hist_ids)
    hist_stats: dict[str, list] = defaultdict(list)
    fy_leave: Counter = Counter()
    for h in hist:
        if h["pid"] == period.id:
            continue                                   # the current month comes from its full rows below
        hist_stats[h["emp_no"]].append((h["ym"], h["st"]))
        if fy_of(*h["ym"], policy.fy_start_month) == fy:
            fy_leave[h["emp_no"]] += h["data"].get("pl_days") or 0

    # ---------------------------------------------------------------- per-employee scorecard
    rows = []
    arrivals = Counter()                       # (weekday, half-hour bucket) -> count
    arrivals_who = defaultdict(Counter)        # "Mon|09:30" -> {emp_no: days}   (drill-down, v1.13)
    hours_hist = Counter()
    hours_who = defaultdict(Counter)
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
        st_m = AS.month_stats(days)
        unpl_spells_m, unpl_days_m = st_m["sp"], st_m["un"]
        past = hist_stats.get(r.emp_no, [])
        s12, d12 = AS.join_spells(past + [((y, m), st_m)])
        tenure_m = None
        if e and e.doj:
            tenure_m = (month_end.year - e.doj.year) * 12 + (month_end.month - e.doj.month)
            if tenure_m < 0:
                tenure_m = None
        req = d.get("required_hours") or 0
        act = d.get("actual_hours") or 0
        ent = policy.annual_leave_days
        row = {
            "emp_no": r.emp_no, "name": r.name, "group": r.group or "", "location": r.location or "",
            "category": r.category or d.get("category") or "",
            "behaviour": (r.group or "") not in excl,
            "tenure_m": tenure_m, "doj": e.doj.isoformat() if e and e.doj else "",
            "workdays": workdays, "present_days": d.get("days_present") or 0,
            "attendance_pct": _pct(d.get("days_present") or 0, workdays),
            "unplanned_days": unpl_days_m, "unplanned_spells": unpl_spells_m,
            "absence_pct": _pct(unpl_days_m, workdays),
            "leave_days": d.get("pl_days") or 0, "compoff_days": d.get("cof_days") or 0,
            "absence_12m_days": d12, "absence_12m_spells": s12, "absence_12m_months": len(past) + 1,
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
            "extra_days": d.get("extra_days") or 0, "compoff_earned": d.get("compoff_earned") or 0,
            "deduction_days": d.get("deduction_days") or 0,
            "leave_used_fy": round(fy_leave.get(r.emp_no, 0) + (d.get("pl_days") or 0), 1),
            "leave_used_fy_pct": _pct(fy_leave.get(r.emp_no, 0) + (d.get("pl_days") or 0), ent),
            "flags": r.flag_count,
        }
        row.update(_attention(row, policy))
        rows.append(row)
        if row["behaviour"]:
            for x in with_in:
                h, mi = map(int, x["in"].split(":"))
                wd = x["wd"] if isinstance(x.get("wd"), int) else date.fromisoformat(x["date"]).weekday()
                b_ = "< 08:00" if h < 8 else "13:00 +" if h >= 13 else f"{h:02d}:{(mi // 30) * 30:02d}"
                arrivals[(wd, b_)] += 1
                arrivals_who[f"{WD[wd]}|{b_}"][r.emp_no] += 1
            for x in worked:
                h = x["work_min"] / 60
                for lo, hi, lab in HOURS_BUCKETS:
                    if lo <= h < hi:
                        hours_hist[lab] += 1
                        hours_who[lab][r.emp_no] += 1
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
        "absence_12m_days": round(sum(x["absence_12m_days"] for x in rows), 1),
        "absence_12m_people": sum(1 for x in rows if x["absence_12m_days"]),
        "punctuality_pct": _pct(punct_num, punct_den),
        "utilisation_pct": _pct(tot("actual_hours"), tot("required_hours")),
        "overtime_hours": round(sum(max(0, x["balance_hours"]) for x in rows), 1),
        "shortfall_hours": round(sum(max(0, -x["balance_hours"]) for x in rows), 1),
        "long_days": int(tot("long_days", beh)),
        "late_sitting_days": int(tot("late_sitting_days", beh)),
        "wfh_pct": _pct(tot("wfh_days"), tot("present_days")),
        "regularisation_pct": _pct(tot("regularised_days", beh), tot("present_days", beh)),
        "leave_used_fy_pct": _pct(tot("leave_used_fy"), policy.annual_leave_days * len(rows)),
        "attrition_pct": round(100 * len(exits12) / avg_hc, 1),
        "exits_12m": len(exits12), "joiners_12m": len(joins12),
        "avg_tenure_years": round(sum(tenures) / len(tenures), 1) if tenures else None,
        "median_in": _hm(median(ins)) if ins else "",
        "median_in_min": round(median(ins), 1) if ins else None,
        "attention_high": sum(1 for x in rows if x["attention"] >= 50),
        "fy_label": f"FY {fy}-{str(fy + 1)[-2:]}",
    }

    # ---------------------------------------------------------------- group comparison (v1.13: groups only)
    groups = _compare(rows, "group")
    firm = _compare([dict(x, group="All") for x in rows], "group")

    # ---------------------------------------------------------------- arrival heatmap & hours histogram
    buckets = sorted({b for _, b in arrivals}, key=lambda b: "00" if b.startswith("<") else "99" if "+" in b else b)
    arrival = {"buckets": buckets, "rows": [{"wd": WD[w], "counts": [arrivals.get((w, b), 0) for b in buckets]}
                                            for w in range(7) if any(arrivals.get((w, b)) for b in buckets)]}
    arrival["max"] = max([c for r in arrival["rows"] for c in r["counts"]] or [0])

    out = {
        "period": {"key": period.key, "label": period.label},
        "kpis": kpis, "rows": rows, "groups": groups, "firm": firm[0] if firm else None, "arrival": arrival,
        "hours_hist": [{"bucket": lab, "count": hours_hist.get(lab, 0)} for _, _, lab in HOURS_BUCKETS],
        "top": {
            "absence_month": [{"emp_no": x["emp_no"], "name": x["name"], "group": x["group"], "value": x["unplanned_days"],
                               "sub": f"{x['group']} · {x['unplanned_spells']} spell{'s' if x['unplanned_spells'] != 1 else ''}"}
                              for x in sorted(rows, key=lambda x: (-x["unplanned_days"], x["name"])) if x["unplanned_days"]],
            "absence_12m": [{"emp_no": x["emp_no"], "name": x["name"], "group": x["group"], "value": x["absence_12m_days"],
                             "sub": f"{x['group']} · {x['absence_12m_spells']} spell{'s' if x['absence_12m_spells'] != 1 else ''}"
                                    f" in {x['absence_12m_months']} month{'s' if x['absence_12m_months'] != 1 else ''}"}
                            for x in sorted(rows, key=lambda x: (-x["absence_12m_days"], x["name"])) if x["absence_12m_days"]],
        },
        "drill": {"hours": {k: [[e, n] for e, n in c.most_common()] for k, c in hours_who.items()},
                  "arrival": {k: [[e, n] for e, n in c.most_common()] for k, c in arrivals_who.items()}},
        "weights": WEIGHTS,
        "trend": insight_trend(db, period, hist, hist_ids),
    }
    if with_matrix:
        out["matrix"] = matrix(res)
    return out


def _attention(row: dict, policy) -> dict:
    """0–100 composite: higher = needs a conversation. Each part is 0..1 then weighted (see WEIGHTS)."""
    parts = {
        "absence": min(1.0, (row["absence_12m_days"] or 0) / ABSENCE_FULL_DAYS),
        "punctuality": (1 - (100 if row["punctuality_pct"] is None else row["punctuality_pct"]) / 100) if row["behaviour"] else 0,
        "utilisation": max(0.0, min(1.0, (100 - (100 if row["utilisation_pct"] is None else row["utilisation_pct"])) / 20)),
        "regularisation": min(1.0, (row["regularised_days"] or 0) / max(1, policy.persistent_regularisation_days * 2))
        if row["behaviour"] else 0,
        "wfh": min(1.0, max(0.0, (row["wfh_days"] - policy.wfh_limit_days) / max(1, policy.wfh_limit_days * 2))),
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
    if "lop" in b:                    # months calculated before v1.13: loss of pay shows as leave
        return "L"
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
        out.append({"emp_no": r.emp_no, "name": r.name, "group": r.group or "", "category": r.category or "", "s": "".join(
            (c[0] if c not in ("LOP", "LATE", "HALF", "WFH", "XW") else {"LOP": "X", "LATE": "T", "HALF": "½",
                                                                       "WFH": "W", "XW": "E"}[c]) if c else "."
            for c in cells)})
    return {"dates": dates, "weekdays": [WD[date.fromisoformat(d).weekday()] for d in dates], "rows": out,
            "legend": [("P", "Present", "p"), ("T", "Late", "t"), ("W", "WFH", "w"), ("½", "Half leave", "h"),
                       ("L", "Leave", "l"), ("C", "Comp-off", "c"), ("A", "Unapplied leave (absent)", "a"),
                       ("O", "Weekly off", "o"), ("H", "Holiday", "y"), ("E", "Worked off-day", "e")]}


def history(db: Session, pids: dict) -> list[dict]:
    """One row per employee-month for the given periods: result data + compact stats, never the day detail.
    Months calculated before v1.4.1 have no stored stats; those are derived from their days (one extra query)."""
    if not pids:
        return []
    out, missing = [], []
    for rid, emp_no, grp, data, pid in db.execute(select(MonthResult.id, MonthResult.emp_no, MonthResult.group,
                                                         MonthResult.data, MonthResult.period_id)
                                                  .where(MonthResult.period_id.in_(list(pids)))):
        p = pids[pid]
        row = {"id": rid, "emp_no": emp_no, "group": grp or "", "data": data or {}, "pid": pid,
               "ym": (p.year, p.month), "st": (data or {}).get("_ins")}
        if not row["st"]:
            missing.append(row)
        out.append(row)
    if missing:
        by_id = {r["id"]: r for r in missing}
        ids = list(by_id)
        for i in range(0, len(ids), 500):
            for rid, days in db.execute(select(MonthResult.id, MonthResult.days).where(MonthResult.id.in_(ids[i:i + 500]))):
                by_id[rid]["st"] = AS.month_stats(days or [])
    return out


def insight_trend(db: Session, period: Period, hist: Optional[list] = None, pids: Optional[dict] = None) -> list[dict]:
    excl = set(get_policy(db).metrics_exclude_groups)
    if hist is None:
        window = set(_months_back(period, 12))
        pids = {p.id: p for p in db.scalars(select(Period).where(Period.computed_at.is_not(None)))
                if (p.year, p.month) in window}
        hist = history(db, pids)
    agg: dict = defaultdict(Counter)
    for h in hist:
        a, d, st = agg[h["pid"]], h["data"], h["st"]
        a["hc"] += 1
        a["act"] += d.get("actual_hours") or 0
        a["req"] += d.get("required_hours") or 0
        a["pres"] += d.get("days_present") or 0
        a["wfh"] += d.get("wfh_days") or 0
        a["wd"] += st["wd"]
        a["un"] += st["un"]
        if h["group"] not in excl:
            a["pd"] += st["pw"]
            a["pn"] += st["ot"]
    out = []
    for pid, p in sorted((pids or {}).items(), key=lambda x: (x[1].year, x[1].month)):
        if pid not in agg or (p.year, p.month) > (period.year, period.month):
            continue
        a = agg[pid]
        out.append({"key": p.key, "label": p.label, "headcount": a["hc"], "absenteeism_pct": _pct(a["un"], a["wd"]),
                    "attendance_pct": _pct(a["pres"], a["wd"]), "punctuality_pct": _pct(a["pn"], a["pd"]),
                    "utilisation_pct": _pct(a["act"], a["req"]), "wfh_pct": _pct(a["wfh"], a["pres"])})
    return out


_SC_KEYS = [("attendance_pct", "Attendance", "%", True), ("punctuality_pct", "On-time arrivals", "%", True),
            ("utilisation_pct", "Hours vs required", "%", True), ("avg_hours", "Average day (h)", "", True),
            ("absence_pct", "Unapplied leave (absent)", "%", False), ("absence_12m_days", "Unapplied leave days (12 m)", "", False),
            ("wfh_pct", "WFH share of days", "%", False), ("regularisation_pct", "Regularised days", "%", False),
            ("long_days", "Long days (≥ 2 h over)", "", None), ("attention", "Attention score", "", False)]


def _sc_aggregates(ins: dict) -> dict:
    """Group/firm averages, attention ranks and a by-employee index, computed once per insights result."""
    agg = ins.get("_sc_agg")
    if agg is None:
        by_g = defaultdict(list)
        for x in ins["rows"]:
            by_g[x["group"]].append(x)
        agg = {"firm": {k: _avg([x.get(k) for x in ins["rows"]]) for k, *_ in _SC_KEYS},
               "group": {g: {k: _avg([x.get(k) for x in rs]) for k, *_ in _SC_KEYS} for g, rs in by_g.items()},
               "rank": {x["emp_no"]: i + 1 for i, x in enumerate(sorted(ins["rows"], key=lambda x: -x["attention"]))},
               "by_emp": {x["emp_no"]: x for x in ins["rows"]}}
        ins["_sc_agg"] = agg
    return agg


def employee_scorecard(ins: dict, emp_no: str) -> Optional[dict]:
    """One employee's scorecard next to their group's and the firm's averages."""
    agg = _sc_aggregates(ins)
    me = agg["by_emp"].get(emp_no)
    if not me:
        return None
    out = []
    for k, label, unit, higher_better in _SC_KEYS:
        mv, ga, fa = me.get(k), agg["group"][me["group"]][k], agg["firm"][k]
        verdict = ""
        if mv is not None and fa is not None and higher_better is not None:
            diff = (mv - fa) if higher_better else (fa - mv)
            scale = max(abs(fa) * (0.05 if unit == "%" else 0.1), 1)
            verdict = "better" if diff > scale else "worse" if diff < -scale else "typical"
        out.append({"key": k, "label": label, "unit": unit, "me": mv, "group": ga, "firm": fa, "verdict": verdict})
    return {"row": me, "metrics": out, "rank": agg["rank"][emp_no], "of": len(ins["rows"]), "weights": WEIGHTS}
