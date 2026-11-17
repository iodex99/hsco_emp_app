"""Passwords, secret encryption, sessions, CSRF, TOTP, recovery codes, email OTP, WebAuthn.

Design notes in docs/ARCHITECTURE.md §Security.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import smtplib
import ssl
import time as _time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional

import pyotp
import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import EmailOtp, MfaMethod, RecoveryCode, User, UserSession

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


# ---------------------------------------------------------------- secret encryption

def _fernet() -> Fernet:
    key = hashlib.sha256(get_config().secret_key + b"mfa-secrets").digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def decrypt(s: str) -> str:
    try:
        return _fernet().decrypt(s.encode()).decode()
    except InvalidToken:
        return ""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- sessions

def create_session(db: Session, user: User, ip: str, ua: str) -> str:
    cfg = get_config()
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_h(token), user_id=user.id, csrf_token=secrets.token_urlsafe(24),
                       expires_at=datetime.now() + timedelta(hours=cfg.session_hours), ip=ip, user_agent=ua[:250],
                       mfa_passed=False, pending={}))
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
    idle = timedelta(minutes=cfg.idle_minutes if s.mfa_passed else 10)
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


# ---------------------------------------------------------------- TOTP

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def verify_totp(method: MfaMethod, code: str, secret: Optional[str] = None) -> bool:
    """Accept current ±1 step; reject reuse of an already-used step (replay)."""
    code = (code or "").replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    sec = secret or decrypt(method.secret)
    if not sec:
        return False
    t = pyotp.TOTP(sec)
    now_step = int(_time.time()) // 30
    for step in (now_step - 1, now_step, now_step + 1):
        if hmac.compare_digest(t.at(step * 30), code):
            if step <= (method.last_totp_step or 0):
                return False
            method.last_totp_step = step
            return True
    return False


# ---------------------------------------------------------------- recovery codes

def new_recovery_codes(db: Session, user: User, n: int = 10) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = []
    for _ in range(n):
        c = "-".join(secrets.token_hex(3) for _ in range(2)).upper()
        codes.append(c)
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_password(c)))
    return codes


def use_recovery_code(db: Session, user: User, code: str) -> bool:
    code = (code or "").strip().upper()
    for rc in db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))):
        if verify_password(rc.code_hash, code):
            rc.used_at = datetime.now()
            return True
    return False


def recovery_remaining(db: Session, user: User) -> int:
    return len(db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))).all())


# ---------------------------------------------------------------- email OTP

def smtp_configured(general: dict) -> bool:
    return bool(general.get("smtp_host") and general.get("smtp_from"))


def send_email_otp(db: Session, user: User, general: dict) -> bool:
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.execute(delete(EmailOtp).where(EmailOtp.user_id == user.id))
    db.add(EmailOtp(user_id=user.id, code_hash=hash_password(code), expires_at=datetime.now() + timedelta(minutes=10)))
    msg = EmailMessage()
    msg["Subject"] = f"{general.get('org_name', '')} attendance app — sign-in code"
    msg["From"] = general["smtp_from"]
    msg["To"] = user.email
    msg.set_content(f"Your sign-in code is {code}. It expires in 10 minutes.\n"
                    "If you did not try to sign in, contact your administrator.")
    sender = getattr(send_email_otp, "_test_sink", None)
    if sender is not None:  # tests capture the code instead of sending
        sender(user.email, code)
        return True
    with smtplib.SMTP(general["smtp_host"], int(general.get("smtp_port") or 587), timeout=15) as s:
        if general.get("smtp_starttls", True):
            s.starttls(context=ssl.create_default_context())
        if general.get("smtp_user"):
            s.login(general["smtp_user"], general.get("smtp_password", ""))
        s.send_message(msg)
    return True


def verify_email_otp(db: Session, user: User, code: str) -> bool:
    row = db.scalar(select(EmailOtp).where(EmailOtp.user_id == user.id, EmailOtp.used.is_(False)))
    if row is None or row.expires_at < datetime.now() or row.attempts >= 5:
        return False
    row.attempts += 1
    if verify_password(row.code_hash, (code or "").strip()):
        row.used = True
        return True
    return False
