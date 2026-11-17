"""Detect and parse every greytHR export / template the firm uses. See docs/DATA_ANALYSIS.md."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.engine.model import MusterDay, Swipe

# --------------------------------------------------------------------------- helpers

def norm(h: Any) -> str:
    s = "" if h is None else str(h)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


_DT_FORMATS = (
    "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %B %Y %H:%M:%S", "%d %B %Y %H:%M",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
)
_D_FORMATS = ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y")


def parse_dt(v: Any) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        if 20000 < float(v) < 80000:  # Excel serial
            return datetime(1899, 12, 30) + timedelta(days=float(v))
        return None
    s = re.sub(r"\s+", " ", str(v).strip())
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    for f in _D_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in f or "%B" in f else s, f)
        except ValueError:
            pass
    return None


def parse_date(v: Any) -> Optional[date]:
    d = parse_dt(v)
    return d.date() if d else None


def parse_hhmm(v: Any) -> int:
    """'08 : 29' / '186:38' / 8.5 / time -> minutes."""
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(round(float(v) * 60))
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return v.hour * 60 + v.minute
    s = str(v).replace(" ", "")
    m = re.match(r"^(-?)(\d+):(\d{1,2})(?::\d{1,2})?$", s)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3))
        return -mins if m.group(1) else mins
    try:
        return int(round(float(s) * 60))
    except ValueError:
        return 0


def num(v: Any, default: float = 0.0) -> float:
    if v in (None, ""):
        return default
    try:
        return float(str(v).strip())
    except ValueError:
        return default


def emp_id(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


_COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*[, ]\s*(-?\d{1,3}\.\d{3,})")
_PERIOD_RE = re.compile(r"from\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+to\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.I)


# --------------------------------------------------------------------------- detection

SIGNATURES = {
    "muster": {"employee no", "attendance date", "session1 status", "session2 status"},
    "swipes": {"employee no", "swipe date"},
    "summary": {"employee no", "total week days", "hours required"},
    "doj": {"employee number", "joined on"},
    "mis_manual": {"employee no", "group", "total week days", "hours required to be completed"},
    "leave_opening": {"employee no", "leave balance"},
    "holidays": {"date", "holiday"},
    "employee_master": {"employee no", "group"},
}
KIND_LABELS = {
    "muster": "Attendance Muster Report (greytHR)",
    "swipes": "Employee Swipe Details (greytHR)",
    "summary": "Attendance Summary Report (greytHR)",
    "doj": "Date of Joining Report (greytHR)",
    "mis_manual": "Manual MIS working (for reconciliation / employee master)",
    "leave_opening": "Leave / comp-off opening balances",
    "holidays": "Holiday list",
    "employee_master": "Employee master",
}
PRIORITY = ["muster", "swipes", "summary", "doj", "mis_manual", "leave_opening", "holidays", "employee_master"]


@dataclass
class DetectedSheet:
    sheet: str
    kind: str
    header_row: int
    headers: list
    title_lines: list = field(default_factory=list)


def detect(sheets: dict[str, list[list]]) -> list[DetectedSheet]:
    found = []
    for name, rows in sheets.items():
        best = None
        for i, row in enumerate(rows[:15]):
            hs = {norm(c) for c in row if c not in (None, "")}
            if len(hs) < 2:
                continue
            for kind in PRIORITY:
                if SIGNATURES[kind] <= hs:
                    best = DetectedSheet(name, kind, i, [norm(c) for c in row],
                                         [str(r[0]) for r in rows[:i] if r and r[0]])
                    break
            if best:
                break
        if best:
            found.append(best)
    return found


def detect_period(ds: DetectedSheet, rows: list[list]) -> Optional[tuple[int, int]]:
    for t in ds.title_lines:
        m = _PERIOD_RE.search(t)
        if m:
            d = parse_date(m.group(1))
            if d:
                return d.year, d.month
    return None


def _col(headers: list, *names: str, required: bool = True) -> Optional[int]:
    for n in names:
        if n in headers:
            return headers.index(n)
    for n in names:  # prefix match
        for i, h in enumerate(headers):
            if h.startswith(n):
                return i
    if required:
        raise ValueError(f"Column not found: {names[0]}")
    return None


def _data_rows(rows, ds):
    for r in rows[ds.header_row + 1:]:
        if r is None or all(v in (None, "") for v in r):
            continue
        yield r


def _get(r, i):
    return r[i] if i is not None and i < len(r) else None


# --------------------------------------------------------------------------- parsers

def parse_muster(rows, ds) -> tuple[list[MusterDay], list[str]]:
    h = ds.headers
    c_name, c_emp, c_date = _col(h, "name", required=False), _col(h, "employee no"), _col(h, "attendance date")
    c_s1, c_s2 = _col(h, "session1 status"), _col(h, "session2 status")
    c_in, c_out = _col(h, "in time", required=False), _col(h, "out time", required=False)
    c_shift, c_hrs = _col(h, "shift name", required=False), _col(h, "total work hours", required=False)
    out, warn = [], []
    for n, r in enumerate(_data_rows(rows, ds)):
        e = emp_id(_get(r, c_emp))
        d = parse_date(_get(r, c_date))
        if not e or not d:
            warn.append(f"row {ds.header_row + n + 2}: missing employee/date — skipped")
            continue
        it, ot = parse_dt(_get(r, c_in)), parse_dt(_get(r, c_out))
        out.append(MusterDay(emp_no=e, day=d, s1=str(_get(r, c_s1) or "").strip().upper(),
                             s2=str(_get(r, c_s2) or "").strip().upper(), in_time=it, out_time=ot,
                             work_min=parse_hhmm(_get(r, c_hrs)), shift=str(_get(r, c_shift) or "").strip(),
                             name=str(_get(r, c_name) or "").strip()))
    return out, warn


def parse_swipes(rows, ds) -> tuple[list[Swipe], dict, list[str]]:
    h = ds.headers
    c_emp, c_name = _col(h, "employee no"), _col(h, "employee name", "name", required=False)
    c_ts = _col(h, "swipe date")
    c_door = _col(h, "door/address", "door", "address", required=False)
    c_loc = _col(h, "location type", required=False)
    c_type = _col(h, "swipe type", required=False)
    c_lat = next((i for i, x in enumerate(h) if x in ("lat", "latitude") or x.endswith(" latitude")), None)
    c_lon = next((i for i, x in enumerate(h) if x in ("lon", "lng", "long", "longitude") or x.endswith(" longitude")), None)
    out, names, warn = [], {}, []
    for n, r in enumerate(_data_rows(rows, ds)):
        e = emp_id(_get(r, c_emp))
        ts = parse_dt(_get(r, c_ts))
        if not e or not ts:
            warn.append(f"row {ds.header_row + n + 2}: missing employee/time — skipped")
            continue
        lat = lon = None
        if c_lat is not None and c_lon is not None:
            lat, lon = num(_get(r, c_lat), None), num(_get(r, c_lon), None)
        if lat is None:
            for v in r:
                if isinstance(v, str):
                    m = _COORD_RE.search(v)
                    if m:
                        lat, lon = float(m.group(1)), float(m.group(2))
                        break
        if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
            lat = lon = None
        if c_name is not None:
            names[e] = str(_get(r, c_name) or "").strip()
        out.append(Swipe(emp_no=e, ts=ts, door=str(_get(r, c_door) or "").strip(),
                         location_type=str(_get(r, c_loc) or "").strip(),
                         swipe_type=str(_get(r, c_type) or "").strip(), lat=lat, lon=lon))
    return out, names, warn


def parse_summary(rows, ds) -> list[dict]:
    h = ds.headers
    cols = {k: _col(h, *v, required=False) for k, v in {
        "emp_no": ("employee no",), "name": ("employee name",), "location": ("location",),
        "present": ("present days",), "absent": ("absent days",), "leave": ("leave days",),
        "on_duty": ("on duty days",), "holiday": ("holiday days",), "week_days": ("total week days",),
        "total_hours": ("total hours",), "hours_required": ("hours required",), "leave_hours": ("leave hours",),
        "short_fall": ("short fall",), "excess": ("excess hours",)}.items()}
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_get(r, cols["emp_no"]))
        if not e:
            continue
        out.append({k: (str(_get(r, i)).strip() if _get(r, i) is not None else "") for k, i in cols.items()} | {"emp_no": e})
    return out


def parse_doj(rows, ds) -> list[dict]:
    h = ds.headers
    c_emp, c_name, c_doj = _col(h, "employee number"), _col(h, "employee name", required=False), _col(h, "joined on")
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_get(r, c_emp))
        if not e or e.lower().startswith("total number"):
            continue
        out.append({"emp_no": e, "name": str(_get(r, c_name) or "").strip(), "doj": parse_date(_get(r, c_doj))})
    return out


def parse_mis_manual(rows, ds) -> list[dict]:
    """Manual MIS 'Reference' sheet → master fields + the manual figures (for reconciliation)."""
    h = ds.headers
    m = {
        "emp_no": ("employee no",), "name": ("name",), "group": ("group",), "location": ("location",),
        "total_week_days": ("total week days",), "holidays": ("holiday",), "leave_days": ("days leaves taken",),
        "absent_days": ("days absent",), "days_present": ("days present",), "wfh_days": ("wfh",),
        "total_hours_text": ("total hours present",), "extra_days": ("extra working days",),
        "required_hours": ("hours required to be completed",), "total_hours": ("adjusted total hours present",),
        "extra_hours": ("working on holiday to exclude",), "actual_hours": ("actual hours worked",),
        "balance_hours": ("shortfall / excess",), "remark": ("remark",),
    }
    cols = {k: _col(h, *v, required=False) for k, v in m.items()}
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_get(r, cols["emp_no"]))
        if not e or not re.match(r"^[A-Z0-9]+$", e):
            continue
        rec = {"emp_no": e}
        for k, i in cols.items():
            if k == "emp_no":
                continue
            v = _get(r, i)
            if k in ("name", "group", "location", "remark", "total_hours_text"):
                rec[k] = "" if v is None else str(v).strip()
            else:
                rec[k] = num(v, None)
        if rec.get("total_hours") is None and rec.get("total_hours_text"):
            rec["total_hours"] = parse_hhmm(rec["total_hours_text"]) / 60
        out.append(rec)
    return out


_BOOL_T = {"y", "yes", "true", "1", "exempt"}


def parse_employee_master(rows, ds) -> list[dict]:
    h = ds.headers
    cols = {k: _col(h, *v, required=False) for k, v in {
        "emp_no": ("employee no", "employee number", "emp no"), "name": ("name", "employee name"),
        "group": ("group",), "location": ("location",), "weekly_off": ("weekly off pattern", "weekly off"),
        "working_saturdays": ("working saturdays",), "hours_per_day": ("hours per day",),
        "exempt": ("exempt from deduction", "exempt"), "exempt_reason": ("exempt reason",),
        "holiday_calendar": ("holiday calendar",), "doj": ("date of joining", "doj", "joined on"),
        "dol": ("date of leaving", "dol", "left on"), "email": ("email",), "department": ("department",),
        "designation": ("designation",)}.items()}
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_get(r, cols["emp_no"]))
        if not e:
            continue
        rec = {"emp_no": e}
        for k, i in cols.items():
            if k == "emp_no" or i is None:
                continue
            v = _get(r, i)
            if k in ("doj", "dol"):
                rec[k] = parse_date(v)
            elif k == "hours_per_day":
                rec[k] = num(v, None)
            elif k == "exempt":
                rec[k] = str(v or "").strip().lower() in _BOOL_T
            else:
                rec[k] = "" if v is None else str(v).strip()
        out.append(rec)
    return out


def parse_leave_opening(rows, ds) -> list[dict]:
    h = ds.headers
    c_emp = _col(h, "employee no")
    c_lb = _col(h, "leave balance")
    c_cb = _col(h, "comp off balance", "compoff balance", "comp-off balance", required=False)
    c_asof = _col(h, "as of", "as on", "as at", required=False)
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_get(r, c_emp))
        if e:
            out.append({"emp_no": e, "leave_balance": num(_get(r, c_lb), None),
                        "compoff_balance": num(_get(r, c_cb), None) if c_cb is not None else None,
                        "as_of": parse_date(_get(r, c_asof)) if c_asof is not None else None})
    return out


def parse_holidays(rows, ds) -> list[dict]:
    h = ds.headers
    c_d, c_n = _col(h, "date"), _col(h, "holiday")
    c_cal = _col(h, "calendar", required=False)
    out = []
    for r in _data_rows(rows, ds):
        d = parse_date(_get(r, c_d))
        if d:
            out.append({"date": d, "name": str(_get(r, c_n) or "").strip(),
                        "calendar": str(_get(r, c_cal) or "").strip() if c_cal is not None else ""})
    return out


def infer_period_from_dates(dates: list[date]) -> Optional[tuple[int, int]]:
    if not dates:
        return None
    (y, m), _ = Counter((d.year, d.month) for d in dates).most_common(1)[0]
    return y, m
