"""Excel workbook for a period: payroll leave sheet (MIS layout, live formulas) + board metric sheets."""
from __future__ import annotations

import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image
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
TEXT_COLS = {1, 2, 3, 4, 19, 27, 28}   # payroll sheet columns holding imported text (never formulas)
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
    org = _org(db)
    L = LOC.report(db, period)
    emps = MX.scoped_employees(db)
    names = {k: e.name for k, e in emps.items()}
    mobile = {(r["emp_no"], r["ts"]): r for r in L["rows"]}
    kind = lambda t: {"astra": "Biometric", "mobile sign in": "Mobile", "permission": "Permission"}.get((t or "").strip().lower(), t or "")  # noqa: E731
    wb = Workbook()
    ws = wb.active
    ws.title = "Punches"
    rows = []
    from app.services.scope import ignored_emp_nos
    skip = ignored_emp_nos(db)
    for s in db.scalars(select(SwipeRow).where(SwipeRow.period_id == period.id).order_by(SwipeRow.ts, SwipeRow.emp_no)):
        if s.emp_no in skip:                        # ignored groups (OTHERS, v1.13)
            continue
        r = mobile.get((s.emp_no, s.ts))
        if r:
            rows.append([s.ts, s.emp_no, names.get(s.emp_no, s.emp_no), kind(r["swipe_type"]) or "Mobile",
                         r["type"] if (r["swipe_type"] or "").lower() != "permission" else "Permission", r["place"],
                         r["note"], r["reason"], r["device"], r["approval"], s.lat, s.lon])
        else:
            rows.append([s.ts, s.emp_no, names.get(s.emp_no, s.emp_no), kind(s.swipe_type),
                         s.location_type or ("Office (biometric)" if (s.swipe_type or "").lower() == "astra"
                                             else "Permission" if (s.swipe_type or "").lower() == "permission" else ""),
                         s.place or ("Office biometric" + (f" ({s.door})" if s.door else "")), s.note or "",
                         s.reason or "", s.device or "", s.approval or "", s.lat, s.lon])
    _table(ws, 5, ["When", "Employee No", "Name", "Punch type", "Tagged as", "Place", "Remark",
                   "Permission reason", "Phone", "Status", "Latitude", "Longitude"], rows,
           [16, 10, 22, 13, 15, 46, 30, 22, 16, 10, 11, 11], org,
           f"Punches and where they were made — {period.label} (place names from the built-in offline map of India)")
    for i in range(6, 6 + len(rows)):
        ws.cell(row=i, column=1).number_format = "dd-mmm-yyyy hh:mm"
    k = L["kpis"]
    ws.cell(row=4, column=1, value=(f"{len(rows)} punches: {k['mobile']} mobile sign-ins ({k['with_gps']} with GPS), "
                                    f"{k['biometric']} office biometric · {k['client_visits']} client visits · "
                                    f"{k['wfh']} WFH sign-ins"))
    ws2 = wb.create_sheet("Places")
    _table(ws2, 5, ["Place", "Mostly tagged", "Tags", "Sign-ins", "People", "Who", "Remarks", "First", "Last"],
           [[b["place"], b["main_type"], b["types"], b["count"], b["people"], ", ".join(b["people_names"]), b["notes"],
             b["first"], b["last"]] for b in L["places"]],
           [46, 14, 30, 9, 8, 44, 36, 16, 16], org, f"Places people signed in from — {period.label}")
    ws3 = wb.create_sheet("By person")
    _table(ws3, 5, ["Employee No", "Name", "Group", "Category", "Location", "Mobile sign-ins", "With GPS", "Places",
                    "Client", "WFH", "On duty", "Tagged Office", "Permission requests"],
           [[p["emp_no"], p["name"], getattr(emps.get(p["emp_no"]), "group", ""), getattr(emps.get(p["emp_no"]), "category", ""),
             getattr(emps.get(p["emp_no"]), "location", ""), p["sign_ins"], p["gps"], p["places"], p["client"], p["wfh"],
             p["onduty"], p["office_tagged"], p["permission"]] for p in L["people"]],
           [10, 24, 9, 18, 10] + [12] * 8, org, f"Mobile sign-ins by person — {period.label}")
    for wsx, cols in ((ws2, (8, 9)),):
        for row in wsx.iter_rows(min_row=6):
            for ci in cols:
                if row[ci - 1].value:
                    row[ci - 1].number_format = "dd-mmm-yyyy hh:mm"
    _finish(wb, f"Attendance MIS — punches & locations, {period.label}", org, _status(period),
            guide=[("Punches", NAVY, ""), ("Places", "2E7D32", ""), ("By person", "2E7D32", "")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _org(db: Session) -> dict:
    """Organisation details for the sheet headers, with the logo as PNG under "_logo" (when there is one)."""
    from app.services import branding
    org = dict(get_general(db))
    try:
        org["_logo"] = branding.png()
    except OSError:
        org["_logo"] = None
    return org


def _status(period: Period) -> str:
    """'Locked' for a locked month, '' otherwise (owner, v1.15: no "Draft" in the file or its sheets)."""
    return "Locked" if period.status == "FINALISED" else ""


class _PngImage(Image):
    """A PNG embedded without Pillow (openpyxl needs Pillow only to read the size / convert — we know both)."""

    def __init__(self, data: bytes, width: int, height: int):  # noqa: D401 — no super().__init__ (it imports PIL)
        self.ref, self._bytes, self.width, self.height, self.format = None, data, width, height, "png"
        self.anchor = "A1"

    def _data(self):
        return self._bytes


def _logo(ws, org: dict, row_height: float = 46) -> bool:
    """The firm's logo at the top left of a sheet, in place of the firm name (owner, v1.15). False if there is no PNG
    of the logo yet (the browser makes it the first time someone opens the app — services/branding.png)."""
    lg = org.get("_logo")
    if not lg:
        return False
    data, w, h = lg
    hh = 56                                                # pixels
    img = _PngImage(data, max(1, round(w * hh / h)), hh)
    img.anchor = "A1"
    ws.add_image(img)
    ws.row_dimensions[1].height = row_height
    return True


def _title(ws, org: dict, title: str, ncols: int):
    if _logo(ws, org):
        ws["A1"] = None
    else:
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


def _wfh_term(d: dict, i: int, pol) -> str:
    """Excel term for the WFH-over-limit deduction of one payroll row, matching what the app charged that month:
    none for results calculated before the rule (v1.6.5), for people not deducted for WFH, or when the month's
    stored figures show no WFH deduction despite days over the limit (e.g. exempt under v1.6.5 rules); otherwise the
    rate actually charged (stored deduction ÷ extra days), falling back to the current setting."""
    if "wfh_deduction_days" not in d or d.get("wfh_deduction_exempt"):
        return ""
    excess, ded = d.get("wfh_excess_days") or 0, d.get("wfh_deduction_days") or 0
    ok = d.get("wfh_approved_days") or 0                    # extra days approved by manager / partner (v1.7.5)
    charged = max(0, excess - ok)
    if excess and charged and not ded:
        return ""
    rate = round(ded / charged, 4) if charged else pol.wfh_excess_deduction_days
    if not rate:
        return ""
    return f"+CEILING(MAX(0,J{i}-{pol.wfh_limit_days:g}" + (f"-{ok:g}" if ok else "") + f"),1)*{rate:g}"


def _deduction_remark(d: dict, pol, manual=None) -> str:
    """Why this person loses leave this month, in words: the hours part and the WFH part (Payroll Input)."""
    parts = []
    sd, short = d.get("shortfall_deduction_days") or 0, d.get("shortfall_hours") or 0
    if sd:
        parts.append(f"Hours shortfall: {short:.2f} h short of {d.get('required_hours') or 0:g} h required → {sd:g} day")
    wd = d.get("wfh_deduction_days") or 0
    if wd:
        ex, ok = d.get("wfh_excess_days") or 0, d.get("wfh_approved_days") or 0
        parts.append(f"WFH over the limit: {d.get('wfh_days') or 0:g} WFH days, limit {pol.wfh_limit_days:g}"
                     + (f", {ok:g} approved" if ok else "") + f" → {max(0, ex - ok):g} extra × "
                     f"{pol.wfh_excess_deduction_days:g} = {wd:g} day")
    if manual is not None:
        parts.append(f"Deduction set by hand to {d.get('deduction_days') or 0:g} day ({manual.created_by}: {manual.reason})")
    return "; ".join(parts)


def build_workbook(db: Session, period: Period) -> bytes:
    org = _org(db)
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
    title = f"Attendance MIS — leave calculation, {period.label}" + (" (locked)" if _status(period) else "")
    heads = ["Employee No", "Name", "Group", "Location", "Total Week Days", "Holiday", "Days Leaves Taken",
             "Days Absent", "Days Present", "WFH", "Total Hours Present (HH:MM)", "Extra Working Days",
             "Total Days Worked", "Hours Required", "Total Hours Present (decimal)", "Off-day / holiday hours (excluded)",
             "Actual Hours Worked", "Shortfall / Excess", "Remark",
             "Hours / Day", "Exempt (hours)", "Deduction Days", "PL Taken", "Comp-off Taken",
             "Unapplied Leave (Absence)", "Comp-off Earned", "Flags", "Category"]
    widths = [10, 24, 9, 9, 8, 8, 8, 8, 8, 6, 10, 8, 8, 9, 10, 10, 10, 10, 34, 7, 7, 10, 7, 8, 11, 8, 40, 20]
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
            21: "Yes" if d.get("exempt") else ("Waived" if str((d.get("overrides") or {}).get("shortfall_waived") or "")
                                                .lower() == "true" else "No"),
            # hours part (0 when exempt or waived for the month: column U not "No") + WFH part (row without it when the person is not deducted
            # for WFH: Partner-type group or a month exception)
            22: f'=IF(U{i}<>"No",0,IF(-R{i}>{grace},CEILING(ROUND(-R{i},4)/{blk},1)*{per_blk},0))' + _wfh_term(d, i, pol),
            23: d.get("pl_days"), 24: d.get("cof_days"), 25: d.get("unauth_absent_days"),
            26: d.get("compoff_earned"),
            27: "; ".join(MX.FLAG_LABELS.get(f["code"], f["code"]) for f in MX.live_flags(r.flags)
                          if f["severity"] in ("critical", "high", "medium")),
            28: (r.category or "") + (f" + {d['additional_category']}" if d.get("additional_category") else ""),
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
                c.comment = Comment(f"Manual override by {o.created_by}: {o.reason}", "Attendance MIS")
        if d.get("deduction_days"):
            ws.cell(row=i, column=22).fill = RED
    last = HR_ + len(res)
    ws.cell(row=HR_, column=22).comment = Comment(
        f"½ day per {pol.shortfall_block_hours:g} h short (rounded up; none when column U is Yes / Waived) + "
        f"{pol.wfh_excess_deduction_days:g} day per WFH day over {pol.wfh_limit_days:g} not approved by a manager / partner",
        "Attendance MIS")
    ws.auto_filter.ref = f"A{HR_}:{get_column_letter(len(heads))}{last}"
    ws.conditional_formatting.add(f"R{HR_ + 1}:R{last}", CellIsRule(operator="lessThan", formula=["0"], fill=RED))
    ws.conditional_formatting.add(f"J{HR_ + 1}:J{last}", CellIsRule(operator="greaterThan", formula=[str(pol.wfh_limit_days)], fill=AMBER))
    tr = last + 2
    ws.cell(row=tr, column=2, value="TOTAL").font = Font(bold=True)
    for col in (5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 22, 23, 24, 25, 26):
        L = get_column_letter(col)
        ws.cell(row=tr, column=col, value=f"=SUBTOTAL(9,{L}{HR_ + 1}:{L}{last})").font = Font(bold=True)
    ws.cell(row=tr + 2, column=1, value="Yellow cells = manual override (hover for reason). Formulas are live: edit E–H, "
                                          "O, P, T or J (WFH) and Excel recalculates the deduction. Generated "
                                          f"{datetime.now():%d-%b-%Y %H:%M} from the app's figures at that moment, "
                                          "including every correction (see the Corrections sheet) and review "
                                          "(see Flags). Leave balances are kept in greytHR — this sheet gives the "
                                          "deduction to apply.").font = Font(italic=True, color="666666")

    # ------------------------------------------------------------------ payroll input (what the payroll team needs)
    # owner (v1.9): only people with a leave deduction, the deduction split into hours shortfall and WFH over the
    # limit — in columns and in words. Linked to the payroll sheet: the hours part uses the same formula as column V,
    # the WFH part is the rest of V (a deduction set by hand: the app's split, and the remark says so).
    pi = wb.create_sheet("Payroll Input")
    rowmap = {r.emp_no: i for i, r in enumerate(res, start=HR_ + 1)}
    pick = [r for r in res if (r.data or {}).get("deduction_days")]
    P = "'Payroll Leave Sheet'!"
    _table(pi, 5, ["Employee No", "Name", "Group", "Category", "Location", "Hours shortfall deduction (days)",
                   "WFH over-limit deduction (days)", "Total leave deduction (days)", "Remark"], [],
           [10, 24, 9, 18, 10, 14, 14, 14, 80], org,
           f"Payroll input — {period.label}{' (locked)' if _status(period) else ''}: "
           f"{len(pick)} {'person' if len(pick) == 1 else 'people'} with a leave deduction")
    for k_, r in enumerate(pick, start=6):
        d, i = r.data, rowmap[r.emp_no]
        manual = (r.emp_no, "deduction_days") in ovr
        if manual:
            hours, wfh = d.get("shortfall_deduction_days") or 0, d.get("wfh_deduction_days") or 0
        else:
            hours = (f'=IF({P}U{i}<>"No",0,IF(-{P}R{i}>{grace},CEILING(ROUND(-{P}R{i},4)/{blk},1)*{per_blk},0))')
            wfh = f"={P}V{i}-F{k_}"
        vals = [r.emp_no, r.name, r.group, r.category, r.location, hours, wfh, f"={P}V{i}",
                _deduction_remark(d, pol, ovr.get((r.emp_no, "deduction_days")))]
        for j_, v in enumerate(vals, start=1):
            c = _put(pi, k_, j_, v, text=j_ in (1, 2, 3, 4, 5, 9))
            if 6 <= j_ <= 8:
                c.number_format = "0.0#"
        pi.cell(row=k_, column=9).alignment = Alignment(wrap_text=True, vertical="top")
    if pick:
        last_p = 5 + len(pick)
        pi.auto_filter.ref = f"A5:I{last_p}"
        t = last_p + 2
        pi.cell(row=t, column=2, value="TOTAL").font = Font(bold=True)
        for col in "FGH":
            pi[f"{col}{t}"] = f"=SUBTOTAL(9,{col}6:{col}{last_p})"
            pi[f"{col}{t}"].font = Font(bold=True)
            pi[f"{col}{t}"].number_format = "0.0#"
    else:
        pi.cell(row=6, column=1, value="Nobody has a leave deduction this month.")
    pi.cell(row=4, column=1, value="Linked to the Payroll Leave Sheet (follows any edit there). Hours shortfall: "
                                   f"{pol.deduction_per_block_days:g} day per {pol.shortfall_block_hours:g} h short "
                                   f"(rounded up). WFH: {pol.wfh_excess_deduction_days:g} day per WFH day over "
                                   f"{pol.wfh_limit_days:g} not approved.").font = Font(italic=True, color="666666")

    # ------------------------------------------------------------------ 2. board summary
    ws = wb.create_sheet("Board Summary")
    _title(ws, org, f"Board Summary — {period.label}", 6)
    k, pk = m["kpis"], m.get("prev_kpis") or {}
    kp = [("Total head count", "headcount"), ("New joinees", "new_joinees"), ("Left employees", "exits"),
          ("Total attendance %", "attendance_rate"), ("Present days", "present_days"), ("People present all days", "present_all_days"),
          ("People with unapplied leave (absent)", "absent_people"), ("Unapplied leave (absent) days", "absent_days"),
          ("Avg hours / present day", "avg_work_hours"), ("Avg in-time", "avg_in_time"), ("Avg out-time", "avg_out_time"),
          ("Employees with leave deduction", "deduction_employees"), ("Total deduction days", "deduction_days"),
          ("Leave days taken", "leave_days"), ("Comp-off days taken", "compoff_days"),
          ("WFH days", "wfh_days"), ("People who worked from home", "wfh_people"),
          (f"Employees over WFH limit ({pol.wfh_limit_days:g})", "wfh_over_limit"),
          ("Late arrivals (days)", "late_days"), ("Persistently late employees", "persistent_late"),
          ("Regularised days", "regularised_days"), ("Persistent regularisation employees", "persistent_regularisation"),
          ("People late-sitting on more than 10 days", "late_sitting_people"),
          ("People with hours shortfall > 20 h", "shortfall_people"), ("People with excess hours > 20 h", "excess_people"),
          ("People who earned a comp-off", "compoff_people"), ("Comp-offs earned (days)", "compoff_earned")]
    _header(ws, 5, ["Metric", period.label, m.get("prev_label") or "Last month", "Change"], [44, 14, 14, 12])
    for i, (a, key) in enumerate(kp, start=6):
        cur, prv = k.get(key), pk.get(key)
        _put(ws, i, 1, a, text=True)
        _put(ws, i, 2, cur, text=True)
        _put(ws, i, 3, prv if pk else None, text=True)
        if isinstance(cur, (int, float)) and isinstance(prv, (int, float)):
            c = _put(ws, i, 4, round(cur - prv, 2))
            c.number_format = '+0.##;-0.##;0'
        else:
            _put(ws, i, 4, None)
    r0 = 6 + len(kp) + 2
    ws.cell(row=r0, column=1, value="By group").font = Font(bold=True, size=12)
    gh = ["Group", "Headcount", "Present days", "PL", "Comp-off", "Unapplied leave (absent)", "WFH", "Late days",
          "Regularised", "Deduction days", "Employees deducted", "Hours vs required %"]
    for i, h in enumerate(gh, start=1):
        c = ws.cell(row=r0 + 1, column=i, value=h)
        c.fill, c.font = HDR, HFONT
    for j, g in enumerate(m["groups"], start=r0 + 2):
        for i, key in enumerate(["group", "headcount", "days_present", "pl_days", "cof_days", "unauth_absent_days",
                                 "wfh_days", "late_days", "regularised_days", "deduction_days",
                                 "deduction_emps", "hours_vs_required_pct"], start=1):
            _put(ws, j, i, g.get(key), text=True)
    if m["groups"]:                                       # total row (v1.13)
        jt = r0 + 2 + len(m["groups"])
        _put(ws, jt, 1, "Total").font = Font(bold=True)
        for i, key in enumerate(["headcount", "days_present", "pl_days", "cof_days", "unauth_absent_days", "wfh_days",
                                 "late_days", "regularised_days", "deduction_days", "deduction_emps"], start=2):
            c = _put(ws, jt, i, round(sum(g.get(key) or 0 for g in m["groups"]), 2))
            c.font = Font(bold=True)
        act, req = sum(g.get("actual_hours") or 0 for g in m["groups"]), sum(g.get("required_hours") or 0 for g in m["groups"])
        _put(ws, jt, 12, round(100 * act / req, 1) if req else None).font = Font(bold=True)
    for i in range(3, 14):
        ws.column_dimensions[get_column_letter(i)].width = 12

    # ------------------------------------------------------------------ 3. flags
    ws = wb.create_sheet("Flags")
    from app.models import FlagReview
    rv = {(r.emp_no, r.code): r for r in db.scalars(select(FlagReview).where(FlagReview.period_id == period.id))}
    rows = []
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    from app.services.workflow import AUTO_NOTE, REVIEW_CODES
    m["flags"] = sorted(m["flags"], key=lambda f: (1 if rv.get((f["emp_no"], f["code"])) else 0 if f["code"] in REVIEW_CODES else 2,
                                                   sev_rank.get(f["severity"], 9), f["code"], f["emp_no"]))
    for f in m["flags"]:
        r = rv.get((f["emp_no"], f["code"]))
        status = "Reviewed" if r else ("Open" if f["code"] in REVIEW_CODES else AUTO_NOTE)
        rows.append([f["emp_no"], f["name"], f["group"], f["severity"].upper(), MX.FLAG_LABELS.get(f["code"], f["code"]),
                     f["message"], status, r.reviewed_by if r else "", r.reviewed_at if r else None, r.note if r else ""])
    _table(ws, 5, ["Employee No", "Name", "Group", "Severity", "Flag", "Detail", "Review", "Reviewed by", "Reviewed on",
                   "Review note"], rows, [10, 24, 9, 10, 34, 70, 10, 14, 16, 40], org, f"Exceptions & flags — {period.label}")
    for i, f in enumerate(m["flags"], start=6):
        ws.cell(row=i, column=4).fill = SEV_FILL.get(f["severity"], GREY)
        ws.cell(row=i, column=9).number_format = "dd-mmm-yyyy hh:mm"
        if rows[i - 6][6] == "Open":
            ws.cell(row=i, column=7).fill = AMBER

    # ------------------------------------------------------------------ every manual correction, with who and why
    from app.models import DayOverrideRow
    from app.routes.periods import OVERRIDE_LABELS
    names = {r.emp_no: r.name for r in res}
    corr = []
    for o in db.scalars(select(DayOverrideRow).where(DayOverrideRow.period_id == period.id).order_by(DayOverrideRow.created_at)):
        what = "; ".join(x for x in (f"day type {o.day_type}" if o.day_type else "",
                                     f"session 1 {o.s1}" if o.s1 else "", f"session 2 {o.s2}" if o.s2 else "",
                                     f"hours {MX.hm(o.work_min)}" if o.work_min is not None else "") if x)
        corr.append([o.emp_no, names.get(o.emp_no, o.emp_no), "Day", o.day, what, o.reason, o.created_by, o.created_at])
    for o in db.scalars(select(MonthOverrideRow).where(MonthOverrideRow.period_id == period.id).order_by(MonthOverrideRow.created_at)):
        corr.append([o.emp_no, names.get(o.emp_no, o.emp_no), "Month figure", None,
                     f"{OVERRIDE_LABELS.get(o.field, o.field)} = {o.value}", o.reason, o.created_by, o.created_at])
    ws = wb.create_sheet("Corrections")
    _table(ws, 5, ["Employee No", "Name", "Correction", "Day", "What was changed", "Reason", "By", "When"],
           corr, [10, 24, 13, 12, 40, 50, 14, 17], org,
           f"Manual corrections included in these figures — {period.label} ({len(corr)})")
    for i in range(6, 6 + len(corr)):
        ws.cell(row=i, column=4).number_format = "dd-mmm-yyyy"
        ws.cell(row=i, column=8).number_format = "dd-mmm-yyyy hh:mm"
    if not corr:
        ws.cell(row=6, column=1, value="No manual corrections this month.")

    # ------------------------------------------------------------------ 4. behaviour
    ws = wb.create_sheet("Late & Punctuality")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, r.category, d.get("late_days"), d.get("very_late_days"), d.get("avg_late_min"),
                     MX.hm(d.get("avg_in_min")), MX.hm(d.get("avg_out_min")), d.get("early_exit_days"),
                     d.get("short_days"), d.get("late_sitting_days"), d.get("avg_work_hours")])
    rows.sort(key=lambda x: -(x[4] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", "Category", f"Late days (> {pol.late_grace_min} min)", "Very late (> 1 h)",
                   "Avg minutes late", "Avg in-time", "Avg out-time", "Early exits", f"Days < {pol.min_daily_hours:g} h",
                   "Late sitting days", "Avg hours / day"], rows, [10, 24, 9, 18, 10, 10, 10, 10, 10, 10, 10, 10, 10], org,
           f"Punctuality — {period.label} (information only: 'late coming' is not a deduction under policy)")

    ws = wb.create_sheet("WFH & Locations")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, r.category, d.get("wfh_days"), d.get("partial_wfh_days"),
                     "OVER LIMIT" if (d.get("wfh_days") or 0) > pol.wfh_limit_days else "", d.get("client_days"),
                     d.get("onduty_days"), d.get("biometric_swipes"), d.get("mobile_swipes"), d.get("mobile_office_swipes")])
    rows.sort(key=lambda x: -(x[4] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", "Category", "WFH days", "of which partial", f"> {pol.wfh_limit_days:g} days",
                   "Client-location days", "On-duty days", "Biometric swipes", "Mobile swipes", "Mobile 'Office' swipes"],
           rows, [10, 24, 9, 18, 9, 9, 12, 10, 10, 10, 10, 12], org, f"WFH & work locations — {period.label}")
    for i, row in enumerate(rows, start=6):
        if row[6]:
            ws.cell(row=i, column=7).fill = AMBER

    # ------------------------------------------------------------------ mobile sign-ins with place names
    from app.services import locations as LOC
    L = LOC.report(db, period)
    if L["rows"]:
        ws = wb.create_sheet("Sign-ins & Places")
        _table(ws, 5, ["When", "Employee No", "Name", "Tagged as", "Place", "Remark", "Permission reason",
                       "Phone", "Status", "Latitude", "Longitude"],
               [[r["ts"], r["emp_no"], r["name"], r["type"] if r["swipe_type"].lower() != "permission" else "Permission",
                 r["place"], r["note"], r["reason"], r["device"], r["approval"], r["lat"], r["lon"]]
                for r in L["rows"]],
               [16, 10, 22, 14, 44, 30, 22, 16, 10, 11, 11], org,
               f"Mobile sign-ins and places — {period.label} (place names from the built-in offline map of India)")
        for i in range(6, 6 + len(L["rows"])):
            ws.cell(row=i, column=1).number_format = "dd-mmm-yyyy hh:mm"
        ws = wb.create_sheet("Places")
        _table(ws, 5, ["Place", "Mostly tagged", "Tags", "Sign-ins", "People", "Who", "Remarks", "First", "Last"],
               [[b["place"], b["main_type"], b["types"], b["count"], b["people"], ", ".join(b["people_names"]),
                 b["notes"], b["first"], b["last"]] for b in L["places"]],
               [44, 14, 30, 9, 8, 40, 36, 16, 16], org, f"Places people signed in from — {period.label}")
        for row in ws.iter_rows(min_row=6):
            for ci in (8, 9):
                if row[ci - 1].value:
                    row[ci - 1].number_format = "dd-mmm-yyyy hh:mm"

    ws = wb.create_sheet("Regularisation & Punches")
    rows = []
    for r in res:
        d = r.data
        rows.append([r.emp_no, r.name, r.group, r.category, d.get("regularised_days"), d.get("permission_swipes"),
                     "Yes" if d.get("auto_attendance") else "", d.get("total_swipes")])
    rows.sort(key=lambda x: -(x[4] or 0))
    _table(ws, 5, ["Employee No", "Name", "Group", "Category", "Regularised days", "Permission swipes",
                   "Auto attendance", "Total swipes"], rows, [10, 24, 9, 18, 12, 12, 10, 10],
           org, f"Regularisation — {period.label}")

    # punch exceptions, day by day (same list as the Punch exceptions page)
    from app.services.exceptions import month_exceptions
    X = month_exceptions(db, period)
    ws = wb.create_sheet("Punch Exceptions")
    _table(ws, 5, ["Date", "Day", "Employee No", "Name", "Group", "Category", "Issues", "In", "Out", "Hours", "Punches",
                   "Permission reason", "Remark", "greytHR status", "Corrected in app", "Notes"],
           [[date.fromisoformat(x["date"]), x["wd"], x["emp_no"], x["name"], x["group"], x.get("category", ""),
             ", ".join(X["labels"][k] + (f" {x['late_min']} min" if k == "very_late" and x["late_min"] else "")
                       for k in x["issues"]),
             x["in"], x["out"], round(x["work_min"] / 60, 2) if x["work_min"] else None, x["punches"], x["reason"],
             x["remark"], x["status"], "Yes" if x["overridden"] else "", x["notes"]] for x in X["rows"]],
           [11, 6, 10, 24, 9, 18, 40, 7, 7, 7, 40, 24, 24, 10, 9, 50], org,
           f"Punch exceptions — {period.label} (incorrect punches, regularisations, WFH, late / early / short, "
           f"unapplied leave, off-day work)")
    for row in ws.iter_rows(min_row=6, max_col=1):
        row[0].number_format = "dd-mmm-yyyy"
    ws = wb.create_sheet("Exceptions by Employee")
    keys = ["edited", "permission", "rejected", "wfh", "wfh_over", "wfh_closed", "wfh_offday", "wfh_leave", "very_late",
            "early", "short", "absent", "offday_work"]
    _table(ws, 5, ["Employee No", "Name", "Group", "Category"] + [X["labels"][k] for k in keys] + ["WFH deduction (days)"],
           [[pp["emp_no"], pp["name"], pp["group"], pp.get("category", "")] + [pp[k] or None for k in keys]
            + [pp["wfh_deduction"] or None] for pp in X["people"]],
           [10, 24, 9, 18] + [11] * (len(keys) + 1), org, f"Exceptions by employee — {period.label}")

    ws = wb.create_sheet("Comp-off Ledger")
    rows = [[r.emp_no, r.name, r.group, r.category, (r.data or {}).get("compoff_earned") or 0,
             (r.data or {}).get("cof_days") or 0] for r in res]
    _table(ws, 5, ["Employee No", "Name", "Group", "Category", "Comp-off earned", "Comp-off taken"], rows,
           [10, 24, 9, 18, 14, 14], org, f"Comp-off — {period.label} (earned for ≥ {pol.comp_off_min_hours:g} h on a "
                                         "weekly off / holiday; taken = COF days in greytHR)")

    # ------------------------------------------------------------------ insights scorecard (docs/METRICS.md "Insights")
    from app.services import insights as INS
    ins = INS.insights(db, period, with_matrix=False)
    ik = ins["kpis"]
    ws = wb.create_sheet("Scorecard")
    rows = [[r["emp_no"], r["name"], r["group"], r.get("category", ""), r["location"], r["tenure_m"], r["attention"],
             ", ".join(r["attention_why"]), r["attendance_pct"], r["absence_pct"], r["absence_12m_days"], r["absence_12m_spells"],
             r["punctuality_pct"], r["avg_in"], r["avg_hours"], r["utilisation_pct"], r["balance_hours"], r["long_days"],
             r["late_sitting_days"], r["wfh_days"], r["regularised_days"], r["leave_used_fy"]]
            for r in sorted(ins["rows"], key=lambda x: -x["attention"])]
    _table(ws, 6, ["Emp No", "Name", "Group", "Category", "Location", "Tenure (months)", "Attention (0-100)", "Main reasons",
                   "Attendance %", "Unapplied leave (absent) %", "Unapplied leave days (12 m)", "Absence spells (12 m)", "On time %",
                   "Avg first punch", "Avg day (h)", "Hours vs required %", "Balance (h)", "Long days", "Late sitting", "WFH days",
                   "Regularised", f"Leave used {ik['fy_label']}"],
           rows, [10, 24, 9, 18, 10] + [11] * 20, org, f"Employee scorecard — {period.label}")
    ws.cell(row=4, column=1, value=(
        f"Firm: unapplied leave (absent) {ik['absenteeism_pct']}% · on time {ik['punctuality_pct']}% · hours vs required "
        f"{ik['utilisation_pct']}% · attrition (12 m) {ik['attrition_pct']}%. "
        "Attention = unapplied leave (12 m) 30 + punctuality 20 + hours 25 + regularisation 15 + WFH 10."))
    ws.freeze_panes = "C7"
    ws2 = wb.create_sheet("Groups Compared")
    _table(ws2, 5, ["Group", "People", "Attendance %", "Unapplied leave (absent) %", "On time %", "Hours vs required %",
                    "Avg day (h)", "WFH share %", "Regularised %", "Unapplied leave days (12 m)", "Avg attention", "Deduction days"],
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
                v = p.get(kk, "")
                if kk in ("doj", "dol") and isinstance(v, str) and len(v) == 10 and v[4] == "-":
                    v = date.fromisoformat(v)
                c = _put(ws, row, i, v, text=True)
                if isinstance(v, date):
                    c.number_format = "dd-mmm-yyyy"
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
                                    ("REGULARISED", dd.get("regularised")),
                                    ("WFH", dd.get("wfh")), ("OFF-DAY WORK", dd.get("extra")),
                                    ("COMP-OFF EARNED", dd.get("co_earned")), ("OVERRIDE", dd.get("overridden"))) if on]
            rows.append([r.emp_no, r.name, date.fromisoformat(dd["date"]), dd["wd"],
                         {"WORKDAY": "Working day", "WEEKLY_OFF": "Weekly off", "HOLIDAY": "Holiday"}.get(dd["type"], dd["type"])
                         + (f" — {dd['holiday']}" if dd.get("holiday") else ""),
                         dd["s1"], dd["s2"], dd["in"], dd["out"],
                         round(dd["work_min"] / 60, 2) if dd["work_min"] else None, dd.get("late_min") or None,
                         ", ".join(dd.get("locs", [])),
                         _day_places(dd), ", ".join(tags), "; ".join(dd.get("notes", []))])
    _table(ws, 5, ["Employee No", "Name", "Date", "Day", "Day type", "Session 1", "Session 2", "In", "Out", "Hours",
                   "Minutes late", "Tagged as", "Places (punch time · place · remark)", "Tags", "Notes"], rows,
           [10, 22, 11, 5, 18, 8, 8, 7, 7, 7, 8, 22, 60, 30, 40], org, f"Daily detail — {period.label}")
    for i in range(6, 6 + len(rows)):
        ws.cell(row=i, column=3).number_format = "dd-mmm-yyyy"
        ws.cell(row=i, column=10).number_format = "0.00"

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
    from app.services.hours_policies import all_policies
    hps = all_policies(db)
    notes = [
        ("Required hours", f"Hours per day (column T) × days present (E − F − G − H). Hours per day: the person's "
                           f"minimum-hours policy, else their own, else their category's, else the firm's {pol.hours_per_day:g} h"),
        ("Minimum hours policies", "; ".join(f"{x['name']} = {x['hours']:g} h ({len(x.get('emp_nos') or [])} employees: "
                                             f"{', '.join(x.get('emp_nos') or []) or 'none'})" for x in hps) or "none"),
        ("Holiday on a weekly off", "Counted as a weekly off, not a holiday (not in Total Week Days or Holidays)"),
        ("Holiday / off-day work", "Hours worked on weekly-offs and holidays are excluded from actual hours"),
        ("Deduction", f"Shortfall above {pol.shortfall_grace_hours:g} h → {pol.deduction_per_block_days:g} day per "
                      f"{pol.shortfall_block_hours:g} h block (rounded up)"),
        ("Exempt groups", ", ".join(pol.exempt_groups)),
        ("Comp-off", f"Earned for ≥ {pol.comp_off_min_hours:g} h on a weekly-off / holiday"),
        ("WFH limit", f"{pol.wfh_limit_days:g} days per month ({pol.wfh_method.replace('_', ' ')})"),
        ("Late arrival", f"First-in more than {pol.late_grace_min} min after shift start (information only)"),
        ("Loss of pay", "LOP / LWP in greytHR counts as leave taken (Leaves, G) — there is no separate LOP (v1.13)"),
        ("Unapplied leave (absent)", "Absent on a working day without leave applied (greytHR A) — column H"),
        ("Ignored groups", (", ".join(pol.ignored_groups) or "none") + " — not in any sheet"),
        ("Leave balances", "Kept in greytHR — not calculated by the app. The deduction days (column V) are what to "
                           "take from leave."),
        ("Source", "greytHR Muster (day status & hours), Swipe Details (punches/locations), DOJ report, app master data"),
    ]
    _table(ws, 5, ["Rule", "Applied as"], [list(n) for n in notes], [26, 110], org, "Rules applied in this workbook")
    ws.auto_filter.ref = None  # noqa
    ws.freeze_panes = "A6"
    for i in range(6, 6 + len(notes)):
        ws.cell(row=i, column=2).alignment = Alignment(wrap_text=True, vertical="top")

    status = _status(period)
    kk = m.get("kpis", {})
    open_flags = sum(1 for f in m["flags"] if f["code"] in REVIEW_CODES and not rv.get((f["emp_no"], f["code"])))
    _finish(wb, f"Attendance MIS — {period.label}", org, status, contents_lines=[
        ("Month", period.label + (" — locked" if status else "")),
        ("Employees", len(res)),
        ("People with a leave deduction", sum(1 for r in res if (r.data or {}).get("deduction_days"))),
        ("Leave deduction (days)", round(sum((r.data or {}).get("deduction_days") or 0 for r in res), 2)),
        ("of which hours shortfall (days)", round(sum((r.data or {}).get("shortfall_deduction_days") or 0 for r in res
                                                      if (r.data or {}).get("deduction_days")), 2)),
        ("of which WFH over the limit (days)", round(sum((r.data or {}).get("wfh_deduction_days") or 0 for r in res
                                                         if (r.data or {}).get("deduction_days")), 2)),
        ("Open exceptions (not reviewed)", open_flags),
        ("Manual corrections", len(corr)),
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------- finishing touches (v1.8)
SHEET_GUIDE = [
    # (sheet, tab colour, one line on what it is for) — also the order of the workbook
    ("Contents", NAVY, "This page: key figures and every sheet."),
    ("Payroll Leave Sheet", NAVY, "The month for payroll in the MIS layout — live formulas; edit E–H, J, O, P or T and the deduction recalculates."),
    ("Payroll Input", NAVY, "Only what payroll needs: people with a leave deduction, split into hours shortfall and WFH over the limit, with the reason (linked to the payroll sheet)."),
    ("Comp-off Ledger", NAVY, "Comp-off earned and taken per person this month."),
    ("Corrections", NAVY, "Every manual correction included in these figures, with the reason, who and when."),
    ("Flags", NAVY, "Exceptions found by the app: WFH above the limit and Group not set need a review (open first); the rest are reviewed automatically."),
    ("Punch Exceptions", "C77700", "Day by day: incorrect punches, regularisations, WFH, late, early, short, unapplied leave."),
    ("Exceptions by Employee", "C77700", "The same exceptions counted per person."),
    ("Regularisation & Punches", "C77700", "Regularised days and permission swipes per person."),
    ("Late & Punctuality", "C77700", "Late arrivals, early exits, short days, late sitting (information only)."),
    ("WFH & Locations", "C77700", "WFH days (limit), client / on-duty days, biometric vs mobile punches."),
    ("Board Summary", "2E7D32", "Firm-level figures for partners, and by group."),
    ("Scorecard", "2E7D32", "Per person: attention score and its reasons, attendance, punctuality, hours, leave used."),
    ("Groups Compared", "2E7D32", "Groups side by side."),
    ("People Movement", "2E7D32", "Joiners, exits, possible exits, anniversaries."),
    ("Reconciliation", "2E7D32", "App vs the manual MIS sheet, employee by employee."),
    ("Daily Detail", "7A869A", "Every person, every day: status, in / out, hours, places, tags, notes."),
    ("Sign-ins & Places", "7A869A", "Every mobile sign-in with its place name."),
    ("Places", "7A869A", "Places people signed in from."),
    ("Rules Applied", "7A869A", "The rules and settings behind these figures."),
]
NO_HIDE = {"Contents", "Payroll Leave Sheet", "Payroll Input", "Comp-off Ledger", "Board Summary", "People Movement",
           "Rules Applied"}


def _header_row(ws):
    fp = ws.freeze_panes
    if not fp:
        return None
    import re as _re
    m = _re.match(r"[A-Z]+(\d+)$", fp)
    return int(m.group(1)) - 1 if m and int(m.group(1)) > 1 else None


def _polish(ws, footer: str, a3: bool = False, hide_empty: bool = True) -> None:
    """Print setup (landscape, fit to one page wide, header row repeated, footer), number formats by header, and
    columns that are empty for the whole sheet hidden."""
    from openpyxl.worksheet.properties import PageSetupProperties
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3 if a3 else ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.oddFooter.left.text = footer
    ws.oddFooter.right.text = "Page &P of &N"
    hr = _header_row(ws)
    if not hr:
        return
    ws.print_title_rows = f"{hr}:{hr}"
    last = ws.max_row
    for col in range(1, ws.max_column + 1):
        h = str(ws.cell(row=hr, column=col).value or "")
        fmt = "0.00000" if h in ("Latitude", "Longitude") else "0.0" if "%" in h else None
        if fmt:
            for r in range(hr + 1, last + 1):
                c = ws.cell(row=r, column=col)
                if isinstance(c.value, (int, float)):
                    c.number_format = fmt
        if hide_empty and ws.title not in NO_HIDE and last > hr and h not in ("Employee No", "Name"):
            if all(ws.cell(row=r, column=col).value in (None, "") for r in range(hr + 1, last + 1)):
                ws.column_dimensions[get_column_letter(col)].hidden = True


def _finish(wb, title: str, org: dict, status: str, guide=None, contents_lines=None) -> None:
    """Workbook properties, tab colours, order, a Contents sheet with hyperlinks, print setup on every sheet."""
    from app import __version__
    guide = guide or SHEET_GUIDE
    wb.properties.title = title
    wb.properties.creator = org.get("org_name") or "HSCO"
    wb.properties.keywords = "; ".join(x for x in (status, f"app v{__version__}") if x)
    footer = " · ".join(x for x in (org.get("org_short") or org.get("org_name", ""), title, status) if x)
    for ws in wb.worksheets:
        _polish(ws, footer, a3=ws.title == "Payroll Leave Sheet")
    if contents_lines is not None:
        ws = wb.create_sheet("Contents", 0)
        ws["A1"] = None if _logo(ws, org) else org.get("org_name", "")
        ws["A1"].font = Font(bold=True, size=14, color=NAVY)
        ws["A2"] = title
        ws["A2"].font = Font(bold=True, size=12)
        ws["A3"] = " · ".join(x for x in (status, f"generated {datetime.now():%d-%b-%Y %H:%M} from the app's current figures",
                                           f"app v{__version__}") if x)
        ws["A3"].font = Font(italic=True, color="666666")
        r = 5
        ws.cell(row=r, column=1, value="Key figures").font = Font(bold=True, size=12)
        for k, v in contents_lines:
            r += 1
            ws.cell(row=r, column=1, value=k)
            c = ws.cell(row=r, column=2, value=v)
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="left")
        r += 2
        ws.cell(row=r, column=1, value="Sheets").font = Font(bold=True, size=12)
        names = set(wb.sheetnames)
        for name, _colour, what in guide:
            if name == "Contents" or name not in names:
                continue
            r += 1
            c = ws.cell(row=r, column=1, value=name)
            c.hyperlink = f"#'{name}'!A1"
            c.font = Font(color="1F5FBF", underline="single")
            ws.cell(row=r, column=2, value=what).alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 110
        _polish(ws, footer, hide_empty=False)
    # order and tab colours
    order = [n for n, _c, _w in guide if n in wb.sheetnames]
    rest = [ws.title for ws in wb.worksheets if ws.title not in order]
    wb._sheets = [wb[n] for n in order + rest]
    colours = {n: c for n, c, _w in guide}
    for ws in wb.worksheets:
        if ws.title in colours:
            ws.sheet_properties.tabColor = colours[ws.title]
    wb.active = 0
