"""Small, dependency-free summaries of an employee-month's day-by-day results.

Stored with every calculated month (MonthResult.data["_ins"]) so Insights history, 12-month absence and trends read a
few numbers instead of decoding the full day detail (~20 KB per employee-month). See docs/METRICS.md "Insights".
"""
from __future__ import annotations

UNPLANNED = ("absent", "lop")   # "lop": months calculated before v1.13 only
STATS_VERSION = 1


def unplanned(x: dict) -> bool:
    return x.get("b1") in UNPLANNED or x.get("b2") in UNPLANNED


def spells(days: list[dict]) -> tuple[int, float]:
    """Unplanned-absence spells and days. Consecutive absent working days form one spell; weekly-offs and holidays
    between them do not break it (a Friday + Monday absence is one spell)."""
    n, total, in_spell = 0, 0.0, False
    for dd in sorted(days, key=lambda d: d["date"]):
        if dd.get("type") != "WORKDAY":
            continue
        halves = sum(0.5 for b in (dd.get("b1"), dd.get("b2")) if b in UNPLANNED)
        if halves:
            if not in_spell:
                n += 1
            in_spell = True
            total += halves
        else:
            in_spell = False
    return n, total


def month_stats(days: list[dict]) -> dict:
    """wd working days · un unplanned-absence days · sp spells · sa/ea first/last working day had unplanned absence
    (joins spells across month boundaries) · pd present days · pw present days with a first punch · ot of those on time."""
    sp, un = spells(days)
    work = sorted((x for x in days if x.get("type") == "WORKDAY"), key=lambda x: x["date"])
    present = [x for x in work if "present" in (x.get("b1"), x.get("b2"))]
    with_in = [x for x in present if x.get("in")]
    return {"v": STATS_VERSION, "wd": len(work), "un": un, "sp": sp, "sa": bool(work and unplanned(work[0])),
            "ea": bool(work and unplanned(work[-1])), "pd": len(present), "pw": len(with_in),
            "ot": sum(1 for x in with_in if not x.get("late"))}


def next_month(ym: tuple) -> tuple:
    return (ym[0] + (ym[1] == 12), ym[1] % 12 + 1)


def join_spells(months: list[tuple]) -> tuple[int, float]:
    """[(ym, stats)] → (spells, days); a spell that continues into the next calendar month is counted once."""
    n, total, prev = 0, 0.0, None
    for ym, st in sorted(months, key=lambda x: x[0]):
        n += st["sp"]
        total += st["un"]
        if prev and next_month(prev[0]) == ym and prev[1]["ea"] and st["sa"]:
            n -= 1
        prev = (ym, st)
    return n, total
