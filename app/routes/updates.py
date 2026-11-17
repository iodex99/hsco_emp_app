"""Update endpoints. Status is visible to every signed-in user; checking and installing need an admin."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import updater
from app.services.audit import audit
from app.web import Ctx, admin, viewer

router = APIRouter()


@router.get("/api/update/status")
def update_status(c: Ctx = Depends(viewer)):
    return JSONResponse(updater.status())


@router.post("/api/update/check")
def update_check(c: Ctx = Depends(admin)):
    st = updater.check()
    audit(c.db, c.user, "update.check", details={"latest": (st.get("latest") or {}).get("build"),
                                                 "error": st.get("check_error", "")}, ip=c.ip)
    return JSONResponse(st)


@router.post("/api/update/install")
def update_install(c: Ctx = Depends(admin)):
    try:
        st = updater.install(actor=c.user.username)
    except RuntimeError as ex:
        return JSONResponse({"error": str(ex)}, status_code=409)
    audit(c.db, c.user, "update.install", details={"to_build": (st.get("target") or {}).get("build"),
                                                   "version": (st.get("target") or {}).get("version")}, ip=c.ip)
    return JSONResponse(st)
