"""Minimum-hours policies (owner, v1.14): named "hours per day" values targeted at specific employees — e.g. an 8-hour
day for some headcount while the firm's is 8.5 h.

Stored as one setting ("hours_policies": {"next": n, "items": [{"id", "name", "hours", "emp_nos": [...]}]}). An
employee is in at most one policy. What applies to a person (services/categories.effective): their policy → their own
hours per day (employee page) → their category → the firm's (Calculation rules). Every change is audit-logged by the
route and recalculates the draft months.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Setting

KEY = "hours_policies"
MIN_H, MAX_H = 1.0, 16.0


def _load(db: Session) -> dict:
    s = db.get(Setting, KEY)
    v = dict(s.value or {}) if s else {}
    return {"next": int(v.get("next") or 1), "items": [dict(x) for x in (v.get("items") or [])]}


def _save(db: Session, data: dict) -> None:
    s = db.get(Setting, KEY)
    val = {"next": data["next"], "items": data["items"]}
    if s is None:
        db.add(Setting(key=KEY, value=val))
    else:
        s.value = val


def all_policies(db: Session) -> list[dict]:
    return sorted(_load(db)["items"], key=lambda x: (x["hours"], x["name"].lower()))


def get(db: Session, pid: int) -> Optional[dict]:
    return next((x for x in _load(db)["items"] if x["id"] == pid), None)


def assignments(db: Session) -> dict:
    """{emp_no: (policy name, hours per day, policy id)}."""
    out = {}
    for x in _load(db)["items"]:
        for e in x.get("emp_nos") or []:
            out[e] = (x["name"], float(x["hours"]), x["id"])
    return out


def parse_hours(v) -> float:
    """'8', '8.0', '7:30' → hours; ValueError if not between MIN_H and MAX_H."""
    t = str(v or "").strip().replace(",", ".")
    if ":" in t:
        h, m = t.split(":", 1)
        val = int(h) + int(m) / 60
    else:
        val = float(t)
    if not (MIN_H <= val <= MAX_H):
        raise ValueError(f"Hours per day must be between {MIN_H:g} and {MAX_H:g}.")
    return round(val, 4)


def create(db: Session, name: str, hours) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the policy a name.")
    data = _load(db)
    if any(x["name"].strip().lower() == name.lower() for x in data["items"]):
        raise ValueError(f"A policy called “{name}” already exists.")
    item = {"id": data["next"], "name": name[:80], "hours": parse_hours(hours), "emp_nos": []}
    data["items"].append(item)
    data["next"] += 1
    _save(db, data)
    return item


def update(db: Session, pid: int, name: str, hours) -> dict:
    data = _load(db)
    item = next((x for x in data["items"] if x["id"] == pid), None)
    if item is None:
        raise ValueError("That policy no longer exists.")
    name = (name or "").strip() or item["name"]
    if any(x["id"] != pid and x["name"].strip().lower() == name.lower() for x in data["items"]):
        raise ValueError(f"A policy called “{name}” already exists.")
    item["name"], item["hours"] = name[:80], parse_hours(hours)
    _save(db, data)
    return item


def delete(db: Session, pid: int) -> dict:
    data = _load(db)
    item = next((x for x in data["items"] if x["id"] == pid), None)
    if item is None:
        raise ValueError("That policy no longer exists.")
    data["items"] = [x for x in data["items"] if x["id"] != pid]
    _save(db, data)
    return item


def assign(db: Session, pid: Optional[int], emp_nos) -> dict:
    """Put these employees in policy pid (taking them out of any other), or out of every policy when pid is None.
    Returns {"moved": {emp_no: old policy name}, "added": n}."""
    data = _load(db)
    target = next((x for x in data["items"] if x["id"] == pid), None) if pid is not None else None
    if pid is not None and target is None:
        raise ValueError("That policy no longer exists.")
    wanted = [str(e).strip().upper() for e in emp_nos if str(e).strip()]
    moved = {}
    for x in data["items"]:
        if x is target:
            continue
        keep = []
        for e in x.get("emp_nos") or []:
            if e in wanted:
                moved[e] = x["name"]
            else:
                keep.append(e)
        x["emp_nos"] = keep
    added = 0
    if target is not None:
        have = set(target.get("emp_nos") or [])
        for e in wanted:
            if e not in have:
                have.add(e)
                added += 1
        target["emp_nos"] = sorted(have)
    _save(db, data)
    return {"moved": moved, "added": added}


def remove(db: Session, pid: int, emp_no: str) -> None:
    data = _load(db)
    for x in data["items"]:
        if x["id"] == pid:
            x["emp_nos"] = [e for e in x.get("emp_nos") or [] if e != emp_no]
    _save(db, data)
