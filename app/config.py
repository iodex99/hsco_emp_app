"""Runtime configuration. Everything lives under DATA_DIR (default ./data) on the server's own disk."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent


class Config:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("HSCO_DATA_DIR", BASE_DIR / "data")).resolve()
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
