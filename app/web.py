"""Shared web plumbing: DB session per request, auth context, role guards, CSRF, template rendering."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import APP_DIR, get_config
from app.db import SessionLocal
from app.models import Period, User, UserSession
from app.security import core as sec
from app.services.settings import get_general

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
ROLES = {"viewer": 0, "hr": 1, "admin": 2}


def _fmt_num(v, nd=2):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if f.is_integer():
        return f"{int(f)}"
    return f"{f:.{nd}f}"


def _fmt_hm(minutes):
    if minutes in (None, ""):
        return ""
    m = int(round(float(minutes)))
    return f"{m // 60:02d}:{m % 60:02d}"


templates.env.filters["n"] = _fmt_num
templates.env.filters["hm"] = _fmt_hm
def _tojson_safe(v) -> Markup:
    """JSON for embedding inside <script>: neutralise '</' and '<!--' so data can never close the script tag."""
    return Markup(json.dumps(v, default=str).replace("</", "<\\/").replace("<!--", "<\\!--"))


templates.env.filters["tojson_safe"] = _tojson_safe
templates.env.filters["dmy"] = lambda d: d.strftime("%d-%b-%Y") if isinstance(d, (date, datetime)) else (d or "")
templates.env.globals["now"] = datetime.now


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@dataclass
class Ctx:
    request: Request
    db: Session
    session: Optional[UserSession]
    user: Optional[User]

    @property
    def ip(self) -> str:
        return self.request.client.host if self.request.client else ""

    def can(self, role: str) -> bool:
        return bool(self.user) and ROLES.get(self.user.role, -1) >= ROLES[role]


def _load(request: Request, db: Session) -> Ctx:
    token = request.cookies.get(sec.SESSION_COOKIE)
    s = sec.load_session(db, token)
    user = db.get(User, s.user_id) if s else None
    if user is not None and not user.is_active:
        sec.destroy_session(db, token)
        s, user = None, None
    return Ctx(request, db, s, user)


async def partial_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Password verified, MFA may still be pending. Unsafe methods are CSRF-checked."""
    c = _load(request, db)
    if c.user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        await verify_csrf(request, c)
    return c


def _require(role: str):
    async def dep(request: Request, db: Session = Depends(get_db)) -> Ctx:
        c = _load(request, db)
        if c.user is None:
            nxt = request.url.path if request.method == "GET" else "/"
            raise HTTPException(status_code=303, headers={"Location": f"/login?next={nxt}"})
        if not c.session.mfa_passed:
            raise HTTPException(status_code=303, headers={"Location": "/mfa"})
        if c.user.must_change_password and not request.url.path.startswith(("/security", "/logout")):
            raise HTTPException(status_code=303, headers={"Location": "/security?must_change=1"})
        if not c.can(role):
            raise HTTPException(status_code=403, detail="You do not have permission for this page.")
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            await verify_csrf(request, c)
        return c
    return dep


viewer = _require("viewer")
hr = _require("hr")
admin = _require("admin")


async def verify_csrf(request: Request, c: Ctx) -> None:
    token = request.headers.get("x-csrf-token")
    if not token and "form" in request.headers.get("content-type", ""):
        form = await request.form()
        token = form.get("csrf")
    if not c.session or not sec.check_csrf(c.session, token):
        raise HTTPException(status_code=400, detail="Security token expired — reload the page and try again.")


def periods(db: Session) -> list[Period]:
    return list(db.scalars(select(Period).order_by(Period.year.desc(), Period.month.desc())))


def render(c: Ctx, name: str, **kw) -> HTMLResponse:
    g = get_general(c.db)
    flash = c.request.cookies.get("flash")
    resp = templates.TemplateResponse(c.request, name, {
        "user": c.user, "csrf": c.session.csrf_token if c.session else "", "org": g.get("org_name"),
        "general": g, "can_hr": c.can("hr"), "can_admin": c.can("admin"), "all_periods": periods(c.db),
        "flash": flash, "path": c.request.url.path, **kw,
    })
    if flash:
        resp.delete_cookie("flash")
    return resp


def redirect(url: str, flash: str = "") -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if flash:
        r.set_cookie("flash", flash[:300], max_age=30, httponly=True, samesite="strict", secure=get_config().secure_cookies)
    return r
