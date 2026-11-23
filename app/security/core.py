"""Passwords, sessions and CSRF (design notes in docs/ARCHITECTURE.md §Security). Two-step verification was removed
in v1.10 at the owner's request: the password is the only sign-in factor.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import User, UserSession

_ph = PasswordHasher()
SESSION_COOKIE = "hsco_session"

# ---------------------------------------------------------------- passwords


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    try:
        return _ph.verify(hash_, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problems(pw: str, min_len: int = 10, username: str = "") -> list[str]:
    p = []
    if len(pw) < min_len:
        p.append(f"at least {min_len} characters")
    if not any(c.isupper() for c in pw) or not any(c.islower() for c in pw):
        p.append("upper and lower case letters")
    if not any(c.isdigit() for c in pw):
        p.append("a number")
    if not any(not c.isalnum() for c in pw):
        p.append("a symbol")
    if username and username.lower() in pw.lower():
        p.append("must not contain the username")
    return p


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- sessions

def create_session(db: Session, user: User, ip: str, ua: str) -> str:
    cfg = get_config()
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_h(token), user_id=user.id, csrf_token=secrets.token_urlsafe(24),
                       expires_at=datetime.now() + timedelta(hours=cfg.session_hours), ip=ip, user_agent=ua[:250],
                       mfa_passed=True, pending={}))      # (legacy columns: signed in = password verified)
    db.flush()
    return token


def load_session(db: Session, token: Optional[str]) -> Optional[UserSession]:
    if not token:
        return None
    s = db.scalar(select(UserSession).where(UserSession.token_hash == _h(token)))
    if s is None:
        return None
    now = datetime.now()
    cfg = get_config()
    idle = timedelta(minutes=cfg.idle_minutes)
    if s.expires_at < now or s.last_seen + idle < now:
        db.delete(s)
        db.flush()
        return None
    if (now - s.last_seen).total_seconds() > 60:   # avoid a write on every request
        s.last_seen = now
    return s


def destroy_session(db: Session, token: Optional[str]) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _h(token)))


def destroy_user_sessions(db: Session, user_id: int, except_id: Optional[int] = None) -> None:
    q = delete(UserSession).where(UserSession.user_id == user_id)
    if except_id:
        q = q.where(UserSession.id != except_id)
    db.execute(q)


def check_csrf(sess: UserSession, token: Optional[str]) -> bool:
    return bool(token) and hmac.compare_digest(sess.csrf_token, token)
