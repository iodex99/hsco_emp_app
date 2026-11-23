"""Shared web plumbing: DB session per request, auth context, role guards, CSRF, template rendering."""
from __future__ import annotations

import json

import anyio
from urllib.parse import quote, unquote
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
    return f"{f:.{nd}f}".rstrip("0").rstrip(".")          # 0.5 · 34.5 · 2.25 (no trailing zeros)


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


def _plural(n, one: str, many: str = "") -> str:
    """'1 day' / '2 days' / '0.5 day' — {{ n|plural('day') }}."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return one
    return one if abs(f) <= 1 else (many or one + "s")


def _readable(v) -> str:
    """Audit details: lists joined, dicts as key: value, never unicode escapes."""
    if isinstance(v, list):
        return ", ".join(_readable(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_readable(x)}" for k, x in v.items())
    return "" if v is None else str(v)


templates.env.filters["plural"] = _plural
templates.env.filters["readable"] = _readable
templates.env.filters["tojson_safe"] = _tojson_safe
templates.env.filters["dmy"] = lambda d: d.strftime("%d-%b-%Y") if isinstance(d, (date, datetime)) else (d or "")
templates.env.globals["now"] = datetime.now


def _asset_version() -> str:
    """Fingerprint of the app's own CSS/JS/icons. Added to their URLs (?v=…) so a browser fetches the new files right
    after an update instead of reusing its cached copies (v1.6.4 — a cached app.js/app.css hid new features)."""
    import hashlib
    from app.config import APP_DIR
    h = hashlib.sha1()
    for n in ("app.css", "app.js", "table.js", "pdf.js", "icons.svg"):
        try:
            h.update((APP_DIR / "static" / n).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


ASSET_V = _asset_version()
templates.env.globals["asset_v"] = ASSET_V


async def get_db():
    """One DB session per request, committed before the response is sent (Depends(..., scope="function")).

    Cleanup is shielded from cancellation: if the client disconnects mid-request the task can be cancelled, and an
    un-ended SQLite write transaction would otherwise keep the database locked for every later request.
    """
    db = SessionLocal()
    try:
        yield db
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(db.commit)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(db.rollback)
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(db.close)


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


async def partial_ctx(request: Request, db: Session = Depends(get_db, scope="function")) -> Ctx:
    """Signed in, any role, even with a temporary password (sign-out). Unsafe methods are CSRF-checked."""
    c = await anyio.to_thread.run_sync(_load, request, db)   # never block the event loop on SQLite
    if c.user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        await verify_csrf(request, c)
    return c


def _require(role: str):
    async def dep(request: Request, db: Session = Depends(get_db, scope="function")) -> Ctx:
        c = await anyio.to_thread.run_sync(_load, request, db)   # never block the event loop on SQLite
        if c.user is None:
            nxt = request.url.path if request.method == "GET" else "/"
            raise HTTPException(status_code=303, headers={"Location": f"/login?next={nxt}"})
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


def current_period(c: Ctx, all_periods: list[Period], explicit=None) -> Period | None:
    """Period shown in the top bar: explicit page period → ?period= → /periods/<key> → latest computed."""
    if isinstance(explicit, Period):
        return explicit
    key = c.request.query_params.get("period")
    if not key and c.request.url.path.startswith("/periods/"):
        key = c.request.url.path.split("/")[2]
    for p in all_periods:
        if key and p.key == key:
            return p
    return next((p for p in all_periods if p.computed_at), all_periods[0] if all_periods else None)


def _update_status(c: Ctx):
    if c.user is None or not c.session:
        return None
    from app import updater
    try:
        return updater.status()
    except Exception:  # noqa: BLE001 - never break a page over update info
        return None


def render(c: Ctx, name: str, **kw) -> HTMLResponse:
    g = get_general(c.db)
    flash = unquote(c.request.cookies.get("flash", ""))
    all_p = periods(c.db)
    cur = current_period(c, all_p, kw.get("period"))
    nav_open = 0
    if cur is not None and c.user is not None and c.session:
        from app.services.workflow import actionable_flags
        nav_open = sum(1 for f in actionable_flags(c.db, cur) if f["actionable"] and not f["reviewed"])
    resp = templates.TemplateResponse(c.request, name, {
        "user": c.user, "csrf": c.session.csrf_token if c.session else "", "org": g.get("org_name"),
        "general": g, "can_hr": c.can("hr"), "can_admin": c.can("admin"), "all_periods": all_p,
        "cur_period": cur, "nav_open": nav_open, "upd": _update_status(c),
        "flash": flash, "path": c.request.url.path, **kw,
    })
    if flash:
        resp.delete_cookie("flash")
    return resp


def _brand() -> dict:
    from app.services.branding import info
    try:
        return info()
    except OSError:
        return {"logo": False, "v": 0, "kind": ""}


templates.env.globals["brand_info"] = _brand      # {% set B = brand_info() %} in any template (incl. sign-in pages)


def redirect(url: str, flash: str = "") -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if flash:
        r.set_cookie("flash", quote(flash[:300]), max_age=30, httponly=True, samesite="strict", secure=get_config().secure_cookies)
    return r
