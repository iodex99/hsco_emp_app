"""Where people signed in from: the Locations page and its Excel workbook (docs/METRICS.md).

Works from the stored punches alone (SwipeRow), so it is available for a month as soon as its swipe export is
imported — even before the muster exists (e.g. the month in progress).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.model import REJECTED_APPROVALS
from app.models import Employee, Geofence, MonthResult, Period, SwipeRow
from app.services import location_checks as LC

TYPE_LABEL = {"": "Not given"}


def _is_mobile(s) -> bool:
    return (s.swipe_type or "").strip().lower() != "astra"


def report(db: Session, period: Period) -> dict:
    fences = db.scalars(select(Geofence)).all()
    offices = LC.office_fences(fences)
    names = {e.emp_no: e.name for e in db.scalars(select(Employee))}
    computed = set(db.scalars(select(MonthResult.emp_no).where(MonthResult.period_id == period.id)))
    sw = list(db.scalars(select(SwipeRow).where(SwipeRow.period_id == period.id).order_by(SwipeRow.ts)))
    mobile = [s for s in sw if _is_mobile(s)]
    shared = LC.shared_devices(mobile)
    rows, by_place, by_person = [], {}, defaultdict(Counter)
    client_visits = set()
    for s in mobile:
        rejected = (s.approval or "").strip().lower() in REJECTED_APPROVALS
        lt = (s.location_type or "").strip()
        office_tag = LC.is_office_tagged(s)
        check = LC.at_office(s, offices) if office_tag and not rejected else None
        place = s.place or ("" if s.lat is None else f"{s.lat:.5f}, {s.lon:.5f}")
        tags = []
        if office_tag and check is False:
            tags.append("away")
        if lt.lower() == "client location":
            tags.append("client")
            if place:
                client_visits.add((s.emp_no, s.ts.date(), place))
        if lt.lower() in ("work from home", "wfh"):
            tags.append("wfh")
        if lt.lower() == "on-duty":
            tags.append("onduty")
        if (s.swipe_type or "").lower() == "permission":
            tags.append("permission")
        if s.device_id and s.device_id in shared:
            tags.append("shared")
        if rejected:
            tags.append("rejected")
        if s.lat is None:
            tags.append("nogps")
        rows.append({"ts": s.ts, "emp_no": s.emp_no, "name": names.get(s.emp_no, s.emp_no), "type": lt or "—",
                     "swipe_type": s.swipe_type or "", "place": place, "note": s.note or "", "reason": s.reason or "",
                     "device": s.device or "", "approval": s.approval or "", "lat": s.lat, "lon": s.lon,
                     "office_check": "Away from office" if check is False else "At office" if check else "",
                     "office_tag": office_tag,
                     "tags": " ".join(tags), "href": f"/periods/{period.key}/employee/{s.emp_no}" if s.emp_no in computed
                     else _only(period, s.emp_no)})
        p = by_person[s.emp_no]
        p["sign_ins"] += 1
        p["gps"] += s.lat is not None
        for t in tags:
            p[t] += 1
        if office_tag:
            p["office_tagged"] += 1
        if place and not rejected:
            b = by_place.setdefault(place, {"place": place, "count": 0, "people": set(), "types": Counter(),
                                            "notes": Counter(), "first": s.ts, "last": s.ts})
            b["count"] += 1
            b["people"].add(s.emp_no)
            b["types"][lt or "—"] += 1
            if s.note:
                b["notes"][s.note.strip()] += 1
            b["last"] = s.ts
    places = sorted(({**b, "people": len(b["people"]), "people_names": sorted(names.get(e, e) for e in b["people"]),
                      "types": ", ".join(f"{t} {n}" for t, n in b["types"].most_common()),
                      "main_type": b["types"].most_common(1)[0][0],
                      "notes": "; ".join(n for n, _ in b["notes"].most_common(3))} for b in by_place.values()),
                    key=lambda x: -x["count"])
    people = sorted(({"emp_no": e, "name": names.get(e, e), "href": f"/periods/{period.key}/employee/{e}" if e in computed
                      else _only(period, e), **{k: c.get(k, 0) for k in ("sign_ins", "gps", "client", "wfh", "onduty",
                                                                             "office_tagged", "away", "permission",
                                                                             "shared", "rejected")},
                      "places": len({r["place"] for r in rows if r["emp_no"] == e and r["place"]})}
                     for e, c in by_person.items()), key=lambda x: (-x["away"], -x["sign_ins"]))
    with_gps = sum(1 for s in mobile if s.lat is not None)
    kpis = {"mobile": len(mobile), "biometric": len(sw) - len(mobile), "with_gps": with_gps,
            "gps_pct": round(100 * with_gps / len(mobile), 1) if mobile else None,
            "places": len(places), "client_visits": len(client_visits),
            "wfh": sum(1 for r in rows if "wfh" in r["tags"]),
            "wfh_people": len({r["emp_no"] for r in rows if "wfh" in r["tags"]}),
            "office_tagged": sum(1 for r in rows if r["office_tag"]),
            "away": sum(1 for r in rows if "away" in r["tags"]),
            "away_people": len({r["emp_no"] for r in rows if "away" in r["tags"]}),
            "shared_devices": len(shared), "offices_named": len(offices),
            "permission": sum(1 for r in rows if "permission" in r["tags"]),
            "rejected": sum(1 for r in rows if "rejected" in r["tags"])}
    return {"period": period, "kpis": kpis, "rows": rows, "places": places, "people": people,
            "tag_counts": Counter(t for r in rows for t in r["tags"].split()),
            "shared": {d: sorted(names.get(e, e) for e in es) for d, es in shared.items()}}


def _only(period: Period, emp_no: str) -> str:
    """Month not calculated yet (no muster): the Locations page filtered to this person's sign-ins."""
    return f"/locations?period={period.key}&t_signins=" + quote(json.dumps({"q": emp_no}))


def months_with_signins(db: Session) -> list[Period]:
    ids = set(db.scalars(select(SwipeRow.period_id).distinct()))
    return [p for p in db.scalars(select(Period).order_by(Period.year.desc(), Period.month.desc())) if p.id in ids]


def pick(db: Session, key: Optional[str]) -> Optional[Period]:
    ps = months_with_signins(db)
    if key:
        for p in ps:
            if p.key == key:
                return p
    return ps[0] if ps else None
