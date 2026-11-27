"""Firm logo (Settings → Branding). Stored with the data in DATA_DIR/branding — never in the code or the public repo.

Accepted: SVG (sanitised: shapes, gradients, clip paths and text only — no scripts, event handlers, external links,
embedded documents or entity declarations), PNG, JPEG or WebP; up to 1 MB. Shown in the sidebar, the sign-in
screens and as the browser-tab icon. For an SVG logo the tab icon is its left square-ish
mark (a box twice as wide as high — e.g. the red "HSCo" block of a mark + wordmark logo).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from app.config import get_config

MAX_BYTES = 1_000_000
SVG_NS = "http://www.w3.org/2000/svg"
ALLOWED_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs", "clippath",
                "lineargradient", "radialgradient", "stop", "title", "desc", "mask", "use", "symbol", "text",
                "tspan", "style", "pattern", "filter", "fegaussianblur", "feoffset", "feblend", "feflood",
                "fecomposite", "femerge", "femergenode", "fecolormatrix", "metadata"}
RASTER = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"RIFF": ("webp", "image/webp")}
MIME = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _dir() -> Path:
    d = get_config().data_dir / "branding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_svg(data: bytes) -> str:
    """Return a safe SVG document or raise ValueError explaining why it was refused."""
    text = data.decode("utf-8-sig", errors="strict")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("SVG with DOCTYPE/ENTITY declarations is not accepted")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        raise ValueError(f"not a valid SVG file ({ex})") from ex
    if root.tag.split("}")[-1].lower() != "svg":
        raise ValueError("the file is not an SVG image")
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag not in ALLOWED_TAGS:
            raise ValueError(f"SVG element <{tag}> is not allowed in a logo")
        for k, v in el.attrib.items():
            name = k.split("}")[-1].lower()
            if name.startswith("on"):
                raise ValueError("SVG event handlers are not allowed")
            if name == "href" and not v.strip().startswith("#"):
                raise ValueError("SVG links to other files are not allowed")
            if re.search(r"url\(\s*['\"]?(?!#)", v, re.I) or re.search(r"javascript:|expression\(", v, re.I):
                raise ValueError("SVG references to other files are not allowed")
        if tag == "style" and el.text and (re.search(r"@import|url\(\s*['\"]?(?!#)|javascript:|expression\(", el.text, re.I)):
            raise ValueError("SVG styles that load other files are not allowed")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")


def save(data: bytes, filename: str = "") -> str:
    """Validate and store a new logo (replacing any previous one). Returns the stored kind (svg/png/jpg/webp)."""
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError("the logo must be 1 MB or smaller")
    kind = next((k for magic, (k, _) in RASTER.items() if data.startswith(magic)), None)
    if kind == "webp" and data[8:12] != b"WEBP":
        kind = None
    if kind is None:
        head = data[:600].decode("utf-8", errors="ignore").lower()
        if "<svg" not in head and not filename.lower().endswith(".svg"):
            raise ValueError("use an SVG, PNG, JPEG or WebP image")
        content = sanitize_svg(data).encode("utf-8")
        kind = "svg"
    else:
        content = data
    d = _dir()
    for old in d.glob("logo.*"):
        old.unlink()
    (d / RASTER_NAME).unlink(missing_ok=True)       # the PNG copy is made again from the new logo
    (d / f"logo.{kind}").write_bytes(content)
    return kind


def remove() -> bool:
    found = False
    for old in _dir().glob("logo.*"):
        old.unlink()
        found = True
    (_dir() / RASTER_NAME).unlink(missing_ok=True)
    return found


# ----------------------------------------------------------------------------- PNG copy for the Excel workbooks (v1.15)
# Excel needs a raster image; the server has no image library, so the browser draws the logo (SVG / WebP / JPEG) on
# a canvas once and sends the PNG (base.html). A PNG logo is used as it is.
RASTER_NAME = "raster.png"
MAX_RASTER = 2 * 1024 * 1024


def png_size(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG, or None if it is not one."""
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return (w, h) if 0 < w <= 8000 and 0 < h <= 8000 else None


def save_raster(data: bytes) -> tuple[int, int]:
    if not _path():
        raise ValueError("no logo")
    if len(data) > MAX_RASTER:
        raise ValueError("too large")
    size = png_size(data)
    if not size:
        raise ValueError("not a PNG image")
    (_dir() / RASTER_NAME).write_bytes(data)
    return size


def png() -> Optional[tuple[bytes, int, int]]:
    """The logo as PNG for the Excel workbooks: (bytes, width, height), or None (no logo / no PNG copy yet)."""
    p = _path()
    if not p:
        return None
    for f in ((p,) if p.suffix == ".png" else ()) + (_dir() / RASTER_NAME,):
        try:
            data = f.read_bytes()
        except OSError:
            continue
        size = png_size(data)
        if size:
            return data, size[0], size[1]
    return None


def _path() -> Optional[Path]:
    try:
        return next(iter(sorted(_dir().glob("logo.*"))), None)
    except OSError:
        return None


def info() -> dict:
    """{'logo': bool, 'v': cache-busting stamp, 'kind': 'svg'|…} for templates."""
    p = _path()
    if not p:
        return {"logo": False, "v": 0, "kind": ""}
    return {"logo": True, "v": int(p.stat().st_mtime), "kind": p.suffix[1:],
            "raster": p.suffix == ".png" or (_dir() / RASTER_NAME).exists()}


def logo() -> Optional[tuple[bytes, str]]:
    p = _path()
    if not p:
        return None
    return p.read_bytes(), MIME.get(p.suffix[1:], "application/octet-stream")


def favicon_svg() -> Optional[bytes]:
    """The logo's left mark as a tab icon (SVG logos only): its viewBox cropped to a box 2× as wide as high."""
    lg = logo()
    if not lg or lg[1] != "image/svg+xml":
        return None
    s = lg[0].decode("utf-8")
    m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*"', s)
    if not m:
        return lg[0]
    x, y, w, h = (float(v) for v in m.groups())
    cw = min(w, h * 2.04)
    s = s.replace(m.group(0), f'viewBox="{x:g} {y:g} {cw:g} {h:g}"', 1)
    s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r"\1", s, count=1)
    s = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r"\1", s, count=1)
    return s.encode("utf-8")
