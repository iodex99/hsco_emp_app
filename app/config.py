"""Runtime configuration. Everything lives under DATA_DIR (default ./data) on the server's own disk."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
# written when the data folder is moved from the app (services/datadir.py); never replaced by an update
POINTER = BASE_DIR / "data_location.json"


def same_path(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, TypeError, ValueError):
        return False


def base_data_dir() -> Path:
    """Where the data lives unless it was moved: HSCO_DATA_DIR (installer) or <app>/data."""
    return Path(os.environ.get("HSCO_DATA_DIR", BASE_DIR / "data")).resolve()


def resolve_data_dir() -> Path:
    """The data folder: the base, or where it was moved to (data_location.json) — the move is followed only while the
    base is still the one it was moved from (a test or a fresh install never follows an old pointer)."""
    base = base_data_dir()
    try:
        j = json.loads(POINTER.read_text(encoding="utf-8"))
        if j.get("dir") and same_path(j.get("base", ""), base):
            return Path(j["dir"]).resolve()
    except (OSError, ValueError, TypeError):
        pass
    return base


class Config:
    def __init__(self) -> None:
        self.data_dir = resolve_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        (self.data_dir / "exports").mkdir(exist_ok=True)
        (self.data_dir / "backups").mkdir(exist_ok=True)
        self.db_url = os.environ.get("HSCO_DB_URL", f"sqlite:///{(self.data_dir / 'hsco.db').as_posix()}")
        self.secret_key = self._secret()
        self.org_name = os.environ.get("HSCO_ORG_NAME", "Hasmukh Shah & Co. LLP")
        self.secure_cookies = os.environ.get("HSCO_INSECURE_COOKIES", "0") != "1"
        self.session_hours = float(os.environ.get("HSCO_SESSION_HOURS", "10"))
        self.idle_minutes = float(os.environ.get("HSCO_IDLE_MINUTES", "60"))
        self.max_upload_mb = int(os.environ.get("HSCO_MAX_UPLOAD_MB", "40"))

    def _secret(self) -> bytes:
        p = self.data_dir / "secret.key"
        if not p.exists():
            p.write_bytes(secrets.token_bytes(64))
        return p.read_bytes()


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:  # tests
    global _config
    _config = None
