"""Admin settings: policy rules, status codes, shifts, general/security, holiday calendars, logo, backups."""
from __future__ import annotations

import os
from datetime import date

import anyio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app.engine.model import BUCKETS
from app.engine.model import PATTERNS, WEEKDAY_NAMES
from app.models import Category, Employee, Holiday, HolidayCalendar, MonthResult, Period, SwipeRow
from app.services import categories as CAT
from app.services import holidays as TH
from app.services import branding
from app.services.audit import audit
from app.services.compute import compute_period
from app.services.settings import get_general, get_policy, get_policy_json, save_general, save_policy
from app.web import Ctx, admin, hr, redirect, render

router = APIRouter()

POLICY_NUMBERS = [
    ("hours_per_day", "Required hours per working day", "8.5"),
    ("shortfall_block_hours", "Shortfall block (hours)", "4"),
    ("deduction_per_block_days", "Leave deducted per block (days)", "0.5"),
    ("shortfall_grace_hours", "Shortfall grace (hours, 0 = none)", "0"),
    ("comp_off_min_hours", "Min hours on off-day to earn comp-off", "5"),
    ("wfh_limit_days", "WFH days allowed per month", "2"),
    ("wfh_excess_deduction_days", "Leave deducted per WFH day over the limit (0 = flag only)", "0.5"),
    ("late_grace_min", "Late grace after shift start (min)", "15"),
    ("very_late_min", "Very late threshold (min)", "60"),
    ("early_exit_grace_min", "Early-exit grace before shift end (min)", "15"),
    ("persistent_late_days", "Persistent late: days per month", "10"),
    ("persistent_regularisation_days", "Persistent regularisation: days per month", "3"),
    ("min_daily_hours", "Encouraged minimum hours per day", "7"),
    ("late_sitting_extra_min", "Late sitting: worked more than required by (min)", "60"),
    ("annual_leave_days", "Paid leave per financial year (Details: leave used)", "15"),
    ("fy_start_month", "Financial year start month (1-12)", "4"),
]


def _recompute_drafts(c: Ctx):
    for p in c.db.scalars(select(Period).where(Period.status == "DRAFT", Period.computed_at.is_not(None))
                          .order_by(Period.year, Period.month)):
        compute_period(c.db, p)
        c.db.commit()          # one month per transaction: never hold the write lock for all of them


@router.get("/settings", response_class=HTMLResponse)
def settings_page(c: Ctx = Depends(hr)):
    cals = c.db.scalars(select(HolidayCalendar).order_by(HolidayCalendar.name)).all()
    from app.engine.compute import parse_shift
    from app.models import MusterRow
    from app.services.settings import get_policy
    pol = get_policy(c.db)
    shift_names = [(x, *parse_shift(x, pol)) for x in sorted(c.db.scalars(select(MusterRow.shift).distinct())) if x]
    from app.services import backups as BK
    # categories: settings, people, gaps, and days where a category's list and greytHR disagree
    cat_rows = CAT.overview(c.db)
    cal_use = {}
    for r in cat_rows:
        if r["c"].holiday_calendar_id:
            cal_use.setdefault(r["c"].holiday_calendar_id, []).append(r["c"].name)
    latest = c.db.scalar(select(Period).where(Period.computed_at.is_not(None), Period.status == "DRAFT")
                         .order_by(Period.year.desc(), Period.month.desc()))
    conflicts = CAT.calendar_conflicts(c.db, c.db.scalars(select(MonthResult).where(MonthResult.period_id == latest.id))
                                       ) if latest else []
    this_year = date.today().year
    from app.services import datadir as DD, geo as GEO
    return render(c, "settings.html", backups=BK.list_backups()[:12], bk=BK.summary(),
                  dd=DD.info() if c.can("admin") else None,
                  geo_ok=GEO.SRC.exists(),
                  pol=get_policy_json(c.db), g=get_general(c.db), numbers=POLICY_NUMBERS,
                  buckets=BUCKETS, cals=cals, shift_names=shift_names,
                  cat_rows=cat_rows, cal_use=cal_use, conflicts=conflicts, conflict_period=latest,
                  uncategorised=CAT.uncategorised(c.db), patterns=PATTERNS, weekday_names=WEEKDAY_NAMES,
                  this_year=this_year, th_categories=[r["c"].name for r in cat_rows], **_hours_ctx(c))


def _hours_ctx(c: Ctx) -> dict:
    """Settings → Minimum hours: the policies, who is in each, and everyone who can be added."""
    from app.services import hours_policies as HP
    from app.services.scope import employees as scoped
    items = HP.all_policies(c.db)
    asg = HP.assignments(c.db)
    people = [{"emp_no": e.emp_no, "name": e.name or e.emp_no, "group": e.group or "", "policy": asg.get(e.emp_no, ("",))[0],
               "own": e.hours_per_day} for e in scoped(c.db, order=Employee.name) if not e.dol]
    names = {p["emp_no"]: p for p in people}
    for x in items:
        x["members"] = [names.get(e) or {"emp_no": e, "name": e, "group": "", "own": None} for e in x.get("emp_nos") or []]
    return {"hp_items": items, "hp_people": people, "hp_groups": sorted({p["group"] for p in people if p["group"]}),
            "hp_firm": get_policy(c.db).hours_per_day}


def _hours_changed(c: Ctx, action: str, details: dict, msg: str):
    audit(c.db, c.user, action, "hours_policy", str(details.get("id", "")), details, c.ip)
    c.db.commit()
    _recompute_drafts(c)
    return redirect("/settings#hours", msg + " Draft months recalculated.")


@router.post("/settings/hours-policies/new")
def hours_policy_new(name: str = Form(""), hours: str = Form(""), c: Ctx = Depends(hr)):
    from app.services import hours_policies as HP
    try:
        x = HP.create(c.db, name, hours)
    except ValueError as ex:
        return redirect("/settings#hours", str(ex))
    return _hours_changed(c, "settings.hours_policy.create", {"id": x["id"], "name": x["name"], "hours": x["hours"]},
                          f"Policy “{x['name']}” ({x['hours']:g} h a day) created — now add the employees it applies to.")


@router.post("/settings/hours-policies/{pid}/save")
def hours_policy_save(pid: int, name: str = Form(""), hours: str = Form(""), c: Ctx = Depends(hr)):
    from app.services import hours_policies as HP
    old = HP.get(c.db, pid)
    try:
        x = HP.update(c.db, pid, name, hours)
    except ValueError as ex:
        return redirect("/settings#hours", str(ex))
    return _hours_changed(c, "settings.hours_policy.update",
                          {"id": pid, "name": [old["name"] if old else "", x["name"]], "hours": [old["hours"] if old else "", x["hours"]]},
                          f"Policy “{x['name']}” saved ({x['hours']:g} h a day).")


@router.post("/settings/hours-policies/{pid}/delete")
def hours_policy_delete(pid: int, c: Ctx = Depends(hr)):
    from app.services import hours_policies as HP
    try:
        x = HP.delete(c.db, pid)
    except ValueError as ex:
        return redirect("/settings#hours", str(ex))
    return _hours_changed(c, "settings.hours_policy.delete", {"id": pid, "name": x["name"], "employees": x.get("emp_nos")},
                          f"Policy “{x['name']}” deleted — its {len(x.get('emp_nos') or [])} employee(s) follow their own / category / firm hours again.")


@router.post("/settings/hours-policies/{pid}/members")
def hours_policy_members(pid: int, emp: list[str] = Form([]), c: Ctx = Depends(hr)):
    from app.services import hours_policies as HP
    if not emp:
        return redirect("/settings#hours", "Tick the employees to add first.")
    try:
        r = HP.assign(c.db, pid, emp)
    except ValueError as ex:
        return redirect("/settings#hours", str(ex))
    x = HP.get(c.db, pid)
    moved = f" ({len(r['moved'])} moved from another policy)" if r["moved"] else ""
    return _hours_changed(c, "settings.hours_policy.assign", {"id": pid, "name": x["name"], "added": emp, "moved_from": r["moved"]},
                          f"{r['added']} employee(s) added to “{x['name']}”{moved}.")


@router.post("/settings/hours-policies/{pid}/remove")
def hours_policy_remove(pid: int, emp_no: str = Form(""), c: Ctx = Depends(hr)):
    from app.services import hours_policies as HP
    x = HP.get(c.db, pid)
    if x is None:
        return redirect("/settings#hours", "That policy no longer exists.")
    HP.remove(c.db, pid, emp_no.strip().upper())
    return _hours_changed(c, "settings.hours_policy.remove", {"id": pid, "name": x["name"], "removed": emp_no},
                          f"{emp_no} removed from “{x['name']}”.")


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
    data["office_open_saturdays"] = sorted({int(x) for x in form.get("office_open_saturdays", "").replace(" ", "").split(",")
                                            if x.isdigit() and 1 <= int(x) <= 5})
    for key in ("exempt_groups", "metrics_exclude_groups", "wfh_location_types", "wfh_deduction_exempt_groups",
                "ignored_groups"):
        data[key] = [x.strip() for x in form.get(key, "").split(",") if x.strip()]
    data["wfh_location_types"] = [x.lower() for x in data["wfh_location_types"]]
    data["wfh_method"] = form.get("wfh_method", "distinct_days")
    data["persistent_late_basis"] = form.get("persistent_late_basis", "very_late")
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
        "password_min_length": max(8, int(form.get("password_min_length") or 10)),
        "lockout_threshold": max(3, int(form.get("lockout_threshold") or 5)),
        "lockout_minutes": max(1, int(form.get("lockout_minutes") or 15)),
        "online_geocoding": bool(form.get("online_geocoding")),
        "geocode_max_km": float(form.get("geocode_max_km") or 15),
        "groups": [x.strip() for x in form.get("groups", "").split(",") if x.strip()],
        "locations": [x.strip() for x in form.get("locations", "").split(",") if x.strip()],
    }
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
def add_holiday(cid: int, day: str = Form(...), name: str = Form(""), optional: str = Form(""),
                c: Ctx = Depends(hr)):
    cal = c.db.get(HolidayCalendar, cid)
    if not cal:
        return redirect("/settings#holidays", "Calendar not found.")
    d = date.fromisoformat(day)
    h = c.db.scalar(select(Holiday).where(Holiday.calendar_id == cid, Holiday.day == d))
    if h is None:
        c.db.add(Holiday(calendar_id=cid, day=d, name=name.strip(), optional=bool(optional)))
    else:
        h.name, h.optional = name.strip() or h.name, bool(optional)
    audit(c.db, c.user, "holiday.add", "calendar", cal.name, {"day": day, "name": name, "optional": bool(optional)}, c.ip)
    c.db.flush()
    _recompute_drafts(c)
    return redirect("/settings#holidays", f"Holiday {d:%d-%b-%Y} added to {cal.name}.")


@router.post("/settings/calendars/{cid}/paste")
def paste_holidays(cid: int, text: str = Form(""), c: Ctx = Depends(hr)):
    """Holidays pasted as text (e.g. copied from a PDF). A full year (≥ 5 holidays) replaces that year of the calendar,
    a shorter paste is added — same rule as a holiday-list upload."""
    cal = c.db.get(HolidayCalendar, cid)
    if not cal:
        return redirect("/settings#holidays", "Calendar not found.")
    from app.services.imports import _apply_holiday_lists, _check_weekdays
    rows, skipped = TH.parse_pasted(text)
    if not rows:
        return redirect("/settings#holidays", "No dates found in the pasted text — one holiday per line, with its date.")
    warnings: list = []
    _check_weekdays(rows, warnings)
    plan = [{**r, "target": ("calendar_id", cal.id), "sheet": "pasted"} for r in rows]
    lines = _apply_holiday_lists(c.db, plan, {})
    audit(c.db, c.user, "holiday.paste", "calendar", cal.name, {"rows": len(rows), "summary": lines,
                                                                "skipped": skipped[:10]}, c.ip)
    c.db.flush()
    _recompute_drafts(c)
    msg = " ".join(lines + warnings)
    if skipped:
        msg += f" Lines without a date were skipped: {'; '.join(skipped[:3])}{' …' if len(skipped) > 3 else ''}"
    return redirect("/settings#holidays", msg)


# ---------------------------------------------------------------- categories (v1.7)
@router.post("/settings/categories/save")
def save_category(cid: str = Form(""), name: str = Form(...), holiday_calendar_id: str = Form(""),
                  weekly_off: str = Form(""), off_weekdays: list[str] = Form([]), working_saturdays: str = Form(""),
                  hours_per_day: str = Form(""), notes: str = Form(""), c: Ctx = Depends(hr)):
    name = " ".join(name.split())[:64]
    if not CAT.key(name):
        return redirect("/settings#categories", "Enter a category name.")
    cat = c.db.get(Category, int(cid)) if cid.strip().isdigit() else None
    clash = CAT.find(c.db, name)
    if clash is not None and (cat is None or clash.id != cat.id):
        return redirect("/settings#categories", f"A category like “{name}” already exists: {clash.name}.")
    try:
        hpd = float(hours_per_day) if hours_per_day.strip() else None
    except ValueError:
        return redirect("/settings#categories", "Hours per day must be a number.")
    new = cat is None
    if new:
        cat = Category(name=name)
        c.db.add(cat)
        c.db.flush()
    before = {k: str(getattr(cat, k)) for k in ("name", "holiday_calendar_id", "weekly_off", "off_weekdays",
                                                "working_saturdays", "hours_per_day")}
    old_name = cat.name
    if holiday_calendar_id == "new":
        cal = c.db.scalar(select(HolidayCalendar).where(HolidayCalendar.name == name))
        if cal is None:
            cal = HolidayCalendar(name=name, is_default=False)
            c.db.add(cal)
            c.db.flush()
        cat.holiday_calendar_id = cal.id
    else:
        cat.holiday_calendar_id = int(holiday_calendar_id) if holiday_calendar_id.strip().isdigit() else None
    cat.name = name
    cat.weekly_off = weekly_off if weekly_off in PATTERNS else ""
    cat.off_weekdays = ",".join(sorted({x for x in off_weekdays if x.isdigit() and 0 <= int(x) <= 6})) or "5,6"
    cat.working_saturdays = ",".join(x for x in working_saturdays.replace(" ", "").split(",") if x and x in "12345")
    cat.hours_per_day = hpd
    cat.notes = notes.strip()
    if old_name != name:              # rename: employees keep their category
        for e in c.db.scalars(select(Employee).where((Employee.category == old_name) |
                                                     (Employee.additional_category == old_name))):
            if e.category == old_name:
                e.category = name
            if e.additional_category == old_name:
                e.additional_category = name
    after = {k: str(getattr(cat, k)) for k in before}
    audit(c.db, c.user, "category.create" if new else "category.update", "category", name,
          {k: [before[k], after[k]] for k in before if before[k] != after[k]}, c.ip)
    c.db.flush()
    _recompute_drafts(c)
    return redirect("/settings#categories", f"Category “{name}” saved; draft months recalculated.")


@router.post("/settings/categories/{cid}/delete")
def delete_category(cid: int, c: Ctx = Depends(hr)):
    cat = c.db.get(Category, cid)
    if cat is None:
        return redirect("/settings#categories", "Category not found.")
    used = c.db.scalar(select(Employee.emp_no).where((Employee.category == cat.name) |
                                                    (Employee.additional_category == cat.name)).limit(1))
    if used:
        return redirect("/settings#categories", f"“{cat.name}” still has employees — move them to another category first.")
    audit(c.db, c.user, "category.delete", "category", cat.name, {}, c.ip)
    c.db.delete(cat)
    c.db.flush()
    return redirect("/settings#categories", f"Category “{cat.name}” deleted (its holiday calendar is kept).")


@router.post("/settings/holidays/{hid}/delete")
def delete_holiday(hid: int, c: Ctx = Depends(hr)):
    h = c.db.get(Holiday, hid)
    if h:
        audit(c.db, c.user, "holiday.delete", "calendar", h.calendar_id, {"day": h.day.isoformat()}, c.ip)
        c.db.delete(h)
        c.db.flush()
        _recompute_drafts(c)
    return redirect("/settings#holidays", "Holiday removed.")


def _recompute_month_of(c: Ctx, d: date) -> str:
    """Recalculate the month containing d (months are independent). Returns a note for the flash message when that
    month is finalised (its figures don't change)."""
    from app.services.compute import get_period
    from app.services.imports import recompute_from
    p = get_period(c.db, d.year, d.month)
    if p is None or p.computed_at is None:
        return ""
    if p.status != "DRAFT":
        return f" {p.label} is locked, so its figures don't change — unlock it to apply the holiday."
    c.db.commit()
    recompute_from(c.db, {p.id}, commit_each=True)
    return ""


@router.post("/settings/holidays/categories")
def add_category_holiday(day: str = Form(...), name: str = Form(""), optional: str = Form(""),
                         categories: list[str] = Form([]), c: Ctx = Depends(hr)):
    """One holiday for several categories at once (owner, v1.9: holidays are targeted only by category): it is added
    to each chosen category's own list — the same as a holiday-list row with those categories."""
    from app.services.imports import _apply_holiday_lists
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return redirect("/settings#holidays", "Enter a valid date.")
    cats = TH.split(categories)
    if not cats:
        return redirect("/settings#holidays", "Tick the categories that get this holiday.")
    nm = name.strip()[:128] or "Holiday"
    # a list with no holiday yet in that year counts as loaded once this one is in — say so (greytHR's own
    # holiday marks stop being used for those people for that year)
    new_year = []
    for cn in cats:
        cat = CAT.find(c.db, cn, CAT.by_key(c.db))
        cal_id = cat.holiday_calendar_id if cat else None
        if not cal_id or not c.db.scalar(select(Holiday.id).where(
                Holiday.calendar_id == cal_id, Holiday.day >= date(d.year, 1, 1), Holiday.day <= date(d.year, 12, 31))
                .limit(1)):
            new_year.append(cn)
    emp_cache = {e.emp_no: e for e in c.db.scalars(select(Employee))}
    plan = [{"date": d, "name": nm, "optional": bool(optional), "target": ("category", cn)} for cn in cats]
    lines = _apply_holiday_lists(c.db, plan, emp_cache)
    audit(c.db, c.user, "holiday.add", "holiday", d.isoformat(),
          {"name": nm, "optional": bool(optional), "categories": cats}, c.ip)
    c.db.flush()
    note = _recompute_month_of(c, d)
    warn = (f" Note: {', '.join(new_year)} had no {d.year} holidays yet — upload their {d.year} list, otherwise only "
            f"this holiday counts for them in {d.year}." if new_year else "")
    return redirect("/settings#holidays", f"{nm} ({d:%d-%b-%Y}) added to {len(cats)} "
                    f"{'list' if len(cats) == 1 else 'lists'}: {', '.join(cats)}.{warn}{note}"
                    if lines else "Nothing added.")


@router.post("/settings/data-folder")
def move_data_folder(target: str = Form(""), c: Ctx = Depends(admin)):
    """Move the whole data folder (database, uploads, backups, certificate, logo …) to another folder or drive, then
    restart on it (services/datadir.py). The old folder is left as it was, with a MOVED.txt note."""
    from app import updater
    from app.services import datadir as DD
    ok, why = DD.can_move()
    if not ok:
        return redirect("/settings#general", why)
    t, err = DD.check_target(target)
    if t is None:
        return redirect("/settings#general", err)
    old = str(get_config_dir())
    audit(c.db, c.user, "settings.data_folder", "system", "", {"from": old, "to": str(t)}, c.ip)
    c.db.commit()                       # the entry goes into the copy
    try:
        res = DD.move(t)
    except Exception as ex:  # noqa: BLE001
        return redirect("/settings#general", f"The data was not moved — {ex}. The app keeps using {old}.")
    mb = res["size"] / 1048576
    if os.environ.get("HSCO_SUPERVISED") == "1" and updater._restart_hook is not None:
        import threading
        threading.Timer(1.5, updater._restart_hook).start()
        return redirect("/settings#general", f"Data copied to {t} ({mb:,.0f} MB) — the app restarts on it now; reload "
                                             f"the page in a few seconds. The old folder {old} is kept (see MOVED.txt).")
    return redirect("/settings#general", f"Data copied to {t} ({mb:,.0f} MB). Restart the app to start using it. The "
                                         f"old folder {old} is kept (see MOVED.txt).")


def get_config_dir():
    from app.config import get_config
    return get_config().data_dir


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


@router.post("/branding/raster")
async def logo_raster(request: Request, c: Ctx = Depends(hr)):
    """PNG copy of the logo drawn by the browser (base.html) — the Excel workbooks embed it (v1.15)."""
    data = await request.body()
    try:
        w, h = await anyio.to_thread.run_sync(branding.save_raster, data[: branding.MAX_RASTER + 1])
    except ValueError as ex:
        return Response(str(ex), status_code=400)
    return Response(f"{w}x{h}", media_type="text/plain")


@router.post("/settings/branding/delete")
def delete_logo(c: Ctx = Depends(admin)):
    if branding.remove():
        audit(c.db, c.user, "branding.logo_removed", "settings", "", ip=c.ip)
    return redirect("/settings#general", "Logo removed.")



# ----------------------------------------------------------------------------- backups
@router.post("/settings/backup")
def backup_now(c: Ctx = Depends(admin)):
    from app.services import backups as BK
    try:
        info = BK.make_backup("manual")
    except Exception as ex:  # noqa: BLE001
        return redirect("/settings#general", f"Backup failed: {ex}")
    audit(c.db, c.user, "backup.manual", "settings", info["file"], {"bytes": info["size"]}, c.ip)
    return redirect("/settings#general", f"Backup written and verified: {info['file']}.")
