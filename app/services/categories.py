"""Employee categories (docs/BUSINESS_RULES.md §2b) — greytHR's "Category" (HSCO (Mum), HSCO (Carlyle), …).

Every employee has a category and optionally an additional category (e.g. Article + HSCO (Mum)). A category can carry
a holiday list (calendar), a weekly-off pattern and hours per day. What applies to an employee, first match wins:

* weekly offs:   the employee's own pattern (anything but "Follow category") → category → additional category →
                 greytHR's muster
* hours per day: employee → category → additional category → policy
* holidays:      the employee's own calendar → category's → additional category's → default calendar → greytHR's H

Names from different files are matched loosely ("HSCO (CDTIPL) Boutique" = "HSCO (CDTIPL Boutique)",
"MDS" = "HSCO (MDS)", "Hsco (Mum)" = "HSCO (Mum)") by `key()`. Pure helpers + small queries; no web code.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.model import PATTERNS, P_MUSTER
from app.models import Category, Employee, HolidayCalendar

FOLLOW = "CATEGORY"                     # Employee.weekly_off value: follow the category
FIRM_PREFIXES = ("hsco",)               # dropped when matching names ("HSCO (MDS)" = "MDS")


def key(name: Optional[str]) -> str:
    """Loose identity of a category name: lower-case letters and digits only, without the firm prefix."""
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for p in FIRM_PREFIXES:
        if k.startswith(p) and len(k) > len(p):
            k = k[len(p):]
    return k


def by_key(db: Session) -> dict[str, Category]:
    return {key(c.name): c for c in db.scalars(select(Category))}


def find(db: Session, name: str, cache: Optional[dict] = None) -> Optional[Category]:
    k = key(name)
    if not k:
        return None
    cats = cache if cache is not None else by_key(db)
    return cats.get(k)


def ensure(db: Session, name: str, cache: Optional[dict] = None) -> Optional[Category]:
    """The category with this (loosely matched) name; created when new. cache = by_key() dict kept up to date."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not key(name):
        return None
    cats = cache if cache is not None else by_key(db)
    c = cats.get(key(name))
    if c is None:
        c = Category(name=name[:64])
        db.add(c)
        db.flush()
        cats[key(name)] = c
    return c


def name_from_filename(filename: str) -> str:
    """'Holiday List Importer HSCO (Carlyle) 2026.xlsx' → 'HSCO (Carlyle)'; '' when the name carries no category."""
    base = re.sub(r"\.[a-z0-9]+$", "", filename or "", flags=re.I)
    m = re.search(r"(?i)\b(hsco)\s*\(([^)]+)\)(\s*boutique)?", base)
    if m:
        return f"HSCO ({m.group(2).strip()}){' Boutique' if m.group(3) else ''}"
    m = re.search(r"\(([^)]+)\)", base)
    return m.group(1).strip() if m else ""


@dataclass
class Effective:
    weekly_off: str
    off_weekdays: str
    working_saturdays: str
    hours_per_day: Optional[float]
    calendar_id: Optional[int]
    pattern_from: str          # "employee" | category name | "greytHR"
    hours_from: str            # "employee" | category name | "policy"
    calendar_from: str         # "employee" | category name | "default" | "greytHR"


def effective(e: Employee, cats: dict, default_cal: Optional[int]) -> Effective:
    """What applies to employee e. cats = by_key(db)."""
    chain = [c for c in (cats.get(key(e.category)), cats.get(key(e.additional_category))) if c is not None]
    if e.weekly_off and e.weekly_off != FOLLOW and e.weekly_off in PATTERNS:
        pat, offs, sats, pfrom = e.weekly_off, e.off_weekdays, e.working_saturdays, "employee"
    else:
        src = next((c for c in chain if c.weekly_off in PATTERNS), None)
        if src is not None:
            pat, offs, sats, pfrom = src.weekly_off, src.off_weekdays, src.working_saturdays, src.name
        else:
            pat, offs, sats, pfrom = P_MUSTER, "5,6", "", "greytHR"
    if e.hours_per_day:
        hpd, hfrom = e.hours_per_day, "employee"
    else:
        src = next((c for c in chain if c.hours_per_day), None)
        hpd, hfrom = (src.hours_per_day, src.name) if src is not None else (None, "policy")
    if e.holiday_calendar_id:
        cal, cfrom = e.holiday_calendar_id, "employee"
    else:
        src = next((c for c in chain if c.holiday_calendar_id), None)
        if src is not None:
            cal, cfrom = src.holiday_calendar_id, src.name
        else:
            cal, cfrom = default_cal, ("default" if default_cal else "greytHR")
    return Effective(pat, offs or "5,6", sats or "", hpd, cal, pfrom, hfrom, cfrom)


def default_calendar_id(db: Session) -> Optional[int]:
    return db.scalar(select(HolidayCalendar.id).where(HolidayCalendar.is_default.is_(True)))


def overview(db: Session) -> list[dict]:
    """Categories with their people and settings, for Settings → Categories (and gaps worth fixing)."""
    emps = [e for e in db.scalars(select(Employee)) if not e.dol]
    cats = list(db.scalars(select(Category).order_by(Category.name)))
    ck = {key(c.name): c for c in cats}
    primary, extra, uncovered = Counter(), Counter(), Counter()
    dcal = default_calendar_id(db)
    for e in emps:
        if ck.get(key(e.category)):
            primary[ck[key(e.category)].id] += 1
            if effective(e, ck, dcal).calendar_from in ("default", "greytHR"):
                uncovered[ck[key(e.category)].id] += 1          # no list from the category chain or the employee
        if ck.get(key(e.additional_category)):
            extra[ck[key(e.additional_category)].id] += 1
    cal_names = {c.id: c.name for c in db.scalars(select(HolidayCalendar))}
    out = []
    for c in cats:
        out.append({"c": c, "people": primary[c.id], "extra_people": extra[c.id], "uncovered": uncovered[c.id],
                    "calendar": cal_names.get(c.holiday_calendar_id, ""),
                    "pattern": PATTERNS.get(c.weekly_off, "") if c.weekly_off else ""})
    return out


def uncategorised(db: Session) -> list[Employee]:
    return [e for e in db.scalars(select(Employee).order_by(Employee.emp_no))
            if not e.dol and not (e.category or "").strip()]


def calendar_conflicts(db: Session, results) -> list[dict]:
    """Days where a category's holiday list and greytHR disagree for most of its people in a calculated month
    (e.g. the list has no holiday on a day greytHR marks H) — worth checking before payroll."""
    per = defaultdict(lambda: {"people": set(), "list_says": "", "name": ""})
    size = Counter()
    for r in results:
        cat = r.category or ""
        if not cat:
            continue
        size[cat] += 1
        for dd in r.days or []:
            if "Holiday calendar differs from greytHR" in (dd.get("notes") or []):
                k = (cat, dd["date"])
                per[k]["people"].add(r.emp_no)
                per[k]["list_says"] = "holiday" if dd.get("type") == "HOLIDAY" else "working day"
                per[k]["name"] = dd.get("holiday") or per[k]["name"]
    out = []
    for (cat, d), v in per.items():
        n = len(v["people"])
        if n >= max(2, 0.5 * size[cat]):
            out.append({"category": cat, "date": d, "people": n, "of": size[cat], "list_says": v["list_says"],
                        "name": v["name"]})
    return sorted(out, key=lambda x: (x["date"], x["category"]))
