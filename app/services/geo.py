"""Coordinates → human place names, without sending data off the server by default.

Order (docs/BUSINESS_RULES.md §9): admin geofences → offline GeoNames gazetteer (app/data/in_places.csv.gz,
CC-BY 4.0 geonames.org) → optional online OSM Nominatim (off by default) → raw coordinates.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import threading
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.models import GeoCache, Geofence
from app.services.settings import get_general

_GAZ = None
_LOCK = threading.Lock()
CELL = 0.1  # degrees


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _gazetteer():
    global _GAZ
    with _LOCK:
        if _GAZ is None:
            grid = defaultdict(list)
            path = APP_DIR / "data" / "in_places.csv.gz"
            if path.exists():
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    rd = csv.reader(f)
                    next(rd, None)
                    for name, state, lat, lon in rd:
                        la, lo = float(lat), float(lon)
                        grid[(int(la // CELL), int(lo // CELL))].append((la, lo, name, state))
            _GAZ = grid
    return _GAZ


def offline_name(lat: float, lon: float, max_km: float = 15) -> Optional[str]:
    grid = _gazetteer()
    ci, cj = int(lat // CELL), int(lon // CELL)
    best = None
    rng = max(1, int(math.ceil(max_km / 11)))
    for di in range(-rng, rng + 1):
        for dj in range(-rng, rng + 1):
            for la, lo, name, state in grid.get((ci + di, cj + dj), ()):
                d = haversine_m(lat, lon, la, lo)
                if best is None or d < best[0]:
                    best = (d, name, state)
    if best and best[0] <= max_km * 1000:
        dist = best[0] / 1000
        prefix = "" if dist < 1.0 else "near "
        return f"{prefix}{best[1]}, {best[2]}".strip(", ")
    return None


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
        self.fences = db.scalars(select(Geofence)).all()
        g = get_general(db)
        self.online = bool(g.get("online_geocoding"))
        self.max_km = float(g.get("geocode_max_km") or 15)
        self.cache: dict[str, str] = {}

    def name(self, lat: Optional[float], lon: Optional[float]) -> str:
        if lat is None or lon is None:
            return ""
        best = None
        for f in self.fences:
            d = haversine_m(lat, lon, f.lat, f.lon)
            if d <= f.radius_m and (best is None or d < best[0]):
                best = (d, f)
        if best:
            return f"{best[1].name} ({best[1].category})"
        key = f"{lat:.4f},{lon:.4f}"
        if key in self.cache:
            return self.cache[key]
        row = self.db.get(GeoCache, key)
        if row:
            self.cache[key] = row.name
            return row.name
        name, src = offline_name(lat, lon, self.max_km), "offline"
        if self.online:
            on = online_name(lat, lon)
            if on:
                name, src = on, "osm"
        name = name or f"{lat:.5f}, {lon:.5f}"
        self.db.merge(GeoCache(key=key, name=name, source=src))
        self.cache[key] = name
        return name


def unnamed_clusters(db: Session, limit: int = 30) -> list[dict]:
    """Group swipe coordinates not covered by a geofence (≈110 m grid) so the admin can name them."""
    from app.models import SwipeRow

    fences = db.scalars(select(Geofence)).all()
    buckets: dict = defaultdict(lambda: {"count": 0, "emps": set(), "lat": 0.0, "lon": 0.0, "place": ""})
    for s in db.scalars(select(SwipeRow).where(SwipeRow.lat.is_not(None))):
        if any(haversine_m(s.lat, s.lon, f.lat, f.lon) <= f.radius_m for f in fences):
            continue
        k = (round(s.lat, 3), round(s.lon, 3))
        b = buckets[k]
        b["count"] += 1
        b["emps"].add(s.emp_no)
        b["lat"], b["lon"], b["place"] = s.lat, s.lon, s.place
    out = sorted(buckets.values(), key=lambda b: -b["count"])[:limit]
    return [{**b, "emps": sorted(b["emps"])} for b in out]
