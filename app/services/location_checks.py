"""Checks on mobile sign-ins (docs/BUSINESS_RULES.md §9a).

* One phone, several headcount: the same device id was used to sign in for two or more employees in the month
  (buddy-punching risk) → SHARED_DEVICE (high).
Rejected sign-ins are ignored. Pure functions over engine Swipe objects (no DB access).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from app.engine.model import REJECTED_APPROVALS


def _rejected(s) -> bool:
    return (getattr(s, "approval", "") or "").strip().lower() in REJECTED_APPROVALS


def is_office_tagged(s) -> bool:
    return (s.location_type or "").strip().lower() == "office" and not (s.swipe_type or "").lower() == "astra"


def shared_devices(swipes: Iterable) -> dict[str, set]:
    """device_id → employees who signed in with it (only devices used by 2+ headcount)."""
    users: dict = defaultdict(set)
    for s in swipes:
        if getattr(s, "device_id", "") and not _rejected(s):
            users[s.device_id].add(s.emp_no)
    return {d: e for d, e in users.items() if len(e) > 1}


def flags_by_employee(swipes: list, names: Optional[dict] = None) -> dict[str, list[dict]]:
    """{emp_no: [flag dict, …]} — SHARED_DEVICE (high)."""
    names = names or {}
    out: dict = defaultdict(list)
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
