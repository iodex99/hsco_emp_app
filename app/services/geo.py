"""Coordinates → human place names, without sending data off the server by default.

Order (docs/BUSINESS_RULES.md §9): offline GeoNames gazetteer (app/data/in_places.csv.gz,
CC-BY 4.0 geonames.org: 566k Indian places — towns, villages, neighbourhoods, localities, railway stations, airports —
with district and state; indexed into DATA_DIR/geo on first use) → optional online OSM Nominatim (off by default) →
raw coordinates. Nothing is sent off the server unless online lookup is switched on.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import os
import pathlib
import sqlite3
import threading
import urllib.parse
import urllib.request
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.models import GeoCache
from app.services.settings import get_general

_LOCK = threading.Lock()
CELL = 0.05  # degrees (~5.5 km) — grid used to index the gazetteer
_DB: Optional[str] = None
SRC = APP_DIR / "data" / "in_places.csv.gz"


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def gazetteer_db() -> Optional[str]:
    """Path to an indexed SQLite copy of the bundled gazetteer (built once in DATA_DIR/geo, ~10 s)."""
    global _DB
    if _DB:
        return _DB
    with _LOCK:
        if _DB:
            return _DB
        if not SRC.exists():
            return None
        from app.config import get_config
        st = SRC.stat()
        d = get_config().data_dir / "geo"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"places-{st.st_size}-{int(st.st_mtime)}.sqlite"
        if not path.exists():
            tmp = path.with_suffix(".building")
            tmp.unlink(missing_ok=True)
            con = sqlite3.connect(tmp)
            con.execute("create table p(gx int, gy int, lat real, lon real, name text, district text, state text, kind text)")
            with gzip.open(SRC, "rt", encoding="utf-8") as f:
                rd = csv.reader(f)
                next(rd, None)
                batch = []
                for name, district, state, lat, lon, kind in rd:
                    la, lo = float(lat), float(lon)
                    batch.append((int(la // CELL), int(lo // CELL), la, lo, name, district, state, kind))
                    if len(batch) >= 20000:
                        con.executemany("insert into p values (?,?,?,?,?,?,?,?)", batch)
                        batch = []
                con.executemany("insert into p values (?,?,?,?,?,?,?,?)", batch)
            con.execute("create index ix_p on p(gx, gy)")
            con.commit()
            con.close()
            os.replace(tmp, path)
            for old in d.glob("places-*.sqlite"):
                if old != path:
                    old.unlink(missing_ok=True)
        _DB = str(path)
        return _DB


NAMES_VERSION = 3   # bump when the offline gazetteer changes so cached/stored names are refreshed once


def warm_up() -> None:
    """Background start-up job (see app/services/maintenance.py)."""
    from app.services import maintenance
    maintenance.start()


def upgrade_place_names() -> int:
    """Once per gazetteer version: re-name swipes stored with older offline names, then recalculate draft months.

    Never holds the database write lock for long: names are worked out first (reads only, offline gazetteer only —
    no online lookups), then written in short batches; each draft month is recalculated in its own
    transaction. Finalised months are left untouched (their results are frozen)."""
    from sqlalchemy import delete, update
    from app.db import session_scope
    from app.models import Period, Setting, SwipeRow
    from app.services.compute import compute_period
    with session_scope() as db:
        st = db.get(Setting, "geo_names_version")
        if st and (st.value or {}).get("v", 0) >= NAMES_VERSION:
            return 0
        drafts = {p.id for p in db.scalars(select(Period).where(Period.status == "DRAFT"))}
        max_km = float(get_general(db).get("geocode_max_km") or 15)
        rows = db.execute(select(SwipeRow.id, SwipeRow.period_id, SwipeRow.lat, SwipeRow.lon, SwipeRow.place)
                          .where(SwipeRow.lat.is_not(None), SwipeRow.period_id.in_(drafts or {-1}))).all()
    names: dict = {}
    changes: list = []
    touched: set = set()
    for rid, pid, lat, lon, place in rows:
        key = f"{lat:.4f},{lon:.4f}"
        if key not in names:
            names[key] = offline_name(lat, lon, max_km) or f"{lat:.5f}, {lon:.5f}"
        if names[key] != place:
            changes.append({"i": rid, "p": names[key]})
            touched.add(pid)
    from sqlalchemy import bindparam
    for i in range(0, len(changes), 2000):
        with session_scope() as db:
            db.execute(update(SwipeRow.__table__).where(SwipeRow.__table__.c.id == bindparam("i")).values(place=bindparam("p")),
                       changes[i:i + 2000])
    with session_scope() as db:
        db.execute(delete(GeoCache).where(GeoCache.source == "offline"))
    for pid in sorted(touched):
        with session_scope() as db:
            per = db.get(Period, pid)
            if per is not None and per.status == "DRAFT" and per.computed_at:
                compute_period(db, per)
    with session_scope() as db:
        st = db.get(Setting, "geo_names_version")
        if st is None:
            db.add(Setting(key="geo_names_version", value={"v": NAMES_VERSION}))
        else:
            st.value = {"v": NAMES_VERSION}
    return len(changes)


_TL = threading.local()


def _con() -> Optional[sqlite3.Connection]:
    """One read-only connection per thread (keeps the gazetteer's pages cached between lookups)."""
    db = gazetteer_db()
    if not db:
        return None
    con = getattr(_TL, "con", None)
    if con is None or getattr(_TL, "path", None) != db:
        con = sqlite3.connect(f"file:{pathlib.Path(db).as_posix()}?mode=ro", uri=True)
        _TL.con, _TL.path = con, db
    return con


def _nearby(lat: float, lon: float, max_km: float) -> list:
    """Places within max_km, nearest first: (metres, name, district, state, kind)."""
    con = _con()
    if con is None:
        return []
    dlat = max_km / 111.0
    dlon = max_km / (111.0 * max(0.2, math.cos(math.radians(lat))))   # full radius east-west at any latitude
    rows = con.execute("select lat, lon, name, district, state, kind from p where gx between ? and ? and gy between ? and ?",
                       (int((lat - dlat) // CELL), int((lat + dlat) // CELL),
                        int((lon - dlon) // CELL), int((lon + dlon) // CELL))).fetchall()
    out = [(haversine_m(lat, lon, r[0], r[1]), *r[2:]) for r in rows]
    return sorted((x for x in out if x[0] <= max_km * 1000), key=lambda x: x[0])


def describe(lat: float, lon: float, max_km: float = 15) -> Optional[dict]:
    """Structured offline place for a coordinate: nearest locality/neighbourhood, district, state, landmark."""
    # search a small window first; widen only when the answer could lie outside it (same result, far fewer rows)
    near = _nearby(lat, lon, min(3.0, max_km))
    places = [x for x in near if x[4] != "S"]
    if max_km > 3.0 and (not places or max(1500, places[0][0] * 1.6) > 3000):
        near = _nearby(lat, lon, max_km)
        places = [x for x in near if x[4] != "S"]
    if not places:
        return None
    # prefer a named neighbourhood/locality if it is almost as close as the nearest village/town
    best = places[0]
    hood = next((x for x in places if x[4].endswith("X")), None)
    if hood and hood[0] <= max(1500, best[0] * 1.6):
        best = hood
    land = next((x for x in near if x[4] == "S" and x[0] <= 600), None)
    return {"place": best[1], "district": best[2], "state": best[3], "km": round(best[0] / 1000, 1),
            "landmark": land[1] if land else "", "landmark_m": int(land[0]) if land else None}


def offline_name(lat: float, lon: float, max_km: float = 15) -> Optional[str]:
    d = describe(lat, lon, max_km)
    if not d:
        return None
    parts = [d["place"]]
    if d["district"] and d["district"].lower() not in d["place"].lower():
        parts.append(d["district"])
    if d["state"]:
        parts.append(d["state"])
    label = ", ".join(parts)
    if d["km"] >= 1.0:
        label = f"near {label} ({d['km']:g} km)"
    if d["landmark"] and d["landmark"].lower() != d["place"].lower():
        label += f" · by {d['landmark']}"
    return label


def online_name(lat: float, lon: float) -> Optional[str]:  # pragma: no cover - network, disabled by default
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16})
    req = urllib.request.Request(url, headers={"User-Agent": "attendance-reporting-onprem/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        a = data.get("address", {})
        parts = [a.get(k) for k in ("suburb", "neighbourhood", "city_district", "city", "town", "village", "state") if a.get(k)]
        return ", ".join(dict.fromkeys(parts[:3])) or data.get("display_name")
    except Exception:
        return None


class Namer:
    def __init__(self, db: Session):
        self.db = db
        g = get_general(db)
        self.online = bool(g.get("online_geocoding"))
        self.max_km = float(g.get("geocode_max_km") or 15)
        self.cache: dict[str, str] = {}
        self._known: Optional[dict] = None
        self.new: list = []                          # GeoCache rows to add (written by flush_cache)

    def name(self, lat: Optional[float], lon: Optional[float]) -> str:
        if lat is None or lon is None:
            return ""
        key = f"{lat:.4f},{lon:.4f}"
        if key in self.cache:
            return self.cache[key]
        if self._known is None:                      # load the whole cache once (no per-lookup query/autoflush)
            self._known = {k: n for k, n in self.db.execute(select(GeoCache.key, GeoCache.name))}
        if key in self._known:
            self.cache[key] = self._known[key]
            return self._known[key]
        name, src = offline_name(lat, lon, self.max_km), "offline"
        if self.online:
            on = online_name(lat, lon)
            if on:
                name, src = on, "osm"
        name = name or f"{lat:.5f}, {lon:.5f}"
        self.new.append(GeoCache(key=key, name=name, source=src))
        self.cache[key] = name
        return name

    def flush_cache(self) -> None:
        for g in self.new:
            self.db.merge(g)
        self.new = []
