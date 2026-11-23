"""Moving the data folder from the app (v1.12): where the app and the supervisor look, the checks, the copy."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from tests.conftest import csrf_of, login_full, make_user


@pytest.fixture()
def pointer(tmp_path, monkeypatch):
    """data_location.json in a temp app folder (never the real one)."""
    from app import config as C
    p = tmp_path / "app_root" / "data_location.json"
    p.parent.mkdir()
    monkeypatch.setattr(C, "POINTER", p)
    return p


def test_the_move_is_followed_only_from_the_folder_it_left(data_dir, pointer, tmp_path):
    from app import config as C
    assert C.resolve_data_dir() == Path(data_dir).resolve()                       # nothing moved
    pointer.write_text(json.dumps({"base": str(data_dir), "dir": str(tmp_path / "moved")}), encoding="utf-8")
    assert C.resolve_data_dir() == (tmp_path / "moved").resolve()                 # moved from this base
    pointer.write_text(json.dumps({"base": str(tmp_path / "elsewhere"), "dir": str(tmp_path / "moved")}), encoding="utf-8")
    assert C.resolve_data_dir() == Path(data_dir).resolve()                      # another base: ignored
    pointer.write_text("not json", encoding="utf-8")
    assert C.resolve_data_dir() == Path(data_dir).resolve()                      # unreadable: ignored


def test_supervisor_reads_the_same_pointer_and_updates_never_replace_it(tmp_path, monkeypatch):
    import run_server
    monkeypatch.setattr(run_server, "ROOT", tmp_path)
    monkeypatch.setenv("HSCO_DATA_DIR", str(tmp_path / "base"))
    assert run_server.data_dir() == (tmp_path / "base").resolve()
    (tmp_path / "data_location.json").write_text(json.dumps({"base": str(tmp_path / "base"), "dir": str(tmp_path / "new")}),
                                                 encoding="utf-8")
    assert run_server.data_dir() == (tmp_path / "new").resolve()
    assert "data_location.json" in run_server.PROTECTED
    src = Path(run_server.__file__).read_text(encoding="utf-8")
    assert "dd = data_dir()                                # re-read" in src and '"HSCO_SUPERVISOR_FEATURES": "datadir"' in src


def _signed_in(client):
    make_user()
    login_full(client)
    return client


def test_move_checks_the_target(client, data_dir, pointer, tmp_path):
    c = _signed_in(client)
    page = c.get("/settings").text
    assert 'id="data-folder"' in page and str(Path(data_dir).resolve()) in page
    csrf = csrf_of(c, "/settings")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x.txt").write_text("x")
    for target, msg in (("", "Enter the folder"), ("relative\\folder", "full path"),
                        (str(Path(data_dir) / "inner"), "outside the current data folder"),
                        (str(full), "new or empty"), (str(Path(data_dir).resolve()), "already is")):
        r = c.post("/settings/data-folder", data={"csrf": csrf, "target": target}, follow_redirects=True)
        assert msg in r.text, (target, msg)
    assert not pointer.exists()


def test_move_copies_everything_and_points_the_app_there(client, data_dir, pointer, tmp_path, monkeypatch):
    from app import config as C
    c = _signed_in(client)
    (Path(data_dir) / "uploads" / "keep.txt").write_text("upload")
    (Path(data_dir) / "updates" / "staged").mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / "updates" / "staged" / "big.bin").write_text("transient")
    target = tmp_path / "D" / "HSCOData"
    r = c.post("/settings/data-folder", data={"csrf": csrf_of(c, "/settings"), "target": str(target)},
               follow_redirects=True)
    assert "Data copied to" in r.text and "Restart the app" in r.text            # not supervised: restart by hand
    assert (target / "hsco.db").exists() and (target / "secret.key").exists() and (target / "uploads" / "keep.txt").exists()
    assert not (target / "updates" / "staged").exists()                         # transient update files stay behind
    db = sqlite3.connect(target / "hsco.db")
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT username FROM users").fetchone()[0] == "admin"
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'settings.data_folder'").fetchone()[0] == 1
    db.close()
    j = json.loads(pointer.read_text(encoding="utf-8"))
    assert C.same_path(j["base"], data_dir) and C.same_path(j["dir"], target)
    assert C.resolve_data_dir() == target.resolve()                              # the next start uses it
    assert (Path(data_dir) / "MOVED.txt").exists() and (Path(data_dir) / "hsco.db").exists()   # old folder kept


def test_move_restarts_under_a_supervisor_that_knows_moves(client, data_dir, pointer, tmp_path, monkeypatch):
    from app import updater
    c = _signed_in(client)
    calls = []
    monkeypatch.setattr(updater, "_restart_hook", lambda: calls.append(1))
    monkeypatch.setenv("HSCO_SUPERVISED", "1")
    monkeypatch.setenv("HSCO_SUPERVISOR_FEATURES", "datadir")
    r = c.post("/settings/data-folder", data={"csrf": csrf_of(c, "/settings"), "target": str(tmp_path / "new")},
               follow_redirects=True)
    assert "restarts on it now" in r.text
    for _ in range(40):
        if calls:
            break
        time.sleep(0.1)
    assert calls == [1]


def test_older_supervisor_or_hr_user_cannot_move(client, data_dir, pointer, tmp_path, monkeypatch):
    c = _signed_in(client)
    monkeypatch.setenv("HSCO_SUPERVISED", "1")
    monkeypatch.delenv("HSCO_SUPERVISOR_FEATURES", raising=False)
    assert "Restart the server PC once" in c.get("/settings").text
    r = c.post("/settings/data-folder", data={"csrf": csrf_of(c, "/settings"), "target": str(tmp_path / "new")},
               follow_redirects=True)
    assert "Restart the server PC once" in r.text and not pointer.exists()
    monkeypatch.delenv("HSCO_SUPERVISED")
    make_user("hr1", "hr", "Hr!Person#2026x")
    c.post("/logout", data={"csrf": csrf_of(c)})
    login_full(c, "hr1", "Hr!Person#2026x")
    assert c.post("/settings/data-folder", data={"csrf": csrf_of(c, "/"), "target": str(tmp_path / "x")}).status_code == 403


def test_settings_warns_when_the_offline_place_names_are_missing(client, tmp_path, monkeypatch):
    """v1.12.1: an install without app/data/in_places.csv.gz shows coordinates — Settings says so plainly."""
    from app.services import geo
    c = _signed_in(client)
    assert "place-name file" not in c.get("/settings").text
    monkeypatch.setattr(geo, "SRC", tmp_path / "missing.csv.gz")
    assert "place-name file (app" in c.get("/settings").text
