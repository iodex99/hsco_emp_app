"""Moving the data folder from the app (Settings → Security & system → Data folder, v1.12).

Where the data lives: HSCO_DATA_DIR (set by the installer) or <app>/data — the *base*. After a move,
`data_location.json` next to the app says {"base": <base at the time>, "dir": <new folder>}; config.resolve_data_dir()
uses "dir" while the base is still the one it was moved from (so a test or a fresh install never follows an old
pointer). The supervisor (run_server.py) reads the same file at every (re)start; updates never replace it.

A move copies everything (the database through SQLite's backup API — consistent while headcount work — and checked),
writes the pointer, leaves the old folder untouched with a note, and restarts the app on the new folder.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app import config as C

DB_FILES = {"hsco.db", "hsco.db-wal", "hsco.db-shm"}
SKIP_UPDATES = ("staged", "rollback", "download")      # transient update files are not carried over


def _size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def can_move() -> tuple[bool, str]:
    """A move needs the app to restart on the new folder: under a supervisor that knows about moves, or run by hand."""
    if os.environ.get("HSCO_SUPERVISED") == "1" and "datadir" not in os.environ.get("HSCO_SUPERVISOR_FEATURES", ""):
        return False, ("Restart the server PC once (or the 'HSCO Attendance App' task) after this update — then the "
                       "data folder can be moved from here.")
    return True, ""


def info() -> dict:
    cfg = C.get_config()
    cur, base = cfg.data_dir, C.base_data_dir()
    try:
        free = shutil.disk_usage(cur).free
    except OSError:
        free = None
    ok, why = can_move()
    return {"dir": str(cur), "base": str(base), "moved": not C.same_path(cur, base), "size": _size(cur), "free": free,
            "can_move": ok, "why_not": why, "supervised": os.environ.get("HSCO_SUPERVISED") == "1"}


def check_target(raw: str) -> tuple[Path | None, str]:
    """The folder to move to, or why not."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None, "Enter the folder to move the data to, e.g. D:\\HSCOData."
    t = Path(raw)
    if not t.is_absolute():
        return None, "Enter a full path including the drive, e.g. D:\\HSCOData."
    t = t.resolve()
    cur = C.get_config().data_dir
    if C.same_path(t, cur):
        return None, "That is where the data already is."
    if t.is_relative_to(cur) or cur.is_relative_to(t):
        return None, "Choose a folder outside the current data folder (and not one that contains it)."
    app_root = C.BASE_DIR.resolve()
    if t.is_relative_to(app_root) and not C.same_path(t, app_root / "data"):
        return None, "Choose a folder outside the app's folder (updates replace what is in there)."
    anchor = Path(t.anchor) if t.anchor else None
    if anchor is None or not anchor.exists():
        return None, f"The drive {t.anchor or ''} does not exist on this server."
    if t.exists() and (not t.is_dir() or any(t.iterdir())):
        return None, "The folder must be new or empty (it would otherwise mix with other files)."
    need = _size(cur)
    try:
        free = shutil.disk_usage(t if t.exists() else _existing_parent(t)).free
    except OSError:
        free = None
    if free is not None and free < need * 1.2 + 50 * 1024 * 1024:
        return None, f"Not enough free space there ({free / 1048576:,.0f} MB free, about {need / 1048576:,.0f} MB needed)."
    return t, ""


def _existing_parent(p: Path) -> Path:
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _restrict(folder: Path) -> None:
    """Server installs keep employee data readable by administrators and SYSTEM only (as the installer does)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():       # a PC copy run by its user: leave the user's own rights
            return
        subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", "Administrators:(OI)(CI)F",
                        "SYSTEM:(OI)(CI)F"], capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001 - best effort; the copy itself is what matters
        pass


def move(target: Path) -> dict:
    """Copy the data folder to `target` and point the app at it. Raises on any problem (the old folder stays in
    use). Returns {"from", "to", "size"}."""
    cfg = C.get_config()
    src = cfg.data_dir
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    try:
        for item in src.iterdir():
            if item.name in DB_FILES:
                continue
            dst = target / item.name
            if item.is_dir():
                ignore = shutil.ignore_patterns(*SKIP_UPDATES) if item.name == "updates" else None
                shutil.copytree(item, dst, ignore=ignore, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        s = sqlite3.connect(src / "hsco.db")
        d = sqlite3.connect(target / "hsco.db")
        with d:
            s.backup(d)
        s.close()
        d.close()
        chk = sqlite3.connect(f"file:{(target / 'hsco.db').as_posix()}?mode=ro", uri=True)
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        chk.close()
        if ok != "ok":
            raise RuntimeError(f"the copied database failed its check ({ok})")
    except Exception:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    _restrict(target)
    pointer = {"base": str(C.base_data_dir()), "dir": str(target), "from": str(src),
               "moved_at": datetime.now().isoformat(timespec="seconds")}
    tmp = C.POINTER.with_suffix(".tmp")
    tmp.write_text(json.dumps(pointer, indent=1), encoding="utf-8")
    os.replace(tmp, C.POINTER)
    try:
        (src / "MOVED.txt").write_text(
            f"The Attendance MIS data was moved to {target} on {datetime.now():%d-%b-%Y %H:%M}.\n"
            "This folder is no longer used. Keep it until you have checked the app, then it can be deleted.\n",
            encoding="utf-8")
    except OSError:
        pass
    return {"from": str(src), "to": str(target), "size": _size(target)}
