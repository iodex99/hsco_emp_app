"""Login → second factor (TOTP / passkey / email code / recovery code) → session. Self-service security page."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import MfaMethod, User
from app.security import core as sec
from app.security import passkeys
from app.services.audit import audit
from app.services.settings import get_general
from app.web import Ctx, get_db, partial_ctx, redirect, render, templates, viewer

router = APIRouter()


def _set_cookie(resp, token: str):
    cfg = get_config()
    resp.set_cookie(sec.SESSION_COOKIE, token, httponly=True, secure=cfg.secure_cookies, samesite="strict",
                    max_age=int(cfg.session_hours * 3600))


def _methods(user: User) -> list[MfaMethod]:
    return [m for m in user.mfa_methods if m.confirmed]


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    g = get_general(db)
    first_run = db.scalar(select(User.id).limit(1)) is None
    return templates.TemplateResponse(request, "login.html", {"org": g["org_name"], "next": next, "error": "",
                                                              "first_run": first_run, "user": None, "all_periods": []})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    g = get_general(db)
    ip = request.client.host if request.client else ""
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    err = "Invalid username or password."
    if user and user.locked_until and user.locked_until > datetime.now():
        err = f"Account locked until {user.locked_until:%H:%M}. Contact an administrator."
        user = None
    elif user and user.is_active and sec.verify_password(user.password_hash, password):
        user.failed_attempts = 0
        user.locked_until = None
        token = sec.create_session(db, user, ip, request.headers.get("user-agent", ""))
        audit(db, user, "login.password_ok", ip=ip)
        safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
        resp = redirect(f"/mfa?next={safe_next}")
        _set_cookie(resp, token)
        return resp
    elif user:
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= int(g.get("lockout_threshold", 5)):
            user.locked_until = datetime.now() + timedelta(minutes=int(g.get("lockout_minutes", 15)))
            user.failed_attempts = 0
            audit(db, user, "login.locked", ip=ip)
        audit(db, user, "login.failed", ip=ip)
    db.commit()
    return templates.TemplateResponse(request, "login.html", {"org": g["org_name"], "next": next, "error": err,
                                                              "first_run": False, "user": None, "all_periods": []},
                                      status_code=401)


@router.post("/logout")
def logout(request: Request, c: Ctx = Depends(partial_ctx)):
    audit(c.db, c.user, "logout", ip=c.ip)
    sec.destroy_session(c.db, request.cookies.get(sec.SESSION_COOKIE))
    resp = redirect("/login", "Signed out.")
    resp.delete_cookie(sec.SESSION_COOKIE)
    return resp


# ------------------------------------------------------------------ second factor

@router.get("/mfa", response_class=HTMLResponse)
def mfa_page(next: str = "/", c: Ctx = Depends(partial_ctx)):
    if c.session.mfa_passed:
        return redirect(next or "/")
    g = get_general(c.db)
    methods = _methods(c.user)
    if not methods:
        if g.get("mfa_required", True):
            return redirect("/security/enrol")
        c.session.mfa_passed = True
        return redirect(next or "/")
    kinds = {m.kind for m in methods}
    return render(c, "mfa.html", kinds=kinds, next=next, error="",
                  recovery_left=sec.recovery_remaining(c.db, c.user), smtp=sec.smtp_configured(g))


def _pass(c: Ctx, how: str, next: str):
    c.session.mfa_passed = True
    c.user.last_login = datetime.now()
    audit(c.db, c.user, "login.mfa_ok", details={"method": how}, ip=c.ip)
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    return redirect(nxt)


def _fail(c: Ctx, how: str, next: str):
    audit(c.db, c.user, "login.mfa_failed", details={"method": how}, ip=c.ip)
    g = get_general(c.db)
    c.user.failed_attempts = (c.user.failed_attempts or 0) + 1
    if c.user.failed_attempts >= int(g.get("lockout_threshold", 5)):
        c.user.locked_until = datetime.now() + timedelta(minutes=int(g.get("lockout_minutes", 15)))
        c.user.failed_attempts = 0
        sec.destroy_user_sessions(c.db, c.user.id)
        resp = redirect("/login", "Too many failed codes — account temporarily locked.")
        resp.delete_cookie(sec.SESSION_COOKIE)
        return resp
    return redirect(f"/mfa?next={next}", "That code was not accepted. Try again.")


@router.post("/mfa/totp")
def mfa_totp(code: str = Form(...), next: str = Form("/"), c: Ctx = Depends(partial_ctx)):
    for m in _methods(c.user):
        if m.kind == "totp" and sec.verify_totp(m, code):
            m.last_used = datetime.now()
            c.user.failed_attempts = 0
            return _pass(c, f"totp:{m.label}", next)
    return _fail(c, "totp", next)


@router.post("/mfa/recovery")
def mfa_recovery(code: str = Form(...), next: str = Form("/"), c: Ctx = Depends(partial_ctx)):
    if sec.use_recovery_code(c.db, c.user, code):
        c.user.failed_attempts = 0
        return _pass(c, "recovery", next)
    return _fail(c, "recovery", next)


@router.post("/mfa/email/send")
def mfa_email_send(next: str = Form("/"), c: Ctx = Depends(partial_ctx)):
    g = get_general(c.db)
    if not any(m.kind == "email" for m in _methods(c.user)) or not sec.smtp_configured(g) or not c.user.email:
        return redirect(f"/mfa?next={next}", "Email codes are not available for this account.")
    try:
        sec.send_email_otp(c.db, c.user, g)
    except Exception as ex:  # noqa: BLE001
        return redirect(f"/mfa?next={next}", f"Could not send email: {ex}")
    return redirect(f"/mfa?next={next}&sent=1", f"A code was emailed to {c.user.email[:3]}***.")


@router.post("/mfa/email")
def mfa_email(code: str = Form(...), next: str = Form("/"), c: Ctx = Depends(partial_ctx)):
    if any(m.kind == "email" for m in _methods(c.user)) and sec.verify_email_otp(c.db, c.user, code):
        c.user.failed_attempts = 0
        return _pass(c, "email", next)
    return _fail(c, "email", next)


@router.post("/mfa/webauthn/options")
def mfa_webauthn_options(c: Ctx = Depends(partial_ctx)):
    return JSONResponse(json.loads(passkeys.authentication_options(c.db, c.session, c.user)))


@router.post("/mfa/webauthn/verify")
async def mfa_webauthn_verify(request: Request, c: Ctx = Depends(partial_ctx)):
    body = await request.json()
    ok = passkeys.finish_authentication(c.db, c.session, c.user, body.get("credential", {}))
    nxt = body.get("next") or "/"
    if ok:
        r = _pass(c, "webauthn", nxt)
        return JSONResponse({"ok": True, "redirect": r.headers["location"]})
    audit(c.db, c.user, "login.mfa_failed", details={"method": "webauthn"}, ip=c.ip)
    return JSONResponse({"ok": False, "error": "Security key verification failed"}, status_code=400)


# ------------------------------------------------------------------ enrolment & self-service security

def _security_ctx(c: Ctx, **kw):
    g = get_general(c.db)
    return dict(methods=_methods(c.user), recovery_left=sec.recovery_remaining(c.db, c.user),
                smtp=sec.smtp_configured(g), rp_id=get_config().rp_id, **kw)


@router.get("/security/enrol", response_class=HTMLResponse)
def enrol(c: Ctx = Depends(partial_ctx)):
    """Forced first-time 2FA enrolment (password verified, no factor yet)."""
    if _methods(c.user) and not c.session.mfa_passed:
        return redirect("/mfa")
    secret = (c.session.pending or {}).get("totp_secret")
    if not secret:
        secret = sec.new_totp_secret()
        c.session.pending = {**(c.session.pending or {}), "totp_secret": secret}
    g = get_general(c.db)
    uri = sec.totp_uri(secret, c.user.username, g["org_name"])
    return render(c, "enrol.html", secret=secret, qr=sec.qr_svg(uri), forced=not c.session.mfa_passed,
                  **_security_ctx(c))


@router.post("/security/totp/confirm")
def totp_confirm(code: str = Form(...), label: str = Form("Authenticator app"), c: Ctx = Depends(partial_ctx)):
    if _methods(c.user) and not c.session.mfa_passed:
        return redirect("/mfa")
    secret = (c.session.pending or {}).get("totp_secret")
    if not secret:
        return redirect("/security/enrol", "Enrolment expired — scan the new QR code.")
    m = MfaMethod(user_id=c.user.id, kind="totp", label=label.strip()[:60] or "Authenticator app",
                  secret=sec.encrypt(secret), confirmed=False)
    if not sec.verify_totp(m, code, secret=secret):
        return redirect("/security/enrol", "Code did not match. Check the phone's clock and try again.")
    m.confirmed = True
    c.db.add(m)
    p = dict(c.session.pending)
    p.pop("totp_secret", None)
    c.session.pending = p
    first = not c.session.mfa_passed
    audit(c.db, c.user, "mfa.add", "totp", details={"label": m.label}, ip=c.ip)
    if first:
        c.session.mfa_passed = True
        c.user.last_login = datetime.now()
        codes = sec.new_recovery_codes(c.db, c.user)
        c.db.flush()
        return render(c, "recovery_codes.html", codes=codes, first=True)
    return redirect("/security", "Authenticator app added.")


@router.get("/security", response_class=HTMLResponse)
def security_page(must_change: int = 0, c: Ctx = Depends(viewer)):
    return render(c, "security.html", must_change=must_change or c.user.must_change_password, **_security_ctx(c))


@router.post("/security/password")
def change_password(current: str = Form(...), new: str = Form(...), confirm: str = Form(...), c: Ctx = Depends(viewer)):
    g = get_general(c.db)
    if not sec.verify_password(c.user.password_hash, current):
        return redirect("/security", "Current password is incorrect.")
    if new != confirm:
        return redirect("/security", "New passwords do not match.")
    probs = sec.password_problems(new, int(g.get("password_min_length", 10)), c.user.username)
    if probs:
        return redirect("/security", "Password needs " + ", ".join(probs) + ".")
    c.user.password_hash = sec.hash_password(new)
    c.user.must_change_password = False
    sec.destroy_user_sessions(c.db, c.user.id, except_id=c.session.id)
    audit(c.db, c.user, "password.change", ip=c.ip)
    return redirect("/security", "Password changed. Other sessions were signed out.")


@router.post("/security/recovery/regenerate")
def regen_recovery(c: Ctx = Depends(viewer)):
    codes = sec.new_recovery_codes(c.db, c.user)
    audit(c.db, c.user, "mfa.recovery_regenerated", ip=c.ip)
    return render(c, "recovery_codes.html", codes=codes, first=False)


@router.post("/security/email/enable")
def email_enable(c: Ctx = Depends(viewer)):
    g = get_general(c.db)
    if not sec.smtp_configured(g) or not c.user.email:
        return redirect("/security", "Email codes need SMTP settings (admin) and an email on your account.")
    sec.send_email_otp(c.db, c.user, g)
    return redirect("/security?email_sent=1", f"Verification code sent to {c.user.email}.")


@router.post("/security/email/confirm")
def email_confirm(code: str = Form(...), c: Ctx = Depends(viewer)):
    if not sec.verify_email_otp(c.db, c.user, code):
        return redirect("/security", "Email code not accepted.")
    if not any(m.kind == "email" for m in c.user.mfa_methods):
        c.db.add(MfaMethod(user_id=c.user.id, kind="email", label=c.user.email, confirmed=True))
    audit(c.db, c.user, "mfa.add", "email", ip=c.ip)
    return redirect("/security", "Email sign-in codes enabled.")


@router.post("/security/webauthn/options")
def webauthn_reg_options(c: Ctx = Depends(viewer)):
    g = get_general(c.db)
    return JSONResponse(json.loads(passkeys.registration_options(c.db, c.session, c.user, g["org_name"])))


@router.post("/security/webauthn/register")
async def webauthn_register(request: Request, c: Ctx = Depends(viewer)):
    body = await request.json()
    try:
        m = passkeys.finish_registration(c.db, c.session, c.user, body.get("credential", {}),
                                         (body.get("label") or "Security key")[:60])
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)
    audit(c.db, c.user, "mfa.add", "webauthn", details={"label": m.label}, ip=c.ip)
    return JSONResponse({"ok": True})


@router.post("/security/method/{mid}/delete")
def delete_method(mid: int, c: Ctx = Depends(viewer)):
    m = c.db.get(MfaMethod, mid)
    if m is None or m.user_id != c.user.id:
        return redirect("/security", "Not found.")
    g = get_general(c.db)
    if g.get("mfa_required", True) and len(_methods(c.user)) <= 1:
        return redirect("/security", "You must keep at least one second-factor method.")
    c.db.delete(m)
    audit(c.db, c.user, "mfa.remove", m.kind, details={"label": m.label}, ip=c.ip)
    return redirect("/security", "Method removed.")
