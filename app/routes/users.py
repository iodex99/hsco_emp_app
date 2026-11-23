"""User administration (admin only)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.models import User
from app.security import core as sec
from app.services.audit import audit
from app.web import ROLES, Ctx, admin, redirect, render

router = APIRouter()


def temp_password() -> str:
    return "Tmp-" + secrets.token_urlsafe(9) + "9!"


@router.get("/users", response_class=HTMLResponse)
def users(c: Ctx = Depends(admin)):
    rows = c.db.scalars(select(User).order_by(User.username)).all()
    return render(c, "users.html", rows=rows, roles=list(ROLES), new_password=None)


@router.post("/users", response_class=HTMLResponse)
def create_user(username: str = Form(...), full_name: str = Form(""), email: str = Form(""), role: str = Form("viewer"),
                c: Ctx = Depends(admin)):
    username = username.strip().lower()
    if not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        return redirect("/users", "Username may contain letters, numbers, dot, dash and underscore only.")
    if c.db.scalar(select(User).where(User.username == username)):
        return redirect("/users", "Username already exists.")
    if role not in ROLES:
        role = "viewer"
    pw = temp_password()
    u = User(username=username, full_name=full_name.strip(), email=email.strip(), role=role,
             password_hash=sec.hash_password(pw), must_change_password=True)
    c.db.add(u)
    audit(c.db, c.user, "user.create", "user", username, {"role": role}, c.ip)
    c.db.flush()
    rows = c.db.scalars(select(User).order_by(User.username)).all()
    return render(c, "users.html", rows=rows, roles=list(ROLES), new_password=(username, pw))


@router.post("/users/{uid}/update")
def update_user(uid: int, role: str = Form(...), is_active: str = Form(""), email: str = Form(""),
                full_name: str = Form(""), c: Ctx = Depends(admin)):
    u = c.db.get(User, uid)
    if not u:
        return redirect("/users", "Not found.")
    if u.id == c.user.id and (role != "admin" or not is_active):
        return redirect("/users", "You cannot demote or deactivate yourself.")
    u.role = role if role in ROLES else u.role
    u.is_active = bool(is_active)
    u.email, u.full_name = email.strip(), full_name.strip()
    if not u.is_active:
        sec.destroy_user_sessions(c.db, u.id)
    audit(c.db, c.user, "user.update", "user", u.username, {"role": u.role, "active": u.is_active}, c.ip)
    return redirect("/users", f"{u.username} updated.")


@router.post("/users/{uid}/reset-password", response_class=HTMLResponse)
def reset_password(uid: int, c: Ctx = Depends(admin)):
    u = c.db.get(User, uid)
    if not u:
        return redirect("/users", "Not found.")
    pw = temp_password()
    u.password_hash = sec.hash_password(pw)
    u.must_change_password = True
    u.locked_until = None
    u.failed_attempts = 0
    sec.destroy_user_sessions(c.db, u.id)
    audit(c.db, c.user, "user.reset_password", "user", u.username, ip=c.ip)
    rows = c.db.scalars(select(User).order_by(User.username)).all()
    return render(c, "users.html", rows=rows, roles=list(ROLES), new_password=(u.username, pw))


@router.post("/users/{uid}/unlock")
def unlock(uid: int, c: Ctx = Depends(admin)):
    u = c.db.get(User, uid)
    if u:
        u.locked_until = None
        u.failed_attempts = 0
        audit(c.db, c.user, "user.unlock", "user", u.username, ip=c.ip)
    return redirect("/users", "Account unlocked.")
