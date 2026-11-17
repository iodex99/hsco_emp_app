"""Self-update (docs/DEPLOYMENT.md "Updates", docs/ARCHITECTURE.md "Update system").

Flow: check GitHub Releases → (admin clicks Update) download zip with progress → verify SHA-256 → validate zip →
extract to DATA_DIR/updates/staged + READY marker → ask the server to exit with code 75 → the supervisor
(run_server.py) backs up the DB, swaps the code, starts the new build, health-checks it and rolls back on failure.

Only an HTTPS GET to the configured releases URL leaves the server — no data is sent. Disable in Settings → Updates.
Source checkouts (a .git folder) never self-update; use git pull there.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from app import version as V
from app.config import BASE_DIR, get_config

DEFAULT_URL = "https://api.github.com/repos/iodex99/hsco_emp_app/releases/latest"
RESTART_EXIT_CODE = 75
UA = "attendance-app-updater/1"
REQUIRED_FILES = ("app/main.py", "app/__init__.py", "run_server.py", "BUILD.json", "requirements.txt")

_lock = threading.Lock()
_state = {"phase": "idle", "bytes": 0, "total": 0, "message": "", "error": "", "target": None, "started": None}
_restart_hook: Optional[Callable[[], None]] = None
_checker: Optional[threading.Thread] = None


# ------------------------------------------------------------------ paths & settings

def _dir(*parts) -> Path:
    d = get_config().data_dir / "updates"
    for p in parts:
        d = d / p
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_checkout() -> bool:
    return (BASE_DIR / ".git").exists() and os.environ.get("HSCO_UPDATES_FORCE") != "1"


def supervised() -> bool:
    return os.environ.get("HSCO_SUPERVISED") == "1"


def config() -> dict:
    """Update settings: env overrides (tests / special installs) → Settings → defaults."""
    enabled, url, interval = True, DEFAULT_URL, 10
    try:
        from app.db import session_scope
        from app.services.settings import get_general
        with session_scope() as db:
            g = get_general(db)
        enabled = bool(g.get("update_check", True))
        url = g.get("update_url") or DEFAULT_URL
        interval = int(g.get("update_interval_min") or 10)
    except Exception:  # noqa: BLE001 - settings unavailable (first start) → defaults
        pass
    url = os.environ.get("HSCO_UPDATE_URL", url)
    return {"enabled": enabled, "url": url, "interval_min": max(1, interval)}


def set_restart_hook(fn: Callable[[], None]) -> None:
    global _restart_hook
    _restart_hook = fn


# ------------------------------------------------------------------ state

def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def status() -> dict:
    cur = V.current()
    latest = _read_json(_dir() / "latest.json")
    last = _read_json(_dir() / "last_result.json")
    with _lock:
        st = dict(_state)
    pct = round(100 * st["bytes"] / st["total"], 1) if st["total"] else (100.0 if st["phase"] in ("verifying", "staging", "restarting", "staged") else 0.0)
    available = bool(latest and latest.get("build", 0) > cur["build"] and not source_checkout())
    return {
        "current": cur, "latest": latest, "available": available, "source_checkout": source_checkout(),
        "supervised": supervised(), "phase": st["phase"], "bytes": st["bytes"], "total": st["total"], "percent": pct,
        "message": st["message"], "error": st["error"], "target": st["target"], "last_result": last,
        "staged": (get_config().data_dir / "updates" / "staged" / "READY").exists(),
    }


# ------------------------------------------------------------------ check

def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - https URL from admin settings


def parse_release(data: dict) -> dict:
    tag = data.get("tag_name", "")
    m = re.match(r"^build-(\d+)$", tag)
    if not m:
        raise ValueError(f"Unexpected release tag '{tag}' (expected build-<n>)")
    assets = {a["name"]: a for a in data.get("assets", [])}
    zips = [a for n, a in assets.items() if n.endswith(".zip")]
    if not zips:
        raise ValueError("Release has no .zip asset")
    z = zips[0]
    sha = assets.get(z["name"] + ".sha256")
    vm = re.search(r"v(\d+\.\d+\.\d+\S*)", data.get("name", "") or "")
    return {"build": int(m.group(1)), "tag": tag, "version": vm.group(1) if vm else "", "name": data.get("name", tag),
            "notes": (data.get("body") or "")[:4000], "published_at": data.get("published_at", ""),
            "zip_url": z["browser_download_url"], "zip_name": z["name"], "size": z.get("size", 0),
            "sha_url": sha["browser_download_url"] if sha else "", "html_url": data.get("html_url", "")}


def check(url: Optional[str] = None) -> dict:
    cfg = config()
    url = url or cfg["url"]
    try:
        with _get(url) as r:
            info = parse_release(json.loads(r.read().decode("utf-8")))
        info["checked_at"] = datetime.now().isoformat(timespec="seconds")
        (_dir() / "latest.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        if _state["phase"] == "error" and not _state["target"]:
            _set(phase="idle", error="")
    except Exception as ex:  # noqa: BLE001
        _set(error=f"Update check failed: {ex}") if _state["phase"] == "idle" else None
        return {**status(), "check_error": str(ex)}
    return status()


def start_background_checker() -> None:
    """Check soon after start and then every N minutes (setting). Daemon thread; never touches the DB lock long."""
    global _checker
    if _checker is not None or os.environ.get("HSCO_DISABLE_UPDATE_THREAD") == "1":
        return

    def loop():
        time.sleep(float(os.environ.get("HSCO_UPDATE_FIRST_DELAY", "20")))
        while True:
            cfg = config()
            if cfg["enabled"] and not source_checkout() and _state["phase"] in ("idle", "error"):
                check(cfg["url"])
            time.sleep(cfg["interval_min"] * 60)

    _checker = threading.Thread(target=loop, name="update-checker", daemon=True)
    _checker.start()


# ------------------------------------------------------------------ install

def validate_zip(path: Path, expected_build: int) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n in names:
            pp = PurePosixPath(n)
            if n.startswith(("/", "\\")) or ".." in pp.parts or ":" in n:
                raise ValueError(f"Unsafe path in update package: {n}")
            if pp.parts and pp.parts[0] in ("data", ".venv", "reference", ".git"):
                raise ValueError(f"Update package must not contain {pp.parts[0]}/")
        missing = [f for f in REQUIRED_FILES if f not in names]
        if missing:
            raise ValueError(f"Update package is incomplete (missing {', '.join(missing)})")
        build = json.loads(z.read("BUILD.json").decode("utf-8"))
    if int(build.get("build", -1)) != int(expected_build):
        raise ValueError(f"Package is build {build.get('build')}, expected {expected_build}")
    return build


def install(info: Optional[dict] = None, actor: str = "") -> dict:
    """Start the background download → verify → stage → restart sequence. Returns status immediately."""
    if source_checkout():
        raise RuntimeError("This copy is a source checkout (git). Update it with git pull instead.")
    info = info or _read_json(_dir() / "latest.json")
    if not info:
        raise RuntimeError("No update information — check for updates first.")
    if info["build"] <= V.current()["build"]:
        raise RuntimeError("Already up to date.")
    with _lock:
        if _state["phase"] in ("downloading", "verifying", "staging", "restarting"):
            raise RuntimeError("An update is already in progress.")
        _state.update(phase="downloading", bytes=0, total=int(info.get("size") or 0), message="Starting download…",
                      error="", target=info, started=time.time())
    threading.Thread(target=_run_install, args=(info, actor), name="update-install", daemon=True).start()
    return status()


def _run_install(info: dict, actor: str) -> None:
    dl = _dir("download")
    part = dl / (info["zip_name"] + ".part")
    final = dl / info["zip_name"]
    try:
        h = hashlib.sha256()
        with _get(info["zip_url"], timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            _set(total=total, message="Downloading…")
            got = 0
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                _set(bytes=got)
        os.replace(part, final)
        _set(phase="verifying", message="Verifying checksum…")
        if info.get("sha_url"):
            with _get(info["sha_url"]) as r:
                expected = r.read().decode("utf-8").split()[0].strip().lower()
            if expected != h.hexdigest():
                raise ValueError("Checksum mismatch — the download is corrupt or was tampered with. Nothing was changed.")
        else:
            raise ValueError("Release has no .sha256 file; refusing to install an unverified package.")
        build = validate_zip(final, info["build"])
        _set(phase="staging", message="Preparing the new version…")
        tmp = get_config().data_dir / "updates" / "staging-tmp"
        staged = get_config().data_dir / "updates" / "staged"
        for d in (tmp, staged):
            if d.exists():
                shutil.rmtree(d)
        with zipfile.ZipFile(final) as z:
            z.extractall(tmp)
        (tmp / "READY").write_text(json.dumps({**build, "from_build": V.current()["build"], "by": actor,
                                               "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        os.replace(tmp, staged)
        final.unlink(missing_ok=True)
        if supervised() and _restart_hook is not None:
            _set(phase="restarting", message="Installing and restarting — this takes a few seconds…")
            threading.Timer(1.0, _restart_hook).start()
        else:
            _set(phase="staged", message="Downloaded and verified. Restart the service to finish installing.")
    except Exception as ex:  # noqa: BLE001
        part.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        _set(phase="error", error=str(ex), message="Update failed")
