"""Unit/integration tests for the self-update pieces (no GitHub, no browser)."""
import hashlib
import http.server
import json
import os
import sqlite3
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_release  # noqa: E402
import run_server as sup  # noqa: E402


# ----------------------------------------------------------------- mock release server

class Releases:
    """Tiny stand-in for GitHub: /latest JSON, /zip (streamed slowly), /sha."""

    def __init__(self, zpath: Path, build: int, version: str, sha: str | None = None, delay: float = 0.0):
        self.zpath, self.build, self.version, self.delay = zpath, build, version, delay
        self.sha = sha or hashlib.sha256(zpath.read_bytes()).hexdigest()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/latest":
                    body = json.dumps({
                        "tag_name": f"build-{outer.build}", "name": f"v{outer.version} (build {outer.build})",
                        "body": "Test release notes", "published_at": "2026-11-17T10:00:00Z",
                        "assets": [{"name": outer.zpath.name, "size": outer.zpath.stat().st_size,
                                    "browser_download_url": f"{outer.base}/zip"},
                                   {"name": outer.zpath.name + ".sha256", "size": 80,
                                    "browser_download_url": f"{outer.base}/sha"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/zip":
                    data = outer.zpath.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    step = max(1, len(data) // 20)
                    for i in range(0, len(data), step):
                        self.wfile.write(data[i:i + step])
                        self.wfile.flush()
                        time.sleep(outer.delay)
                elif self.path == "/sha":
                    body = f"{outer.sha}  {outer.zpath.name}\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


# ----------------------------------------------------------------- package

def test_release_package_contents(tmp_path):
    z, digest = build_release.build(42, tmp_path, commit="abc123")
    names = zipfile.ZipFile(z).namelist()
    assert {"app/main.py", "run_server.py", "requirements.txt", "BUILD.json", "app/static/icons.svg",
            "Install.cmd", "deploy/install_windows.ps1"} <= set(names)                # one-click install (v1.9.1)
    assert not any(n.startswith(("data/", "reference/", "tests/", ".git", ".venv")) for n in names)
    assert not any(n.endswith(".pyc") or "__pycache__" in n for n in names)
    meta = json.loads(zipfile.ZipFile(z).read("BUILD.json"))
    assert meta["build"] == 42 and meta["commit"] == "abc123" and meta["version"] == build_release.product_version()
    assert (tmp_path / (z.name + ".sha256")).read_text().split()[0] == digest == hashlib.sha256(z.read_bytes()).hexdigest()
    # version override rewrites app/__init__.py inside the package
    z2, _ = build_release.build(43, tmp_path, version="9.9.9")
    assert b'__version__ = "9.9.9"' in zipfile.ZipFile(z2).read("app/__init__.py")


def test_parse_release_and_validation(tmp_path, data_dir):
    from app import updater
    info = updater.parse_release({"tag_name": "build-7", "name": "v1.3.0 (build 7)", "body": "notes",
                                  "assets": [{"name": "x.zip", "size": 10, "browser_download_url": "https://h/x.zip"},
                                             {"name": "x.zip.sha256", "browser_download_url": "https://h/x.zip.sha256"}]})
    assert info["build"] == 7 and info["version"] == "1.3.0" and info["sha_url"].endswith(".sha256")
    with pytest.raises(ValueError):
        updater.parse_release({"tag_name": "v1.0", "assets": []})
    good, _ = build_release.build(5, tmp_path)
    assert updater.validate_zip(good, 5)["build"] == 5
    with pytest.raises(ValueError, match="expected 6"):
        updater.validate_zip(good, 6)
    for bad_name, content in (("../evil.py", b"x"), ("data/hsco.db", b"x")):
        bad = tmp_path / f"bad{len(bad_name)}.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as dst:
            for n in src.namelist():
                dst.writestr(n, src.read(n))
            dst.writestr(bad_name, content)
        with pytest.raises(ValueError):
            updater.validate_zip(bad, 5)
    inc = tmp_path / "inc.zip"
    with zipfile.ZipFile(inc, "w") as z:
        z.writestr("BUILD.json", json.dumps({"build": 5}))
    with pytest.raises(ValueError, match="incomplete"):
        updater.validate_zip(inc, 5)


# ----------------------------------------------------------------- download / verify / stage

def _wait(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_download_progress_checksum_and_stage(tmp_path, data_dir, monkeypatch):
    from app import updater
    from app.config import get_config
    monkeypatch.setenv("HSCO_UPDATES_FORCE", "1")         # the test runs inside a git checkout
    z, _ = build_release.build(8, tmp_path / "dist", version="9.9.9")
    # 1) tampered package → checksum mismatch, nothing staged
    srv = Releases(z, 8, "9.9.9", sha="0" * 64, delay=0.01)
    monkeypatch.setenv("HSCO_UPDATE_URL", srv.base + "/latest")
    st = updater.check()
    assert st["available"] and st["latest"]["build"] == 8 and st["latest"]["version"] == "9.9.9"
    updater.install()
    seen = []
    st = _wait(lambda: (seen.append(updater.status()["percent"]) or updater.status())
               if updater.status()["phase"] in ("error", "staged") else None)
    assert st["phase"] == "error" and "Checksum mismatch" in st["error"]
    assert not (get_config().data_dir / "updates" / "staged").exists()
    srv.close()
    # 2) genuine package → downloaded with progress, verified, staged (not supervised → waits for restart)
    srv = Releases(z, 8, "9.9.9", delay=0.02)
    monkeypatch.setenv("HSCO_UPDATE_URL", srv.base + "/latest")
    updater.check()
    updater.install(actor="tester")
    pcts = []

    def done():
        s = updater.status()
        pcts.append(s["percent"])
        return s if s["phase"] in ("staged", "error") else None
    st = _wait(done)
    assert st["phase"] == "staged", st
    assert any(0 < p < 100 for p in pcts), "progress should be reported while downloading"
    ready = json.loads((get_config().data_dir / "updates" / "staged" / "READY").read_text())
    assert ready["build"] == 8 and ready["by"] == "tester"
    assert (get_config().data_dir / "updates" / "staged" / "app" / "main.py").exists()
    with pytest.raises(RuntimeError, match="progress|up to date|already"):
        updater._state["phase"] = "downloading"
        updater.install()
    updater._state["phase"] = "idle"
    srv.close()


def test_source_checkout_never_self_updates(data_dir, monkeypatch):
    from app import updater
    monkeypatch.delenv("HSCO_UPDATES_FORCE", raising=False)
    assert updater.source_checkout()  # this repo has .git
    with pytest.raises(RuntimeError, match="git pull"):
        updater.install({"build": 999})


# ----------------------------------------------------------------- supervisor apply / rollback

def _fake_install(tmp_path: Path, build: int) -> Path:
    root = tmp_path / "install"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("OLD")
    (root / "run_server.py").write_text("OLD SUPERVISOR")
    (root / "requirements.txt").write_text("fastapi\n")
    (root / "BUILD.json").write_text(json.dumps({"build": build}))
    return root


def _stage(dd: Path, zpath: Path, build: int):
    staged = dd / "updates" / "staged"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(staged)
    (staged / "READY").write_text(json.dumps({"build": build, "version": "9.9.9"}))


def test_supervisor_apply_and_rollback(tmp_path):
    root = _fake_install(tmp_path, 3)
    dd = tmp_path / "data"
    dd.mkdir()
    with sqlite3.connect(dd / "hsco.db") as con:
        con.execute("create table t(x)")
        con.execute("insert into t values (1)")
    z, _ = build_release.build(4, tmp_path / "dist", version="9.9.9")
    _stage(dd, z, 4)
    res = sup.apply_staged(dd, root, pip=False)
    assert res and "rollback" in res and res["from"] == 3
    assert sup.current_build(root) == 4
    assert (root / "app" / "main.py").read_text() != "OLD" and (root / "app" / "serve.py").exists()
    assert (res["rollback"] / "app" / "main.py").read_text() == "OLD"
    backups = list((dd / "backups").glob("pre-update-build4-*.db"))
    assert backups and sqlite3.connect(backups[0]).execute("select x from t").fetchone() == (1,)
    assert not (dd / "updates" / "staged").exists()
    assert (dd / "hsco.db").exists()                       # data untouched
    sup.rollback(root, res["rollback"])
    assert sup.current_build(root) == 3 and (root / "app" / "main.py").read_text() == "OLD"
    assert (root / "run_server.py").read_text() == "OLD SUPERVISOR"
    assert sup.apply_staged(dd, root, pip=False) is None   # nothing staged any more


def test_supervisor_never_touches_data_even_if_a_package_tries(tmp_path):
    """Defence in depth: even a package that slipped past validation can't replace data, .venv or certificates."""
    root = _fake_install(tmp_path, 3)
    dd = root / "data"                                   # the PC-install layout: data inside the app folder
    dd.mkdir()
    with sqlite3.connect(dd / "hsco.db") as con:
        con.execute("create table t(x)")
        con.execute("insert into t values (42)")
    (dd / "secret.key").write_text("KEY")
    staged = dd / "updates" / "staged"
    (staged / "app").mkdir(parents=True)
    (staged / "app" / "main.py").write_text("NEW")
    (staged / "data").mkdir()
    (staged / "data" / "hsco.db").write_text("EVIL")
    (staged / "READY").write_text(json.dumps({"build": 4, "version": "9.9.9"}))
    res = sup.apply_staged(dd, root, pip=False)
    assert res and "error" in res and "protected" in res["error"]
    assert (root / "app" / "main.py").read_text() == "OLD" and sup.current_build(root) == 3      # code untouched
    with sqlite3.connect(dd / "hsco.db") as con:
        assert con.execute("select x from t").fetchone() == (42,)                                   # data untouched
    assert (dd / "secret.key").read_text() == "KEY" and not staged.exists()
    assert json.loads((dd / "updates" / "last_result.json").read_text())["status"] == "failed"


def test_pre_update_backups_are_verified_and_pruned(tmp_path):
    root = _fake_install(tmp_path, 3)
    dd = tmp_path / "data"
    (dd / "backups").mkdir(parents=True)
    with sqlite3.connect(dd / "hsco.db") as con:
        con.execute("create table t(x)")
    for i in range(14):
        p = dd / "backups" / f"pre-update-build{i}-20260101_0000{i:02d}.db"
        p.write_text("old")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    out = sup.backup_db(dd, "build99")
    assert out.exists() and len(list((dd / "backups").glob("pre-update-*.db"))) == sup.PRE_UPDATE_KEEP
    with sqlite3.connect(out) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
