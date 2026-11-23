"""Employees the app ignores completely (owner, v1.13): anyone whose group is in policy.ignored_groups (default
"OTHERS"). Uploads may contain them; their rows are stored but never calculated, listed, counted or exported.

One place for the rule so every page, metric and export agrees (docs/BUSINESS_RULES.md "Ignored groups").
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Employee


def ignored_names(policy) -> set[str]:
    return {str(g).strip().upper() for g in (policy.ignored_groups or ()) if str(g).strip()}


def is_ignored(group: Optional[str], policy) -> bool:
    return bool(group) and str(group).strip().upper() in ignored_names(policy)


def ignored_emp_nos(db: Session, policy=None) -> set[str]:
    """Employee numbers whose group (employee master) is an ignored group."""
    if policy is None:
        from app.services.settings import get_policy
        policy = get_policy(db)
    names = ignored_names(policy)
    if not names:
        return set()
    return set(db.scalars(select(Employee.emp_no).where(func.upper(func.trim(Employee.group)).in_(names))))


def employees(db: Session, policy=None, order=None) -> list[Employee]:
    """Every employee the app works with (ignored groups left out)."""
    skip = ignored_emp_nos(db, policy)
    q = select(Employee)
    if order is not None:
        q = q.order_by(order)
    return [e for e in db.scalars(q) if e.emp_no not in skip]
