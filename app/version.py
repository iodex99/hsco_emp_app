"""What is running: product version + CI build stamp.

Release zips contain BUILD.json at the install root (written by scripts/build_release.py in CI):
{"version": "1.2.0", "build": 17, "commit": "abc123…", "date": "2026-11-17T10:00:00Z"}
A source checkout has no BUILD.json → build 0, channel "source".
"""
from __future__ import annotations

import json

from app import __version__
from app.config import BASE_DIR


def current() -> dict:
    p = BASE_DIR / "BUILD.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {"version": str(d.get("version") or __version__), "build": int(d.get("build") or 0),
                    "commit": str(d.get("commit") or "")[:12], "date": d.get("date", ""), "channel": "release"}
        except (ValueError, OSError):
            pass
    return {"version": __version__, "build": 0, "commit": "", "date": "", "channel": "source"}
