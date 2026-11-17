"""Background start-up maintenance (one daemon thread, started by app.main.create_app).

1. Build the offline gazetteer index (first start only, ~4 s) so the first import with coordinates is fast.
2. Once per gazetteer version: re-name swipes stored with older offline names (geo.upgrade_place_names).
3. Backfill the compact Insights summary (MonthResult.data["_ins"]) for months calculated by versions before 1.4.1.

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
    from app.services import geo
    for step in (geo.gazetteer_db, geo.upgrade_place_names, backfill_month_stats):
        try:
            step()
        except Exception:  # noqa: BLE001 — one failing step must not stop the others or the app
            log.exception("start-up maintenance step %s failed", getattr(step, "__name__", step))


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
