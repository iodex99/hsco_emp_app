"""Build the release package that servers download when they update.

  python scripts/build_release.py --build 17 --commit <sha> --out dist

Produces dist/hsco_emp_app-v<version>-build<n>.zip and .zip.sha256. The zip holds the code at its root plus
BUILD.json; it never contains data/, reference/, .venv/, tests or git metadata. Used by CI
(.github/workflows/release.yml) and by the update tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["app", "deploy", "docs", "run_server.py", "Install.cmd", "requirements.txt", "requirements-dev.txt", "README.md",
           "CLAUDE.md"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".db", ".log"}


def product_version(root: Path = ROOT) -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "app" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1)


def files(root: Path):
    for item in INCLUDE:
        p = root / item
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not (set(f.relative_to(root).parts) & SKIP_PARTS) and f.suffix not in SKIP_SUFFIX:
                    yield f


def build(build_no: int, out: Path, commit: str = "", root: Path = ROOT, version: str | None = None,
          mutate=None) -> tuple[Path, str]:
    """Write the zip + sha256 file; returns (zip path, sha256 hex). `mutate(name, bytes) -> bytes` lets tests
    produce deliberately broken packages."""
    version = version or product_version(root)
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"hsco_emp_app-v{version}-build{build_no}.zip"
    meta = {"version": version, "build": int(build_no), "commit": commit,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files(root):
            name = f.relative_to(root).as_posix()
            data = f.read_bytes()
            if name == "app/__init__.py":
                data = re.sub(rb'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"'.encode(), data)
            if mutate:
                data = mutate(name, data)
            z.writestr(name, data)
        z.writestr("BUILD.json", json.dumps(meta, indent=1))
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (out / (zpath.name + ".sha256")).write_text(f"{digest}  {zpath.name}\n", encoding="utf-8")
    return zpath, digest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=int, required=True)
    ap.add_argument("--commit", default="")
    ap.add_argument("--out", default="dist")
    a = ap.parse_args(argv)
    z, d = build(a.build, Path(a.out), a.commit)
    print(f"{z} sha256={d} version={product_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
