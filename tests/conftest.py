import os
import sys

# tests must never call GitHub: no background update checks
os.environ.setdefault("HSCO_DISABLE_UPDATE_THREAD", "1")
os.environ.setdefault("HSCO_DISABLE_GEO_WARMUP", "1")
from pathlib import Path

import pyotp
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REF = ROOT / "reference"
HAS_REF = (REF / "Attendance Muster Report.xlsx").exists()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("HSCO_DATA_DIR", str(d))
    monkeypatch.setenv("HSCO_INSECURE_COOKIES", "1")
    monkeypatch.setenv("HSCO_ORIGIN", "http://testserver")
    monkeypatch.setenv("HSCO_RP_ID", "testserver")
    from app.config import reset_config
    from app.db import reset_engine
    reset_config()
    reset_engine()
    yield d
    reset_engine()
    reset_config()


@pytest.fixture()
def fixtures(tmp_path):
    from tests.fixtures.make_fixtures import write_all
    return write_all(tmp_path / "fx")


@pytest.fixture()
def client(data_dir):
    import warnings
    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


ADMIN_PW = "Adm1n!Secure#2026"


def make_user(username="admin", role="admin", pw=ADMIN_PW, email=""):
    from app.db import session_scope
    from app.models import User
    from app.security.core import hash_password
    with session_scope() as db:
        db.add(User(username=username, full_name=username, role=role, email=email, password_hash=hash_password(pw)))


def csrf_of(client, path="/security"):
    import re
    html = client.get(path).text
    m = re.search(r'name="csrf" content="([^"]+)"', html) or re.search(r'name="csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


def login_full(client, username="admin", pw=ADMIN_PW):
    """Password + forced TOTP enrolment (first time) or TOTP challenge. Returns the TOTP secret."""
    import re
    r = client.post("/login", data={"username": username, "password": pw, "next": "/"}, follow_redirects=True)
    if "Set up two-step verification" in r.text or "/security/enrol" in str(r.url):
        secret = re.search(r'id="totp-secret">([A-Z2-7]+)<', r.text).group(1)
        csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
        r2 = client.post("/security/totp/confirm", data={"code": pyotp.TOTP(secret).now(), "label": "phone", "csrf": csrf})
        assert "recovery codes" in r2.text.lower(), r2.text[:500]
        client.secret = secret
        return secret
    secret = getattr(client, "secret", None)
    csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    import time
    code = pyotp.TOTP(secret).at(time.time() + 30)  # next step (current step may be used already)
    r2 = client.post("/mfa/totp", data={"code": code, "next": "/", "csrf": csrf}, follow_redirects=True)
    assert r2.status_code == 200
    return secret
