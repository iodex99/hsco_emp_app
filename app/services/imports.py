"""Upload pipeline: read workbook → detect each sheet → store → recompute affected periods."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time as _time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.importers import parsers as P
from app.importers.readers import read_workbook
from app.config import get_config
from app.models import (
    Category, Employee, Holiday, HolidayCalendar, ImportBatch, ManualMisRow, MonthResult, MusterRow, Period,
    PermissionRow, Setting, SummaryRow,
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
        raise ImportError_(f"{p.label} is locked. An admin must unlock it before importing.")
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
    hol_plan: list = []           # holiday-list rows of every sheet of this file, applied together after the loop
    hol_sheets: list = []         # index in results of each holiday-list sheet (for the summary lines)
    # every employee once (no per-row lookups; new ones are added as they appear)
    emp_cache: dict = {e.emp_no: e for e in db.scalars(select(Employee))}
    for ds in dets:
        rows = sheets[ds.sheet]
        warnings: list = []
        kind = ds.kind
        period: Optional[Period] = None
        n = 0
        # the upload record first: every row this sheet imports carries its id, so deleting the upload removes
        # exactly its data (v1.11); employee fields it changes are remembered so they can be put back
        batch = ImportBatch(kind=kind, filename=filename, sha256=sha, sheet=ds.sheet, rows=0, warnings=[],
                            uploaded_by=user.username if user else "")
        db.add(batch)
        db.flush()
        bid = batch.id
        before = _snapshot(emp_cache)
        notes: list = []
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
                     "in_time": r.in_time, "out_time": r.out_time, "work_min": r.work_min, "shift": r.shift,
                     "batch_id": bid} for r in keep])
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
                     "device_id": r.device_id, "batch_id": bid} for r, pl in zip(keep, places)])
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
                db.add(SummaryRow(period_id=period.id, emp_no=r["emp_no"], data=r, batch_id=bid))
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
        elif kind == "leavers":
            # greytHR "Date Of Leaving" (v1.16, owner): the whole list since the start comes every time — only the
            # employees the app knows are used; the rest (people who left before the app) are left alone
            recs = P.parse_leavers(rows, ds)
            changed = same = 0
            ignored = []
            for r in recs:
                e = emp_cache.get(r["emp_no"])
                if e is None:
                    ignored.append(r["emp_no"])
                    continue
                if e.dol != r["dol"]:
                    e.dol = r["dol"]
                    changed += 1
                else:
                    same += 1
                if e.is_active and r["dol"] <= date.today():
                    e.is_active = False
                n += 1
            notes.append(f"{n} leaving date{'s' if n != 1 else ''} of employees in the app "
                         f"({changed} new or changed, {same} already recorded).")
            if ignored:
                notes.append(f"{len(ignored)} older leaver{'s' if len(ignored) != 1 else ''} not in the app — not needed, "
                             "left out.")
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        elif kind == "permissions":
            # greytHR "Permission Report" (v1.16): WFH / On Duty (shown under Permission on the Overview) and
            # Regularization requests, day by day, with their workflow status
            recs = P.parse_permissions(rows, ds)
            period = _period_for(db, _month_of(ds, rows, [r["day"] for r in recs], warnings, use_title=False),
                                 period_hint)
            keep = [r for r in recs if (r["day"].year, r["day"].month) == (period.year, period.month)]
            _clear(db, PermissionRow, period, {r["emp_no"] for r in keep}, "permission requests", warnings)
            if keep:
                db.execute(insert(PermissionRow), [
                    {"period_id": period.id, "emp_no": r["emp_no"], "day": r["day"], "ptype": r["ptype"],
                     "from_time": r["from_time"], "to_time": r["to_time"], "reason": r["reason"],
                     "status": r["status"], "batch_id": bid} for r in keep])
            n = len(keep)
            off = sum(1 for r in keep if r["status"].lower() not in P.PERMISSION_COUNTED)
            by = Counter(r["ptype"] for r in keep if r["status"].lower() in P.PERMISSION_COUNTED)
            notes.append("Counted (approved or applied): " + (", ".join(f"{v} {k}" for k, v in sorted(by.items())) or "none")
                         + (f"; {off} withdrawn / rejected not counted." if off else "."))
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
                db.add(ManualMisRow(period_id=period.id, emp_no=r["emp_no"], data=r, batch_id=bid))
                n += 1
        elif kind in ("employee_master", "employee_category"):
            recs = P.parse_employee_master(rows, ds)
            from app.services import categories as CAT
            from app.services.settings import get_general
            cat_cache = CAT.by_key(db)
            # an older master whose "Category" column holds groups (Partner, SS, Article …) and no Group column
            groups = {g.lower() for g in get_general(db).get("groups", [])}
            cvals = {(r.get("category") or "").strip().lower() for r in recs} - {""}
            if "group" not in ds.map and cvals and cvals <= groups and kind == "employee_master":
                for r in recs:
                    r["group"], r["category"] = r.get("category", ""), ""
                warnings.append("The Category column holds groups (" + ", ".join(sorted(cvals)) + ") — imported as "
                                "the group. Employee categories come from greytHR's Employee Category Report.")
            n_muster = 0
            for r in recs:
                e = _ensure_employee(db, emp_cache, r["emp_no"], r.get("name", ""), "master")
                for k in ("category", "additional_category"):
                    if r.get(k):
                        c = _category_from_report(db, cat_cache, emp_cache, r[k])
                        setattr(e, k, c.name if c else r[k])
                    elif kind == "employee_category" and k in ds.map:
                        setattr(e, k, "")          # greytHR's report is the full truth: a blank clears it
                for k in ("name", "group", "location", "department", "designation", "email", "exempt_reason"):
                    if r.get(k):
                        setattr(e, k, r[k])
                for k in ("doj", "dol", "hours_per_day"):
                    if r.get(k) is not None:
                        setattr(e, k, r[k])
                if "exempt" in r:
                    e.exempt = bool(r["exempt"])
                if r.get("weekly_off"):
                    from app.engine.model import PATTERNS
                    wo = r["weekly_off"].strip().upper().replace(" ", "_")
                    wo = "CATEGORY" if wo in ("CATEGORY", "FOLLOW_CATEGORY", "AS_PER_CATEGORY") else wo
                    if wo == "MUSTER":            # older templates wrote MUSTER for everyone: follow the category
                        wo, n_muster = "CATEGORY", n_muster + 1
                    if wo in PATTERNS or wo == "CATEGORY":
                        e.weekly_off = wo
                    else:
                        warnings.append(f"{r['emp_no']}: weekly off pattern '{r['weekly_off']}' not recognised — kept "
                                        f"{e.weekly_off}")
                if r.get("working_saturdays"):
                    e.working_saturdays = r["working_saturdays"]
                if e.dol:
                    e.is_active = False
                n += 1
            if n_muster:
                warnings.append(f"{n_muster} row(s) say MUSTER: they follow their category (whose weekly offs default "
                                "to greytHR's). To pin someone to greytHR's weekly offs whatever the category says, "
                                "choose it on their page.")
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        elif kind == "holidays":
            # owner (v1.9): Date, Day, Holiday, Category, Optional — each holiday belongs to its category's list
            # (several categories, comma-separated → each list); no category → the file name's category, else the
            # default list
            recs = P.parse_holidays(rows, ds)
            _check_weekdays(recs, warnings)
            from_file = "" if "category" in ds.map else CAT_NAME_FROM_FILE(db, filename)
            for r in recs:
                names = [x.strip() for x in (r["category"] or from_file).split(",") if x.strip()]
                for nm in names or [None]:
                    hol_plan.append({**r, "target": ("category", nm) if nm else ("default", ""), "sheet": ds.sheet,
                                     "batch_id": bid})
                n += 1
            hol_sheets.append(len(results))
            touched.update(p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT")))
        if period is not None:
            touched.add(period.id)
        db.flush()
        batch.period_id, batch.rows, batch.warnings = (period.id if period else None), n, warnings[:50]
        batch.undo = _changes(before, emp_cache)
        audit(db, user, "import", kind, period.key if period else "", {"file": filename, "sheet": ds.sheet, "rows": n}, ip)
        results.append({"kind": kind, "label": P.KIND_LABELS[kind], "sheet": ds.sheet, "rows": n,
                        "period": period.label if period else "", "warnings": warnings[:20],
                        "mapping": ds.describe(), "source": ds.source, "confidence": ds.confidence,
                        **({"notes": notes} if notes else {})})
    if hol_plan:
        lines = _apply_holiday_lists(db, hol_plan, emp_cache)
        if hol_sheets:
            results[hol_sheets[0]]["notes"] = lines
            audit(db, user, "holidays.list", "holiday", filename, {"summary": lines[:20]}, ip)
    db.flush()
    if touched_out is not None:
        touched_out.update(touched)
    if recompute:
        recompute_from(db, touched)
    return results


# ----------------------------------------------------------------------------- deleting an upload (v1.11)
EMP_FIELDS = ("name", "group", "category", "additional_category", "location", "department", "designation", "email",
              "doj", "dol", "weekly_off", "working_saturdays", "hours_per_day", "exempt", "exempt_reason", "is_active")
ROW_TABLES = {"muster": MusterRow, "swipes": SwipeRow, "summary": SummaryRow, "mis_manual": ManualMisRow,
              "permissions": PermissionRow}
WHAT = {"muster": "attendance days", "swipes": "punches", "summary": "summary rows", "mis_manual": "manual MIS rows",
        "permissions": "permission requests"}


def _jsonable(v):
    return v.isoformat() if isinstance(v, date) else v


def _snapshot(emp_cache: dict) -> dict:
    return {no: tuple(_jsonable(getattr(e, f)) for f in EMP_FIELDS) for no, e in emp_cache.items()}


def _changes(before: dict, emp_cache: dict) -> dict:
    """Employees this sheet created, and the employee fields it changed (before → after)."""
    created = sorted(no for no in emp_cache if no not in before)
    changed = {}
    for no, old in before.items():
        e = emp_cache.get(no)
        if e is None:
            continue
        new = tuple(_jsonable(getattr(e, f)) for f in EMP_FIELDS)
        diff = {f: [a, b] for f, a, b in zip(EMP_FIELDS, old, new) if a != b}
        if diff:
            changed[no] = diff
    return {"created": created, "changed": changed}


def _restore(field: str, v):
    if field in ("doj", "dol") and isinstance(v, str) and v:
        return date.fromisoformat(v)
    return v


def import_effects(db: Session, batch: ImportBatch) -> dict:
    """What deleting this upload removes: {"rows": n (attendance / punches / …), "what": label, "holidays": n,
    "changed": n employees, "created": n employees, "tracked": bool (False = imported before v1.11 with nothing
    linked to it)}."""
    out = {"rows": 0, "what": WHAT.get(batch.kind, "rows"), "holidays": 0, "changed": 0, "created": 0}
    model = ROW_TABLES.get(batch.kind)
    if model is not None:
        out["rows"] = db.scalar(select(func.count()).select_from(model).where(model.batch_id == batch.id)) or 0
    if batch.kind == "holidays":
        out["holidays"] = db.scalar(select(func.count()).select_from(Holiday).where(Holiday.batch_id == batch.id)) or 0
    undo = batch.undo or {}
    out["changed"], out["created"] = len(undo.get("changed") or {}), len(undo.get("created") or [])
    out["tracked"] = bool(out["rows"] or out["holidays"] or out["changed"] or out["created"] or batch.undo is not None)
    return out


def delete_import(db: Session, batch: ImportBatch, user: Optional[User], reason: str, ip: str = "") -> str:
    """Delete an upload and the data it imported, then recalculate. Monthly files: their rows (a later file that
    replaced them already took them away); holiday lists: the holidays they added or changed; employee files: the
    fields they changed go back to what they were (unless edited since) and employees they created with no other
    data go. A finalised month is never touched. Returns a plain-language summary (raises ValueError if refused)."""
    period = db.get(Period, batch.period_id) if batch.period_id else None
    if period is not None and period.status != "DRAFT":
        raise ValueError(f"{period.label} is locked — unlock it first.")
    eff = import_effects(db, batch)
    done: list[str] = []
    model = ROW_TABLES.get(batch.kind)
    if model is not None and eff["rows"]:
        db.execute(delete(model).where(model.batch_id == batch.id))
        done.append(f"{eff['rows']} {eff['what']}")
    if batch.kind == "holidays" and eff["holidays"]:
        db.execute(delete(Holiday).where(Holiday.batch_id == batch.id))
        done.append(f"{eff['holidays']} holiday{'s' if eff['holidays'] != 1 else ''}")
    undo = batch.undo or {}
    restored = 0
    for no, diff in (undo.get("changed") or {}).items():
        e = db.get(Employee, no)
        if e is None:
            continue
        for f, (old, new) in diff.items():
            if _jsonable(getattr(e, f)) == new:            # not edited since: put the earlier value back
                setattr(e, f, _restore(f, old))
                restored += 1
    if restored:
        done.append(f"{restored} employee detail{'s' if restored != 1 else ''} put back")
    gone = []
    for no in undo.get("created") or []:
        if any(db.scalar(select(m.id).where(m.emp_no == no).limit(1)) for m in ROW_TABLES.values()):
            continue                                       # other files have data for them
        e = db.get(Employee, no)
        if e is not None:
            db.execute(delete(MonthResult).where(MonthResult.emp_no == no))
            db.delete(e)
            gone.append(no)
    if gone:
        done.append(f"{len(gone)} employee{'s' if len(gone) != 1 else ''} added by it")
    audit(db, user, "import.delete", batch.kind, period.key if period else "",
          {"file": batch.filename, "sheet": batch.sheet, "uploaded": batch.uploaded_at.isoformat(timespec="minutes"),
           "removed": done, "reason": reason.strip()}, ip)
    db.delete(batch)
    db.flush()
    # recalculate: the file's month (no attendance left → back to "waiting for files"), else every draft month
    if model is not None and period is not None:
        if db.scalar(select(MusterRow.id).where(MusterRow.period_id == period.id).limit(1)):
            compute_period(db, period)
        else:
            db.execute(delete(MonthResult).where(MonthResult.period_id == period.id))
            period.computed_at = None
    else:
        db.commit()
        recompute_from(db, {p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT",
                                                                           Period.computed_at.is_not(None)))},
                       commit_each=True)
    what = ", ".join(done) if done else "nothing was linked to it (imported before v1.11)"
    return f"“{batch.filename}” deleted — removed {what}."


def CAT_NAME_FROM_FILE(db: Session, filename: str) -> str:  # noqa: N802 — small alias
    from app.services.categories import category_in_filename
    return category_in_filename(db, filename)


def _category_from_report(db: Session, cache: dict, emp_cache: dict, name: str):
    """The category for a name in an employee report (loosely matched). A category first created from a holiday
    list with a slightly different spelling and no employees yet takes the report's spelling (master data)."""
    from app.services import categories as CAT
    c = CAT.find(db, name, cache)
    if c is None:
        return CAT.ensure(db, name, cache)
    clean = " ".join(name.split())[:64]
    if c.name != clean and not any(e.category == c.name or e.additional_category == c.name for e in emp_cache.values()):
        if db.scalar(select(Category).where(Category.name == clean)) is None:
            c.name = clean
    return c


def _check_weekdays(recs: list, warnings: list) -> None:
    """Lists with a Day column: the weekday must match the date. A wrong year (a 2025 date in a 2026 list) is corrected
    when the same day and month in the list's year falls on the stated weekday; anything else is reported."""
    from collections import Counter
    years = Counter(r["date"].year for r in recs if r.get("date"))
    main = years.most_common(1)[0][0] if years else None
    names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    for r in recs:
        wd = r.get("weekday")
        if wd is None or not r.get("date") or r["date"].weekday() == wd:
            continue
        d = r["date"]
        alt = None
        if main and d.year != main:
            try:
                alt = d.replace(year=main)
            except ValueError:
                alt = None
        if alt and alt.weekday() == wd:
            warnings.append(f"{r['name'] or 'Holiday'}: {d:%d-%b-%Y} is a {names[d.weekday()]}, but the list says "
                            f"{names[wd]} — taken as {alt:%d-%b-%Y} ({names[wd]}), the list's year.")
            r["date"] = alt
        else:
            warnings.append(f"{r['name'] or 'Holiday'}: {d:%d-%b-%Y} is a {names[d.weekday()]}, but the list says "
                            f"{names[wd]} — please check this date.")


FULL_LIST_MIN = 5    # a sheet with at least this many holidays in a year is a full list for that year


def _apply_holiday_lists(db: Session, plan: list, emp_cache: dict) -> list[str]:
    """Write holiday-list rows (plan rows with target ("category", name), ("default", "") or ("calendar_id", id) —
    the last for a list pasted into a chosen calendar). Each category's rows go
    to that category's own calendar (created and linked on first use, never made the default); rows without a
    category go to the default calendar ("Standard", created as the default when there is none). For every calendar and year with a full list (≥ 5 holidays) the
    file *replaces* that year — dates that moved are removed — so next year's or a revised list is just uploaded
    again. Returns plain-language summary lines (added / removed / changed per calendar and year)."""
    from collections import defaultdict
    from app.services import categories as CAT
    cache = CAT.by_key(db)
    cal_by_name = {c.name.lower(): c for c in db.scalars(select(HolidayCalendar))}
    lines: list[str] = []
    wanted: dict = defaultdict(dict)          # calendar id -> {date: row}
    cal_objs: dict = {}
    own_ok: set = set()                       # calendars already settled as a category's own list in this file
    for r in plan:
        how, name = r["target"]
        if how == "category":
            cat = CAT.find(db, name, cache)
            if cat is None:
                cat = CAT.ensure(db, name, cache)
                if not any(CAT.key(e.category) == CAT.key(cat.name) or CAT.key(e.additional_category) == CAT.key(cat.name)
                           for e in emp_cache.values()):
                    lines.append(f"New category “{cat.name}” (from the holiday list) — no employees in it yet; import the "
                                 "employee category report or set it on the employee's page.")
            cal = db.get(HolidayCalendar, cat.holiday_calendar_id) if cat.holiday_calendar_id else None
            if cal is not None and cal.id not in own_ok and _shared_calendar(db, cal, cat):
                # the category points at a shared / default calendar: give it a list of its own instead of
                # rewriting a calendar other people use
                lines.append(f"{cat.name} now has its own holiday list (it was using “{cal.name}”, which is shared "
                             "and stays as it was).")
                cal = None
            if cal is None:
                cal = cal_by_name.get(cat.name.lower())
                if cal is not None and _shared_calendar(db, cal, cat):
                    cal = None
                if cal is None:
                    nm = cat.name if cat.name.lower() not in cal_by_name else f"{cat.name} list"
                    cal = HolidayCalendar(name=nm, is_default=False)
                    db.add(cal)
                    db.flush()
                    cal_by_name[cal.name.lower()] = cal
                cat.holiday_calendar_id = cal.id
            own_ok.add(cal.id)
        elif how == "calendar_id":
            cal = db.get(HolidayCalendar, int(name))
        else:
            cal = next((c for c in cal_by_name.values() if c.is_default), None)
            if cal is None:
                cal = cal_by_name.get("standard") or HolidayCalendar(name="Standard")
                cal.is_default = True
                db.add(cal)
                db.flush()
                cal_by_name[cal.name.lower()] = cal
                lines.append(f"Rows without a category went to the default list “{cal.name}” (for people whose "
                             "category has no list of its own).")
        cal_objs[cal.id] = cal
        wanted[cal.id].setdefault(r["date"], r)
    for cid, rows in wanted.items():
        cal = cal_objs[cid]
        by_year = defaultdict(dict)
        for d, r in rows.items():
            by_year[d.year][d] = r
        for year, new in sorted(by_year.items()):
            old = {h.day: h for h in db.scalars(select(Holiday).where(Holiday.calendar_id == cid,
                                                                     Holiday.day >= date(year, 1, 1),
                                                                     Holiday.day <= date(year, 12, 31)))}
            full = len(new) >= FULL_LIST_MIN
            # replace only the span the file covers (a list split over two files never wipes the other half) and,
            # for a file of optional holidays only, only optional ones
            lo, hi = min(new), max(new)
            only_opt = all(r.get("optional") for r in new.values())
            removed = [h for d, h in old.items() if d not in new and lo <= d <= hi and (h.optional or not only_opt)] \
                if full else []
            for h in removed:
                db.delete(h)
            db.flush()
            added, changed = 0, 0
            for d, r in sorted(new.items()):
                nm = (r["name"] or "Holiday").strip()[:128]
                h = old.get(d)
                if h is None:
                    db.add(Holiday(calendar_id=cid, day=d, name=nm, optional=bool(r.get("optional")),
                                   batch_id=r.get("batch_id")))
                    added += 1
                elif h.name != nm or bool(h.optional) != bool(r.get("optional")):
                    h.name, h.optional = nm, bool(r.get("optional"))
                    if r.get("batch_id"):
                        h.batch_id = r["batch_id"]
                    changed += 1
            n_opt = sum(1 for r in new.values() if r.get("optional"))
            what = f"{len(new)} holidays" + (f" ({n_opt} optional)" if n_opt else "")
            if not old:
                lines.append(f"{cal.name} {year}: {what} loaded.")
            else:
                bits = [f"{added} new" if added else "", f"{changed} changed" if changed else "",
                        ("removed " + ", ".join(f"{h.name} {h.day:%d-%b}" for h in removed)) if removed else ""]
                bits = [b for b in bits if b]
                lines.append(f"{cal.name} {year}: {what} — " + ("; ".join(bits) if bits else "no changes") + "."
                             + ("" if full else " (Short list: added to the year, nothing removed.)"))
    db.flush()
    return lines


def _shared_calendar(db: Session, cal, cat) -> bool:
    """Is this calendar the default or another category's list?"""
    if cal.is_default:
        return True
    return bool(db.scalar(select(Category.id).where(Category.holiday_calendar_id == cal.id, Category.id != cat.id)
                          .limit(1)))


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
    """Recalculate the given draft months (with attendance), oldest first. Months are independent — nothing carries
    forward (v1.9) — so later months are not touched. commit_each=True commits after every month so the database
    write lock is held for one month's calculation at a time."""
    ps = sorted((p for p in (db.get(Period, i) for i in period_ids or ()) if p), key=lambda p: (p.year, p.month))
    n = 0
    for p in ps:
        if p.status != "DRAFT":
            continue
        if db.scalar(select(MusterRow.id).where(MusterRow.period_id == p.id).limit(1)):
            compute_period(db, p)
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
