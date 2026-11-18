"""Standalone interactive HTML board pack: one self-contained file that works offline (no server, no internet).

Built from the same pieces as the app — stylesheet (with the Inter font embedded), icon sprite, Chart.js and the table
toolkit (search, typed filters, multi-sort, group-by with subtotals, column chooser, saved views, CSV) are inlined.
Numbers come from the same services as the web pages (metrics + insights), so they always agree.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.models import Employee, Period
from app.services import branding
from app.services import insights as INS
from app.services import metrics as MX
from app.services.settings import get_general, get_policy


def _safe_json(o) -> str:
    """JSON that is safe inside <script>: every <, > and & is escaped, so a name or remark containing '</script>' or
    '<!--' can never end or confuse the script block."""
    return (json.dumps(o, default=str, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _day_tags(dd: dict) -> str:
    b = (dd.get("b1"), dd.get("b2"))
    tags = [("absent", "absent" in b or "lop" in b), ("leave", "leave" in b), ("compoff", "compoff" in b),
            ("holiday", dd.get("type") == "HOLIDAY"), ("late", dd.get("late")), ("wfh", dd.get("wfh")),
            ("missing", dd.get("missing")), ("regularised", dd.get("regularised")), ("extra", dd.get("extra")),
            ("corrected", dd.get("overridden"))]
    return " ".join(k for k, on in tags if on)


def _employees(results, ins: dict, emps: dict) -> list[dict]:
    out = []
    for r in results:
        d = r.data
        e = emps.get(r.emp_no)
        sc = INS.employee_scorecard(ins, r.emp_no)
        me = sc["row"] if sc else {}
        out.append({
            "id": r.emp_no, "name": r.name, "group": r.group or "", "loc": r.location or "",
            "doj": e.doj.isoformat() if e and e.doj else "",
            "m": {k: d.get(k) for k in (
                "total_week_days", "holidays", "leave_days", "absent_days", "days_present", "wfh_days", "total_hours",
                "extra_days", "extra_hours", "required_hours", "actual_hours", "balance_hours", "deduction_days", "exempt",
                "remark", "pl_days", "cof_days", "unauth_absent_days", "lop_days", "late_days", "very_late_days",
                "avg_late_min", "missing_punch_days", "regularised_days", "compoff_earned", "avg_in_min", "avg_out_min",
                "avg_work_hours", "leave_opening", "leave_closing", "compoff_closing", "payroll_lop_days",
                "late_sitting_days")},
            "flags": [{"s": f["severity"], "c": f["code"], "t": f["message"]} for f in r.flags],
            "sc": sc["metrics"] if sc else [],
            "att": {"score": me.get("attention", 0), "parts": me.get("attention_parts", {}), "rank": sc["rank"] if sc else 0,
                    "of": sc["of"] if sc else 0, "abs12": me.get("absence_12m_days", 0),
                    "spells12": me.get("absence_12m_spells", 0), "punct": me.get("punctuality_pct")},
            "days": [{"d": dd["date"], "t": dd["type"],
                      "s": dd["s1"] if dd["s1"] == dd["s2"] else f"{dd['s1']}/{dd['s2']}",
                      "b": dd["b1"] if dd["b1"] != "present" else dd["b2"], "i": dd["in"], "o": dd["out"],
                      "h": dd["work_min"], "late": dd["late_min"] if dd.get("late") else 0, "tags": _day_tags(dd),
                      "hol": dd.get("holiday") or "",
                      "locs": sorted({_loc_label(s) for s in dd.get("swipes", [])} - {""}),
                      "n": dd.get("notes", [])} for dd in r.days],
        })
    return out


def _loc_label(s: dict) -> str:
    base = s.get("place") or s.get("loc") or ("Office biometric" if (s.get("type") or "").lower() == "astra"
                                              else s.get("type") or "")
    return f"{base} — “{s['note']}”" if s.get("note") and base else base


def _favicon_uri() -> str:
    fav = branding.favicon_svg()
    return "data:image/svg+xml;base64," + base64.b64encode(fav).decode() if fav else ""


def _icons() -> str:
    svg = (APP_DIR / "static" / "icons.svg").read_text(encoding="utf-8")
    return svg.replace('style="display:none"', 'style="display:none" aria-hidden="true"', 1)


def _css() -> str:
    """App stylesheet with the Inter font embedded, so the standalone file looks identical offline."""
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    font = APP_DIR / "static" / "fonts" / "inter-var.woff2"
    if font.exists():
        uri = "data:font/woff2;base64," + base64.b64encode(font.read_bytes()).decode()
        css = css.replace('url("/static/fonts/inter-var.woff2")', f'url("{uri}")')
    return css


def build_board_pack(db: Session, period: Period) -> str:
    from app.web import templates   # same Jinja environment & filters as the web pages

    from sqlalchemy import select
    m = MX.period_metrics(db, period)
    ins = INS.insights(db, period)
    from app.services import locations as LOC
    loc = LOC.report(db, period)
    pol = get_policy(db)
    org = get_general(db)
    results = MX.results_for(db, period)
    emps = {e.emp_no: e for e in db.scalars(select(Employee))}
    E = _employees(results, ins, emps)
    static = APP_DIR / "static"
    tpl = templates.env.get_template("board_pack.html")
    return tpl.render(
        org=org, period=period, generated=datetime.now().strftime("%d-%b-%Y %H:%M"), m=m, I=ins, rows=results,
        D=[r.data for r in results], policy=pol, icon_base="", labels=MX.FLAG_LABELS, L=loc,
        logo_uri=branding.data_uri(), favicon_uri=_favicon_uri(),
        data_json=_safe_json({"emps": E, "labels": MX.FLAG_LABELS, "m": {k: m[k] for k in (
            "daily", "location_mix", "in_time_hist", "trend", "late_by_weekday")} | {"tenure": m["people"]["tenure_buckets"]},
            "ins": {"hours_hist": ins["hours_hist"], "groups": ins["groups"],
                    "trend": ins["trend"]},
            "policy": {"wfh_limit": pol.wfh_limit_days, "hpd": pol.hours_per_day}}),
        chart_js=(static / "vendor" / "chart.umd.min.js").read_text(encoding="utf-8"),
        table_js=(static / "table.js").read_text(encoding="utf-8").replace("</script", "<\\/script"),
        icons=_icons(), css=_css())
