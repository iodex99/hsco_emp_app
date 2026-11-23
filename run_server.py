"""Start the app under a small supervisor that also installs updates.

  python run_server.py                  HTTPS on 0.0.0.0:8443 (certificate in DATA_DIR/certs)
  python run_server.py --dev            HTTP on 127.0.0.1:8443 (local testing only)
  python run_server.py --no-supervisor  run the web server directly (debugging; updates are not applied)

The supervisor (this file) uses only the standard library so that a broken update can never break it. Loop:
  1. if DATA_DIR/updates/staged/READY exists: back up the DB, move the current code to DATA_DIR/updates/rollback,
     move the new code in, `pip install -r requirements.txt` if it changed;
  2. start `python -m app.serve …` (the web server);
  3. after an update, wait until /healthz reports the new build — otherwise roll back and start the old code;
  4. exit code 75 = restart requested (update staged) → loop; 0 = stop; anything else = crash → restart with backoff.
Results are written to DATA_DIR/updates/last_result.json (shown in the app).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART = 75
CORE = ("app", "run_server.py", "BUILD.json")       # always replaced/rolled back as a unit
# never moved or replaced by an update, whatever a package contains (data, environment, certificates, backups)
PROTECTED = {"data", ".venv", "venv", ".git", "reference", "certs", "backups", "logs", "data_location.json"}
PRE_UPDATE_KEEP = 10


def data_dir() -> Path:
    """HSCO_DATA_DIR or ROOT/data — or where it was moved from the app (data_location.json, same rule as
    app/config.resolve_data_dir; read at every (re)start so a move takes effect on the next restart)."""
    base = Path(os.environ.get("HSCO_DATA_DIR", ROOT / "data")).resolve()
    try:
        j = json.loads((ROOT / "data_location.json").read_text(encoding="utf-8"))
        if j.get("dir") and os.path.normcase(str(Path(j.get("base", "")).resolve())) == os.path.normcase(str(base)):
            return Path(j["dir"]).resolve()
    except (OSError, ValueError, TypeError):
        pass
    return base


def log(msg: str) -> None:
    print(f"[supervisor {datetime.now():%H:%M:%S}] {msg}", flush=True)


def current_build(root: Path = ROOT) -> int:
    try:
        return int(json.loads((root / "BUILD.json").read_text(encoding="utf-8")).get("build", 0))
    except (OSError, ValueError):
        return 0


def write_result(dd: Path, **kw) -> None:
    p = dd / "updates" / "last_result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**kw, "at": datetime.now().isoformat(timespec="seconds")}, indent=1), encoding="utf-8")


def backup_db(dd: Path, label: str) -> Path | None:
    """Verified snapshot before an update. Raises RuntimeError if the copy is not sound (the update is then skipped)."""
    src = dd / "hsco.db"
    if not src.exists():
        return None
    dst = dd / "backups" / f"pre-update-{label}-{datetime.now():%Y%m%d_%H%M%S}.db"
    dst.parent.mkdir(parents=True, exist_ok=True)
    a, b = sqlite3.connect(src, timeout=60), sqlite3.connect(dst)
    try:
        a.backup(b)
        check = b.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        a.close()
        b.close()
    if check != "ok":
        raise RuntimeError(f"the pre-update database backup failed its integrity check ({check}) — update not applied")
    olds = sorted((dd / "backups").glob("pre-update-*.db"), key=lambda p: p.stat().st_mtime)
    for old in olds[:-PRE_UPDATE_KEEP]:
        old.unlink()
    return dst


def _is_protected(name: str, root: Path, dd: Path) -> bool:
    target = (root / name).resolve()
    return name.lower() in PROTECTED or target == dd or dd.is_relative_to(target)


def apply_staged(dd: Path, root: Path = ROOT, pip: bool = True) -> dict | None:
    """Swap in the staged release. Returns {"rollback": dir, "info": {...}} or None if nothing staged.
    On failure the previous code is restored and {"error": ...} is returned."""
    staged = dd / "updates" / "staged"
    ready = staged / "READY"
    if not ready.exists():
        return None
    info = json.loads(ready.read_text(encoding="utf-8"))
    frm = current_build(root)
    rb = dd / "updates" / "rollback" / f"build{frm}-{datetime.now():%Y%m%d_%H%M%S}"
    rb.mkdir(parents=True, exist_ok=True)
    log(f"applying update build {frm} → {info.get('build')} (v{info.get('version')})")
    new_items = sorted(p.name for p in staged.iterdir() if p.name != "READY")
    bad = [n for n in new_items if _is_protected(n, root, dd)]
    try:
        if bad:
            raise RuntimeError(f"update package tries to replace protected items ({', '.join(bad)}) — refused")
        backup_db(dd, f"build{info.get('build')}")
    except Exception as ex:  # noqa: BLE001 — nothing has been touched yet
        log(f"update refused: {ex}")
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(rb, ignore_errors=True)
        write_result(dd, status="failed", from_build=frm, to_build=info.get("build"), version=info.get("version"),
                     error=str(ex))
        return {"error": str(ex)}
    moved, added = [], []
    try:
        for name in sorted(set(new_items) | set(CORE)):
            src = root / name
            if src.exists():
                shutil.move(str(src), str(rb / name))
                moved.append(name)
        for name in new_items:
            shutil.move(str(staged / name), str(root / name))
            added.append(name)
        (rb / "MANIFEST.json").write_text(json.dumps({"moved": moved, "added": added, "info": info, "from": frm}),
                                          encoding="utf-8")
        old_req, new_req = rb / "requirements.txt", root / "requirements.txt"
        if pip and new_req.exists() and (not old_req.exists() or old_req.read_bytes() != new_req.read_bytes()):
            log("requirements changed → pip install")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(new_req)], cwd=root)
            if r.returncode != 0:
                raise RuntimeError("pip install failed (does the server have internet access?)")
        shutil.rmtree(staged, ignore_errors=True)
        return {"rollback": rb, "info": info, "from": frm}
    except Exception as ex:  # noqa: BLE001
        log(f"apply failed: {ex} → restoring previous version")
        _restore(root, rb, added, moved)
        shutil.rmtree(staged, ignore_errors=True)
        write_result(dd, status="failed", from_build=frm, to_build=info.get("build"), version=info.get("version"),
                     error=str(ex))
        return {"error": str(ex)}


def _restore(root: Path, rb: Path, added: list, moved: list) -> None:
    for name in added:
        p = root / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for name in moved:
        if (rb / name).exists():
            shutil.move(str(rb / name), str(root / name))


def rollback(root: Path, rb: Path) -> None:
    m = json.loads((rb / "MANIFEST.json").read_text(encoding="utf-8"))
    _restore(root, rb, m["added"], m["moved"])


def wait_healthy(child: subprocess.Popen, url: str, build: int, timeout: float = 90) -> bool:
    ctx = ssl._create_unverified_context()  # local self-signed certificate
    end = time.time() + timeout
    while time.time() < end:
        if child.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=3, context=ctx) as r:  # noqa: S310 - localhost
                if int(json.loads(r.read().decode()).get("build", -1)) == int(build):
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(1)
    return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-supervisor" in argv or os.environ.get("HSCO_SUPERVISED") == "1":
        sys.path.insert(0, str(ROOT))
        from app.serve import main as serve_main
        return serve_main([a for a in argv if a != "--no-supervisor"])
    dev = "--dev" in argv
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else int(os.environ.get("HSCO_PORT", "8443"))
    health = f"{'http' if dev else 'https'}://127.0.0.1:{port}/healthz"
    env = {**os.environ, "HSCO_SUPERVISED": "1", "HSCO_SUPERVISOR_PID": str(os.getpid()),
           "HSCO_SUPERVISOR_FEATURES": "datadir"}     # this supervisor follows a moved data folder (v1.12)
    backoff = 2
    while True:
        dd = data_dir()                                # re-read: the data folder may have been moved from the app
        applied = apply_staged(dd)
        started = time.time()
        child = subprocess.Popen([sys.executable, "-m", "app.serve", *argv], cwd=str(ROOT), env=env)
        if applied and "rollback" in applied:
            to = int(applied["info"].get("build", 0))
            if wait_healthy(child, health, to):
                log(f"build {to} is running")
                write_result(dd, status="applied", from_build=applied["from"], to_build=to,
                             version=applied["info"].get("version"))
            else:
                log("new build did not start → rolling back")
                child.terminate()
                try:
                    child.wait(15)
                except subprocess.TimeoutExpired:
                    child.kill()
                rollback(ROOT, applied["rollback"])
                write_result(dd, status="rolled_back", from_build=applied["from"], to_build=to,
                             version=applied["info"].get("version"),
                             error="The new version did not start; the previous version was restored.")
                continue
        try:
            code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            child.wait()
            return 0
        if code == RESTART:
            log("restart requested")
            continue
        if code == 0:
            return 0
        if time.time() - started > 120:
            backoff = 2
        log(f"server exited with code {code}; restarting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    sys.exit(main())
