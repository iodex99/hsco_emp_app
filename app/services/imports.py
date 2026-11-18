"""Upload pipeline: read workbook → detect each sheet → store → recompute affected periods."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time as _time
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.importers import parsers as P
from app.importers.readers import read_workbook
from app.config import get_config
from app.models import (
    Employee, Holiday, HolidayCalendar, ImportBatch, LeaveOpening, ManualMisRow, MusterRow, Period, Setting, SummaryRow,
    SwipeRow, User,
)
from app.services.audit import audit
from app.services.compute import compute_period, get_period
from app.services.geo import Namer


class ImportError_(Exception):
    pass


# ----------------------------------------------------------------------------- remembered layouts & pending files

def get_layouts(db: Session) -> list:
    s = db.get(Setting, "import_layouts")
    return list((s.value or {}).get("layouts", [])) if s else []


def save_layout(db: Session, ds: "P.DetectedSheet", label: str = "") -> None:
    raw = getattr(ds, "raw_headers", ds.headers)
    sig = P.layout_signature(raw)
    # column index AND its header text: replay checks the header still sits in that column (duplicate or blank
    # headers can't be told apart by text alone); columns beyond the header row are kept by index
    lay = {"signature": sig, "kind": ds.kind, "label": label,
           "map": {f: {"i": i, "h": raw[i] if i < len(raw) else ""} for f, i in ds.map.items()},
           "saved": date.today().isoformat()}
    layouts = [x for x in get_layouts(db) if x.get("signature") != sig] + [lay]
    s = db.get(Setting, "import_layouts")
    if s is None:
        db.add(Setting(key="import_layouts", value={"layouts": layouts}))
    else:
        s.value = {"layouts": layouts}


def delete_layout(db: Session, signature: str) -> bool:
    s = db.get(Setting, "import_layouts")
    if not s:
        return False
    before = get_layouts(db)
    after = [x for x in before if x.get("signature") != signature]
    s.value = {"layouts": after}
    return len(after) != len(before)


_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _pending_dir() -> Path:
    d = get_config().data_dir / "uploads" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stash_pending(data: bytes, filename: str, sheets: Optional[list] = None) -> str:
    d = _pending_dir()
    for old in d.glob("*"):                      # keep the folder tidy: unmapped uploads expire after 2 days
        try:
            if _time.time() - old.stat().st_mtime > 2 * 86400:
                old.unlink()
        except OSError:
            pass
    token = secrets.token_hex(16)
    (d / f"{token}.bin").write_bytes(data)
    (d / f"{token}.json").write_text(json.dumps({"filename": filename, "open": sheets}), encoding="utf-8")
    return token


def finish_pending(token: str, sheet: str) -> None:
    """A sheet of a stashed upload was imported; delete the stash once no unmapped sheets remain."""
    if not _TOKEN_RE.match(token or ""):
        return
    m = _pending_dir() / f"{token}.json"
    try:
        meta = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    left = [x for x in (meta.get("open") or []) if x != sheet]
    if not left:
        drop_pending(token)
    else:
        meta["open"] = left
        m.write_text(json.dumps(meta), encoding="utf-8")


def load_pending(token: str) -> Optional[tuple[bytes, str]]:
    if not _TOKEN_RE.match(token or ""):
        return None
    d = _pending_dir()
    b, m = d / f"{token}.bin", d / f"{token}.json"
    if not b.exists() or not m.exists():
        return None
    return b.read_bytes(), json.loads(m.read_text(encoding="utf-8")).get("filename", "upload.xlsx")


def drop_pending(token: str) -> None:
    if _TOKEN_RE.match(token or ""):
        for ext in (".bin", ".json"):
            (_pending_dir() / f"{token}{ext}").unlink(missing_ok=True)


def _period_for(db: Session, ym: Optional[tuple], fallback: Optional[tuple]) -> Period:
    ym = ym or fallback
    if not ym:
        raise ImportError_("Could not determine the month of this file; choose a period and retry.")
    today = date.today()
    ahead = (ym[0] - today.year) * 12 + (ym[1] - today.month)
    if ahead > 1:          # attendance can't be in the future: almost always a wrong year in the file
        raise ImportError_(f"The dates in this file are in {date(ym[0], ym[1], 1):%B %Y}, {ahead} months ahead of today. "
                           "Check the dates in the file (often a wrong year) and import again.")
    p = get_period(db, ym[0], ym[1], create=True)
    if p.status == "FINALISED":
        raise ImportError_(f"{p.label} is finalised (locked). Re-open it before importing.")
    return p


def _ensure_employee(db: Session, cache: dict, emp_no: str, name: str = "", source: str = "") -> Employee:
    e = cache.get(emp_no)
    if e is None:
        e = db.get(Employee, emp_no)
        if e is None:
            e = Employee(emp_no=emp_no, name=name or emp_no, source=source)
            db.add(e)
        cache[emp_no] = e
    if name and (not e.name or e.name == e.emp_no):
        e.name = name
    return e


def import_file(db: Session, data: bytes, filename: str, user: Optional[User], period_hint: Optional[tuple] = None,
                ip: str = "", manual: Optional[dict] = None, recompute: bool = True,
                touched_out: Optional[set] = None) -> list[dict]:
    """Import every recognised sheet. Sheets recognised with low confidence (or a file where nothing is recognised) are
    not imported: they come back as `needs_mapping` items so HR can confirm the columns (manual={sheet, kind,
    header_row, map} re-runs the import with that mapping).

    recompute=False leaves recalculation to the caller (several files in one upload are recalculated once); the
    affected period ids are added to touched_out."""
    try:
        sheets = read_workbook(data, filename)
    except Exception as ex:  # noqa: BLE001
        raise ImportError_(f"Could not read {filename}: {ex}") from ex
    results, touched = [], set()
    if manual:
        if manual["sheet"] not in sheets:
            raise ImportError_(f"{filename}: sheet '{manual['sheet']}' not found")
        dets = [P.manual_sheet(manual["sheet"], sheets[manual["sheet"]], manual["kind"], int(manual["header_row"]),
                               manual["map"])]
    else:
        found = P.detect(sheets, get_layouts(db))
        dets = [d for d in found if not P.needs_review(d)]
        unsure = [d for d in found if P.needs_review(d)]
        if not found or unsure:
            token = stash_pending(data, filename, [d.sheet for d in unsure] if found else None)
            if not found:
                results.append({"kind": "needs_mapping", "token": token, "sheet": "", "label": "Not recognised",
                                "reason": "None of the sheets looked like a known report. Tell the app which columns "
                                          "hold what — it will remember this layout next time.", "rows": 0,
                                "period": "", "warnings": []})
            for d in unsure:
                results.append({"kind": "needs_mapping", "token": token, "sheet": d.sheet, "guess": d.kind,
                                "label": P.KIND_LABELS[d.kind], "rows": 0, "period": "", "warnings": [],
                                "reason": f"Looks like {P.KIND_LABELS[d.kind]} ({int(d.confidence * 100)}% sure"
                                          + (", some columns were inferred from their values" if d.inferred else "")
                                          + "). Please confirm the columns before importing."})
    sha = hashlib.sha256(data).hexdigest()
    # every employee once (no per-row lookups; new ones are added as they appear)
    emp_cache: dict = {e.emp_no: e for e in db.scalars(select(Employee))}
    for ds in dets:
        rows = sheets[ds.sheet]
        warnings: list = []
        kind = ds.kind
        period: Optional[Period] = None
        n = 0
        if kind == "muster":
            recs, warnings = P.parse_muster(rows, ds)
            period = _period_for(db, _month_of(ds, rows, [r.day for r in recs], warnings), period_hint)
            keep = [r for r in recs if (r.day.year, r.day.month) == (period.year, period.month)]
            for r in keep:
                _ensure_employee(db, emp_cache, r.emp_no, r.name, "muster")
            db.flush()
            _clear(db, MusterRow, period, {r.emp_no for r in keep}, "attendance days", warnings)
            if keep:
                db.execute(insert(MusterRow), [
                    {"period_id": period.id, "emp_no": r.emp_no, "name": r.name, "day": r.day, "s1": r.s1, "s2": r.s2,
                     "in_time": r.in_time, "out_time": r.out_time, "work_min": r.work_min, "shift": r.shift} for r in keep])
            n = len(keep)
        elif kind == "swipes":
            recs, names, warnings = P.parse_swipes(rows, ds)
            period = _period_for(db, _month_of(ds, rows, [r.ts.date() for r in recs], warnings, use_title=False),
                                 period_hint)
            keep = [r for r in recs if (r.ts.year, r.ts.month) == (period.year, period.month)]
            # names first (gazetteer lookups, maybe a first-time index build) — before any delete takes the write lock
            namer = Namer(db)
            places = [namer.name(r.lat, r.lon) if r.lat is not None else "" for r in keep]
            for r in keep:
                _ensure_employee(db, emp_cache, r.emp_no, names.get(r.emp_no, ""), "swipes")
            db.flush()
            _clear(db, SwipeRow, period, {r.emp_no for r in keep}, "punches", warnings)
            if keep:
                db.execute(insert(SwipeRow), [
                    {"period_id": period.id, "emp_no": r.emp_no, "ts": r.ts, "door": r.door,
                     "location_type": r.location_type, "swipe_type": r.swipe_type, "lat": r.lat, "lon": r.lon,
                     "place": pl, "note": r.note, "reason": r.reason, "approval": r.approval, "device": r.device,
                     "device_id": r.device_id} for r, pl in zip(keep, places)])
            namer.flush_cache()
            n = len(keep)
            if not any(r.lat is not None for r in recs):
                warnings.append("No GPS coordinates in this export — location names come from Location Type only.")
        elif kind == "summary":
            recs = P.parse_summary(rows, ds)
            period = _period_for(db, P.detect_period(ds, rows), period_hint)
            db.execute(delete(SummaryRow).where(SummaryRow.period_id == period.id))
            for r in recs:
                e = _ensure_employee(db, emp_cache, r["emp_no"], r.get("name", ""), "summary")
                if r.get("location") and not e.location:
                    e.location = r["location"]
                db.add(SummaryRow(period_id=period.id, emp_no=r["emp_no"], data=r))
                n += 1
        elif kind == "doj":
            recs = P.parse_doj(rows, ds)
            for r in recs:
                e = _ensure_employee(db, emp_cache, r["emp_no"], r["name"], "doj")
                if r["doj"]:
                    e.doj = r["doj"]
                if r.get("dol"):                       # a joining/leaving list: record the exit
                    e.dol = r["dol"]
                    e.is_active = False
                elif not e.is_active and not e.dol:
                    e.is_active = True
                n += 1
            # active list: employees we know who are missing from it are exit candidates
            active = {r["emp_no"] for r in recs}
            missing = [e.emp_no for e in db.scalars(select(Employee).where(Employee.dol.is_(None))) if e.emp_no not in active]
            if missing:
                warnings.append(f"{len(missing)} employee(s) not in this active-employee list (possible exits): "
                                f"{', '.join(sorted(missing)[:15])}. Set their Date of Leaving in Employees.")
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        elif kind == "mis_manual":
            recs = P.parse_mis_manual(rows, ds)
            period = _period_for(db, P.detect_period(ds, rows), period_hint)
            db.execute(delete(ManualMisRow).where(ManualMisRow.period_id == period.id))
            for r in recs:
                e = _ensure_employee(db, emp_cache, r["emp_no"], r.get("name", ""), "mis")
                if r.get("group") and not e.group:
                    e.group = r["group"]
                if r.get("location") and not e.location:
                    e.location = r["location"]
                db.add(ManualMisRow(period_id=period.id, emp_no=r["emp_no"], data=r))
                n += 1
        elif kind == "employee_master":
            recs = P.parse_employee_master(rows, ds)
            cals = {c.name.lower(): c for c in db.scalars(select(HolidayCalendar))}
            for r in recs:
                e = _ensure_employee(db, emp_cache, r["emp_no"], r.get("name", ""), "master")
                for k in ("name", "group", "location", "department", "designation", "email", "exempt_reason"):
                    if r.get(k):
                        setattr(e, k, r[k])
                for k in ("doj", "dol", "hours_per_day"):
                    if r.get(k) is not None:
                        setattr(e, k, r[k])
                if "exempt" in r:
                    e.exempt = bool(r["exempt"])
                if r.get("weekly_off"):
                    e.weekly_off = r["weekly_off"].strip().upper()
                if r.get("working_saturdays"):
                    e.working_saturdays = r["working_saturdays"]
                if r.get("holiday_calendar"):
                    c = cals.get(r["holiday_calendar"].lower())
                    if c:
                        e.holiday_calendar_id = c.id
                    else:
                        warnings.append(f"{r['emp_no']}: holiday calendar '{r['holiday_calendar']}' not found")
                if e.dol:
                    e.is_active = False
                n += 1
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        elif kind == "leave_opening":
            recs = P.parse_leave_opening(rows, ds)
            for r in recs:
                d: Optional[date] = r.get("as_of")
                ym = (d.year, d.month) if d else period_hint
                if not ym:
                    warnings.append(f"{r['emp_no']}: no 'As of' date and no period selected — skipped")
                    continue
                lo = db.scalar(select(LeaveOpening).where(LeaveOpening.emp_no == r["emp_no"], LeaveOpening.year == ym[0],
                                                          LeaveOpening.month == ym[1]))
                if lo is None:
                    lo = LeaveOpening(emp_no=r["emp_no"], year=ym[0], month=ym[1])
                    db.add(lo)
                lo.leave_balance = r["leave_balance"]
                if r.get("compoff_balance") is not None:
                    lo.compoff_balance = r["compoff_balance"]
                lo.created_by = user.username if user else ""
                lo.note = f"Imported from {filename}"
                p = get_period(db, ym[0], ym[1])
                if p and p.status == "DRAFT":
                    touched.add(p.id)
                n += 1
        elif kind == "holidays":
            recs = P.parse_holidays(rows, ds)
            from app.models import TargetedHoliday
            from app.services import holidays as TH
            emp_lookup = None
            for r in recs:
                if r["for_locations"] or r["for_groups"] or r["for_employees"]:
                    # targeted holiday: employees given by number or exact name
                    if emp_lookup is None:
                        emp_lookup = {}
                        for no, nm in db.execute(select(Employee.emp_no, Employee.name)):
                            emp_lookup[no.lower()] = no
                            emp_lookup.setdefault((nm or "").strip().lower(), no)
                    emps = []
                    for x in TH.split(r["for_employees"]):
                        no = emp_lookup.get(x.lower())
                        if no:
                            emps.append(no)
                        else:
                            warnings.append(f"{r['date']:%d-%b-%Y} {r['name'] or 'holiday'}: employee '{x}' not found")
                    locs, grps, emps = TH.join([r["for_locations"]]), TH.join([r["for_groups"]]), TH.join(emps)
                    if not (locs or grps or emps):
                        continue
                    same = db.scalar(select(TargetedHoliday).where(
                        TargetedHoliday.day == r["date"], TargetedHoliday.locations == locs,
                        TargetedHoliday.groups == grps, TargetedHoliday.emp_nos == emps))
                    if same is None:
                        db.add(TargetedHoliday(day=r["date"], name=r["name"] or "Holiday", locations=locs, groups=grps,
                                               emp_nos=emps, created_by=user.username if user else ""))
                    else:
                        same.name = r["name"] or same.name
                    n += 1
                    continue
                cname = r["calendar"] or "Standard"
                cal = db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == cname))
                if cal is None:
                    first = db.scalar(select(HolidayCalendar.id).limit(1)) is None
                    cal = HolidayCalendar(name=cname, is_default=first)
                    db.add(cal)
                    db.flush()
                h = db.scalar(select(Holiday).where(Holiday.calendar_id == cal.id, Holiday.day == r["date"]))
                if h is None:
                    db.add(Holiday(calendar_id=cal.id, day=r["date"], name=r["name"]))
                else:
                    h.name = r["name"]
                n += 1
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        if period is not None:
            touched.add(period.id)
        db.add(ImportBatch(period_id=period.id if period else None, kind=kind, filename=filename, sha256=sha,
                           sheet=ds.sheet, rows=n, warnings=warnings[:50], uploaded_by=user.username if user else ""))
        audit(db, user, "import", kind, period.key if period else "", {"file": filename, "sheet": ds.sheet, "rows": n}, ip)
        results.append({"kind": kind, "label": P.KIND_LABELS[kind], "sheet": ds.sheet, "rows": n,
                        "period": period.label if period else "", "warnings": warnings[:20],
                        "mapping": ds.describe(), "source": ds.source, "confidence": ds.confidence})
    db.flush()
    if touched_out is not None:
        touched_out.update(touched)
    if recompute:
        recompute_from(db, touched)
    return results


FULL_FILE_SHARE = 0.8


def _clear(db: Session, model, period: Period, emps: set, what: str, warnings: list) -> None:
    """Make room for a file's rows. A file covering most of the month's people (≥ 80 %) is a full export and replaces
    the month; a smaller file (e.g. a correction for one person) replaces only the people it contains."""
    existing = set(db.scalars(select(model.emp_no).where(model.period_id == period.id).distinct()))
    if not existing or len(emps) >= FULL_FILE_SHARE * len(existing):
        db.execute(delete(model).where(model.period_id == period.id))
        return
    db.execute(delete(model).where(model.period_id == period.id, model.emp_no.in_(emps)))
    kept = len(existing - emps)
    warnings.append(f"This file has {what} for {len(emps)} of the {len(existing)} people already in {period.label}: "
                    f"only their {what} were replaced; the other {kept} were kept. To replace the whole month, "
                    "upload the full export.")


def recompute_from(db: Session, period_ids: set, commit_each: bool = False) -> int:
    """Recalculate the affected draft months and every later draft month (leave and comp-off balances carry
    forward), oldest first, each once. commit_each=True commits after every month so the database write lock is
    held for one month's calculation at a time (each month only reads the committed month before it)."""
    if not period_ids:
        return 0
    ps = [p for p in (db.get(Period, i) for i in period_ids) if p]
    if not ps:
        return 0
    start = min((p.year, p.month) for p in ps)
    n = 0
    for p in db.scalars(select(Period).order_by(Period.year, Period.month)):
        if (p.year, p.month) < start or p.status != "DRAFT":
            continue
        if p.id not in period_ids and not p.computed_at:
            continue
        if db.scalar(select(MusterRow.id).where(MusterRow.period_id == p.id).limit(1)):
            compute_period(db, p, cascade=False)
            n += 1
            if commit_each:
                db.commit()
    return n


def _month_of(ds, rows, dates: list, warnings: list, use_title: bool = True) -> Optional[tuple]:
    """The month a day-level file belongs to: the month most of its dates fall in. A title month is only used when
    the file has no dates; if the title disagrees with the data, the data wins and a warning says so."""
    data_ym = P.infer_period_from_dates(dates)
    title_ym = P.detect_period(ds, rows) if use_title else None
    if data_ym and title_ym and data_ym != title_ym:
        warnings.append(f"The title mentions {date(*title_ym, 1):%B %Y} but the dates are mostly {date(*data_ym, 1):%B %Y}"
                        " — imported as the latter.")
    return data_ym or title_ym
