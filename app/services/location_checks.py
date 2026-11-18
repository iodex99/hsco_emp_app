"""Checks on mobile sign-ins that have GPS coordinates (docs/BUSINESS_RULES.md §9a).

* Tagged "Office" but not at an office: the sign-in's coordinates are outside every named place of type Office
  (Settings → Locations). Only checked once at least one office is named; GPS has some slack (OFFICE_SLACK_M).
* One phone, several people: the same device id was used to sign in for two or more employees in the month
  (buddy-punching risk).
Rejected sign-ins are ignored. Pure functions over engine Swipe objects + Geofence rows (no DB access).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from app.engine.model import REJECTED_APPROVALS
from app.services.geo import haversine_m

OFFICE_SLACK_M = 150          # phone GPS is often 50–150 m off indoors


def office_fences(fences: Iterable) -> list:
    return [f for f in fences if (f.category or "").strip().lower() == "office"]


def _rejected(s) -> bool:
    return (getattr(s, "approval", "") or "").strip().lower() in REJECTED_APPROVALS


def is_office_tagged(s) -> bool:
    return (s.location_type or "").strip().lower() == "office" and not (s.swipe_type or "").lower() == "astra"


def at_office(s, offices: list) -> Optional[bool]:
    """True/False for an office-tagged sign-in with GPS when offices are named; None when it can't be judged."""
    if not offices or s.lat is None or s.lon is None:
        return None
    return any(haversine_m(s.lat, s.lon, f.lat, f.lon) <= f.radius_m + OFFICE_SLACK_M for f in offices)


def shared_devices(swipes: Iterable) -> dict[str, set]:
    """device_id → employees who signed in with it (only devices used by 2+ people)."""
    users: dict = defaultdict(set)
    for s in swipes:
        if getattr(s, "device_id", "") and not _rejected(s):
            users[s.device_id].add(s.emp_no)
    return {d: e for d, e in users.items() if len(e) > 1}


def flags_by_employee(swipes: list, fences: Iterable, names: Optional[dict] = None) -> dict[str, list[dict]]:
    """{emp_no: [flag dict, …]} with codes OFF_SITE_OFFICE (medium) and SHARED_DEVICE (high)."""
    names = names or {}
    offices = office_fences(fences)
    out: dict = defaultdict(list)
    away: dict = defaultdict(list)
    if offices:
        for s in swipes:
            if is_office_tagged(s) and not _rejected(s) and at_office(s, offices) is False:
                away[s.emp_no].append(s)
    for emp, lst in away.items():
        lst.sort(key=lambda s: s.ts)
        eg = "; ".join(f"{s.ts:%d-%b} at {s.place or f'{s.lat:.4f}, {s.lon:.4f}'}" for s in lst[:3])
        out[emp].append({"code": "OFF_SITE_OFFICE", "severity": "medium",
                         "message": f"{len(lst)} mobile sign-in(s) tagged 'Office' were not at any office ({eg}"
                                    + (" …" if len(lst) > 3 else "") + ")"})
    for dev, emps in shared_devices(swipes).items():
        count = defaultdict(int)
        model = ""
        for s in swipes:
            if getattr(s, "device_id", "") == dev and not _rejected(s):
                count[s.emp_no] += 1
                model = model or getattr(s, "device", "")
        for emp in emps:
            others = ", ".join(sorted(names.get(o, o) for o in emps if o != emp))
            out[emp].append({"code": "SHARED_DEVICE", "severity": "high",
                             "message": f"Signed in {count[emp]} time(s) from a phone{f' ({model})' if model else ''} "
                                        f"also used to sign in for {others}"})
    return dict(out)
