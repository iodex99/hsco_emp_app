"""FastAPI application factory."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import APP_DIR, get_config
from app.db import init_db
from app.web import templates

VERSION = "1.0.0"


def create_app() -> FastAPI:
    get_config()
    init_db()
    app = FastAPI(title="HSCO Attendance & Leave Intelligence", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    from app.routes import auth, employees, pages, periods, reports, settings, users

    for r in (auth.router, pages.router, periods.router, employees.router, reports.router, settings.router, users.router):
        app.include_router(r)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > get_config().max_upload_mb * 1024 * 1024:
            return JSONResponse({"detail": "Upload too large"}, status_code=413)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        if get_config().secure_cookies:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(("/periods", "/employees", "/exports", "/api")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

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
        return {"ok": True, "version": VERSION}

    return app
