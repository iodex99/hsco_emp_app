"""Admin-editable settings persisted in the `settings` table. Defaults documented in docs/BUSINESS_RULES.md."""
from __future__ import annotations

from dataclasses import asdict, fields
from datetime import time

from sqlalchemy.orm import Session

from app.engine.model import DEFAULT_STATUS_MAP, Policy
from app.models import Setting

GENERAL_DEFAULTS = {
    "org_name": "Hasmukh Shah & Co. LLP",
    "org_address": "409/410 Dalamal Chambers, New Marine Lines, Mumbai 400020",
    "mfa_required": True,
    "allowed_mfa": ["totp", "webauthn", "email", "recovery"],
    "password_min_length": 10,
    "lockout_threshold": 5,
    "lockout_minutes": 15,
    "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "", "smtp_from": "", "smtp_starttls": True,
    "online_geocoding": False,
    "geocode_max_km": 15,
    "groups": ["Partner", "SS", "BSS", "ATA", "GST", "Offshore", "Article", "Trainee"],
    "locations": ["Mumbai", "Delhi"],
}


def _policy_to_json(p: Policy) -> dict:
    out = {}
    for k, v in asdict(p).items():
        if isinstance(v, time):
            out[k] = v.strftime("%H:%M")
        elif isinstance(v, tuple):
            out[k] = list(v)
        elif k == "shift_overrides":
            out[k] = {name: [a.strftime("%H:%M"), b.strftime("%H:%M")] for name, (a, b) in v.items()}
        else:
            out[k] = v
    return out


def _t(s: str) -> time:
    h, m = str(s).split(":")[:2]
    return time(int(h), int(m))


def policy_from_json(d: dict) -> Policy:
    base = Policy()
    kw = {}
    for f in fields(Policy):
        if f.name not in d:
            continue
        v = d[f.name]
        cur = getattr(base, f.name)
        if isinstance(cur, time):
            v = _t(v)
        elif isinstance(cur, tuple):
            v = tuple(v)
        elif f.name == "shift_overrides":
            v = {name: (_t(a), _t(b)) for name, (a, b) in (v or {}).items()}
        elif f.name == "status_map":
            v = {str(k).upper(): str(b) for k, b in (v or {}).items()} or dict(DEFAULT_STATUS_MAP)
        elif isinstance(cur, bool):
            v = bool(v)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            v = int(v)
        elif isinstance(cur, float):
            v = float(v)
        kw[f.name] = v
    return Policy(**{**{f.name: getattr(base, f.name) for f in fields(Policy)}, **kw})


def ensure_defaults(db: Session) -> None:
    if db.get(Setting, "policy") is None:
        db.add(Setting(key="policy", value=_policy_to_json(Policy())))
    g = db.get(Setting, "general")
    if g is None:
        db.add(Setting(key="general", value=dict(GENERAL_DEFAULTS)))
    else:
        merged = {**GENERAL_DEFAULTS, **(g.value or {})}
        if merged != g.value:
            g.value = merged


def get_policy(db: Session) -> Policy:
    s = db.get(Setting, "policy")
    return policy_from_json(s.value if s else {})


def get_policy_json(db: Session) -> dict:
    return _policy_to_json(get_policy(db))


def save_policy(db: Session, data: dict) -> Policy:
    p = policy_from_json({**get_policy_json(db), **data})
    s = db.get(Setting, "policy")
    s.value = _policy_to_json(p)
    return p


def get_general(db: Session) -> dict:
    s = db.get(Setting, "general")
    return {**GENERAL_DEFAULTS, **(s.value if s else {})}


def save_general(db: Session, data: dict) -> dict:
    s = db.get(Setting, "general")
    merged = {**get_general(db), **data}
    if s is None:
        db.add(Setting(key="general", value=merged))
    else:
        s.value = merged
    return merged
