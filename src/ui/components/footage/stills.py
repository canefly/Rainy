"""
Helpers for still photos in the Footage window: honest format labels and lazily built
hover thumbnails. Everything is cached per path so repeated hovers / repaints are free.
"""
import hashlib
import html
import os
import tempfile
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader

THUMB_MAX = 220  # px, longest edge of the hover thumbnail
_THUMB_DIR = os.path.join(tempfile.gettempdir(), "knot_thumbs")

_dims_cache: Dict[str, Optional[Tuple[int, int]]] = {}
_thumb_cache: Dict[str, Optional[str]] = {}


def _key(path: str) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(sig.encode("utf-8", "ignore")).hexdigest()[:20]


def still_dimensions(path: str) -> Optional[Tuple[int, int]]:
    """Pixel size from the image header only (no full decode). None if Qt can't read the format."""
    if path in _dims_cache:
        return _dims_cache[path]
    dims = None
    if path and os.path.isfile(path):
        reader = QImageReader(path)
        reader.setAutoTransform(True)  # respect EXIF rotation, like the photo viewer does
        size = reader.size()
        if size.isValid() and size.width() > 0:
            w, h = size.width(), size.height()
            # EXIF 90/270 rotations swap the displayed dimensions
            try:
                from PySide6.QtGui import QImageIOHandler
                if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
                    w, h = h, w
            except Exception:
                pass
            dims = (w, h)
    _dims_cache[path] = dims
    return dims


def still_format_label(path: str, fallback_resolution: str = "") -> str:
    """e.g. "JPEG  ·  4032×3024" instead of the probe's "MJPEG  ·  4K  ·  25"."""
    ext = os.path.splitext(path or "")[1].lower().lstrip(".")
    kind = {"jpg": "JPEG", "jpeg": "JPEG", "jfif": "JPEG", "tif": "TIFF", "heif": "HEIC"}.get(ext, ext.upper() or "IMAGE")
    dims = still_dimensions(path)
    size_txt = f"{dims[0]}×{dims[1]}" if dims else (fallback_resolution or "")
    return "  ·  ".join(p for p in (kind, size_txt) if p)


def thumbnail_path(path: str) -> Optional[str]:
    """Builds (once) a small PNG thumbnail on disk using a scaled decode. None if unreadable."""
    if path in _thumb_cache:
        return _thumb_cache[path]
    out = None
    if path and os.path.isfile(path):
        os.makedirs(_THUMB_DIR, exist_ok=True)
        candidate = os.path.join(_THUMB_DIR, _key(path) + ".png")
        if os.path.isfile(candidate):
            out = candidate
        else:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            src = reader.size()
            if src.isValid() and src.width() > 0 and src.height() > 0:
                scaled = src.scaled(QSize(THUMB_MAX, THUMB_MAX), Qt.KeepAspectRatio)
                # Scaled decode: JPEG decoders skip most of the work, so this stays fast on 12MP+
                reader.setScaledSize(scaled)
                img = reader.read()
                if not img.isNull() and img.save(candidate, "PNG"):
                    out = candidate
    _thumb_cache[path] = out
    return out


def thumbnail_tooltip_html(path: str, caption: str = "") -> str:
    """Rich-text tooltip with the thumbnail above an optional caption (plain text if no thumb)."""
    thumb = thumbnail_path(path)
    cap = html.escape(caption).replace("\n", "<br>") if caption else ""
    if not thumb:
        return caption
    src = thumb.replace("\\", "/")
    body = f"<img src='{html.escape(src)}'>"
    if cap:
        body += f"<br><span style='font-size:11px'>{cap}</span>"
    return f"<qt>{body}</qt>"
