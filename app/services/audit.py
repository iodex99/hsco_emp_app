from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(db: Session, user: Optional[User], action: str, entity: str = "", entity_id: Any = "",
          details: Optional[dict] = None, ip: str = "") -> None:
    db.add(AuditLog(user_id=user.id if user else None, username=user.username if user else "",
                    action=action, entity=entity, entity_id=str(entity_id or ""),
                    details=_clean(details or {}), ip=ip or ""))


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if "password" in k.lower() or "secret" in k.lower():
            v = "***"
        elif not isinstance(v, (str, int, float, bool, type(None), list, dict)):
            v = str(v)
        out[k] = v
    return out
