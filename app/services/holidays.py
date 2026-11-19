"""Targeted holidays (docs/BUSINESS_RULES.md §2a): a holiday that applies only to some people — chosen locations,
groups and/or named employees — on top of whatever holiday calendar each person follows.

Who gets it: every employee listed by number, plus everyone matching the location, group and category filters (a
filter left empty matches any value; at least one of locations / groups / categories / employees must be given). A
category matches the employee's category or additional category, loosely ("HSCO (Mum)" = "Hsco (Mum)"). Matching ignores case and
surrounding spaces. Pure helpers + one month query; no web code.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TargetedHoliday


def split(s: str | Iterable[str] | None) -> list[str]:
    """'Mumbai, Delhi; Pune' or ['Mumbai', 'Delhi'] → ['Mumbai', 'Delhi', 'Pune'] (de-duplicated, order kept)."""
    if s is None:
        return []
    parts = re.split(r"[,;\n|]+", s) if isinstance(s, str) else [x for v in s for x in re.split(r"[,;\n|]+", v or "")]
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def join(values: Iterable[str]) -> str:
    return ", ".join(split(list(values)))


def applies(h, emp_no: str, location: str, group: str, categories: tuple = ()) -> bool:
    """Does targeted holiday h (.locations/.groups/.categories/.emp_nos strings) apply to this employee?
    categories = the employee's (category, additional category)."""
    from app.services.categories import key
    emps = {x.lower() for x in split(h.emp_nos)}
    if emp_no and emp_no.lower() in emps:
        return True
    locs = {x.lower() for x in split(h.locations)}
    grps = {x.lower() for x in split(h.groups)}
    cats = {key(x) for x in split(getattr(h, "categories", "") or "")}
    if not locs and not grps and not cats:
        return False
    return ((not locs or (location or "").strip().lower() in locs)
            and (not grps or (group or "").strip().lower() in grps)
            and (not cats or bool(cats & {key(c) for c in categories if c})))


def describe(h, names: dict | None = None) -> str:
    """'Delhi & Article · plus Asha Rao (HS001)' style summary of who a targeted holiday is for."""
    parts = []
    cats = split(getattr(h, "categories", "") or "")
    if split(h.locations) or split(h.groups) or cats:
        a = " & ".join(x for x in (" / ".join(split(h.locations)), " / ".join(split(h.groups)), " / ".join(cats)) if x)
        parts.append(a)
    if split(h.emp_nos):
        names = names or {}
        parts.append(("plus " if parts else "") + ", ".join(f"{names[x]} ({x})" if x in names else x
                                                            for x in split(h.emp_nos)))
    return " · ".join(parts)


def for_month(db: Session, year: int, month: int) -> list[TargetedHoliday]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return list(db.scalars(select(TargetedHoliday).where(TargetedHoliday.day >= start, TargetedHoliday.day < end)
                           .order_by(TargetedHoliday.day)))


def by_employee(holidays: list, employees: Iterable) -> dict[str, dict]:
    """{emp_no: {date: name}} for employees (objects with emp_no / location / group) that a holiday applies to."""
    out: dict[str, dict] = {}
    for e in employees:
        for h in holidays:
            if applies(h, e.emp_no, e.location or "", e.group or "",
                       (getattr(e, "category", "") or "", getattr(e, "additional_category", "") or "")):
                out.setdefault(e.emp_no, {})[h.day] = h.name or "Holiday"
    return out


_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|" \
          "september|october|november|december"
_DATE_RES = [
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]+({_MONTHS})[a-z]*[\s\-/.,]+(\d{{4}}|\d{{2}})\b", re.I),
    re.compile(rf"\b({_MONTHS})[a-z]*[\s\-/.]+(\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-/.]+(\d{{4}})\b", re.I),
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b"),
]
_WD_RE = re.compile(r"\b(mon|tues|tue|wednes|wed|thurs|thu|fri|satur|sat|sun)(day)?\b\.?", re.I)
_OPT_RE = re.compile(r"\(?\b(optional|restricted|rh)\b\)?", re.I)


def _ymd(i: int, g: tuple):
    """Date from the groups of _DATE_RES[i] (day-first for dd/mm/yyyy, as in India)."""
    from datetime import date as _d
    mon = {m[:3]: n for n, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"), start=1)}
    try:
        if i == 0:
            dd, mm, yy = int(g[0]), mon[g[1][:3].lower()], int(g[2])
        elif i == 1:
            mm, dd, yy = mon[g[0][:3].lower()], int(g[1]), int(g[2])
        elif i == 2:
            yy, mm, dd = int(g[0]), int(g[1]), int(g[2])
        else:
            dd, mm, yy = int(g[0]), int(g[1]), int(g[2])
        yy = yy + 2000 if yy < 100 else yy
        return _d(yy, mm, dd) if 1990 <= yy <= 2100 else None
    except (KeyError, ValueError):
        return None


_NOYEAR_RES = [
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]+({_MONTHS})[a-z]*\b", re.I),
    re.compile(rf"\b({_MONTHS})[a-z]*[\s\-/.]+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I),
]


def parse_pasted(text: str, default_year: int | None = None) -> tuple[list[dict], list[str]]:
    """Holidays from pasted text — one per line, the date anywhere in it ("1  Republic Day  26 January 2026",
    "26-01-2026 Republic Day", "Republic Day, Jan 26, 2026", "Jan 26 Monday Republic Day"); serial numbers, weekday
    names (before or after the date, or in brackets) and the words optional / restricted are understood. A line
    without a year takes default_year, else the year most other lines have, else this year. Returns (rows {date, name,
    optional, weekday}, lines that had no date)."""
    from collections import Counter
    from datetime import date as _d
    from app.importers.parsers import weekday_of
    rows, skipped = [], []
    for raw in (text or "").splitlines():
        line = " ".join(raw.replace("	", " ").split())
        if not line:
            continue
        d, span, noyear = None, None, None
        for i, rx in enumerate(_DATE_RES):
            m = rx.search(line)
            if m:
                d = _ymd(i, m.groups())
                if d:
                    span = m.span()
                    break
        if d is None:
            for i, rx in enumerate(_NOYEAR_RES):
                m = rx.search(line)
                if m:
                    g = m.groups()
                    dd, mon = (g[0], g[1]) if i == 0 else (g[1], g[0])
                    mm = _ymd(0, (dd, mon, "2000"))
                    if mm:
                        noyear, span = (mm.month, mm.day), m.span()
                        break
        if d is None and noyear is None:
            if any(ch.isalpha() for ch in line) and not re.match(r"(?i)^(s\.?\s*no|sr|holiday|date|day|occasion)\b", line):
                skipped.append(line)
            continue
        wd = None
        after = re.match(r"\s*[,(\-–]?\s*(" + _WD_RE.pattern + r")\s*\)?", line[span[1]:], re.I)
        if after:                                    # "26-01-2026 Monday …": the weekday that goes with the date
            wd = weekday_of(after.group(1))
            line_rest_after = line[span[1] + after.end():]
        else:
            line_rest_after = line[span[1]:]
        rest = (line[:span[0]] + " " + line_rest_after).strip()
        optional = bool(_OPT_RE.search(rest))
        rest = _OPT_RE.sub(" ", rest)
        if wd is None:
            for m in _WD_RE.finditer(rest):
                before = rest[:m.start()].rstrip()
                boxed = before.endswith("(")
                # a weekday column / "(Monday)" — not part of a name such as "Good Friday"
                if boxed or not before or not before[-1].isalpha():
                    wd = weekday_of(m.group(0))
                    rest = rest[:m.start()] + " " + rest[m.end():]
                    break
        rest = re.sub(r"\(\s*\)", " ", rest)
        rest = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", rest)              # serial number
        name = " ".join(rest.strip(" -–—|,;:.").split())
        rows.append({"date": d, "noyear": noyear, "name": name or "Holiday", "optional": optional, "weekday": wd})
    years = Counter(r["date"].year for r in rows if r["date"])
    year = default_year or (years.most_common(1)[0][0] if years else _d.today().year)
    out = []
    for r in rows:
        if r["date"] is None:
            try:
                r["date"] = _d(year, *r["noyear"])
            except ValueError:
                skipped.append(r["name"])
                continue
        r.pop("noyear", None)
        out.append(r)
    return out, skipped
