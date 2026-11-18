"""Excel workbook for a period: payroll leave sheet (MIS layout, live formulas) + board metric sheets."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, MonthOverrideRow, Period
from app.services import metrics as MX
from app.services.settings import get_general, get_policy

NAVY = "1F3A5F"
HDR = PatternFill("solid", fgColor=NAVY)
HFONT = Font(color="FFFFFF", bold=True)
OVR = PatternFill("solid", fgColor="FFF2CC")
RED = PatternFill("solid", fgColor="F8D7DA")
AMBER = PatternFill("solid", fgColor="FFE5B4")
GREEN = PatternFill("solid", fgColor="D4EDDA")
GREY = PatternFill("solid", fgColor="EEF1F5")
THIN = Border(bottom=Side(style="thin", color="D0D7E2"))
TEXT_COLS = {1, 2, 3, 4, 19, 34}   # payroll sheet columns holding imported text (never formulas)
SEV_FILL = {"critical": RED, "high": AMBER, "medium": PatternFill("solid", fgColor="FFF7D6"), "info": GREY}


def _thin(cell) -> None:
    """Same as cell.border = THIN, but registers the border once per workbook instead of hashing a Border object for
    every cell (the dominant cost on large sheets)."""
    wb = cell.parent.parent
    bid = getattr(wb, "_hsco_thin_id", None)
    if bid is None:
        bid = wb._hsco_thin_id = wb._borders.add(THIN)
    if not cell._style:
        from openpyxl.styles.cell_style import StyleArray
        cell._style = StyleArray()
    cell._style.borderId = bid


def _put(ws, row: int, col: int, value, text: bool = False):
    """Write a bordered cell. text=True: imported text is never turned into a formula (=, +, -, @ prefixes)."""
    c = ws.cell(row=row, column=col, value=value)
    if text and isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        c.data_type = "s"
    _thin(c)
    return c


def _day_places(dd: dict) -> str:
    """'09:58 Office biometric; 19:05 Andheri East, Mumbai Suburban — “At Acme”' for one day's punches."""
    out = []
    for s in dd.get("swipes", []):
        where = s.get("place") or ("Office biometric" if (s.get("type") or "").lower() == "astra"
                                   else s.get("loc") or s.get("type") or "")
        if s.get("note"):
            where += f" — “{s['note']}”"
        out.append(f"{(s.get('t') or '')[:5]} {where}".strip())
    return "; ".join(out)


def build_locations_workbook(db: Session, period: Period) -> bytes:
    """Every punch of a month with where it was made — works before the month is calculated (no muster needed)."""
    from app.models import SwipeRow
    from app.services import locations as LOC
    org = get_general(db)
    L = LOC.report(db, period)
    names = {e.emp_no: e.name for e in db.scalars(select(Employee))}
    mobile = {(r["emp_no"], r["ts"]): r for r in L["rows"]}
    wb = Workbook()
    ws = wb.active
    ws.title = "Punches"
    rows = []
    for s in db.scalars(select(SwipeRow).where(SwipeRow.period_id == period.id).order_by(SwipeRow.ts, SwipeRow.emp_no)):
        r = mobile.get((s.emp_no, s.ts))
        if r:
            rows.append([s.ts, s.emp_no, names.get(s.emp_no, s.emp_no), r["swipe_type"] or "Mobile", r["type"], r["place"],
                         r["note"], r["office_check"], r["reason"], r["device"], r["approval"], s.lat, s.lon])
        else:
            rows.append([s.ts, s.emp_no, names.get(s.emp_no, s.emp_no), s.swipe_type or "", s.location_type or "",
                         s.place or ("Office biometric" + (f" ({s.door})" if s.door else "")), s.note or "", "",
                         s.reason or "", s.device or "", s.approval or "", s.lat, s.lon])
    _table(ws, 5, ["When", "Employee No", "Name", "Punch type", "Tagged as", "Place", "Remark", "Office check",
                   "Permission reason", "Phone", "Status", "Latitude", "Longitude"], rows,
           [16, 10, 22, 13, 15, 46, 30, 15, 22, 16, 10, 11, 11], org,
           f"Punches and where they were made — {period.label} (offline place names; named places take priority)")
    for i, r in enumerate(rows, start=6):
        ws.cell(row=i, column=1).number_format = "dd-mmm-yyyy hh:mm"
        if r[7] == "Away from office":
            ws.cell(row=i, column=8).fill = RED
    k = L["kpis"]
    ws.cell(row=4, column=1, value=(f"{len(rows)} punches: {k['mobile']} mobile sign-ins ({k['with_gps']} with GPS), "
                                    f"{k['biometric']} office biometric · {k['client_visits']} client visits · "
                                    f"{k['wfh']} WFH sign-ins" + (f" · {k['away']} tagged 'Office' but not at an office"
                                                                 if k["offices_named"] else "")))
    ws2 = wb.create_sheet("Places")
    _table(ws2, 5, ["Place", "Mostly tagged", "Tags", "Sign-ins", "People", "Who", "Remarks", "First", "Last"],
           [[b["place"], b["main_type"], b["types"], b["count"], b["people"], ", ".join(b["people_names"]), b["notes"],
             b["first"], b["last"]] for b in L["places"]],
           [46, 14, 30, 9, 8, 44, 36, 16, 16], org, f"Places people signed in from — {period.label}")
    ws3 = wb.create_sheet("By person")
    _table(ws3, 5, ["Employee No", "Name", "Mobile sign-ins", "With GPS", "Places", "Client", "WFH", "On duty",
                    "Tagged Office", "…not at an office", "Permission requests"],
           [[p["emp_no"], p["name"], p["sign_ins"], p["gps"], p["places"], p["client"], p["wfh"], p["onduty"],
             p["office_tagged"], p["away"], p["permission"]] for p in L["people"]],
           [10, 24] + [12] * 9, org, f"Mobile sign-ins by person — {period.label}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _title(ws, org: dict, title: str, ncols: int):
    ws["A1"] = org.get("org_name", "")
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    ws["A2"] = org.get("org_address", "")
    ws["A3"] = title
    ws["A3"].font = Font(bold=True, size=12)
    for r in (1, 2, 3):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=max(ncols, 4))


def _header(ws, row: int, headers: list[str], widths: list[int] | None = None):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.fill, c.font = HDR, HFONT
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 45
    ws.freeze_panes = ws.cell(row=row + 1, column=3)


def _table(ws, title_row: int, headers: list, rows: list[list], widths=None, org=None, title=""):
    if org is not None:
        _title(ws, org, title, len(headers))
    _header(ws, title_row, headers, widths)
    for r_i, r in enumerate(rows, start=title_row + 1):
        for c_i, v in enumerate(r, start=1):
            _put(ws, r_i, c_i, v, text=True)
    if rows:
        ws.auto_filter.ref = f"A{title_row}:{get_column_letter(len(headers))}{title_row + len(rows)}"


def build_workbook(db: Session, period: Period) -> bytes:
    org = get_general(db)
    pol = get_policy(db)
    m = MX.period_metrics(db, period)
    res = MX.results_for(db, period)
    ovr = {}
    for o in db.scalars(select(MonthOverrideRow).where(MonthOverrideRow.period_id == period.id)):
        ovr[(o.emp_no, o.field)] = o
    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True

    # ------------------------------------------------------------------ 1. payroll leave sheet
    ws = wb.active
    ws.title = "Payroll Leave Sheet"
    title = f"Attendance & Leave Calculation — {period.label} ({'FINAL' if period.status == 'FINALISED' else 'DRAFT'})"
    heads = ["Employee No", "Name", "Group", "Location", "Total Week Days", "Holiday", "Days Leaves Taken",
             "Days Absent", "Days Present", "WFH", "Total Hours Present (HH:MM)", "Extra Working Days",
             "Total Days Worked", "Hours Required", "Total Hours Present (decimal)", "Working on Holiday/Off-day to exclude",
             "Actual Hours Worked", "Shortfall / Excess", "Remark",
             "Hours / Day", "Exempt", "Deduction Days (½-day per 4h short)", "PL Taken", "Comp-off Taken",
             "Unauthorised Absent", "LOP (approved)", "Leave Opening", "Leave Credit", "Total Leave Debit",
             "Leave Closing", "Salary Deduction Days", "Payroll LOP Days", "Comp-off Earned", "Flags"]
    widths = [10, 24, 9, 9, 8, 8, 8, 8, 8, 6, 10, 8, 8, 9, 10, 10, 10, 10, 34, 7, 7, 10, 7, 8, 9, 7, 8, 7, 8, 8, 9, 8, 8, 40]
    _title(ws, org, title, len(heads))
    HR_ = 5
    _header(ws, HR_, heads, widths)
    fmap = {"total_week_days": 5, "holidays": 6, "leave_days": 7, "absent_days": 8, "wfh_days": 10, "total_hours": 15,
            "extra_days": 12, "extra_hours": 16, "required_hours": 14, "deduction_days": 22, "remark": 19, "exempt": 21}
    blk, per_blk, grace = pol.shortfall_block_hours, pol.deduction_per_block_days, pol.shortfall_grace_hours
    for i, r in enumerate(res, start=HR_ + 1):
        d = r.data
        th = d.get("total_hours") or 0
        vals = {
            1: r.emp_no, 2: r.name, 3: r.group, 4: r.location, 5: d["total_week_days"], 6: d["holidays"],
            7: d["leave_days"], 8: d["absent_days"], 9: f"=E{i}-F{i}-G{i}-H{i}", 10: d["wfh_days"],
            11: MX.hm(th * 60), 12: d["extra_days"], 13: f"=E{i}-F{i}-G{i}-H{i}",
            14: f"=M{i}*T{i}", 15: round(th, 4), 16: round(d["extra_hours"], 4), 17: f"=O{i}-P{i}",
            18: f"=ROUND(Q{i}-N{i},2)", 19: d.get("remark", ""), 20: d.get("hours_per_day", pol.hours_per_day),
            21: "Yes" if d.get("exempt") else "No",
            22: f'=IF(U{i}="Yes",0,IF(-R{i}>{grace},CEILING(ROUND(-R{i},4)/{blk},1)*{per_blk},0))',
            23: d.get("pl_days"), 24: d.get("cof_days"), 25: d.get("unauth_absent_days"), 26: d.get("lop_days"),
            27: d.get("leave_opening"), 28: d.get("leave_credit"), 29: d.get("leave_debit_total"),
            30: d.get("leave_closing"), 31: d.get("salary_deduction_days"), 32: d.get("payroll_lop_days"),
            33: d.get("compoff_earned"),
            34: "; ".join(f["code"] for f in r.flags if f["severity"] in ("critical", "high", "medium")),
        }
        if (r.emp_no, "required_hours") in ovr:
            vals[14] = d["required_hours"]
        if (r.emp_no, "deduction_days") in ovr:
            vals[22] = d["deduction_days"]
        for col, v in vals.items():
            c = _put(ws, i, col, v, text=col in TEXT_COLS)
            if col in (15, 16, 17, 18, 14):
                c.number_format = "0.00"
        for (emp, field), o in ovr.items():
            if emp == r.emp_no and field in fmap:
                c = ws.cell(row=i, column=fmap[field])
                c.fill = OVR
                c.comment = Comment(f"Manual override by {o.created_by}: {o.reason}", "HSCO app")
        if d.get("deduction_days"):
            ws.cell(row=i, column=22).fill = RED
        if d.get("salary_deduction_days"):
            ws.cell(row=i, column=31).fill = RED
    last = HR_ + len(res)
    ws.auto_filter.ref = f"A{HR_}:{get_column_letter(len(heads))}{last}"
    ws.conditional_formatting.add(f"R{HR_ + 1}:R{last}", CellIsRule(operator="lessThan", formula=["0"], fill=RED))
    ws.conditional_formatting.add(f"J{HR_ + 1}:J{last}", CellIsRule(operator="greaterThan", formula=[str(pol.wfh_limit_days)], fill=AMBER))
    tr = last + 2
    ws.cell(row=tr, column=2, value="TOTAL").font = Font(bold=True)
    for col in (5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 22, 23, 24, 25, 26, 31, 32, 33):
        L = get_column_letter(col)
        ws.cell(row=tr, column=col, value=f"=SUBTOTAL(9,{L}{HR_ + 1}:{L}{last})").font = Font(bold=True)
    ws.cell(row=tr + 2, column=1, value="Yellow cells = manual override (hover for reason). Formulas are live: edit E–H, "
                                          "O, P or T and Excel recalculates the deduction. Generated "
                                          f"{datetime.now():%d-%b-%Y %H:%M}.").font = Font(italic=True, color="666666")

    # ------------------------------------------------------------------ 2. board summary
    ws = wb.create_sheet("Board Summary")
    _title(ws, org, f"Board Summary — {period.label}", 6)
    k = m["kpis"]
    kp = [("Headcount (in muster)", k["headcount"]), ("New joinees", k["new_joinees"]), ("Exits", k["exits"]),
          ("Attendance rate %", k["attendance_rate"]), ("Avg hours / present day", k["avg_work_hours"]),
          ("Avg in-time", k["avg_in_time"]), ("Avg out-time", k["avg_out_time"]),
          ("Employees with leave deduction", k["deduction_employees"]), ("Total deduction days", k["deduction_days"]),
          ("Salary deduction days (leave exhausted)", k["salary_deduction_days"]),
          ("Paid leave days taken", k["leave_days"]), ("Comp-off days taken", k["compoff_days"]),
          ("Unauthorised absent days", k["absent_days"]), ("LOP days", k["lop_days"]),
          ("WFH days", k["wfh_days"]), (f"Employees over WFH limit ({pol.wfh_limit_days:g})", k["wfh_over_limit"]),
          ("Late arrivals (days)", k["late_days"]), ("Persistently late employees", k["persistent_late"]),
          ("Regularised days", k["regularised_days"]), ("Persistent regularisation employees", k["persistent_regularisation"]),
          ("Missing-punch days", k["missing_punch_days"]), ("Off-day / holiday working days", k["extra_days"]),
          ("Comp-offs earned (≥ 5 h)", k["compoff_earned"]), ("Excess hours (sum)", k["excess_hours"]),
          ("Shortfall hours (sum)", k["shortfall_hours"]), ("Late-sitting days (after 21:00)", k["late_sitting_days"])]
    _header(ws, 5, ["Metric", "Value"], [44, 14])
    for i, (a, b) in enumerate(kp, start=6):
        _put(ws, i, 1, a, text=True)
        _put(ws, i, 2, b, text=True)
    r0 = 6 + len(kp) + 2
    ws.cell(row=r0, column=1, value="By group").font = Font(bold=True, size=12)
    gh = ["Group", "Headcount", "Present days", "PL", "Comp-off", "Absent", "LOP", "WFH", "Late days", "Regularised",
          "Deduction days", "Employees deducted", "Hours vs required %"]
    for i, h in enumerate(gh, start=1):
        c = ws.cell(row=r0 + 1, column=i, value=h)
        c.fill, c.font = HDR, HFONT
    for j, g in enumerate(m["groups"], start=r0 + 2):
        for i, key in enumerate(["group", "headcount", "days_present", "pl_days", "cof_days", "unauth_absent_days",
                                 "lop_days", "wfh_days", "late_days", "regularised_days", "deduction_days",
                                 "deduction_emps", "hours_vs_required_pct"], start=1):
            _put(ws, j, i, g.get(key), text=True)
    for i in range(3, 14):
        ws.column_dimensions[get_column_letter(i)].width = 12

    # ------------------------------------------------------------------ 3. flags
    ws = wb.create_sheet("Flags")
    rows = [[f["emp_no"], f["name"], f["group"], f["severity"].upper(), MX.FLAG_LABELS.get(f["code"], f["code"]),
             f["message"]] for f in m["flags"]]
    _table(ws, 5, ["Employee No", "Name", "Group", "Severity", "Flag", "Detail"], rows, [10, 24, 9, 10, 34, 70], org,
           f"Exceptions & flags — {period.label}")
    for i, f in enumerate(m["flags"], start=6):
        ws.cell(row=i, column=4).fill = SEV_FILL.get(f["severity"], GREY)

    # ------------------------------------------------------------------ 4. behaviour
    ws = wb.create_sheet("Late & Punctuality")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, d.get("late_days"), d.get("very_late_days"), d.get("avg_late_min"),
                     MX.hm(d.get("avg_in_min")), MX.hm(d.get("avg_out_min")), d.get("early_exit_days"),
                     d.get("short_days"), d.get("late_sitting_days"), d.get("avg_work_hours")])
    rows.sort(key=lambda x: -(x[3] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", f"Late days (> {pol.late_grace_min} min)", "Very late (> 1 h)",
                   "Avg minutes late", "Avg in-time", "Avg out-time", "Early exits", f"Days < {pol.min_daily_hours:g} h",
                   "Late sitting days", "Avg hours / day"], rows, [10, 24, 9, 10, 10, 10, 10, 10, 10, 10, 10, 10], org,
           f"Punctuality — {period.label} (information only: 'late coming' is not a deduction under policy)")

    ws = wb.create_sheet("WFH & Locations")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, d.get("wfh_days"), d.get("partial_wfh_days"),
                     "OVER LIMIT" if (d.get("wfh_days") or 0) > pol.wfh_limit_days else "", d.get("client_days"),
                     d.get("onduty_days"), d.get("biometric_swipes"), d.get("mobile_swipes"), d.get("mobile_office_swipes")])
    rows.sort(key=lambda x: -(x[3] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", "WFH days", "of which partial", f"> {pol.wfh_limit_days:g} days",
                   "Client-location days", "On-duty days", "Biometric swipes", "Mobile swipes", "Mobile 'Office' swipes"],
           rows, [10, 24, 9, 9, 9, 12, 10, 10, 10, 10, 12], org, f"WFH & work locations — {period.label}")
    for i, row in enumerate(rows, start=6):
        if row[5]:
            ws.cell(row=i, column=6).fill = AMBER

    # ------------------------------------------------------------------ mobile sign-ins with place names
    from app.services import locations as LOC
    L = LOC.report(db, period)
    if L["rows"]:
        ws = wb.create_sheet("Sign-ins & Places")
        _table(ws, 5, ["When", "Employee No", "Name", "Tagged as", "Place", "Remark", "Office check", "Permission reason",
                       "Phone", "Status", "Latitude", "Longitude"],
               [[r["ts"], r["emp_no"], r["name"], r["type"] if r["swipe_type"].lower() != "permission" else "Permission",
                 r["place"], r["note"], r["office_check"], r["reason"], r["device"], r["approval"], r["lat"], r["lon"]]
                for r in L["rows"]],
               [16, 10, 22, 14, 44, 30, 16, 22, 16, 10, 11, 11], org,
               f"Mobile sign-ins and places — {period.label} (offline place names; named places take priority)")
        for i, r in enumerate(L["rows"], start=6):
            ws.cell(row=i, column=1).number_format = "dd-mmm-yyyy hh:mm"
            if r["office_check"] == "Away from office":
                ws.cell(row=i, column=7).fill = RED
        ws = wb.create_sheet("Places")
        _table(ws, 5, ["Place", "Mostly tagged", "Tags", "Sign-ins", "People", "Who", "Remarks", "First", "Last"],
               [[b["place"], b["main_type"], b["types"], b["count"], b["people"], ", ".join(b["people_names"]),
                 b["notes"], b["first"], b["last"]] for b in L["places"]],
               [44, 14, 30, 9, 8, 40, 36, 16, 16], org, f"Places people signed in from — {period.label}")

    ws = wb.create_sheet("Regularisation & Punches")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, d.get("regularised_days"), d.get("permission_swipes"),
                     d.get("missing_punch_days"), d.get("no_punch_present_days"), "Yes" if d.get("auto_attendance") else "",
                     d.get("total_swipes")])
    rows.sort(key=lambda x: -(x[3] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", "Regularised days", "Permission swipes", "Missing-punch days",
                   "Present with no punch", "Auto attendance", "Total swipes"], rows, [10, 24, 9, 12, 12, 12, 12, 10, 10],
           org, f"Regularisation & punch integrity — {period.label}")

    ws = wb.create_sheet("Leave & Comp-off Ledger")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, d.get("leave_opening"), d.get("leave_credit"), d.get("leave_debit_pl"),
                     d.get("leave_debit_shortfall"), d.get("leave_debit_absent"), d.get("leave_debit_compoff"),
                     d.get("leave_closing"), d.get("salary_deduction_days"), d.get("compoff_opening"),
                     d.get("compoff_earned"), d.get("cof_days"), d.get("compoff_closing"), d.get("payroll_lop_days")])
    _table(ws, 5, ["Employee No", "Name", "Group", "Leave opening", "Credit", "PL taken", "Shortfall deduction",
                   "Unauthorised absence", "Comp-off overdraft", "Leave closing", "Salary deduction days",
                   "Comp-off opening", "Comp-off earned", "Comp-off taken", "Comp-off closing", "Payroll LOP days"],
           rows, [10, 24, 9] + [11] * 13, org, f"Leave & comp-off ledger — {period.label} (FY Apr–Mar, {pol.annual_leave_days:g} days)")

    # ------------------------------------------------------------------ insights scorecard (docs/METRICS.md "Insights")
    from app.services import insights as INS
    ins = INS.insights(db, period, with_matrix=False)
    ik = ins["kpis"]
    ws = wb.create_sheet("Scorecard")
    rows = [[r["emp_no"], r["name"], r["group"], r["location"], r["tenure_m"], r["attention"],
             ", ".join(r["attention_why"]), r["attendance_pct"], r["absence_pct"], r["absence_12m_days"], r["absence_12m_spells"],
             r["punctuality_pct"], r["avg_in"], r["avg_hours"], r["utilisation_pct"], r["balance_hours"], r["long_days"],
             r["late_sitting_days"], r["wfh_days"], r["regularised_days"], r["missing_punch_days"], r["leave_used_fy"],
             r["leave_closing"], r["compoff_closing"]] for r in sorted(ins["rows"], key=lambda x: -x["attention"])]
    _table(ws, 6, ["Emp No", "Name", "Group", "Location", "Tenure (months)", "Attention (0-100)", "Main reasons",
                   "Attendance %", "Unplanned absence %", "Unplanned absence days (12 m)", "Absence spells (12 m)", "On time %", "Avg first punch",
                   "Avg day (h)", "Hours vs required %", "Balance (h)", "Long days", "Late sitting", "WFH days",
                   "Regularised", "Missing punch", f"Leave used {ik['fy_label']}", "Leave balance", "Comp-off balance"],
           rows, [10, 24, 9, 10] + [11] * 20, org, f"Employee scorecard — {period.label}")
    ws.cell(row=4, column=1, value=(
        f"Firm: unplanned absence {ik['absenteeism_pct']}% · on time {ik['punctuality_pct']}% · hours vs required "
        f"{ik['utilisation_pct']}% · attrition (12 m) {ik['attrition_pct']}% · leave liability {ik['leave_liability_days']} d. "
        "Attention = unplanned absence (12 m) 25 + punctuality 20 + hours 20 + regularisation 15 + WFH 10 + missing punches 10."))
    ws.freeze_panes = "C7"
    ws2 = wb.create_sheet("Groups Compared")
    _table(ws2, 5, ["Group", "People", "Attendance %", "Unplanned absence %", "On time %", "Hours vs required %",
                    "Avg day (h)", "WFH share %", "Regularised %", "Unplanned absence days (12 m)", "Avg attention", "Deduction days"],
           [[g["group"], g["headcount"], g["attendance_pct"], g["absenteeism_pct"], g["punctuality_pct"],
             g["utilisation_pct"], g["avg_hours"], g["wfh_pct"], g["regularisation_pct"], g["absence_12m_days"],
             g["attention_avg"], g["deduction_days"]] for g in ins["groups"]], [12] + [13] * 11, org,
           f"Groups compared — {period.label}")

    ws = wb.create_sheet("People Movement")
    _title(ws, org, f"People movement — {period.label}", 6)
    row = 5
    for label, key in (("New joinees", "joiners"), ("Exits", "exits"),
                       ("Possible exits (in last month's muster, not this month's)", "exit_candidates"),
                       ("Work anniversaries", "anniversaries"), ("Joined in last 6 months", "recent_joiners")):
        ws.cell(row=row, column=1, value=f"{label} ({len(m['people'][key])})").font = Font(bold=True, size=12)
        row += 1
        for i, h in enumerate(["Employee No", "Name", "Group", "Location", "Joined", "Left", "Years"], start=1):
            c = ws.cell(row=row, column=i, value=h)
            c.fill, c.font = HDR, HFONT
        row += 1
        for p in m["people"][key]:
            for i, kk in enumerate(["emp_no", "name", "group", "location", "doj", "dol", "years"], start=1):
                _put(ws, row, i, p.get(kk, ""), text=True)
            row += 1
        row += 1
    for i, w in enumerate([10, 26, 10, 10, 12, 12, 7], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ------------------------------------------------------------------ daily detail
    ws = wb.create_sheet("Daily Detail")
    rows = []
    for r in res:
        for dd in r.days:
            tags = [t for t, on in (("LATE", dd.get("late")), ("EARLY", dd.get("early")), ("SHORT", dd.get("short")),
                                    ("MISSING PUNCH", dd.get("missing")), ("REGULARISED", dd.get("regularised")),
                                    ("WFH", dd.get("wfh")), ("OFF-DAY WORK", dd.get("extra")),
                                    ("COMP-OFF EARNED", dd.get("co_earned")), ("OVERRIDE", dd.get("overridden"))) if on]
            rows.append([r.emp_no, r.name, dd["date"], dd["wd"], dd["type"], dd["s1"], dd["s2"], dd["in"], dd["out"],
                         MX.hm(dd["work_min"]), dd.get("late_min") or "", ", ".join(dd.get("locs", [])),
                         _day_places(dd), ", ".join(tags), "; ".join(dd.get("notes", []))])
    _table(ws, 5, ["Employee No", "Name", "Date", "Day", "Day type", "Session 1", "Session 2", "In", "Out", "Hours",
                   "Minutes late", "Tagged as", "Places (punch time · place · remark)", "Tags", "Notes"], rows,
           [10, 22, 11, 5, 11, 8, 8, 7, 7, 7, 8, 22, 60, 30, 40], org, f"Daily detail — {period.label}")

    # ------------------------------------------------------------------ reconciliation
    rc = MX.reconciliation(db, period)
    if rc["has_manual"]:
        ws = wb.create_sheet("Reconciliation")
        rows = []
        for x in rc["rows"]:
            if not x["diffs"]:
                rows.append([x["emp_no"], x["name"], "MATCH", "", "", "", x["manual_remark"]])
            for df in x["diffs"]:
                rows.append([x["emp_no"], x["name"], "DIFF", df["field"], df["manual"], df["app"], x["manual_remark"]])
        _table(ws, 5, ["Employee No", "Name", "Result", "Field", "Manual", "App", "Manual remark"], rows,
               [10, 24, 8, 18, 10, 10, 30], org,
               f"App vs manual MIS — {rc['matched']} of {rc['compared']} employees identical")

    # ------------------------------------------------------------------ notes
    ws = wb.create_sheet("Rules Applied")
    notes = [
        ("Required hours", f"{pol.hours_per_day:g} h × days present (E − F − G − H); per-employee hours/day where set"),
        ("Holiday / off-day work", "Hours worked on weekly-offs and holidays are excluded from actual hours"),
        ("Deduction", f"Shortfall above {pol.shortfall_grace_hours:g} h → {pol.deduction_per_block_days:g} day per "
                      f"{pol.shortfall_block_hours:g} h block (rounded up)"),
        ("Exempt groups", ", ".join(pol.exempt_groups)),
        ("Comp-off", f"Earned for ≥ {pol.comp_off_min_hours:g} h on a weekly-off / holiday"),
        ("WFH limit", f"{pol.wfh_limit_days:g} days per month ({pol.wfh_method.replace('_', ' ')})"),
        ("Late arrival", f"First-in more than {pol.late_grace_min} min after shift start (information only)"),
        ("LOP in MIS columns", f"Counted under {'Leaves (G)' if pol.lop_bucket == 'leave' else 'Absent (H)'}"),
        ("Leave", f"{pol.annual_leave_days:g} days per FY, credit mode '{pol.leave_credit_mode}', unauthorised absence "
                  f"{'debits' if pol.absent_debits_leave else 'does not debit'} leave"),
        ("Source", "greytHR Muster (day status & hours), Swipe Details (punches/locations), DOJ report, app master data"),
    ]
    _table(ws, 5, ["Rule", "Applied as"], [list(n) for n in notes], [26, 110], org, "Rules applied in this workbook")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
