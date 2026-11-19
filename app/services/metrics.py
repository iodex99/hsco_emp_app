"""Organisation-level metrics for the dashboard and the Excel workbook (docs/METRICS.md)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.orm import Session

from app.models import Employee, ManualMisRow, MonthResult, MusterRow, Period, SummaryRow
from app.engine.compute import is_persistent_late
from app.services.compute import previous_period
from app.services.settings import get_policy

FLAG_LABELS = {
    "NO_ATTENDANCE": "No attendance all month",
    "SHORTFALL_DEDUCTION": "Hours shortfall → leave deduction",   # hours part only (v1.6.7)
    "SHORTFALL_EXEMPT": "Shortfall but exempt",
    "UNAUTHORISED_ABSENCE": "Unauthorised absence",
    "WFH_OVER_LIMIT": "WFH above monthly limit",
    "PERSISTENT_LATE": "Often more than 1 h late",
    "PERSISTENT_REGULARISATION": "Persistent regularisation",
    "MISSING_PUNCHES": "Repeated missing punches",
    "MOBILE_AT_OFFICE": "Mobile sign-in tagged 'Office'",
    "OFF_SITE_OFFICE": "Tagged 'Office' but not at an office",
    "SHARED_DEVICE": "Phone used to sign in for several people",
    "LATE_SITTING": "Frequent late sitting",
    "SANDWICH_PATTERN": "Leave adjacent to weekends/holidays",
    "LOP": "Loss of pay",
    "AUTO_ATTENDANCE": "Auto attendance (no punches)",
    "PRESENT_WITHOUT_PUNCH": "Present without any punch",
    "GROUP_NOT_SET": "Group not set",
    "DOJ_MISSING": "Date of joining missing",
    "NEW_JOINEE": "New joinee",
    "EXIT": "Exit",
    "CALENDAR_MISMATCH": "App calendar differs from greytHR",
    "MANUAL_OVERRIDE": "Manual override applied",
    "NOT_IN_MASTER": "Not in employee master",
    "UNKNOWN_STATUS": "Unknown status code",
    "NO_OPENING_BALANCE": "Leave opening balance not set",
    "LEAVE_EXHAUSTED": "Leave exhausted → salary deduction",
    "COMPOFF_OVERDRAWN": "Comp-off overdrawn",
}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def hm(minutes: Optional[float]) -> str:
    if minutes is None:
        return ""
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def results_for(db: Session, period: Period, with_days: bool = True) -> list[MonthResult]:
    """with_days=False skips the day-by-day detail (~20 KB JSON per row) when only figures and flags are needed."""
    q = select(MonthResult).where(MonthResult.period_id == period.id).order_by(MonthResult.emp_no)
    if not with_days:
        q = q.options(defer(MonthResult.days))
    return list(db.scalars(q))


def compute_kpis(db: Session, period: Period, policy=None, res=None, emps=None) -> dict:
    policy = policy or get_policy(db)
    res = res if res is not None else results_for(db, period, with_days=False)
    emps = emps if emps is not None else {e.emp_no: e for e in db.scalars(select(Employee))}
    y, m = period.year, period.month
    month_start = date(y, m, 1)
    month_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    D = [r.data for r in res]

    def tot(k):
        return round(sum((d.get(k) or 0) for d in D), 2)

    behaviour = [d for d in D if d.get("group") not in set(policy.metrics_exclude_groups)]
    kpis = {
        "headcount": len(D),
        "new_joinees": sum(1 for e in emps.values() if e.doj and month_start <= e.doj <= month_end),
        "exits": sum(1 for e in emps.values() if e.dol and month_start <= e.dol <= month_end),
        "deduction_employees": sum(1 for d in D if d.get("deduction_days")),
        "deduction_days": tot("deduction_days"),
        "salary_deduction_days": tot("salary_deduction_days"),
        "payroll_lop_days": tot("payroll_lop_days"),
        "leave_days": tot("pl_days"),
        "compoff_days": tot("cof_days"),
        "absent_days": tot("unauth_absent_days"),
        "lop_days": tot("lop_days"),
        "wfh_days": tot("wfh_days"),
        "wfh_over_limit": sum(1 for d in D if (d.get("wfh_days") or 0) > policy.wfh_limit_days),
        "late_days": sum(d.get("late_days", 0) for d in behaviour),
        "persistent_late": sum(1 for d in behaviour if is_persistent_late(d.get("late_days", 0), d.get("very_late_days", 0), policy)),
        "regularised_days": sum(d.get("regularised_days", 0) for d in behaviour),
        "persistent_regularisation": sum(1 for d in behaviour if d.get("regularised_days", 0) >= policy.persistent_regularisation_days),
        "missing_punch_days": sum(d.get("missing_punch_days", 0) for d in behaviour),
        "extra_days": tot("extra_days"),
        "compoff_earned": tot("compoff_earned"),
        "total_hours": tot("actual_hours"),
        "required_hours": tot("required_hours"),
        "excess_hours": round(sum(max(0, d.get("balance_hours", 0)) for d in D), 2),
        "shortfall_hours": round(sum(max(0, -d.get("balance_hours", 0)) for d in D), 2),
        "late_sitting_days": sum(d.get("late_sitting_days", 0) for d in behaviour),
        "no_attendance": sum(1 for r in res if any(f["code"] == "NO_ATTENDANCE" for f in r.flags)),
    }
    present_days = sum(d.get("days_present", 0) for d in D)
    kpis["attendance_rate"] = round(100 * present_days / max(1, sum(d.get("total_week_days", 0) - d.get("holidays", 0) for d in D)), 1)
    hrs = [d["avg_work_hours"] for d in behaviour if d.get("avg_work_hours")]
    kpis["avg_work_hours"] = round(sum(hrs) / len(hrs), 2) if hrs else 0
    ins = [d["avg_in_min"] for d in behaviour if d.get("avg_in_min")]
    kpis["avg_in_time"] = hm(sum(ins) / len(ins)) if ins else ""
    outs = [d["avg_out_min"] for d in behaviour if d.get("avg_out_min")]
    kpis["avg_out_time"] = hm(sum(outs) / len(outs)) if outs else ""

    return kpis


def _breakdown(D: list[dict], by: str) -> list[dict]:
    """Group / category table: headcount and totals per value of `by` (blank → "(not set)")."""
    groups = defaultdict(lambda: Counter())
    for d in D:
        c = groups[d.get(by) or "(not set)"]
        c["headcount"] += 1
        for k in ("days_present", "pl_days", "cof_days", "unauth_absent_days", "lop_days", "wfh_days", "late_days",
                  "regularised_days", "deduction_days", "actual_hours", "required_hours", "extra_days"):
            c[k] += d.get(k) or 0
        c["deduction_emps"] += 1 if d.get("deduction_days") else 0
    rows = []
    for g, c in sorted(groups.items()):
        row = {"group": g, **{k: round(v, 2) for k, v in c.items()}}
        row["hours_vs_required_pct"] = round(100 * c["actual_hours"] / c["required_hours"], 1) if c["required_hours"] else None
        rows.append(row)
    return rows


def period_metrics(db: Session, period: Period) -> dict:
    policy = get_policy(db)
    res = results_for(db, period)
    emps = {e.emp_no: e for e in db.scalars(select(Employee))}
    y, m = period.year, period.month
    D = [r.data for r in res]
    kpis = compute_kpis(db, period, policy, res, emps)
    prev = previous_period(db, period)
    prev_kpis = compute_kpis(db, prev, policy, None, emps) if prev and prev.computed_at else None

    # flags
    flag_counts = Counter()
    flag_rows = []
    for r in res:
        for f in r.flags:
            flag_counts[(f["code"], f["severity"])] += 1
            flag_rows.append({"emp_no": r.emp_no, "name": r.name, "group": r.group, **f})
    flag_rows.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["code"], f["emp_no"]))
    flag_summary = [{"code": c, "label": FLAG_LABELS.get(c, c), "severity": s, "count": n}
                    for (c, s), n in sorted(flag_counts.items(), key=lambda x: (SEV_ORDER.get(x[0][1], 9), -x[1]))]

    group_rows = _breakdown(D, "group")
    category_rows = _breakdown(D, "category")

    # location mix
    loc_mix = Counter()
    late_by_wd = Counter()
    in_hist = Counter()
    daily = defaultdict(lambda: Counter())
    for r in res:
        if r.group in set(policy.metrics_exclude_groups):
            continue
        for dd in r.days:
            for s in dd.get("swipes", []):
                loc_mix[s["loc"] or ("Office (biometric)" if s["type"].lower() == "astra" else s["type"] or "Other")] += 1
            if dd.get("late"):
                late_by_wd[dd["wd"]] += 1
            if dd.get("in") and dd["type"] == "WORKDAY" and dd["b1"] == "present":
                h, mi = map(int, dd["in"].split(":"))
                bucket = f"{h:02d}:{(mi // 30) * 30:02d}"
                in_hist[bucket] += 1
            dc = daily[dd["date"]]
            if dd["type"] == "WORKDAY":
                for b in (dd["b1"], dd["b2"]):
                    dc[b] += 0.5
                if dd.get("wfh"):
                    dc["wfh"] += 1
                if dd.get("late"):
                    dc["late"] += 1
    wd_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    people = movement(db, period, emps)
    return {
        "period": {"key": period.key, "label": period.label, "status": period.status},
        "kpis": kpis,
        "prev_kpis": prev_kpis,
        "prev_label": prev.label if prev and prev_kpis else "",
        "flag_summary": flag_summary,
        "flags": flag_rows,
        "groups": group_rows,
        "categories": category_rows,
        "location_mix": [{"label": k, "count": v} for k, v in loc_mix.most_common()],
        "late_by_weekday": [{"wd": w, "count": late_by_wd.get(w, 0)} for w in wd_order],
        "in_time_hist": [{"bucket": k, "count": in_hist[k]} for k in sorted(in_hist)],
        "daily": [{"date": k, **{kk: round(vv, 1) for kk, vv in v.items()}} for k, v in sorted(daily.items())],
        "people": people,
        "top": top_lists(D, policy),
        "trend": trend(db),
    }


def top_lists(D: list[dict], policy) -> dict:
    beh = [d for d in D if d.get("group") not in set(policy.metrics_exclude_groups)]

    def pick(rows, key, minv=0.0001):
        """Everyone with a value of at least minv, highest first (the page shows the top N and can search)."""
        return [{"emp_no": d["emp_no"], "name": d["name"], "group": d.get("group", ""), "value": round(d.get(key) or 0, 2)}
                for d in sorted(rows, key=lambda d: (-(d.get(key) or 0), d["name"])) if (d.get(key) or 0) >= minv]
    return {
        "late": pick(beh, "very_late_days", minv=1),
        "wfh": pick(D, "wfh_days", minv=policy.wfh_limit_days + 0.001),
        "regularised": pick(beh, "regularised_days", minv=1),
        "missing_punch": pick(beh, "missing_punch_days", minv=1),
        "absent": pick(D, "unauth_absent_days", minv=0.5),
        "excess_hours": pick(D, "balance_hours", minv=0.01),
        "shortfall": [{"emp_no": d["emp_no"], "name": d["name"], "group": d.get("group", ""),
                       "value": round(-d["balance_hours"], 2),
                       "deduction": d.get("shortfall_deduction_days", d.get("deduction_days", 0))}
                      for d in sorted(D, key=lambda d: d.get("balance_hours", 0)) if d.get("balance_hours", 0) < 0],
        "late_sitting": pick(beh, "late_sitting_days", minv=1),
        "compoff_earned": pick(D, "compoff_earned", minv=1),
        "sandwich": pick(D, "sandwich_count", minv=1),
    }


def movement(db: Session, period: Period, emps: dict) -> dict:
    y, m = period.year, period.month
    month_start = date(y, m, 1)
    month_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    joiners = sorted([e for e in emps.values() if e.doj and month_start <= e.doj <= month_end], key=lambda e: e.doj)
    exits = sorted([e for e in emps.values() if e.dol and month_start <= e.dol <= month_end], key=lambda e: e.dol)
    anniv = sorted([e for e in emps.values() if e.doj and e.doj.month == m and e.doj.year < y and not e.dol],
                   key=lambda e: e.doj.day)
    # exit candidates: in previous month's muster but not this month's, and no DOL
    cur = set(db.scalars(select(MusterRow.emp_no).where(MusterRow.period_id == period.id).distinct()))
    prev = previous_period(db, period)
    cand = []
    if prev:
        prv = set(db.scalars(select(MusterRow.emp_no).where(MusterRow.period_id == prev.id).distinct()))
        cand = [emps[e] for e in sorted(prv - cur) if e in emps and not emps[e].dol]
    # recent joiners (last 6 months) = probation watch
    six = date(y, m, 1) - timedelta(days=183)
    recent = sorted([e for e in emps.values() if e.doj and six <= e.doj <= month_end and not e.dol], key=lambda e: e.doj)
    headcount_by_group = Counter((e.group or "(not set)") for e in emps.values() if e.is_active and not e.dol)

    def row(e, extra=None):
        return {"emp_no": e.emp_no, "name": e.name, "group": e.group, "location": e.location,
                "doj": e.doj.isoformat() if e.doj else "", "dol": e.dol.isoformat() if e.dol else "", **(extra or {})}
    return {
        "joiners": [row(e) for e in joiners],
        "exits": [row(e) for e in exits],
        "exit_candidates": [row(e) for e in cand],
        "anniversaries": [row(e, {"years": y - e.doj.year}) for e in anniv],
        "recent_joiners": [row(e) for e in recent],
        "headcount_by_group": dict(sorted(headcount_by_group.items())),
        "tenure_buckets": _tenure(emps, month_end),
    }


def _tenure(emps: dict, asof: date) -> list[dict]:
    b = Counter()
    order = ["< 6 months", "6-12 months", "1-3 years", "3-5 years", "5-10 years", "10+ years", "unknown"]
    for e in emps.values():
        if e.dol or not e.is_active:
            continue
        if not e.doj:
            b["unknown"] += 1
            continue
        yrs = (asof - e.doj).days / 365.25
        b[order[0] if yrs < .5 else order[1] if yrs < 1 else order[2] if yrs < 3 else order[3] if yrs < 5
          else order[4] if yrs < 10 else order[5]] += 1
    return [{"bucket": k, "count": b.get(k, 0)} for k in order]


def trend(db: Session, n: int = 12) -> list[dict]:
    periods = list(db.scalars(select(Period).order_by(Period.year.desc(), Period.month.desc()).limit(n)))[::-1]
    by_p: dict = defaultdict(list)
    if periods:
        for pid, data in db.execute(select(MonthResult.period_id, MonthResult.data)
                                    .where(MonthResult.period_id.in_([p.id for p in periods]))):
            by_p[pid].append(data or {})
    out = []
    for p in periods:
        D = by_p.get(p.id, [])
        if not D:
            continue
        out.append({
            "key": p.key, "label": p.label, "headcount": len(D),
            "deduction_days": round(sum(d.get("deduction_days", 0) for d in D), 2),
            "late_days": sum(d.get("late_days", 0) for d in D),
            "wfh_days": round(sum(d.get("wfh_days", 0) for d in D), 1),
            "absent_days": round(sum(d.get("unauth_absent_days", 0) for d in D), 1),
            "leave_days": round(sum(d.get("pl_days", 0) for d in D), 1),
            "regularised_days": sum(d.get("regularised_days", 0) for d in D),
            "excess_hours": round(sum(max(0, d.get("balance_hours", 0)) for d in D), 1),
        })
    return out


def employee_history(db: Session, emp_no: str) -> list[dict]:
    rows = db.execute(select(Period, MonthResult).join(MonthResult, MonthResult.period_id == Period.id)
                      .where(MonthResult.emp_no == emp_no).order_by(Period.year, Period.month)).all()
    return [{"key": p.key, "label": p.label, **{k: r.data.get(k) for k in (
        "days_present", "pl_days", "cof_days", "unauth_absent_days", "lop_days", "wfh_days", "late_days", "regularised_days",
        "missing_punch_days", "actual_hours", "required_hours", "balance_hours", "deduction_days", "leave_closing",
        "avg_in_min", "avg_work_hours", "extra_days")}} for p, r in rows]


RECON_FIELDS = [
    ("total_week_days", "Total Week Days"), ("holidays", "Holiday"), ("leave_days", "Leaves"),
    ("absent_days", "Absent"), ("days_present", "Present"), ("wfh_days", "WFH"), ("total_hours", "Total Hours"),
    ("extra_days", "Extra Days"), ("extra_hours", "Holiday Hours"), ("required_hours", "Required Hours"),
    ("actual_hours", "Actual Hours"), ("balance_hours", "Shortfall/Excess"),
]


def reconciliation(db: Session, period: Period) -> dict:
    """Compare app results to (a) the uploaded manual MIS and (b) the greytHR Summary report."""
    res = {r.emp_no: r for r in results_for(db, period, with_days=False)}
    manual = {r.emp_no: r.data for r in db.scalars(select(ManualMisRow).where(ManualMisRow.period_id == period.id))}
    summ = {r.emp_no: r.data for r in db.scalars(select(SummaryRow).where(SummaryRow.period_id == period.id))}
    rows = []
    for emp_no in sorted(res.keys() | manual.keys()):
        r = res.get(emp_no)
        mrow = manual.get(emp_no)
        diffs = []
        if r and mrow:
            for k, label in RECON_FIELDS:
                mv, av = mrow.get(k), r.data.get(k)
                if mv is None or av is None:
                    continue
                if abs(float(mv) - float(av)) > 0.02:
                    d = {"field": label, "manual": round(float(mv), 2), "app": round(float(av), 2), "rule": ""}
                    off = (r.data.get("offday_wfh_days") or 0) + (r.data.get("leaveday_wfh_days") or 0) +                         (r.data.get("office_closed_wfh_days") or 0)
                    if k == "wfh_days" and off and abs(float(mv) - float(av) - off) <= 0.51:
                        # HR counts WFH on weekly offs / holidays / leave days; the app does not (BUSINESS_RULES §6)
                        d["rule"] = (f"{off} WFH day(s) on a weekly off, holiday, leave day or a Saturday the office "
                                     f"is closed — not counted under the app's rules")
                    diffs.append(d)
            mremark = (mrow.get("remark") or "").lower()
            mded = 0.0
            if "deduction" in mremark:
                try:
                    mded = float(mremark.split()[0])
                except ValueError:
                    mded = 0.0
            if abs(mded - (r.data.get("deduction_days") or 0)) > 0.001:
                wd = r.data.get("wfh_deduction_days") or 0
                rule = (f"{wd:g} day(s) for {r.data.get('wfh_excess_days') or 0:g} WFH day(s) over the limit, "
                        f"not deducted in the manual sheet") if wd and abs((r.data.get("deduction_days") or 0) - wd - mded) <= 0.001 else ""
                diffs.append({"field": "Deduction days", "manual": mded, "app": r.data.get("deduction_days"), "rule": rule})
        rows.append({"emp_no": emp_no, "name": (r.name if r else mrow.get("name", "")),
                     "in_app": bool(r), "in_manual": bool(mrow), "diffs": diffs,
                     "manual_remark": (mrow or {}).get("remark", ""), "app_remark": r.data.get("remark", "") if r else ""})
    summ_diffs = []
    for emp_no, s in summ.items():
        r = res.get(emp_no)
        if not r:
            continue
        try:
            wk = float(s.get("week_days") or 0)
        except ValueError:
            wk = 0
        if abs(wk - r.data.get("total_week_days", 0)) > 0.01:
            summ_diffs.append({"emp_no": emp_no, "name": r.name, "field": "Total Week Days",
                               "greythr": wk, "app": r.data.get("total_week_days")})
    return {"has_manual": bool(manual), "has_summary": bool(summ), "rows": rows,
            "matched": sum(1 for x in rows if x["in_app"] and x["in_manual"] and not x["diffs"]),
            "rule_only": sum(1 for x in rows if x["diffs"] and all(d["rule"] for d in x["diffs"])),
            "compared": sum(1 for x in rows if x["in_app"] and x["in_manual"]),
            "summary_diffs": summ_diffs}
