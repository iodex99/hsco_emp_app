"""Admin settings: policy rules, status codes, shifts, general/security/SMTP, holiday calendars, geofences."""
from __future__ import annotations

from datetime import date

import anyio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app.engine.model import BUCKETS
from app.models import Geofence, Holiday, HolidayCalendar, Period, SwipeRow
from app.services import branding
from app.services.audit import audit
from app.services.compute import compute_period
from app.services.geo import rename_swipes, unnamed_clusters
from app.services.settings import get_general, get_policy_json, save_general, save_policy
from app.web import Ctx, admin, hr, redirect, render

router = APIRouter()

POLICY_NUMBERS = [
    ("hours_per_day", "Required hours per working day", "8.5"),
    ("shortfall_block_hours", "Shortfall block (hours)", "4"),
    ("deduction_per_block_days", "Leave deducted per block (days)", "0.5"),
    ("shortfall_grace_hours", "Shortfall grace (hours, 0 = none)", "0"),
    ("comp_off_min_hours", "Min hours on off-day to earn comp-off", "5"),
    ("wfh_limit_days", "WFH days allowed per month", "2"),
    ("late_grace_min", "Late grace after shift start (min)", "15"),
    ("very_late_min", "Very late threshold (min)", "60"),
    ("early_exit_grace_min", "Early-exit grace before shift end (min)", "15"),
    ("persistent_late_days", "Persistent late: days per month", "10"),
    ("persistent_regularisation_days", "Persistent regularisation: days per month", "3"),
    ("min_daily_hours", "Encouraged minimum hours per day", "7"),
    ("annual_leave_days", "Paid leave per financial year", "15"),
    ("fy_start_month", "Financial year start month (1-12)", "4"),
]


def _recompute_drafts(c: Ctx):
    for p in c.db.scalars(select(Period).where(Period.status == "DRAFT", Period.computed_at.is_not(None))
                          .order_by(Period.year, Period.month)):
        compute_period(c.db, p, cascade=False)
        c.db.commit()          # one month per transaction: never hold the write lock for the whole cascade


@router.get("/settings", response_class=HTMLResponse)
def settings_page(c: Ctx = Depends(hr)):
    cals = c.db.scalars(select(HolidayCalendar).order_by(HolidayCalendar.name)).all()
    fences = c.db.scalars(select(Geofence).order_by(Geofence.name)).all()
    from app.engine.compute import parse_shift
    from app.models import MusterRow
    from app.services.settings import get_policy
    pol = get_policy(c.db)
    shift_names = [(x, *parse_shift(x, pol)) for x in sorted(c.db.scalars(select(MusterRow.shift).distinct())) if x]
    return render(c, "settings.html", pol=get_policy_json(c.db), g=get_general(c.db), numbers=POLICY_NUMBERS,
                  buckets=BUCKETS, cals=cals, fences=fences, clusters=unnamed_clusters(c.db), shift_names=shift_names)


@router.post("/settings/policy")
async def save_policy_route(request: Request, c: Ctx = Depends(admin)):
    form = await request.form()
    return await anyio.to_thread.run_sync(_save_policy, c, form)


def _save_policy(c: Ctx, form):
    data = {}
    for key, _, _ in POLICY_NUMBERS:
        v = form.get(key, "").strip()
        if v:
            try:
                data[key] = float(v)
            except ValueError:
                return redirect("/settings#policy", f"'{key}' must be a number.")
    for key in ("exempt_groups", "metrics_exclude_groups", "wfh_location_types"):
        data[key] = [x.strip() for x in form.get(key, "").split(",") if x.strip()]
    data["wfh_location_types"] = [x.lower() for x in data["wfh_location_types"]]
    data["lop_bucket"] = form.get("lop_bucket", "leave")
    data["wfh_method"] = form.get("wfh_method", "distinct_days")
    data["persistent_late_basis"] = form.get("persistent_late_basis", "very_late")
    data["leave_credit_mode"] = form.get("leave_credit_mode", "annual")
    data["absent_debits_leave"] = bool(form.get("absent_debits_leave"))
    data["cof_overdraft_to_leave"] = bool(form.get("cof_overdraft_to_leave"))
    data["late_sitting_after"] = form.get("late_sitting_after", "21:00")
    data["default_shift_start"] = form.get("default_shift_start", "10:00")
    data["default_shift_end"] = form.get("default_shift_end", "18:30")
    # status map lines "CODE=bucket"
    sm = {}
    for line in form.get("status_map", "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if v.strip() in BUCKETS and k.strip():
                sm[k.strip().upper()] = v.strip()
    if sm:
        data["status_map"] = sm
    # shift overrides lines "Shift name=HH:MM-HH:MM"
    so = {}
    for line in form.get("shift_overrides", "").splitlines():
        if "=" in line and "-" in line.split("=", 1)[1]:
            k, v = line.split("=", 1)
            a, b = v.strip().split("-", 1)
            so[k.strip()] = [a.strip(), b.strip()]
    data["shift_overrides"] = so
    for k in ("fy_start_month", "late_grace_min", "very_late_min", "early_exit_grace_min", "persistent_late_days",
              "persistent_regularisation_days"):
        if k in data:
            data[k] = int(data[k])
    try:
        save_policy(c.db, data)
    except Exception as ex:  # noqa: BLE001
        return redirect("/settings#policy", f"Could not save: {ex}")
    audit(c.db, c.user, "settings.policy", details=data, ip=c.ip)
    _recompute_drafts(c)
    return redirect("/settings#policy", "Policy saved; draft months recomputed.")


@router.post("/settings/general")
async def save_general_route(request: Request, c: Ctx = Depends(admin)):
    form = await request.form()
    return await anyio.to_thread.run_sync(_save_general, c, form)


def _save_general(c: Ctx, form):
    g = get_general(c.db)
    data = {
        "org_name": form.get("org_name", g["org_name"]).strip() or g["org_name"],
        "org_address": form.get("org_address", "").strip(),
        "mfa_required": bool(form.get("mfa_required")),
        "password_min_length": max(8, int(form.get("password_min_length") or 10)),
        "lockout_threshold": max(3, int(form.get("lockout_threshold") or 5)),
        "lockout_minutes": max(1, int(form.get("lockout_minutes") or 15)),
        "smtp_host": form.get("smtp_host", "").strip(), "smtp_port": int(form.get("smtp_port") or 587),
        "smtp_user": form.get("smtp_user", "").strip(), "smtp_from": form.get("smtp_from", "").strip(),
        "smtp_starttls": bool(form.get("smtp_starttls")),
        "online_geocoding": bool(form.get("online_geocoding")),
        "geocode_max_km": float(form.get("geocode_max_km") or 15),
        "groups": [x.strip() for x in form.get("groups", "").split(",") if x.strip()],
        "locations": [x.strip() for x in form.get("locations", "").split(",") if x.strip()],
    }
    if form.get("smtp_password"):
        data["smtp_password"] = form.get("smtp_password")
    save_general(c.db, data)
    audit(c.db, c.user, "settings.general", details=data, ip=c.ip)
    return redirect("/settings#general", "Settings saved.")


# ---------------------------------------------------------------- holiday calendars

@router.post("/settings/calendars")
def add_calendar(name: str = Form(...), is_default: str = Form(""), c: Ctx = Depends(hr)):
    name = name.strip()
    if not name or c.db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == name)):
        return redirect("/settings#holidays", "Calendar name missing or already exists.")
    if is_default:
        for cal in c.db.scalars(select(HolidayCalendar)):
            cal.is_default = False
    c.db.add(HolidayCalendar(name=name, is_default=bool(is_default)))
    audit(c.db, c.user, "calendar.add", "calendar", name, ip=c.ip)
    _recompute_drafts(c)
    return redirect("/settings#holidays", f"Calendar '{name}' added.")


@router.post("/settings/calendars/{cid}/default")
def default_calendar(cid: int, c: Ctx = Depends(hr)):
    for cal in c.db.scalars(select(HolidayCalendar)):
        cal.is_default = cal.id == cid
    audit(c.db, c.user, "calendar.default", "calendar", cid, ip=c.ip)
    c.db.flush()
    _recompute_drafts(c)
    return redirect("/settings#holidays", "Default calendar changed.")


@router.post("/settings/calendars/{cid}/delete")
def delete_calendar(cid: int, c: Ctx = Depends(hr)):
    cal = c.db.get(HolidayCalendar, cid)
    if cal:
        audit(c.db, c.user, "calendar.delete", "calendar", cal.name, ip=c.ip)
        c.db.delete(cal)
        c.db.flush()
        _recompute_drafts(c)
    return redirect("/settings#holidays", "Calendar deleted.")


@router.post("/settings/calendars/{cid}/holiday")
def add_holiday(cid: int, day: str = Form(...), name: str = Form(""), c: Ctx = Depends(hr)):
    cal = c.db.get(HolidayCalendar, cid)
    if not cal:
        return redirect("/settings#holidays", "Calendar not found.")
    d = date.fromisoformat(day)
    if c.db.scalar(select(Holiday).where(Holiday.calendar_id == cid, Holiday.day == d)) is None:
        c.db.add(Holiday(calendar_id=cid, day=d, name=name.strip()))
    audit(c.db, c.user, "holiday.add", "calendar", cal.name, {"day": day, "name": name}, c.ip)
    c.db.flush()
    _recompute_drafts(c)
    return redirect("/settings#holidays", f"Holiday {d:%d-%b-%Y} added to {cal.name}.")


@router.post("/settings/holidays/{hid}/delete")
def delete_holiday(hid: int, c: Ctx = Depends(hr)):
    h = c.db.get(Holiday, hid)
    if h:
        audit(c.db, c.user, "holiday.delete", "calendar", h.calendar_id, {"day": h.day.isoformat()}, c.ip)
        c.db.delete(h)
        c.db.flush()
        _recompute_drafts(c)
    return redirect("/settings#holidays", "Holiday removed.")


# ---------------------------------------------------------------- geofences

@router.post("/settings/geofences")
def add_geofence(name: str = Form(...), category: str = Form("Client"), lat: float = Form(...), lon: float = Form(...),
                 radius_m: int = Form(200), c: Ctx = Depends(hr)):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return redirect("/settings#locations", "Invalid coordinates.")
    c.db.add(Geofence(name=name.strip(), category=category, lat=lat, lon=lon, radius_m=max(25, radius_m)))
    audit(c.db, c.user, "geofence.add", "geofence", name, {"lat": lat, "lon": lon, "radius": radius_m}, c.ip)
    c.db.flush()
    _rename_swipes(c)
    return redirect("/settings#locations", f"Location '{name}' saved and swipes renamed.")


@router.post("/settings/geofences/{gid}/delete")
def delete_geofence(gid: int, c: Ctx = Depends(hr)):
    g = c.db.get(Geofence, gid)
    if g:
        audit(c.db, c.user, "geofence.delete", "geofence", g.name, ip=c.ip)
        c.db.delete(g)
        c.db.flush()
        _rename_swipes(c)
    return redirect("/settings#locations", "Location removed.")


def _rename_swipes(c: Ctx):
    for pid in rename_swipes(c.db):
        p = c.db.get(Period, pid)
        if p and p.computed_at:
            compute_period(c.db, p, cascade=False)
            c.db.commit()


@router.post("/settings/updates")
def save_updates(update_check: str = Form(""), update_interval_min: str = Form("10"), update_url: str = Form(""),
                 c: Ctx = Depends(admin)):
    url = update_url.strip()
    if url and not url.startswith("https://"):
        return redirect("/settings#updates", "The release source must be an https:// URL.")
    try:
        interval = max(1, min(1440, int(update_interval_min or 10)))
    except ValueError:
        interval = 10
    data = {"update_check": bool(update_check), "update_interval_min": interval, "update_url": url}
    save_general(c.db, data)
    audit(c.db, c.user, "settings.updates", details=data, ip=c.ip)
    return redirect("/settings#updates", "Update settings saved.")



# ----------------------------------------------------------------------------- branding (firm logo)
_IMG_HEADERS = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}


@router.get("/branding/logo")
def branding_logo():
    """Public (the sign-in page shows it before anyone is signed in); the file holds no data."""
    lg = branding.logo()
    if not lg:
        return Response(status_code=404)
    return Response(lg[0], media_type=lg[1], headers=_IMG_HEADERS)


@router.get("/branding/favicon.svg")
def branding_favicon():
    fav = branding.favicon_svg()
    if not fav:
        return Response(status_code=404)
    return Response(fav, media_type="image/svg+xml", headers=_IMG_HEADERS)


@router.post("/settings/branding")
async def upload_logo(file: UploadFile = File(...), c: Ctx = Depends(admin)):
    data = await file.read(branding.MAX_BYTES + 1)
    name = file.filename or ""

    def run():
        try:
            kind = branding.save(data, name)
        except ValueError as ex:
            return redirect("/settings#general", f"Logo not saved: {ex}.")
        audit(c.db, c.user, "branding.logo", "settings", name, {"type": kind, "bytes": len(data)}, c.ip)
        return redirect("/settings#general", "Logo saved — it now appears in the app, on the sign-in page and in the browser tab.")
    return await anyio.to_thread.run_sync(run)


@router.post("/settings/branding/delete")
def delete_logo(c: Ctx = Depends(admin)):
    if branding.remove():
        audit(c.db, c.user, "branding.logo_removed", "settings", "", ip=c.ip)
    return redirect("/settings#general", "Logo removed.")
