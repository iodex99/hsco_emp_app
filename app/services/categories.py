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
    return ""


def category_in_filename(db: Session, filename: str) -> str:
    """The category a holiday-list file is for, from its name: the "HSCO (…)" form, else the longest existing category
    name contained in it; "" otherwise (never "(1)" or "(final)")."""
    n = name_from_filename(filename)
    if n:
        return n
    fk = key(re.sub(r"\.[a-z0-9]+$", "", filename or "", flags=re.I))
    best = ""
    for c in db.scalars(select(Category)):
        k = key(c.name)
        if len(k) >= 3 and k in fk and len(k) > len(key(best)):
            best = c.name
    return best


@dataclass
class Effective:
    weekly_off: str
    off_weekdays: str
    working_saturdays: str
    hours_per_day: Optional[float]
    calendar_id: Optional[int]
    pattern_from: str          # "employee" | category name | "greytHR"
    hours_from: str            # "employee" | category name | "policy"
    calendar_from: str         # category name | "default" | "greytHR" (holidays never per employee, v1.9)


def effective(e: Employee, cats: dict, default_cal: Optional[int], loaded: Optional[set] = None) -> Effective:
    """What applies to employee e. cats = by_key(db). loaded = ids of calendars with holidays in the year being
    calculated: a list not loaded for that year is passed over (next in the chain, finally greytHR's marks)."""
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
    # holidays follow the category only (owner, v1.9): category → additional category → default → greytHR
    cands = [(c.holiday_calendar_id, c.name) for c in chain if c.holiday_calendar_id] + \
        ([(default_cal, "default")] if default_cal else [])
    usable = [x for x in cands if loaded is None or x[0] in loaded]
    if usable:
        cal, cfrom = usable[0]
    else:
        cal, cfrom = None, ("greytHR (list not loaded for this year)" if cands else "greytHR")
    return Effective(pat, offs or "5,6", sats or "", hpd, cal, pfrom, hfrom, cfrom)


def default_calendar_id(db: Session) -> Optional[int]:
    return db.scalar(select(HolidayCalendar.id).where(HolidayCalendar.is_default.is_(True)))


def overview(db: Session) -> list[dict]:
    """Categories with their people and settings, for Settings → Categories (and gaps worth fixing)."""
    everyone = list(db.scalars(select(Employee)))
    emps = [e for e in everyone if not e.dol]
    cats = list(db.scalars(select(Category).order_by(Category.name)))
    anyone = Counter()
    for e in everyone:
        for n in {key(e.category), key(e.additional_category)} - {""}:
            anyone[n] += 1
    ck = {key(c.name): c for c in cats}
    primary, extra, uncovered = Counter(), Counter(), Counter()
    dcal = default_calendar_id(db)
    for e in emps:
        if ck.get(key(e.category)):
            primary[ck[key(e.category)].id] += 1
            if effective(e, ck, dcal).calendar_from in ("default", "greytHR"):
                uncovered[ck[key(e.category)].id] += 1          # no list from the category chain
        if ck.get(key(e.additional_category)):
            extra[ck[key(e.additional_category)].id] += 1
    cal_names = {c.id: c.name for c in db.scalars(select(HolidayCalendar))}
    out = []
    for c in cats:
        out.append({"c": c, "people": primary[c.id], "extra_people": extra[c.id], "uncovered": uncovered[c.id],
                    "deletable": not anyone[key(c.name)],
                    "calendar": cal_names.get(c.holiday_calendar_id, ""),
                    "pattern": PATTERNS.get(c.weekly_off, "") if c.weekly_off else ""})
    return out


def uncategorised(db: Session) -> list[Employee]:
    return [e for e in db.scalars(select(Employee).order_by(Employee.emp_no))
            if not e.dol and not (e.category or "").strip()]


def calendar_conflicts(db: Session, results) -> list[dict]:
    """Days where a holiday list and greytHR disagree for most of the people using that list in a calculated month
    (e.g. the list has no holiday on a day greytHR marks H) — worth checking before payroll. People are counted under
    the list they actually used (an article on the HSCO (Mum) list counts there)."""
    per = defaultdict(lambda: {"people": set(), "list_says": "", "name": ""})
    size = Counter()
    for r in results:
        src = (r.data or {}).get("calendar_from")          # the list the person actually used (v1.7.1)
        if src is None:                                      # calculated before v1.7.1
            cat = r.category or ""
        elif src == "default":
            cat = "default calendar"
        elif src == "employee" or src.startswith("greytHR"):
            cat = ""                                         # own calendar / no list: nothing to compare per list
        else:
            cat = src
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
