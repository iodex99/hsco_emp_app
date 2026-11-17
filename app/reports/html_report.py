"""Standalone interactive HTML board pack (single file, works offline, no server needed)."""
from __future__ import annotations

import json
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.models import Period
from app.services import metrics as MX
from app.services.settings import get_general, get_policy

_env = Environment(loader=FileSystemLoader(str(APP_DIR / "templates")), autoescape=select_autoescape(["html"]))


def _safe_json(o) -> str:
    return json.dumps(o, default=str, separators=(",", ":")).replace("</", "<\\/")


def build_board_pack(db: Session, period: Period) -> str:
    m = MX.period_metrics(db, period)
    pol = get_policy(db)
    org = get_general(db)
    emps = []
    for r in MX.results_for(db, period):
        d = r.data
        emps.append({
            "id": r.emp_no, "name": r.name, "group": r.group, "loc": r.location,
            "m": {k: d.get(k) for k in (
                "total_week_days", "holidays", "leave_days", "absent_days", "days_present", "wfh_days", "partial_wfh_days",
                "total_hours", "extra_days", "extra_hours", "required_hours", "actual_hours", "balance_hours",
                "deduction_days", "exempt", "remark", "pl_days", "cof_days", "unauth_absent_days", "lop_days", "late_days",
                "very_late_days", "avg_late_min", "early_exit_days", "short_days", "late_sitting_days",
                "missing_punch_days", "regularised_days", "client_days", "onduty_days", "mobile_office_swipes",
                "compoff_earned", "sandwich_count", "avg_in_min", "avg_out_min", "avg_work_hours", "leave_opening",
                "leave_closing", "salary_deduction_days", "payroll_lop_days", "hours_per_day")},
            "flags": r.flags,
            "days": [{"d": dd["date"], "w": dd["wd"], "t": dd["type"], "s": dd["s1"] if dd["s1"] == dd["s2"] else f"{dd['s1']}/{dd['s2']}",
                      "b": dd["b1"] if dd["b1"] != "present" else dd["b2"], "i": dd["in"], "o": dd["out"], "h": dd["work_min"],
                      "late": dd["late_min"] if dd.get("late") else 0, "wfh": dd.get("wfh"), "miss": dd.get("missing"),
                      "reg": dd.get("regularised"), "x": dd.get("extra"), "ov": dd.get("overridden"),
                      "locs": [s.get("place") or s.get("loc") or ("Office biometric" if s.get("type") == "Astra" else s.get("type"))
                               for s in dd.get("swipes", [])], "n": dd.get("notes", [])} for dd in r.days],
        })
    chart_js = (APP_DIR / "static" / "vendor" / "chart.umd.min.js").read_text(encoding="utf-8")
    tpl = _env.get_template("board_pack.html")
    return tpl.render(
        org=org, period=period, generated=datetime.now().strftime("%d-%b-%Y %H:%M"),
        data_json=_safe_json({"m": m, "emps": emps, "labels": MX.FLAG_LABELS,
                              "policy": {"wfh_limit": pol.wfh_limit_days, "late_days": pol.persistent_late_days,
                                         "reg_days": pol.persistent_regularisation_days, "hpd": pol.hours_per_day,
                                         "exempt_groups": list(pol.exempt_groups)}}),
        chart_js=chart_js, css=_css())


def _css() -> str:
    """App stylesheet with the Inter font embedded, so the standalone file looks identical offline."""
    import base64
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    font = APP_DIR / "static" / "fonts" / "inter-var.woff2"
    if font.exists():
        uri = "data:font/woff2;base64," + base64.b64encode(font.read_bytes()).decode()
        css = css.replace('url("/static/fonts/inter-var.woff2")', f'url("{uri}")')
    return css
