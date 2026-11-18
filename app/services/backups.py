"""Database backups and health (docs/DEPLOYMENT.md "Backup & restore").

* Daily automatic backup made by the app itself (maintenance thread) — no scheduled task needed — plus "Back up now"
  in Settings and `python -m app.cli backup`. A backup is an online, consistent SQLite snapshot (backup API, safe while
  people work), checked with PRAGMA integrity_check before it is kept.
* The encryption key (secret.key — needed to read two-step-verification secrets) is copied next to the backups once;
  a backup restored without it would lock everyone out of 2-step sign-in.
* Retention: every backup of the last 14 days, then one per month for 24 months; pre-update backups: the last 10.
* Health: PRAGMA quick_check at start-up, recorded in DATA_DIR/backups/health.json and shown in Settings.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.config import get_config

DAILY_KEEP_DAYS = 14
MONTHLY_KEEP = 24
PRE_UPDATE_KEEP = 10
EVERY = timedelta(hours=24)


def _dirs() -> tuple[Path, Path]:
    dd = get_config().data_dir
    b = dd / "backups"
    b.mkdir(parents=True, exist_ok=True)
    return dd, b


def _ts(p: Path) -> Optional[datetime]:
    """Time a backup was taken, from its name (…20261118_101500…), else the file's modification time."""
    m = re.search(r"(\d{8}_\d{6})", p.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    return datetime.fromtimestamp(p.stat().st_mtime)


def integrity(path: Path, quick: bool = False) -> str:
    """'ok' or the first problem SQLite reports."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check" if quick else "PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        return str(row[0]) if row else "no result"
    except sqlite3.Error as ex:
        return f"error: {ex}"


def make_backup(reason: str = "daily") -> dict:
    """Snapshot the live database into DATA_DIR/backups and verify it. Raises RuntimeError if the copy is bad."""
    dd, bdir = _dirs()
    src = dd / "hsco.db"
    if not src.exists():
        raise RuntimeError("no database yet")
    dst = bdir / f"hsco_{datetime.now():%Y%m%d_%H%M%S}_{reason}.db"
    tmp = dst.with_suffix(".partial")
    with sqlite3.connect(src, timeout=60) as a, sqlite3.connect(tmp) as b:
        a.backup(b)
    a.close()
    b.close()
    check = integrity(tmp)
    if check != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed its integrity check: {check}")
    tmp.replace(dst)
    key = dd / "secret.key"
    kcopy = bdir / "secret.key"
    if key.exists() and (not kcopy.exists() or kcopy.read_bytes() != key.read_bytes()):
        shutil.copy2(key, kcopy)
    prune()
    info = {"file": dst.name, "size": dst.stat().st_size, "at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason, "check": check}
    _write_state(last_backup=info)
    return info


def prune(now: Optional[datetime] = None) -> list[str]:
    """Apply retention; returns removed file names."""
    _, bdir = _dirs()
    now = now or datetime.now()
    removed = []
    regular = sorted((p for p in bdir.glob("hsco_*.db")), key=lambda p: _ts(p) or now)
    months_seen: set = set()
    for p in reversed(regular):                       # newest first
        t = _ts(p) or now
        if now - t <= timedelta(days=DAILY_KEEP_DAYS):
            continue
        m = (t.year, t.month)
        if m not in months_seen and len(months_seen) < MONTHLY_KEEP:
            months_seen.add(m)                        # keep the newest backup of each month
            continue
        p.unlink(missing_ok=True)
        removed.append(p.name)
    pre = sorted(bdir.glob("pre-update-*.db"), key=lambda p: p.stat().st_mtime)
    for p in pre[:-PRE_UPDATE_KEEP]:
        p.unlink(missing_ok=True)
        removed.append(p.name)
    return removed


def list_backups() -> list[dict]:
    _, bdir = _dirs()
    out = []
    for p in list(bdir.glob("hsco_*.db")) + list(bdir.glob("pre-update-*.db")):
        out.append({"file": p.name, "size": p.stat().st_size, "at": _ts(p),
                    "kind": "before update" if p.name.startswith("pre-update") else
                    ("manual" if p.stem.endswith("_manual") else "daily")})
    return sorted(out, key=lambda x: x["at"] or datetime.min, reverse=True)


def due(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    last = [b["at"] for b in list_backups() if b["kind"] != "before update" and b["at"]]
    return not last or now - max(last) >= EVERY


def check_health() -> dict:
    dd, _ = _dirs()
    res = {"at": datetime.now().isoformat(timespec="seconds"), "check": integrity(dd / "hsco.db", quick=True),
           "key_backed_up": (dd / "backups" / "secret.key").exists()}
    _write_state(health=res)
    return res


def state() -> dict:
    _, bdir = _dirs()
    try:
        return json.loads((bdir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**kw) -> None:
    _, bdir = _dirs()
    s = state()
    s.update(kw)
    (bdir / "state.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")


def restore(backup_file: Path) -> Path:
    """Replace the database with a backup (the app must be stopped). The current database is kept as a safety copy.
    Returns the safety copy's path."""
    dd, bdir = _dirs()
    backup_file = Path(backup_file)
    if not backup_file.exists():
        backup_file = bdir / backup_file.name
    if not backup_file.exists():
        raise FileNotFoundError(backup_file)
    check = integrity(backup_file)
    if check != "ok":
        raise RuntimeError(f"that backup failed its integrity check: {check}")
    db = dd / "hsco.db"
    safety = bdir / f"before-restore-{datetime.now():%Y%m%d_%H%M%S}.db"
    if db.exists():
        with sqlite3.connect(db) as a, sqlite3.connect(safety) as b:
            a.backup(b)
        a.close()
        b.close()
    for ext in ("-wal", "-shm"):
        (dd / f"hsco.db{ext}").unlink(missing_ok=True)
    shutil.copy2(backup_file, db)
    return safety


def summary() -> dict:
    """What Settings shows: health, last backup (as datetimes) and whether the key copy exists — read live."""
    dd, bdir = _dirs()
    st = state()
    h, lb = st.get("health") or {}, st.get("last_backup") or {}
    parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
    return {"check": h.get("check"), "checked_at": parse(h.get("at")), "last_at": parse(lb.get("at")),
            "last_reason": lb.get("reason"), "key_copy": (bdir / "secret.key").exists()
            and (dd / "secret.key").exists() and (bdir / "secret.key").read_bytes() == (dd / "secret.key").read_bytes()}
