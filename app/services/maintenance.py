"""Background start-up maintenance (one daemon thread, started by app.main.create_app).

1. Build the offline gazetteer index (first start only, ~4 s) so the first import with coordinates is fast.
2. Once per gazetteer version: re-name swipes stored with older offline names (geo.upgrade_place_names).
3. Backfill the compact Insights summary (MonthResult.data["_ins"]) for months calculated by versions before 1.4.1.
3a. Once per calculation-rules version (engine.compute.RULES_VERSION): recalculate draft months (finalised never).
4. Health check (PRAGMA quick_check) at start, then a daily backup (services/backups.py) for as long as the app runs.

Every database write happens in short transactions so users never wait on a long-held write lock
(docs/ARCHITECTURE.md "Concurrency rule"). Disabled with HSCO_DISABLE_GEO_WARMUP=1 (tests).
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("hsco.maintenance")
_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=run, name="maintenance", daemon=True).start()


def run() -> None:
    from app.services import backups, geo
    for step in (backups.check_health, geo.gazetteer_db, geo.upgrade_place_names, recalc_on_rules_change,
                 backfill_month_stats):
        try:
            step()
        except Exception:  # noqa: BLE001 — one failing step must not stop the others or the app
            log.exception("start-up maintenance step %s failed", getattr(step, "__name__", step))
    backup_loop()


def backup_loop(check_every_s: int = 1800) -> None:
    """Daily automatic backup by the app itself (works on any install, with or without a scheduled task)."""
    import time
    from app.services import backups
    while True:
        try:
            if backups.due():
                info = backups.make_backup("daily")
                log.info("daily backup written: %s", info["file"])
        except Exception:  # noqa: BLE001
            log.exception("daily backup failed")
        time.sleep(check_every_s)


def recalc_on_rules_change() -> int:
    """After an update that changes a calculation rule, recalculate every calculated draft month once (oldest first,
    one month per transaction). Finalised months keep their figures. Returns the number of months recalculated."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.engine.compute import RULES_VERSION
    from app.models import Period, Setting
    from app.services.compute import compute_period
    with session_scope() as db:
        st = db.get(Setting, "rules_version")
        if st and (st.value or {}).get("v", 0) >= RULES_VERSION:
            return 0
        ids = [p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT", Period.computed_at.is_not(None))
                                        .order_by(Period.year, Period.month))]
    for pid in ids:
        with session_scope() as db:
            per = db.get(Period, pid)
            if per is not None and per.status == "DRAFT":
                compute_period(db, per)
    with session_scope() as db:
        st = db.get(Setting, "rules_version")
        if st is None:
            db.add(Setting(key="rules_version", value={"v": RULES_VERSION}))
        else:
            st.value = {"v": RULES_VERSION}
    if ids:
        log.info("calculation rules v%s: recalculated %d draft month(s)", RULES_VERSION, len(ids))
    return len(ids)


def backfill_month_stats(batch: int = 200) -> int:
    """Store data["_ins"] on results that lack it. Adds a derived summary only; no figure changes."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import MonthResult
    from app.services.attendance_stats import month_stats

    done = 0
    while True:
        with session_scope() as db:
            ids = [i for i, d in db.execute(select(MonthResult.id, MonthResult.data)) if not (d or {}).get("_ins")][:batch]
            if not ids:
                return done
            for r in db.scalars(select(MonthResult).where(MonthResult.id.in_(ids))):
                data = dict(r.data or {})
                data["_ins"] = month_stats(r.days or [])
                r.data = data
            done += len(ids)
