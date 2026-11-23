"""Regression tests (v1.1) for the "database is locked" HTTP 500s seen in manual use (first 2FA code, quick search).

Root cause: the auth dependency was `async def` and wrote the session's `last_seen` with blocking SQLite calls ON
THE EVENT LOOP. When request A held SQLite's write lock (its commit is scheduled via the event loop) and request B's
auth check started writing, B blocked the whole loop waiting for the lock, so A could never commit → deadlock until
SQLite's 30 s busy timeout → 500. Fix: every DB call runs in a worker thread (web.py `_load` via anyio.to_thread,
sync endpoints, async endpoints hand off after reading the body), DB cleanup is shielded from cancellation, and the
middleware is pure ASGI. These tests run a real uvicorn server and real overlapping / aborted connections.
"""
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

from tests.conftest import make_user


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def live(tmp_path, monkeypatch):
    import uvicorn
    port = _free_port()
    d = tmp_path / "data"
    for k, v in {"HSCO_DATA_DIR": str(d), "HSCO_INSECURE_COOKIES": "1"}.items():
        monkeypatch.setenv(k, v)
    from app.config import reset_config
    from app.db import reset_engine
    reset_config()
    reset_engine()
    from app.main import create_app
    app = create_app()
    from app.db import session_scope
    from app.services.imports import import_file
    from tests.fixtures.make_fixtures import write_all
    fx = write_all(tmp_path / "fx")
    for k in ("master", "doj", "swipes", "muster"):
        with session_scope() as db:
            import_file(db, fx[k].read_bytes(), fx[k].name, None)
    make_user()
    # a signed-in session whose last_seen is old -> the next request writes it
    from app.models import User, UserSession
    from app.security import core as sec
    from sqlalchemy import select
    tokens = []
    with session_scope() as db:
        u = db.scalar(select(User))
        for _ in range(2):
            tokens.append(sec.create_session(db, u, "127.0.0.1", "test"))
        for s in db.scalars(select(UserSession)):
            s.last_seen = datetime.now() - timedelta(minutes=5)
    # make the overview slow so we can hang up mid-request
    import app.routes.pages as pages
    orig = pages.MX.period_metrics

    def slow(*a, **kw):
        time.sleep(1.5)
        return orig(*a, **kw)
    monkeypatch.setattr(pages.MX, "period_metrics", slow)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield port, tokens, d / "hsco.db"
    srv.should_exit = True
    th.join(timeout=5)
    reset_engine()
    reset_config()


def test_client_disconnect_does_not_lock_database(live):
    port, tokens, dbfile = live
    token = tokens[0]
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET / HTTP/1.1\r\nHost: localhost\r\nCookie: hsco_session={token}\r\n\r\n".encode())
    time.sleep(0.4)          # request is inside the slow endpoint, session write already flushed
    s.close()                # browser navigates away
    time.sleep(2.5)          # let the server finish / clean up
    con = sqlite3.connect(dbfile, timeout=3)
    try:
        con.execute("BEGIN IMMEDIATE")   # needs the write lock
        con.execute("ROLLBACK")
    except sqlite3.OperationalError as ex:  # pragma: no cover - this is the bug
        pytest.fail(f"database left locked after client disconnect: {ex}")
    finally:
        con.close()


def test_overlapping_requests_do_not_deadlock(live):
    """Request A is slow and holds a write; request B starts meanwhile. Both must finish promptly (old code: B hung
    for SQLite's 30 s busy timeout and returned HTTP 500)."""
    import http.client
    port, tokens, _ = live
    out = {}

    def get(name, path, token, delay):
        time.sleep(delay)
        t0 = time.time()
        con = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
        con.request("GET", path, headers={"Cookie": f"hsco_session={token}"})
        r = con.getresponse()
        r.read()
        out[name] = (r.status, round(time.time() - t0, 2))
        con.close()
    ts = [threading.Thread(target=get, args=("A", "/", tokens[0], 0)),
          threading.Thread(target=get, args=("B", "/employees", tokens[1], 0.4))]
    [t.start() for t in ts]
    [t.join(timeout=15) for t in ts]
    assert out.get("A", (None,))[0] == 200 and out.get("B", (None,))[0] == 200, out
    assert out["B"][1] < 6, f"request B waited {out['B'][1]} s — event loop was blocked by a DB lock wait"
