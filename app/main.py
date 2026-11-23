"""FastAPI application factory."""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import APP_DIR, get_config
from app.db import init_db
from app.web import templates

from app import __version__ as VERSION  # noqa: E402

CSP = ("default-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; form-action 'self'")
# frame-ancestors / X-Frame-Options 'self' (v1.13.1, was none / DENY): the full-report PDF lays out the app's own pages in a
# hidden frame; no other site may frame the app


class SecurityHeaders:
    """Pure ASGI middleware (NOT BaseHTTPMiddleware, which cancels the request task when the client disconnects
    and could leave a DB transaction open). Adds security headers and rejects oversized uploads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length", b"")
        if cl.isdigit() and int(cl) > get_config().max_upload_mb * 1024 * 1024:
            resp = JSONResponse({"detail": "Upload too large"}, status_code=413)
            return await resp(scope, receive, send)
        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers", []))
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"SAMEORIGIN"),
                      (b"referrer-policy", b"same-origin"),
                      (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
                      (b"content-security-policy", CSP.encode())]
                if get_config().secure_cookies:
                    h.append((b"strict-transport-security", b"max-age=31536000"))
                if path.startswith(("/periods", "/employees", "/exports", "/api", "/flags")):
                    h.append((b"cache-control", b"no-store"))
                message = {**message, "headers": h}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app() -> FastAPI:
    get_config()
    init_db()
    app = FastAPI(title="HSCO Attendance & Leave Intelligence", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    from app.routes import auth, employees, pages, periods, reports, settings, updates, users

    for r in (auth.router, pages.router, periods.router, employees.router, reports.router, settings.router, users.router,
              updates.router):
        app.include_router(r)

    from app import updater
    updater.start_background_checker()
    if not os.environ.get("HSCO_DISABLE_GEO_WARMUP"):
        from app.services import maintenance
        maintenance.start()

    app.add_middleware(SecurityHeaders)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (301, 302, 303, 307) and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/api") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"code": exc.status_code, "detail": exc.detail,
                                                                  "user": None, "org": "", "all_periods": []},
                                          status_code=exc.status_code)

    @app.get("/healthz")
    def healthz():
        from app.version import current
        return {"ok": True, **current()}

    return app
