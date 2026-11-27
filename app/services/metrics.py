"""Organisation-level metrics for the dashboard and the Excel workbook (docs/METRICS.md)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.orm import Session

from app.models import Employee, ManualMisRow, MonthResult, MusterRow, Period, PermissionRow, SummaryRow
from app.engine.compute import is_persistent_late
from app.services.compute import previous_period
from app.services.settings import get_policy

FLAG_LABELS = {
    "NO_ATTENDANCE": "No attendance all month",
    "SHORTFALL_DEDUCTION": "Hours shortfall → leave deduction",   # hours part only (v1.6.7)
    "SHORTFALL_EXEMPT": "Shortfall but exempt",
    "UNAUTHORISED_ABSENCE": "Unapplied leave (absent)",
    "WFH_OVER_LIMIT": "WFH above monthly limit",
    "PERSISTENT_LATE": "Often more than 1 h late",
    "PERSISTENT_REGULARISATION": "Persistent regularisation",
    "MOBILE_AT_OFFICE": "Mobile sign-in tagged 'Office'",
    "OFF_SITE_OFFICE": "Tagged 'Office' but not at an office",
    "SHARED_DEVICE": "Phone used to sign in for several people",
    "LATE_SITTING": "Frequent late sitting",
    "SANDWICH_PATTERN": "Leave adjacent to weekends/holidays",
    "AUTO_ATTENDANCE": "Auto attendance (no punches)",
    "GROUP_NOT_SET": "Group not set",
    "DOJ_MISSING": "Date of joining missing",
    "NEW_JOINEE": "New joinee",
    "EXIT": "Exit",
    "CALENDAR_MISMATCH": "App calendar differs from greytHR",
    "MANUAL_OVERRIDE": "Manual override applied",
    "NOT_IN_MASTER": "Not in employee master",
    "UNKNOWN_STATUS": "Unknown status code",
    # no longer raised since v1.9 (leave balances are kept in greytHR); kept so finalised months still read well
    "NO_OPENING_BALANCE": "Leave opening balance not set",
    "LEAVE_EXHAUSTED": "Leave exhausted → salary deduction",
    "COMPOFF_OVERDRAWN": "Comp-off overdrawn",
}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}
LATE_SITTING_PEOPLE_DAYS = 10     # Overview: people late-sitting on more than this many days (owner, v1.15)
HOURS_PEOPLE_LIMIT = 20           # Overview: people more than this many hours short / over (owner, v1.15)
# flags no longer raised (owner, v1.13: LOP removed; "missing punches" and "present without any punch" removed
# everywhere) — months finalised before still carry them in their stored results: never shown or counted
RETIRED_FLAGS = frozenset({"LOP", "MISSING_PUNCHES", "PRESENT_WITHOUT_PUNCH"})


def live_flags(flags) -> list:
    return [f for f in (flags or []) if f.get("code") not in RETIRED_FLAGS]


def scoped_employees(db: Session, policy=None) -> dict:
    """{emp_no: Employee} without the ignored groups (OTHERS, v1.13)."""
    from app.services.scope import employees
    return {e.emp_no: e for e in employees(db, policy)}


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
    emps = emps if emps is not None else scoped_employees(db, policy)
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
        "leave_days": tot("pl_days"),
        "compoff_days": tot("cof_days"),
        "absent_days": tot("unauth_absent_days"),
        "absent_people": sum(1 for d in D if d.get("unauth_absent_days")),
        "wfh_days": tot("wfh_days"),
        "wfh_over_limit": sum(1 for d in D if (d.get("wfh_days") or 0) > policy.wfh_limit_days),
        "wfh_people": sum(1 for d in D if (d.get("wfh_days") or 0) > 0),
        "wfh_over_days": round(sum(max(0.0, (d.get("wfh_days") or 0) - policy.wfh_limit_days) for d in D), 1),
        "late_days": sum(d.get("late_days", 0) for d in behaviour),
        "persistent_late": sum(1 for d in behaviour if is_persistent_late(d.get("late_days", 0), d.get("very_late_days", 0), policy)),
        "regularised_days": sum(d.get("regularised_days", 0) for d in behaviour),
        "persistent_regularisation": sum(1 for d in behaviour if d.get("regularised_days", 0) >= policy.persistent_regularisation_days),
        "extra_days": tot("extra_days"),
        "compoff_earned": tot("compoff_earned"),
        "total_hours": tot("actual_hours"),
        "required_hours": tot("required_hours"),
        "excess_hours": round(sum(max(0, d.get("balance_hours", 0)) for d in D), 2),
        "shortfall_hours": round(sum(max(0, -d.get("balance_hours", 0)) for d in D), 2),
        "late_sitting_days": sum(d.get("late_sitting_days", 0) for d in behaviour),
        "no_attendance": sum(1 for r in res if any(f["code"] == "NO_ATTENDANCE" for f in r.flags)),
    }
    # v1.15 (owner): figures shown on the Overview with their comparison with last month
    def _working(d):
        return (d.get("total_week_days") or 0) - (d.get("holidays") or 0)
    kpis["present_all_days"] = sum(1 for d in D if _working(d) > 0 and (d.get("days_present") or 0) >= _working(d) - 1e-9)
    kpis["late_sitting_people"] = sum(1 for d in behaviour if (d.get("late_sitting_days") or 0) > LATE_SITTING_PEOPLE_DAYS)
    kpis["shortfall_people"] = sum(1 for d in D if (d.get("balance_hours") or 0) < -HOURS_PEOPLE_LIMIT)
    kpis["excess_people"] = sum(1 for d in D if (d.get("balance_hours") or 0) > HOURS_PEOPLE_LIMIT)
    kpis["compoff_people"] = sum(1 for d in D if (d.get("compoff_earned") or 0) > 0)
    present_days = sum(d.get("days_present", 0) for d in D)
    kpis["present_days"] = round(present_days, 1)
    kpis["attendance_rate"] = round(100 * present_days / max(1, sum(d.get("total_week_days", 0) - d.get("holidays", 0) for d in D)), 1)
    hrs = [d["avg_work_hours"] for d in behaviour if d.get("avg_work_hours")]
    kpis["avg_work_hours"] = round(sum(hrs) / len(hrs), 2) if hrs else 0
    ins = [d["avg_in_min"] for d in behaviour if d.get("avg_in_min")]
    kpis["avg_in_time"] = hm(sum(ins) / len(ins)) if ins else ""
    kpis["avg_in_min"] = round(sum(ins) / len(ins), 1) if ins else None
    outs = [d["avg_out_min"] for d in behaviour if d.get("avg_out_min")]
    kpis["avg_out_time"] = hm(sum(outs) / len(outs)) if outs else ""
    kpis["avg_out_min"] = round(sum(outs) / len(outs), 1) if outs else None

    return kpis


def _breakdown(D: list[dict], by: str) -> list[dict]:
    """Group / category table: headcount and totals per value of `by` (blank → "(not set)")."""
    groups = defaultdict(lambda: Counter())
    for d in D:
        c = groups[d.get(by) or "(not set)"]
        c["headcount"] += 1
        for k in ("days_present", "pl_days", "cof_days", "unauth_absent_days", "wfh_days", "late_days",
                  "regularised_days", "deduction_days", "actual_hours", "required_hours", "extra_days"):
            c[k] += d.get(k) or 0
        c["deduction_emps"] += 1 if d.get("deduction_days") else 0
    rows = []
    for g, c in sorted(groups.items()):
        row = {"group": g, **{k: round(v, 2) for k, v in c.items()}}
        row["hours_vs_required_pct"] = round(100 * c["actual_hours"] / c["required_hours"], 1) if c["required_hours"] else None
        rows.append(row)
    return rows


WD_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _ranked(c: Counter) -> list:
    """[[emp_no, value], …] highest first — who is behind one bar of a chart (the page opens it as a list)."""
    return [[k, round(v, 2)] for k, v in sorted(c.items(), key=lambda x: (-x[1], x[0]))]


# first punch of the day (owner, v1.16): people by their usual (average) first punch, in four ranges
ARRIVAL_RANGES = [("09:30–10:00", 570, 600), ("10:00–10:30", 600, 630), ("10:30–11:00", 630, 660),
                  ("After 11:00", 660, 24 * 60 * 2)]
# "Permission" (owner, v1.16): a heading with WFH and On Duty under it; punches tagged Permission are not shown
PERMISSION_KINDS = ("WFH", "On Duty")
PUNCH_PERMISSION = {"work from home": "WFH", "wfh": "WFH", "on-duty": "On Duty", "on duty": "On Duty"}


def arrival_ranges(D: list[dict]) -> dict:
    """{"rows": [{"bucket", "count"}], "who": {bucket: [[emp, "hh:mm"], …]}, "earlier": n} — each person once, by
    their average first punch on days present."""
    who = {lab: [] for lab, _, _ in ARRIVAL_RANGES}
    earlier = 0
    for d in sorted(D, key=lambda d: d.get("avg_in_min") or 0):
        a = d.get("avg_in_min")
        if not a:
            continue
        if a < ARRIVAL_RANGES[0][1]:
            earlier += 1
            continue
        for lab, lo, hi in ARRIVAL_RANGES:
            if lo <= a < hi:
                who[lab].append([d["emp_no"], hm(a)])
                break
    return {"rows": [{"bucket": lab, "count": len(who[lab])} for lab, _, _ in ARRIVAL_RANGES], "who": who,
            "earlier": earlier}


def permission_mix(db: Session, period: Period, res, excl: set) -> dict:
    """WFH and On Duty under "Permission": from the Permission Report when one is imported for the month (approved
    or applied requests; withdrawn / rejected left out), else from sign-ins tagged Work from home / On-duty.
    Each: people and days (instances)."""
    from app.importers.parsers import PERMISSION_COUNTED
    scope = {r.emp_no for r in res if r.group not in excl}
    rows = list(db.scalars(select(PermissionRow).where(PermissionRow.period_id == period.id)))
    who = {k: defaultdict(set) for k in PERMISSION_KINDS}
    if rows:
        source = "report"
        for r in rows:
            if r.ptype in who and r.emp_no in scope and (r.status or "").strip().lower() in PERMISSION_COUNTED:
                who[r.ptype][r.emp_no].add(r.day)
    else:
        source = "punches"
        for r in res:
            if r.emp_no not in scope:
                continue
            for dd in r.days:
                for sw in dd.get("swipes", []):
                    k = PUNCH_PERMISSION.get((sw.get("loc") or "").strip().lower())
                    if k:
                        who[k][r.emp_no].add(dd["date"])
    out = [{"label": k, "people": len(who[k]), "count": sum(len(v) for v in who[k].values())} for k in PERMISSION_KINDS]
    return {"source": source if any(x["count"] for x in out) else "", "rows": out,
            "who": {k: _ranked(Counter({e: len(v) for e, v in who[k].items()})) for k in PERMISSION_KINDS}}


def loc_label(s: dict) -> str:
    return s.get("loc") or ("Office (biometric)" if (s.get("type") or "").lower() == "astra" else s.get("type") or "Other")


def period_metrics(db: Session, period: Period) -> dict:
    policy = get_policy(db)
    res = results_for(db, period)
    emps = scoped_employees(db, policy)
    D = [r.data for r in res]
    kpis = compute_kpis(db, period, policy, res, emps)
    prev = previous_period(db, period)
    prev_kpis = compute_kpis(db, prev, policy, None, emps) if prev and prev.computed_at else None

    # flags (retired codes of months finalised before v1.13 are left out)
    flag_counts = Counter()
    flag_rows = []
    for r in res:
        for f in live_flags(r.flags):
            flag_counts[(f["code"], f["severity"])] += 1
            flag_rows.append({"emp_no": r.emp_no, "name": r.name, "group": r.group, **f})
    flag_rows.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["code"], f["emp_no"]))
    flag_summary = [{"code": c, "label": FLAG_LABELS.get(c, c), "severity": s, "count": n}
                    for (c, s), n in sorted(flag_counts.items(), key=lambda x: (SEV_ORDER.get(x[0][1], 9), -x[1]))]

    group_rows = _breakdown(D, "group")

    # charts, and who is behind every bar (drill-down, v1.13)
    loc_mix, loc_who = Counter(), defaultdict(Counter)
    late_by_wd, late_who = Counter(), defaultdict(Counter)
    daily = defaultdict(lambda: Counter())
    daily_who = defaultdict(lambda: defaultdict(Counter))
    excl = set(policy.metrics_exclude_groups)
    for r in res:
        if r.group in excl:
            continue
        for dd in r.days:
            for s in dd.get("swipes", []):
                lab = loc_label(s)
                low = lab.strip().lower()
                if low == "permission" or low in PUNCH_PERMISSION:
                    continue                    # under "Permission" (WFH / On Duty) — v1.16
                loc_mix[lab] += 1
                loc_who[lab][r.emp_no] += 1
            # late arrivals by weekday: more than 1 h late (owner, v1.16; was the 15-minute grace)
            if dd.get("very_late") or (dd.get("late_min") or 0) > policy.very_late_min:
                late_by_wd[dd["wd"]] += 1
                late_who[dd["wd"]][r.emp_no] += 1
            dc, dw = daily[dd["date"]], daily_who[dd["date"]]
            if dd["type"] == "WORKDAY":
                for b in (dd["b1"], dd["b2"]):
                    b = "leave" if b in ("compoff", "lop") else b      # chart series: present / leave / absent
                    if b == "present" and dd.get("wfh"):
                        b = "wfh"            # a bar of its own (owner, v1.16): present in office / WFH / leave / absent
                    dc[b] += 0.5
                    dw[b][r.emp_no] += 0.5
                if dd.get("late"):
                    dc["late"] += 1

    people = movement(db, period, emps)
    who = {r.emp_no: [r.name, r.group or ""] for r in res}
    for k in ("tenure_who",):
        for rows in people[k].values():
            for e, _v in rows:
                if e not in who and e in emps:
                    who[e] = [emps[e].name or e, emps[e].group or ""]
    wfh_people = sorted(({"emp_no": d["emp_no"], "name": d["name"], "group": d.get("group") or "",
                          "value": round(d.get("wfh_days") or 0, 2),
                          "over": round(max(0.0, (d.get("wfh_days") or 0) - policy.wfh_limit_days), 2),
                          "approved": d.get("wfh_approved_days") or 0, "deduction": d.get("wfh_deduction_days") or 0}
                         for d in D if (d.get("wfh_days") or 0) > 0), key=lambda x: (-x["value"], x["name"]))
    loc_total = sum(loc_mix.values())
    arrivals = arrival_ranges([d for d in D if d.get("group") not in excl])
    perm = permission_mix(db, period, res, excl)
    return {
        "period": {"key": period.key, "label": period.label, "status": period.status},
        "kpis": kpis,
        "prev_kpis": prev_kpis,
        "prev_label": prev.label if prev and prev_kpis else "",
        "flag_summary": flag_summary,
        "flags": flag_rows,
        "groups": group_rows,
        "location_mix": [{"label": k, "count": v, "people": len(loc_who[k]),
                          "pct": round(100 * v / loc_total, 1) if loc_total else 0}
                         for k, v in sorted(loc_mix.items(), key=lambda x: (-len(loc_who[x[0]]), -x[1]))],
        "loc_people": len({e for c in loc_who.values() for e in c}),
        "permission_mix": {"source": perm["source"], "rows": perm["rows"]},
        "late_by_weekday": [{"wd": w, "count": late_by_wd.get(w, 0)} for w in WD_ORDER],
        "in_time_hist": arrivals["rows"],
        "arrivals_earlier": arrivals["earlier"],
        "daily": [{"date": k, "wd": WD_ORDER[date.fromisoformat(k).weekday()],
                   **{kk: round(vv, 1) for kk, vv in v.items()}} for k, v in sorted(daily.items())],
        "people": {k: v for k, v in people.items() if k != "tenure_who"},
        "top": top_lists(D, policy),
        "wfh_people": wfh_people,
        "present_all": [[d["emp_no"], d.get("days_present") or 0] for d in D
                        if (d.get("total_week_days") or 0) - (d.get("holidays") or 0) > 0
                        and (d.get("days_present") or 0) >= (d.get("total_week_days") or 0) - (d.get("holidays") or 0) - 1e-9],
        "trend": trend(db),
        "who": who,
        # per person, for "who is in this group" (By group rows): present, leave, comp-off, unapplied leave, WFH, late
        # days, regularised days, deduction days
        "person_rows": [[d["emp_no"], d.get("days_present") or 0, d.get("pl_days") or 0, d.get("cof_days") or 0,
                         d.get("unauth_absent_days") or 0, d.get("wfh_days") or 0, d.get("late_days") or 0,
                         d.get("regularised_days") or 0, d.get("deduction_days") or 0] for d in D],
        "drill": {
            "daily": {d: {b: _ranked(c) for b, c in v.items()} for d, v in daily_who.items()},
            "loc": {k: _ranked(c) for k, c in loc_who.items()},
            "late_wd": {k: _ranked(c) for k, c in late_who.items()},
            "in_time": arrivals["who"],
            "perm": perm["who"],
            "tenure": people["tenure_who"],
        },
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
        "tenure_who": _tenure(emps, month_end, who=True),
    }


def _tenure(emps: dict, asof: date, who: bool = False):
    """Active employees by length of service; who=True: {bucket: [[emp_no, years], …]} (chart drill-down)."""
    b = Counter()
    w = defaultdict(list)
    order = ["< 6 months", "6-12 months", "1-3 years", "3-5 years", "5-10 years", "10+ years", "unknown"]
    for e in emps.values():
        if e.dol or not e.is_active:
            continue
        if not e.doj:
            b["unknown"] += 1
            w["unknown"].append([e.emp_no, None])
            continue
        yrs = (asof - e.doj).days / 365.25
        k = (order[0] if yrs < .5 else order[1] if yrs < 1 else order[2] if yrs < 3 else order[3] if yrs < 5
             else order[4] if yrs < 10 else order[5])
        b[k] += 1
        w[k].append([e.emp_no, round(yrs, 1)])
    if who:
        return {k: sorted(v, key=lambda x: -(x[1] or 0)) for k, v in w.items()}
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
        "days_present", "pl_days", "cof_days", "unauth_absent_days", "wfh_days", "late_days", "regularised_days",
        "actual_hours", "required_hours", "balance_hours", "deduction_days",
        "avg_in_min", "avg_work_hours", "extra_days")}} for p, r in rows]


RECON_FIELDS = [
    ("total_week_days", "Total Week Days"), ("holidays", "Holiday"), ("leave_days", "Leaves"),
    ("absent_days", "Absent"), ("days_present", "Present"), ("wfh_days", "WFH"), ("total_hours", "Total Hours"),
    ("extra_days", "Extra Days"), ("extra_hours", "Holiday Hours"), ("required_hours", "Required Hours"),
    ("actual_hours", "Actual Hours"), ("balance_hours", "Shortfall/Excess"),
]


def reconciliation(db: Session, period: Period) -> dict:
    """Compare app results to (a) the uploaded manual MIS and (b) the greytHR Summary report."""
    from app.services.scope import ignored_emp_nos
    skip = ignored_emp_nos(db)                         # OTHERS: not compared (v1.13)
    res = {r.emp_no: r for r in results_for(db, period, with_days=False)}
    manual = {r.emp_no: r.data for r in db.scalars(select(ManualMisRow).where(ManualMisRow.period_id == period.id))
              if r.emp_no not in skip}
    summ = {r.emp_no: r.data for r in db.scalars(select(SummaryRow).where(SummaryRow.period_id == period.id))
            if r.emp_no not in skip}
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
