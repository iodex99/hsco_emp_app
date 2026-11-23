"""Sign-in (user name + password → session), sign-out and the self-service password page.

Two-step verification was removed at the owner's request (v1.10): the password is the only factor. Lockout after
repeated failures, strong-password rules and the forced change of a temporary password stay.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import User
from app.security import core as sec
from app.services.audit import audit
from app.services.settings import get_general
from app.web import Ctx, get_db, partial_ctx, redirect, render, templates, viewer

router = APIRouter()


def _set_cookie(resp, token: str):
    cfg = get_config()
    resp.set_cookie(sec.SESSION_COOKIE, token, httponly=True, secure=cfg.secure_cookies, samesite="strict",
                    max_age=int(cfg.session_hours * 3600))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db, scope="function")):
    g = get_general(db)
    first_run = db.scalar(select(User.id).limit(1)) is None
    return templates.TemplateResponse(request, "login.html", {"org": g["org_name"], "next": next, "error": "",
                                                              "first_run": first_run, "user": None, "all_periods": []})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db, scope="function")):
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
        user.last_login = datetime.now()
        token = sec.create_session(db, user, ip, request.headers.get("user-agent", ""))
        audit(db, user, "login.password_ok", ip=ip)
        safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
        resp = redirect("/security?must_change=1" if user.must_change_password else safe_next)
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


# ------------------------------------------------------------------ own password

@router.get("/security", response_class=HTMLResponse)
def security_page(must_change: int = 0, c: Ctx = Depends(viewer)):
    g = get_general(c.db)
    return render(c, "security.html", must_change=must_change or c.user.must_change_password,
                  min_len=int(g.get("password_min_length", 10)))


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
    was_temporary = c.user.must_change_password
    c.user.password_hash = sec.hash_password(new)
    c.user.must_change_password = False
    sec.destroy_user_sessions(c.db, c.user.id, except_id=c.session.id)
    audit(c.db, c.user, "password.change", ip=c.ip)
    if was_temporary:
        return redirect("/", "Password set — welcome.")
    return redirect("/security", "Password changed. Other sessions were signed out.")
