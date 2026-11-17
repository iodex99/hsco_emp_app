"""Universal importer: recognise and parse attendance reports by MEANING, not by exact layout.

How a sheet is understood (docs/ARCHITECTURE.md "Importer"):
1. Find the header row (any of the first 30 rows) and normalise each header ("In Time  [Asia/Kolkata]" → "in time").
2. Score every header against each report type's fields using synonyms (exact, whole-word containment, fuzzy
   similarity). Column ORDER never matters.
3. Check the values under each candidate column (dates must parse as dates, IDs look like IDs, coordinates are in
   range …); a header whose data doesn't fit is rejected.
4. If a required field has no header, infer it from the data (e.g. the only column full of dates).
5. Pick the report type with the best score whose required fields are all present. Low confidence → the import
   screen asks the user to confirm/correct the column mapping, and that layout is remembered (settings
   `import_layouts`) so the same report is recognised automatically next time.
Values are normalised too: dates in any common format or Excel serials, times with or without a date, hours as
"08 : 29" / "8:29" / 8.48 / "8h 29m", status as codes (P, A, PL…) or words (Present, Week Off, Half Day…), split
date + time columns, a single status column or two session columns, coordinates in one "lat, long" cell or two columns.
"""
from __future__ import annotations

import difflib
import functools
import hashlib
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from app.engine.model import DEFAULT_STATUS_MAP, MusterDay, Swipe

# ============================================================================ value parsing

def norm(h: Any) -> str:
    s = "" if h is None else str(h)
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = s.replace("_", " ").replace("/", " / ")
    s = re.sub(r"[^0-9a-zA-Z/+#%&]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


_DT_FORMATS = (
    "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %B %Y %H:%M:%S", "%d %B %Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %I:%M %p", "%d/%m/%Y %I:%M:%S %p", "%d-%b-%Y %I:%M %p",
    "%d %b %Y %I:%M %p", "%d-%m-%Y %I:%M %p", "%Y-%m-%d %I:%M %p", "%d.%m.%Y %H:%M",
)
_D_FORMATS = ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d.%m.%Y",
              "%d-%m-%Y", "%d %b %y", "%b %d, %Y", "%B %d, %Y", "%d-%B-%Y", "%a, %d %b %Y", "%A, %d %B %Y")
_T_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p")


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v).strip())


def parse_dt(v: Any) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < float(v) < 80000:  # Excel serial (with optional fraction of a day)
            return datetime(1899, 12, 30) + timedelta(days=float(v))
        return None
    return _parse_dt_str(_clean(v))


@functools.lru_cache(maxsize=65536)
def _parse_dt_str(s: str) -> Optional[datetime]:
    if not s or not any(ch.isdigit() for ch in s) or len(s) > 40:
        return None
    s = re.sub(r"(?i)(?<=\d)(st|nd|rd|th)\b", "", s)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?", s):
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    for f in _D_FORMATS:
        try:
            return datetime.strptime(s.title() if ("%b" in f or "%B" in f or "%a" in f or "%A" in f) else s, f)
        except ValueError:
            pass
    return None


def parse_date(v: Any) -> Optional[date]:
    d = parse_dt(v)
    return d.date() if d else None


def parse_time(v: Any) -> Optional[time]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= float(v) < 1:   # Excel time fraction
        secs = round(float(v) * 86400)
        return time(secs // 3600 % 24, secs // 60 % 60, secs % 60)
    s = _clean(v).upper().replace(".", ":") if re.fullmatch(r"\d{1,2}\.\d{2}", _clean(v)) else _clean(v).upper()
    for f in _T_FORMATS:
        try:
            return datetime.strptime(s, f).time()
        except ValueError:
            pass
    d = parse_dt(v)
    return d.time() if d and (d.hour or d.minute or d.second) else None


def parse_hhmm(v: Any) -> int:
    """'08 : 29' / '186:38' / 8.5 / '8h 29m' / time(8,29) -> minutes."""
    if v in (None, ""):
        return 0
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(round(float(v) * 60))       # decimal hours (8.5 → 510)
    if isinstance(v, timedelta):               # [h]:mm cells, may exceed 24 h
        return int(v.total_seconds() // 60)
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return v.hour * 60 + v.minute
    s = str(v).strip().lower().replace(" ", "")
    m = re.match(r"^(\d+)days?,(\d+):(\d{2})(?::\d{2})?$", s)          # str(timedelta) e.g. "1 day, 2:05:00"
    if m:
        return int(m.group(1)) * 1440 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.match(r"^(-?)(\d+):(\d{1,2})(?::\d{1,2})?$", s)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3))
        return -mins if m.group(1) else mins
    m = re.match(r"^(\d+)h(?:rs?|ours?)?(\d+)?m?(?:in|ins|inutes?)?$", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.match(r"^(\d+)m(?:in|ins|inutes?)?$", s)
    if m:
        return int(m.group(1))
    try:
        return int(round(float(s) * 60))
    except ValueError:
        return 0


def num(v: Any, default: float = 0.0) -> float:
    if v in (None, ""):
        return default
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return default


def emp_id(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


_STATUS_WORDS = {   # words only — short codes (P, A, PL, WO, PH, AB…) are kept as written; see status_codes()
    "present": "P", "work from home": "WFH", "on duty": "OD",
    "absent": "A", "unpaid leave": "LOP", "leave without pay": "LOP", "loss of pay": "LOP",
    "week off": "OFF", "weekly off": "OFF", "weekoff": "OFF", "rest day": "OFF",
    "holiday": "H", "public holiday": "H", "festival holiday": "H",
    "leave": "PL", "paid leave": "PL", "privilege leave": "PL", "earned leave": "PL", "casual leave": "CL",
    "sick leave": "SL", "comp off": "COF", "compoff": "COF", "comp-off": "COF", "compensatory off": "COF",
}


def status_codes(v: Any) -> tuple[str, str]:
    """Status cell → (session1, session2). 'P', 'PL/P', 'Present', 'Week Off', 'Half Day' (P/A), 'Half Day Leave'
    (PL/P), 'Half Day Absent' (A/P), 'P(HD)' (P/A), 'PL (HD)' (PL/P). Short codes are kept exactly as written."""
    s = _clean(v) if v not in (None, "") else ""
    if not s:
        return "", ""
    low = s.lower()
    m = re.fullmatch(r"([a-z]{1,4})\s*\(\s*(?:hd|half(?: day)?)\s*\)", low)
    if m:
        code = status_codes(m.group(1))[0]
        return (code, "A") if code == "P" else (code, "P")
    if low in ("hd", "p/2", "½p"):
        return "P", "A"
    if re.search(r"\bhalf\b", low):
        rest = re.sub(r"\bhalf\b|\bday\b|[-:()]", " ", low).strip()
        code = status_codes(rest)[0] if rest else "P"
        return ("P", "A") if code in ("P", "") else (code, "P")
    if "/" in s:
        a, b = [x.strip() for x in s.split("/", 1)]
        return status_codes(a)[0], status_codes(b)[0]
    word = _STATUS_WORDS.get(low.strip(" ."))
    code = word if word and (len(low) > 4 or " " in low) else s.upper().strip()
    return code, code


_COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*[,; ]\s*(-?\d{1,3}\.\d{3,})")
_PERIOD_RE = re.compile(r"(?:from|for|period)\s*:?\s*(\d{1,2}[\s\-/][A-Za-z]+[\s\-/]\d{2,4}|\d{1,2}/\d{1,2}/\d{4})\s*(?:to|-)\s*"
                        r"(\d{1,2}[\s\-/][A-Za-z]+[\s\-/]\d{2,4}|\d{1,2}/\d{1,2}/\d{4})", re.I)
_MONTH_TITLE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-']+(\d{4})\b", re.I)

# ============================================================================ report schemas

# field → (synonyms, value type). Value types drive validation and inference.
F = {
    "emp_no": (["employee no", "employee number", "emp no", "emp code", "employee code", "employee id", "emp id", "staff id",
                "staff no", "staff code", "ecode", "e code", "empid", "emp num", "personnel no", "payroll no",
                "employee", "emp", "id", "code"], "id"),
    "name": (["employee name", "name", "full name", "emp name", "staff name", "name of employee"], "text"),
    "date": (["attendance date", "date", "work date", "day date", "attendance day", "punch date", "for date", "on date"], "date"),
    "s1": (["session1 status", "session 1 status", "first half", "first half status", "1st half", "session1", "fh status",
            "first session"], "status"),
    "s2": (["session2 status", "session 2 status", "second half", "second half status", "2nd half", "session2",
            "sh status", "second session"], "status"),
    "status": (["status", "attendance status", "day status", "attendance", "present absent", "attendance type",
                "remarks status", "final status"], "status"),
    "in_time": (["in time", "first in", "check in", "punch in", "time in", "sign in", "entry time", "in punch", "login time",
                 "in", "clock in", "first punch", "start time"], "time"),
    "out_time": (["out time", "last out", "check out", "punch out", "time out", "sign out", "exit time", "out punch",
                  "logout time", "out", "clock out", "last punch", "end time"], "time"),
    "hours": (["total work hours", "work hours", "working hours", "total hours", "hours worked", "duration", "worked hours",
               "effective hours", "net hours", "work duration", "total duration", "hrs worked", "actual hours"], "hours"),
    "shift": (["shift name", "shift", "shift code", "shift timing"], "text"),
    "timestamp": (["swipe date", "swipe time", "punch time", "punch date time", "punch datetime", "timestamp", "date time",
                   "datetime", "log time", "event time", "swipe date time", "punch", "log date"], "datetime"),
    "time": (["time", "punch time", "swipe time", "log time", "clock time"], "time"),
    "door": (["door / address", "door", "address", "device", "reader", "terminal", "device name", "machine",
              "sign in address", "location address", "punch address"], "text"),
    "location_type": (["location type", "work location type", "mode", "work mode", "attendance mode", "punch mode",
                       "location category"], "text"),
    "swipe_type": (["swipe type", "punch type", "source", "entry type", "method", "punch source", "device type"], "text"),
    "direction": (["in / out", "in out", "direction", "punch direction", "entry exit", "io type"], "text"),
    "lat": (["latitude", "lat", "geo lat", "gps lat"], "lat"),
    "lon": (["longitude", "lon", "lng", "long", "geo long", "gps long", "gps lng"], "lon"),
    "coords": (["coordinates", "geo location", "geolocation", "gps", "lat long", "lat / long", "location coordinates",
                "geo coordinates", "lat lng", "sign in location", "punch location", "location"], "coords"),
    "doj": (["joined on", "date of joining", "doj", "joining date", "join date", "date joined", "hire date", "start date"], "date"),
    "dol": (["date of leaving", "dol", "leaving date", "exit date", "last working day", "relieving date", "lwd",
             "date of exit"], "date"),
    "group": (["group", "category", "employee group", "cadre", "staff category", "grade group"], "text"),
    "location": (["location", "branch", "office", "work location", "city", "base location"], "text"),
    "department": (["department", "dept", "division", "team"], "text"),
    "designation": (["designation", "title", "job title", "role", "position"], "text"),
    "email": (["email", "e mail", "email id", "mail id", "work email"], "text"),
    "weekly_off": (["weekly off pattern", "weekly off", "week off pattern", "weekoff pattern"], "text"),
    "working_saturdays": (["working saturdays", "working saturday"], "text"),
    "hours_per_day": (["hours per day", "required hours per day", "daily hours"], "number"),
    "exempt": (["exempt from deduction", "exempt", "exemption"], "text"),
    "exempt_reason": (["exempt reason", "exemption reason"], "text"),
    "holiday_calendar": (["holiday calendar", "calendar name"], "text"),
    "leave_balance": (["leave balance", "pl balance", "privilege leave balance", "earned leave balance", "el balance",
                       "balance leave", "leaves balance", "available leave"], "number"),
    "compoff_balance": (["comp off balance", "compoff balance", "comp off", "co balance", "compensatory off balance"], "number"),
    "as_of": (["as of", "as on", "as at", "balance date", "date"], "date"),
    "holiday_name": (["holiday", "holiday name", "occasion", "festival", "description"], "text"),
    "calendar": (["calendar", "holiday calendar", "location"], "text"),
    # greytHR Attendance Summary / manual MIS figures
    "present": (["present days", "days present", "present"], "number"),
    "absent": (["absent days", "days absent", "absent"], "number"),
    "leave": (["leave days", "days leaves taken", "leaves taken", "leave"], "number"),
    "on_duty": (["on duty days"], "number"),
    "holiday": (["holiday days", "holidays", "holiday"], "number"),
    "week_days": (["total week days", "week days", "working days", "total working days"], "number"),
    "total_hours": (["total hours", "total hours present", "hours present"], "hours"),
    "hours_required": (["hours required", "required hours"], "hours"),
    "leave_hours": (["leave hours"], "hours"),
    "short_fall": (["short fall", "shortfall"], "hours"),
    "excess": (["excess hours", "excess"], "hours"),
    "wfh": (["wfh", "work from home", "wfh days"], "number"),
    "extra_days": (["extra working days"], "number"),
    "required_hours_mis": (["hours required to be completed"], "number"),
    "adjusted_hours": (["adjusted total hours present"], "number"),
    "holiday_hours": (["working on holiday to exclude"], "number"),
    "actual_hours": (["actual hours worked"], "number"),
    "balance_hours": (["shortfall / excess"], "number"),
    "remark": (["remark", "remarks", "comment", "comments", "note", "notes"], "text"),
    "days_present_mis": (["days present"], "number"),
}

# kind → required (each item: field or tuple of alternatives), optional fields, specificity bonus
SCHEMAS = {
    "muster": (["emp_no", "date", ("s1", "status")], ["name", "s2", "in_time", "out_time", "hours", "shift"], 1.0),
    "swipes": (["emp_no", ("timestamp", "time")], ["name", "date", "door", "location_type", "swipe_type", "direction",
                                                   "lat", "lon", "coords", "shift"], 1.0),
    "summary": (["emp_no", "week_days", "hours_required"], ["name", "location", "present", "absent", "leave", "on_duty",
                                                             "holiday", "total_hours", "leave_hours", "short_fall", "excess"], 1.2),
    "mis_manual": (["emp_no", "group", "week_days", "required_hours_mis"],
                   ["name", "location", "holiday", "leave", "absent", "days_present_mis", "wfh", "total_hours",
                    "extra_days", "adjusted_hours", "holiday_hours", "actual_hours", "balance_hours", "remark"], 1.5),
    "doj": (["emp_no", "doj"], ["name", "dol", "department", "designation", "location"], 0.8),
    "employee_master": (["emp_no", ("group", "weekly_off", "hours_per_day", "exempt")],
                        ["name", "location", "weekly_off", "working_saturdays", "hours_per_day", "exempt", "exempt_reason",
                         "holiday_calendar", "doj", "dol", "email", "department", "designation", "group"], 0.9),
    "leave_opening": (["emp_no", "leave_balance"], ["name", "compoff_balance", "as_of"], 1.1),
    "holidays": (["date", "holiday_name"], ["calendar"], 0.7),
}
KIND_LABELS = {
    "muster": "Attendance Muster Report (day-wise status)",
    "swipes": "Employee Swipe Details (punch log)",
    "summary": "Attendance Summary Report",
    "doj": "Date of Joining Report (joining dates)",
    "mis_manual": "Manual MIS working (for reconciliation / employee master)",
    "leave_opening": "Leave / comp-off opening balances",
    "holidays": "Holiday list",
    "employee_master": "Employee master",
}
FIELD_LABELS = {k: v[0][0].title() for k, v in F.items()}
FIELD_LABELS.update({"emp_no": "Employee number", "s1": "Status — first half", "s2": "Status — second half",
                     "status": "Status (whole day)", "timestamp": "Punch date & time", "time": "Punch time (if date is separate)",
                     "in_time": "In time", "out_time": "Out time", "hours": "Hours worked", "coords": "Coordinates (lat, long)",
                     "doj": "Date of joining", "dol": "Date of leaving", "holiday_name": "Holiday name"})
MIN_CONFIDENCE = 0.72


def schema_fields(kind: str) -> list[str]:
    req, opt, _ = SCHEMAS[kind]
    out = []
    for r in req:
        out.extend(r if isinstance(r, tuple) else [r])
    return list(dict.fromkeys(out + opt))


# ============================================================================ header matching

@functools.lru_cache(maxsize=8192)
def _fscore(header: str, fld: str) -> float:
    return _score(header, F[fld][0])


def _score(header: str, syns: list[str]) -> float:
    if not header or len(header) > 60:
        return 0.0
    best = 0.0
    htoks = header.split()
    for s in syns:
        if header == s:
            return 1.0
        stoks = s.split()
        if len(s) <= 3:                       # short synonyms ("id", "in", "lat") only match exactly / as whole header word
            if len(htoks) <= 2 and s in htoks:
                best = max(best, 0.8)
            continue
        if f" {s} " in f" {header} ":         # whole-word containment
            best = max(best, 0.9 - 0.02 * max(0, len(htoks) - len(stoks)))
        elif all(t in htoks for t in stoks):
            best = max(best, 0.82)
        elif abs(len(header) - len(s)) <= 0.3 * len(s):
            sm = difflib.SequenceMatcher(None, header, s)
            r = sm.ratio() if sm.real_quick_ratio() >= 0.86 and sm.quick_ratio() >= 0.86 else 0
            if r >= 0.86:
                best = max(best, 0.75 + (r - 0.86))
    return best


def _values(rows, header_row, col, n=40):
    out = []
    for r in rows[header_row + 1: header_row + 1 + 400]:
        if r and col < len(r) and r[col] not in (None, ""):
            out.append(r[col])
            if len(out) >= n:
                break
    return out


def _fits(vtype: str, vals: list) -> float:
    """Share of sample values that look like the field's type (1.0 when there are no values to judge)."""
    if not vals:
        return 0.6
    def ok(v):
        if vtype == "id":
            s = emp_id(v)
            looks_date = isinstance(v, (datetime, date)) or (isinstance(v, str) and parse_dt(v) is not None)
            return 0 < len(s) <= 20 and bool(re.search(r"\d", s)) and not looks_date and " " not in s.strip()
        if vtype == "date":
            d = parse_dt(v)
            return d is not None and 1990 <= d.year <= 2100
        if vtype == "datetime":
            d = parse_dt(v)
            return d is not None and 1990 <= d.year <= 2100 and (d.hour or d.minute or d.second or isinstance(v, str) and ":" in v)
        if vtype == "time":
            return parse_time(v) is not None or str(v).strip() in ("-", "--", "")
        if vtype == "hours":
            return isinstance(v, (int, float, time)) or bool(re.search(r"\d", str(v)))
        if vtype == "number":
            return isinstance(v, (int, float)) or bool(re.fullmatch(r"-?[\d,]*\.?\d+", str(v).strip()))
        if vtype == "status":
            s = str(v).strip()
            return 0 < len(s) <= 20
        if vtype == "lat":
            return -90 <= num(v, 999) <= 90
        if vtype == "lon":
            return -180 <= num(v, 999) <= 180
        if vtype == "coords":
            return bool(_COORD_RE.search(str(v)))
        return True
    return sum(1 for v in vals if ok(v)) / len(vals)


_TL = threading.local()


def _fit_cache() -> dict:
    """Per-thread memo of value checks for the sheet being recognised (two uploads at once never share it)."""
    c = getattr(_TL, "fits", None)
    if c is None:
        c = _TL.fits = {}
    return c


def _fit_cached(rows, hr, col, vtype):
    cache = _fit_cache()
    k = (id(rows), hr, col, vtype)
    v = cache.get(k)
    if v is None:
        v = cache[k] = _fits(vtype, _values(rows, hr, col))
    return v


def _assign(headers: list[str], rows, hr: int, fields: list[str]) -> dict:
    cands = []
    for f in fields:
        vtype = F[f][1]
        for i, h in enumerate(headers):
            sc = _fscore(h, f)
            if sc >= 0.75:
                fit = _fit_cached(rows, hr, i, vtype)
                if fit >= 0.6:
                    cands.append((sc * (0.7 + 0.3 * fit), f, i))
    cands.sort(reverse=True)
    out, used = {}, set()
    for sc, f, i in cands:
        if f in out or i in used:
            continue
        out[f] = (i, round(sc, 3))
        used.add(i)
    return out


def _infer(rows, hr: int, ncols: int, vtype: str, used: set) -> Optional[int]:
    best = None
    for i in range(ncols):
        if i in used:
            continue
        vals = _values(rows, hr, i)
        if len(vals) < 3:
            continue
        if vtype in ("date", "datetime") and all(isinstance(v, (int, float)) for v in vals):
            continue                                   # bare numbers (e.g. employee codes) are never inferred as dates
        fit = _fit_cached(rows, hr, i, vtype)
        if vtype == "id":
            distinct = len({emp_id(v) for v in vals}) / len(vals)
            fit = fit * (0.5 + 0.5 * distinct)
        if fit >= 0.9 and (best is None or fit > best[0]):
            best = (fit, i)
    return best[1] if best else None


def _evaluate(kind: str, headers: list[str], rows, hr: int) -> Optional[tuple]:
    req, opt, bonus = SCHEMAS[kind]
    fields = schema_fields(kind)
    m = _assign(headers, rows, hr, fields)
    strong = len(m)
    used = {i for i, _ in m.values()}
    inferred = []
    for r in req:
        alts = r if isinstance(r, tuple) else (r,)
        if any(a in m for a in alts):
            continue
        if strong >= 2 and len(alts) == 1 and F[alts[0]][1] in ("id", "date", "datetime", "coords"):
            i = _infer(rows, hr, len(headers), F[alts[0]][1], used)
            if i is not None:
                m[alts[0]] = (i, 0.7)
                used.add(i)
                inferred.append(alts[0])
                continue
        return None
    if kind == "swipes" and "timestamp" not in m and not ("time" in m and "date" in m):
        return None
    req_scores = []
    for r in req:
        alts = r if isinstance(r, tuple) else (r,)
        req_scores.append(max(m[a][1] for a in alts if a in m))
    conf = sum(req_scores) / len(req_scores)
    score = conf * len(req_scores) * bonus + 0.15 * sum(v[1] for k, v in m.items() if k in opt)
    return score, conf, {k: v[0] for k, v in m.items()}, inferred


def _header_candidates(rows) -> list[int]:
    """Rows that look like column titles: mostly short text, few digits (data rows carry IDs, dates, times)."""
    out = []
    for i, r in enumerate(rows[:30]):
        cells = [c for c in (r or []) if c not in (None, "")]
        texts = [c for c in cells if isinstance(c, str) and len(c) <= 60 and sum(ch.isdigit() for ch in c) <= 2]
        if len(texts) >= 2 and len(texts) >= 0.7 * len(cells):
            out.append(i)
        if len(out) >= 6:
            break
    return out


def layout_signature(headers: list) -> str:
    keys = sorted({norm(h) for h in headers if norm(h)})
    return hashlib.sha1("|".join(keys).encode()).hexdigest()[:16]


@dataclass
class DetectedSheet:
    sheet: str
    kind: str
    header_row: int
    headers: list
    title_lines: list = field(default_factory=list)
    map: dict = field(default_factory=dict)          # field -> column index
    confidence: float = 1.0
    inferred: list = field(default_factory=list)
    source: str = "auto"                             # auto | layout (remembered) | manual

    def describe(self) -> list[dict]:
        raw = self.raw_headers if hasattr(self, "raw_headers") else self.headers
        return [{"field": f, "label": FIELD_LABELS.get(f, f), "column": (raw[i] if i < len(raw) else f"#{i + 1}"),
                 "inferred": f in self.inferred} for f, i in sorted(self.map.items(), key=lambda x: x[1])]


def detect_sheet(name: str, rows: list[list], layouts: Optional[list] = None) -> Optional[DetectedSheet]:
    _fit_cache().clear()
    try:
        return _detect_sheet(name, rows, layouts)
    finally:
        _fit_cache().clear()


def _detect_sheet(name: str, rows: list[list], layouts: Optional[list] = None) -> Optional[DetectedSheet]:
    best = None
    for hr in _header_candidates(rows):
        raw = [("" if c is None else str(c)) for c in rows[hr]]
        headers = [norm(c) for c in raw]
        sig = layout_signature(raw)
        for lay in layouts or []:
            if lay.get("signature") == sig and lay.get("kind") in SCHEMAS:
                mp = _replay_layout(lay, raw)
                if mp is None or missing_required(lay["kind"], mp):
                    continue                           # layout no longer fits: fall back to recognition/review
                ds = DetectedSheet(name, lay["kind"], hr, headers, [str(r[0]) for r in rows[:hr] if r and r[0]], mp, 1.0, [],
                                   "layout")
                ds.raw_headers = raw
                return ds
        for kind in SCHEMAS:
            ev = _evaluate(kind, headers, rows, hr)
            if ev and (best is None or ev[0] > best[0]):
                best = (ev[0], kind, hr, headers, raw, ev)
    if not best:
        return None
    _, kind, hr, headers, raw, (score, conf, mp, inferred) = best
    ds = DetectedSheet(name, kind, hr, headers, [str(r[0]) for r in rows[:hr] if r and r[0]], mp, round(conf, 3), inferred)
    ds.raw_headers = raw
    return ds


def _replay_layout(lay: dict, raw: list) -> Optional[dict]:
    """Remembered layout → field → column. New layouts store {"i": index, "h": header}: the header must still be in
    that column (blank/duplicate headers stay unambiguous). Older layouts stored the header text only and are used
    only when that header occurs exactly once."""
    out = {}
    for f, spec in (lay.get("map") or {}).items():
        if isinstance(spec, dict):
            i, h = int(spec.get("i", -1)), spec.get("h", "")
            if i < 0:
                return None
            if i < len(raw):
                if norm(raw[i]) != norm(h):
                    return None
            elif h:
                return None
            out[f] = i
        else:
            hits = [i for i, x in enumerate(raw) if norm(x) == norm(spec)]
            if len(hits) != 1:
                return None
            out[f] = hits[0]
    return out


def detect(sheets: dict[str, list[list]], layouts: Optional[list] = None) -> list[DetectedSheet]:
    out = []
    for name, rows in sheets.items():
        ds = detect_sheet(name, rows, layouts)
        if ds:
            out.append(ds)
    return out


def guess_header_row(rows: list[list]) -> int:
    c = _header_candidates(rows)
    return c[0] if c else 0


def manual_sheet(name: str, rows: list[list], kind: str, header_row: int, mapping: dict) -> DetectedSheet:
    raw = [("" if c is None else str(c)) for c in rows[header_row]]
    ds = DetectedSheet(name, kind, header_row, [norm(c) for c in raw],
                       [str(r[0]) for r in rows[:header_row] if r and r[0]], {f: int(i) for f, i in mapping.items()}, 1.0, [],
                       "manual")
    ds.raw_headers = raw
    return ds


def suggest(rows: list[list], header_row: int, kind: str) -> dict:
    """Best-guess field → column mapping for a chosen report type (pre-fills the manual mapping screen)."""
    raw = rows[header_row] if 0 <= header_row < len(rows) else []
    headers = [norm(c) for c in raw]
    _fit_cache().clear()
    m = {k: v[0] for k, v in _assign(headers, rows, header_row, schema_fields(kind)).items()}
    used = set(m.values())
    ncols = max([len(r or []) for r in rows[header_row: header_row + 30]] + [len(headers)])

    def looks(i, test):
        vals = [v for v in _values(rows, header_row, i, 30) if str(v).strip() not in ("", "-", "--")]
        return len(vals) >= 2 and sum(1 for v in vals if test(v)) / len(vals) >= 0.9

    is_status = lambda v: (str(v).strip().upper() in DEFAULT_STATUS_MAP or str(v).strip().lower() in _STATUS_WORDS
                           or "/" in str(v) and all(x.strip().upper() in DEFAULT_STATUS_MAP for x in str(v).split("/")))
    is_id = lambda v: bool(re.fullmatch(r"[A-Za-z]{0,6}\d{1,8}", emp_id(v))) and not (isinstance(v, (datetime, date)) or isinstance(v, str) and parse_dt(v))
    is_date = lambda v: not isinstance(v, (int, float)) and (lambda d: d is not None and 1990 <= d.year <= 2100)(parse_dt(v))
    is_time = lambda v: parse_time(v) is not None and not is_date(v)
    is_dt = lambda v: (lambda d: d is not None and 1990 <= d.year <= 2100 and (d.hour or d.minute))(parse_dt(v))
    tests = {"emp_no": is_id, "date": is_date, "s1": is_status, "s2": is_status, "status": is_status,
             "in_time": lambda v: is_time(v) or is_dt(v), "out_time": lambda v: is_time(v) or is_dt(v),
             "timestamp": is_dt, "time": is_time, "doj": is_date, "dol": is_date, "as_of": is_date,
             "coords": lambda v: bool(_COORD_RE.search(str(v)))}
    order = [f for f in schema_fields(kind) if f in tests]
    if "s1" in order and "status" in order:     # two status columns → halves; one → whole day (decided below)
        order.remove("status")
    for f in order:
        if f in m:
            continue
        i = next((c for c in range(ncols) if c not in used and looks(c, tests[f])), None)
        if i is not None:
            m[f] = i
            used.add(i)
    if kind == "muster" and "s1" in m and "s2" not in m and "status" not in m:
        m["status"] = m.pop("s1")               # a single status column is a whole-day status
    _fit_cache().clear()
    return m


def needs_review(ds: DetectedSheet) -> bool:
    return ds.source == "auto" and (ds.confidence < MIN_CONFIDENCE or bool(ds.inferred))


def missing_required(kind: str, mapping: dict) -> list[str]:
    req, _, _ = SCHEMAS[kind]
    miss = []
    for r in req:
        alts = r if isinstance(r, tuple) else (r,)
        if not any(a in mapping for a in alts):
            miss.append(" or ".join(FIELD_LABELS.get(a, a) for a in alts))
    if kind == "swipes" and "timestamp" not in mapping and "time" in mapping and "date" not in mapping:
        miss.append("Date (needed with a separate time column)")
    return miss


def detect_period(ds: DetectedSheet, rows: list[list]) -> Optional[tuple[int, int]]:
    for t in ds.title_lines:
        m = _PERIOD_RE.search(t)
        if m:
            d = parse_date(m.group(1).replace("-", " ").replace("/", " ") if not re.match(r"\d{1,2}/\d{1,2}/", m.group(1)) else m.group(1))
            if d:
                return d.year, d.month
    for t in ds.title_lines:
        if re.search(r"\b(printed|generated|run|downloaded|exported|as on|as of)\b", t, re.I):
            continue                                   # a print/run date is not the report month
        m = _MONTH_TITLE_RE.search(t)
        if m:
            d = parse_date(f"01 {m.group(1)} {m.group(2)}")
            if d:
                return d.year, d.month
    return None


# ============================================================================ parsers

def _data_rows(rows, ds):
    for _, r in _numbered_rows(rows, ds):
        yield r


def _numbered_rows(rows, ds):
    """(spreadsheet row number, row) for non-blank rows under the header."""
    for i, r in enumerate(rows[ds.header_row + 1:], start=ds.header_row + 2):
        if r is None or all(v in (None, "") for v in r):
            continue
        yield i, r


def _g(r, ds, f):
    i = ds.map.get(f)
    return r[i] if i is not None and i < len(r) else None


def _combine(day: date, v: Any) -> Optional[datetime]:
    if v in (None, "", "-", "--"):
        return None
    d = parse_dt(v)
    if d and d.year > 1900 and (d.hour or d.minute or d.second or isinstance(v, datetime)):
        return d
    t = parse_time(v)
    return datetime.combine(day, t) if t else None


def parse_muster(rows, ds) -> tuple[list[MusterDay], list[str]]:
    out, warn = [], []
    for n, r in _numbered_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        d = parse_date(_g(r, ds, "date"))
        if not e or not d:
            if any(v not in (None, "") for v in r):
                warn.append(f"row {n}: missing employee/date — skipped")
            continue
        if "s1" in ds.map:
            s1 = status_codes(_g(r, ds, "s1"))[0]
            s2 = status_codes(_g(r, ds, "s2"))[0] if "s2" in ds.map else s1
        else:
            s1, s2 = status_codes(_g(r, ds, "status"))
        it, ot = _combine(d, _g(r, ds, "in_time")), _combine(d, _g(r, ds, "out_time"))
        if it and ot and ot < it:
            ot += timedelta(days=1)                      # night shift crosses midnight
        if "hours" in ds.map:
            mins = parse_hhmm(_g(r, ds, "hours"))
        else:
            mins = int((ot - it).total_seconds() // 60) if it and ot else 0
        out.append(MusterDay(emp_no=e, day=d, s1=s1, s2=s2, in_time=it, out_time=ot, work_min=max(0, mins),
                             shift=str(_g(r, ds, "shift") or "").strip(), name=str(_g(r, ds, "name") or "").strip()))
    return out, warn[:50]


def parse_swipes(rows, ds) -> tuple[list[Swipe], dict, list[str]]:
    out, names, warn = [], {}, []
    for n, r in _numbered_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        ts = None
        if "timestamp" in ds.map:
            ts = parse_dt(_g(r, ds, "timestamp"))
            if ts and not (ts.hour or ts.minute or ts.second) and "time" in ds.map:
                t = parse_time(_g(r, ds, "time"))
                ts = datetime.combine(ts.date(), t) if t else ts
        elif "time" in ds.map and "date" in ds.map:
            dd = parse_date(_g(r, ds, "date"))
            ts = _combine(dd, _g(r, ds, "time")) if dd else None
        if not e or not ts:
            if any(v not in (None, "") for v in r):
                warn.append(f"row {n}: missing employee/time — skipped")
            continue
        lat = lon = None
        if "lat" in ds.map and "lon" in ds.map:
            lat, lon = num(_g(r, ds, "lat"), None), num(_g(r, ds, "lon"), None)
        if lat is None:
            for v in ([_g(r, ds, "coords")] if "coords" in ds.map else []) + list(r):
                if isinstance(v, str):
                    m = _COORD_RE.search(v)
                    if m:
                        lat, lon = float(m.group(1)), float(m.group(2))
                        break
        if lat is not None and (lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0)):
            lat = lon = None
        if "name" in ds.map:
            names[e] = str(_g(r, ds, "name") or "").strip()
        stype = str(_g(r, ds, "swipe_type") or "").strip()
        if not stype and "direction" in ds.map:
            stype = "Punch " + str(_g(r, ds, "direction") or "").strip()
        out.append(Swipe(emp_no=e, ts=ts, door=str(_g(r, ds, "door") or "").strip(),
                         location_type=str(_g(r, ds, "location_type") or "").strip(), swipe_type=stype, lat=lat, lon=lon))
    return out, names, warn[:50]


def parse_summary(rows, ds) -> list[dict]:
    keys = {"emp_no": "emp_no", "name": "name", "location": "location", "present": "present", "absent": "absent",
            "leave": "leave", "on_duty": "on_duty", "holiday": "holiday", "week_days": "week_days",
            "total_hours": "total_hours", "hours_required": "hours_required", "leave_hours": "leave_hours",
            "short_fall": "short_fall", "excess": "excess"}
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        if not e:
            continue
        rec = {k: (str(_g(r, ds, f)).strip() if _g(r, ds, f) is not None else "") for k, f in keys.items()}
        rec["emp_no"] = e
        out.append(rec)
    return out


def parse_doj(rows, ds) -> list[dict]:
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        if not e or e.lower().startswith("total"):
            continue
        out.append({"emp_no": e, "name": str(_g(r, ds, "name") or "").strip(), "doj": parse_date(_g(r, ds, "doj")),
                    "dol": parse_date(_g(r, ds, "dol")) if "dol" in ds.map else None})
    return out


def parse_mis_manual(rows, ds) -> list[dict]:
    """Manual MIS 'Reference' sheet → master fields + the manual figures (for reconciliation)."""
    fm = {"name": "name", "group": "group", "location": "location", "total_week_days": "week_days",
          "holidays": "holiday", "leave_days": "leave", "absent_days": "absent", "days_present": "days_present_mis",
          "wfh_days": "wfh", "total_hours_text": "total_hours", "extra_days": "extra_days",
          "required_hours": "required_hours_mis", "total_hours": "adjusted_hours", "extra_hours": "holiday_hours",
          "actual_hours": "actual_hours", "balance_hours": "balance_hours", "remark": "remark"}
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        if not e or not re.match(r"^[A-Z0-9]+$", e):
            continue
        rec = {"emp_no": e}
        for k, f in fm.items():
            v = _g(r, ds, f)
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
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        if not e:
            continue
        rec = {"emp_no": e}
        for k in ("name", "group", "location", "weekly_off", "working_saturdays", "hours_per_day", "exempt",
                  "exempt_reason", "holiday_calendar", "doj", "dol", "email", "department", "designation"):
            if k not in ds.map:
                continue
            v = _g(r, ds, k)
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
    out = []
    for r in _data_rows(rows, ds):
        e = emp_id(_g(r, ds, "emp_no"))
        if e:
            out.append({"emp_no": e, "leave_balance": num(_g(r, ds, "leave_balance"), None),
                        "compoff_balance": num(_g(r, ds, "compoff_balance"), None) if "compoff_balance" in ds.map else None,
                        "as_of": parse_date(_g(r, ds, "as_of")) if "as_of" in ds.map else None})
    return out


def parse_holidays(rows, ds) -> list[dict]:
    out = []
    for r in _data_rows(rows, ds):
        d = parse_date(_g(r, ds, "date"))
        if d:
            out.append({"date": d, "name": str(_g(r, ds, "holiday_name") or "").strip(),
                        "calendar": str(_g(r, ds, "calendar") or "").strip() if "calendar" in ds.map else ""})
    return out


PARSERS = {"muster": parse_muster, "swipes": parse_swipes, "summary": parse_summary, "doj": parse_doj,
           "mis_manual": parse_mis_manual, "employee_master": parse_employee_master, "leave_opening": parse_leave_opening,
           "holidays": parse_holidays}


def infer_period_from_dates(dates: list[date]) -> Optional[tuple[int, int]]:
    if not dates:
        return None
    (y, m), _ = Counter((d.year, d.month) for d in dates).most_common(1)[0]
    return y, m
