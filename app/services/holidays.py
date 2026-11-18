"""Targeted holidays (docs/BUSINESS_RULES.md §2a): a holiday that applies only to some people — chosen locations,
groups and/or named employees — on top of whatever holiday calendar each person follows.

Who gets it: every employee listed by number, plus everyone matching the location and group filters (a filter left
empty matches any value; at least one of locations / groups / employees must be given). Matching ignores case and
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


def applies(h, emp_no: str, location: str, group: str) -> bool:
    """Does targeted holiday h (anything with .locations/.groups/.emp_nos strings) apply to this employee?"""
    emps = {x.lower() for x in split(h.emp_nos)}
    if emp_no and emp_no.lower() in emps:
        return True
    locs = {x.lower() for x in split(h.locations)}
    grps = {x.lower() for x in split(h.groups)}
    if not locs and not grps:
        return False
    return (not locs or (location or "").strip().lower() in locs) and (not grps or (group or "").strip().lower() in grps)


def describe(h, names: dict | None = None) -> str:
    """'Delhi & Article · plus Asha Rao (HS001)' style summary of who a targeted holiday is for."""
    parts = []
    if split(h.locations) or split(h.groups):
        a = " & ".join(x for x in (" / ".join(split(h.locations)), " / ".join(split(h.groups))) if x)
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
            if applies(h, e.emp_no, e.location or "", e.group or ""):
                out.setdefault(e.emp_no, {})[h.day] = h.name or "Holiday"
    return out
