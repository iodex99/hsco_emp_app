"""Holiday helpers (docs/BUSINESS_RULES.md §2a). Holidays are targeted only by category (owner, v1.9): each category
has its own list (a holiday calendar) and a one-off holiday for some categories is simply added to each of their
lists. Here: parsing a pasted list (e.g. copied from a PDF) and splitting a "Category A, Category B" cell."""
from __future__ import annotations

import re
from typing import Iterable


def split(s: str | Iterable[str] | None) -> list[str]:
    """'HSCO (Mum), HSCO (Carlyle)' or a list → the names, de-duplicated, order kept."""
    if s is None:
        return []
    parts = re.split(r"[,;\n|]+", s) if isinstance(s, str) else [x for v in s for x in re.split(r"[,;\n|]+", v or "")]
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|" \
          "september|october|november|december"
_DATE_RES = [
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]+({_MONTHS})[a-z]*[\s\-/.,]+(\d{{4}}|\d{{2}})\b", re.I),
    re.compile(rf"\b({_MONTHS})[a-z]*[\s\-/.]+(\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-/.]+(\d{{4}})\b", re.I),
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b"),
]
_WD_RE = re.compile(r"\b(mon|tues|tue|wednes|wed|thurs|thu|fri|satur|sat|sun)(day)?\b\.?", re.I)
_OPT_RE = re.compile(r"\(?\b(optional|restricted|rh)\b\)?", re.I)


def _ymd(i: int, g: tuple):
    """Date from the groups of _DATE_RES[i] (day-first for dd/mm/yyyy, as in India)."""
    from datetime import date as _d
    mon = {m[:3]: n for n, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"), start=1)}
    try:
        if i == 0:
            dd, mm, yy = int(g[0]), mon[g[1][:3].lower()], int(g[2])
        elif i == 1:
            mm, dd, yy = mon[g[0][:3].lower()], int(g[1]), int(g[2])
        elif i == 2:
            yy, mm, dd = int(g[0]), int(g[1]), int(g[2])
        else:
            dd, mm, yy = int(g[0]), int(g[1]), int(g[2])
        yy = yy + 2000 if yy < 100 else yy
        return _d(yy, mm, dd) if 1990 <= yy <= 2100 else None
    except (KeyError, ValueError):
        return None


_NOYEAR_RES = [
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]+({_MONTHS})[a-z]*\b", re.I),
    re.compile(rf"\b({_MONTHS})[a-z]*[\s\-/.]+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I),
]


def parse_pasted(text: str, default_year: int | None = None) -> tuple[list[dict], list[str]]:
    """Holidays from pasted text — one per line, the date anywhere in it ("1  Republic Day  26 January 2026",
    "26-01-2026 Republic Day", "Republic Day, Jan 26, 2026", "Jan 26 Monday Republic Day"); serial numbers, weekday
    names (before or after the date, or in brackets) and the words optional / restricted are understood. A line
    without a year takes default_year, else the year most other lines have, else this year. Returns (rows {date, name,
    optional, weekday}, lines that had no date)."""
    from collections import Counter
    from datetime import date as _d
    from app.importers.parsers import weekday_of
    rows, skipped = [], []
    for raw in (text or "").splitlines():
        line = " ".join(raw.replace("	", " ").split())
        if not line:
            continue
        d, span, noyear = None, None, None
        for i, rx in enumerate(_DATE_RES):
            m = rx.search(line)
            if m:
                d = _ymd(i, m.groups())
                if d:
                    span = m.span()
                    break
        if d is None:
            for i, rx in enumerate(_NOYEAR_RES):
                m = rx.search(line)
                if m:
                    g = m.groups()
                    dd, mon = (g[0], g[1]) if i == 0 else (g[1], g[0])
                    mm = _ymd(0, (dd, mon, "2000"))
                    if mm:
                        noyear, span = (mm.month, mm.day), m.span()
                        break
        if d is None and noyear is None:
            if any(ch.isalpha() for ch in line) and not re.match(r"(?i)^(s\.?\s*no|sr|holiday|date|day|occasion)\b", line):
                skipped.append(line)
            continue
        wd = None
        after = re.match(r"\s*[,(\-–]?\s*(" + _WD_RE.pattern + r")\s*\)?", line[span[1]:], re.I)
        if after:                                    # "26-01-2026 Monday …": the weekday that goes with the date
            wd = weekday_of(after.group(1))
            line_rest_after = line[span[1] + after.end():]
        else:
            line_rest_after = line[span[1]:]
        rest = (line[:span[0]] + " " + line_rest_after).strip()
        optional = bool(_OPT_RE.search(rest))
        rest = _OPT_RE.sub(" ", rest)
        if wd is None:
            for m in _WD_RE.finditer(rest):
                before = rest[:m.start()].rstrip()
                boxed = before.endswith("(")
                # a weekday column / "(Monday)" — not part of a name such as "Good Friday"
                if boxed or not before or not before[-1].isalpha():
                    wd = weekday_of(m.group(0))
                    rest = rest[:m.start()] + " " + rest[m.end():]
                    break
        rest = re.sub(r"\(\s*\)", " ", rest)
        rest = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", rest)              # serial number
        name = " ".join(rest.strip(" -–—|,;:.").split())
        rows.append({"date": d, "noyear": noyear, "name": name or "Holiday", "optional": optional, "weekday": wd})
    years = Counter(r["date"].year for r in rows if r["date"])
    year = default_year or (years.most_common(1)[0][0] if years else _d.today().year)
    out = []
    for r in rows:
        if r["date"] is None:
            try:
                r["date"] = _d(year, *r["noyear"])
            except ValueError:
                skipped.append(r["name"])
                continue
        r.pop("noyear", None)
        out.append(r)
    return out, skipped
