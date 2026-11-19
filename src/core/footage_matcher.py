import os
import re
import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QObject, Signal, QRunnable

from .parser import normalize_link
from .unfurl_engine import LinkMetadata, unfurl_engine, format_bytes_display


# Still-image handling: ffprobe reports a JPEG as a one-frame "MJPEG, 25 fps, 0s" video, so
# stills must be recognised up front or they inherit meaningless FPS / VFR / re-encode options.
STILL_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".avif", ".svg",
}
_STILL_CODECS = {"mjpeg", "jpeg", "jpegls", "png", "bmp", "gif", "webp", "tiff", "jpeg2000", "svg", "heic", "avif"}


def is_still_media(path: Optional[str], video_codec: str = "", duration_seconds: Optional[float] = None) -> bool:
    """True for photos/stills: known image extension, or an image codec with no real duration."""
    if path:
        ext = os.path.splitext(path)[1].lower()
        if ext in STILL_IMAGE_EXTENSIONS:
            return True
        if ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".flv", ".webm", ".mts", ".m2ts"):
            return False
    codec = (video_codec or "").lower()
    return codec in _STILL_CODECS and (duration_seconds is None or duration_seconds < 0.05)


def is_still_item(item) -> bool:
    """is_still_media for a FootageMatchItem (uses the linked file, else the script title)."""
    return is_still_media(
        item.matched_path or getattr(item, "title", "") or "",
        getattr(item, "video_codec", ""),
        getattr(item, "duration_seconds", None),
    )


@dataclass
class FootageMatchItem:
    url: str
    norm_url: str
    title: str
    file_type: str
    file_size_bytes: Optional[int]
    file_size_display: str
    matched_path: Optional[str] = None
    match_tier: str = "MISSING"  # EXACT_NAME | EXACT_SIZE | FUZZY_NAME | MANUAL | MISSING
    is_matched: bool = False
    duration_seconds: Optional[float] = None
    duration_display: str = ""
    resolution: str = ""
    frame_rate: Optional[float] = None
    is_vfr: bool = False
    is_interlaced: bool = False
    field_order: str = "progressive"
    video_codec: str = ""
    audio_codec: str = ""


def sanitize_stem(filename: str) -> str:
    """Strips extension and non-alphanumeric noise for fuzzy stem comparisons."""
    name_without_ext = os.path.splitext(filename)[0]
    # Remove common video edit noise: [RAW], (REDACTED), _1080p, copy, etc.
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', name_without_ext)
    cleaned = re.sub(r'[\W_]+', '', cleaned).lower().strip()
    return cleaned or name_without_ext.lower().strip()


class FootageMatcher:
    """
    Scans a local footage folder recursively and matches local media files against
    script source links using multi-tier heuristics:
    Tier 1: Exact Filename Match (case-insensitive)
    Tier 2: Exact Byte Size Match (via range-probed file_size_bytes)
    Tier 3: Fuzzy Name Stem Match (ignoring redaction suffixes and symbols)
    """

    SUPPORTED_EXTENSIONS = {
        # Video
        ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".flv", ".webm", ".mts", ".m2ts",
        # Audio
        ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".wma",
        # Image
        ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg",
        ".heic", ".heif", ".avif",
        # Document
        ".pdf", ".docx", ".doc", ".txt", ".rtf", ".csv"
    }

    def __init__(self, footage_dir: str):
        self.footage_dir = os.path.abspath(footage_dir)
        self.name_index: Dict[str, List[str]] = {}       # clean_basename_lower -> [paths]
        self.size_index: Dict[int, List[str]] = {}       # byte_size -> [paths]
        self.all_files: List[Tuple[str, str, int]] = []   # (abs_path, basename, size_bytes)
        self._scanned = False

    def scan_directory(self, max_files: int = 50000):
        """Recursively indexes all files in footage_dir."""
        if not os.path.isdir(self.footage_dir):
            return

        self.name_index.clear()
        self.size_index.clear()
        self.all_files.clear()

        count = 0
        for root, _, files in os.walk(self.footage_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in self.SUPPORTED_EXTENSIONS:
                    continue

                abs_path = os.path.normpath(os.path.join(root, f))
                try:
                    size = os.path.getsize(abs_path)
                except (OSError, PermissionError):
                    size = 0

                lower_name = f.lower().strip()
                self.name_index.setdefault(lower_name, []).append(abs_path)

                if size > 0:
                    self.size_index.setdefault(size, []).append(abs_path)

                self.all_files.append((abs_path, f, size))
                count += 1
                if count >= max_files:
                    break
            if count >= max_files:
                break

        self._scanned = True

    def match_links(
        self,
        links: List[str],
        manual_overrides: Optional[Dict[str, str]] = None
    ) -> List[FootageMatchItem]:
        """
        Matches an array of script source links against the scanned local files.
        manual_overrides: Dict mapping norm_url -> verified local_path
        """
        if not self._scanned:
            self.scan_directory()

        overrides = manual_overrides or {}
        results: List[FootageMatchItem] = []
        already_assigned_paths: set = set()

        # Pre-assign manual overrides
        for raw_link in links:
            if not raw_link or raw_link == "#":
                continue
            norm = normalize_link(raw_link)
            cached_meta = unfurl_engine.get_cached(norm) or unfurl_engine.get_cached(raw_link)
            title = cached_meta.title if (cached_meta and cached_meta.title) else os.path.basename(raw_link)
            f_type = cached_meta.file_type if cached_meta else "LINK"
            f_bytes = cached_meta.file_size_bytes if cached_meta else None
            f_disp = cached_meta.file_size_display if cached_meta else ""

            # Check manual override first
            override_path = overrides.get(norm) or overrides.get(raw_link)
            if override_path and os.path.isfile(override_path):
                already_assigned_paths.add(override_path)
                results.append(FootageMatchItem(
                    url=raw_link,
                    norm_url=norm,
                    title=title,
                    file_type=f_type,
                    file_size_bytes=f_bytes,
                    file_size_display=f_disp,
                    matched_path=override_path,
                    match_tier="MANUAL",
                    is_matched=True,
                ))
                continue

            results.append(FootageMatchItem(
                url=raw_link,
                norm_url=norm,
                title=title,
                file_type=f_type,
                file_size_bytes=f_bytes,
                file_size_display=f_disp,
                matched_path=None,
                match_tier="MISSING",
                is_matched=False,
            ))

        # Pass 1: Tier 1 - Exact Filename Match
        for item in results:
            if item.is_matched:
                continue

            target_filename = item.title.strip()
            # Clean up trailing site suffixes if present
            target_filename = re.sub(r'\s*[-–—]\s*Google\s+(?:Drive|Docs)\s*$', '', target_filename, flags=re.IGNORECASE)
            target_lower = target_filename.lower().strip()

            candidates = self.name_index.get(target_lower, [])
            for cand in candidates:
                if cand not in already_assigned_paths:
                    item.matched_path = cand
                    item.match_tier = "EXACT_NAME"
                    item.is_matched = True
                    already_assigned_paths.add(cand)
                    break

        # Pass 2: Tier 2 - Exact Byte Size Match (Zero False Positive for Renamed Files)
        for item in results:
            if item.is_matched or not item.file_size_bytes or item.file_size_bytes <= 0:
                continue

            candidates = self.size_index.get(item.file_size_bytes, [])
            for cand in candidates:
                if cand not in already_assigned_paths:
                    # Verify extension family matches (don't match video size with txt size)
                    cand_ext = os.path.splitext(cand)[1].lower()
                    title_ext = os.path.splitext(item.title)[1].lower()
                    if not title_ext or cand_ext == title_ext or (cand_ext in FootageMatcher.SUPPORTED_EXTENSIONS):
                        item.matched_path = cand
                        item.match_tier = "EXACT_SIZE"
                        item.is_matched = True
                        already_assigned_paths.add(cand)
                        break

        # Pass 3: Tier 3 - Fuzzy Stem Match
        for item in results:
            if item.is_matched:
                continue

            target_stem = sanitize_stem(item.title)
            target_ext = os.path.splitext(item.title)[1].lower()
            if len(target_stem) < 4:
                continue

            best_ratio = 0.0
            best_cand = None

            for abs_path, base_name, _ in self.all_files:
                if abs_path in already_assigned_paths:
                    continue
                cand_ext = os.path.splitext(base_name)[1].lower()
                if target_ext and cand_ext != target_ext:
                    continue

                cand_stem = sanitize_stem(base_name)
                ratio = difflib.SequenceMatcher(None, target_stem, cand_stem).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_cand = abs_path

            if best_ratio >= 0.82 and best_cand:
                item.matched_path = best_cand
                item.match_tier = "FUZZY_NAME"
                item.is_matched = True
                already_assigned_paths.add(best_cand)

        # Pass 4: Enrich matched items with exact media probe properties (duration, resolution, fps)
        from .media_probe import probe_media_file
        for item in results:
            if item.is_matched and item.matched_path:
                try:
                    probed = probe_media_file(item.matched_path, use_cache=True)
                    if probed:
                        item.duration_seconds = probed.duration_seconds
                        item.duration_display = probed.duration_display
                        item.resolution = probed.resolution
                        item.frame_rate = probed.frame_rate
                        item.is_vfr = probed.is_vfr
                        item.is_interlaced = probed.is_interlaced
                        item.field_order = probed.field_order or "progressive"
                        item.video_codec = probed.video_codec or ""
                        item.audio_codec = probed.audio_codec or ""
                        if probed.file_size > 0:
                            item.file_size_bytes = probed.file_size
                            item.file_size_display = format_bytes_display(probed.file_size)
                except Exception:
                    pass

        return results


class FootageScanSignals(QObject):
    finished = Signal(object)  # List[FootageMatchItem]
    progress = Signal(int, int) # (processed, total)
    error = Signal(str)


class FootageScanWorker(QRunnable):
    """Background QRunnable for non-blocking local directory indexing and link matching."""
    def __init__(
        self,
        footage_dir: str,
        links: List[str],
        manual_overrides: Optional[Dict[str, str]] = None
    ):
        super().__init__()
        self.footage_dir = footage_dir
        self.links = links
        self.manual_overrides = manual_overrides or {}
        self.signals = FootageScanSignals()

    def run(self):
        try:
            matcher = FootageMatcher(self.footage_dir)
            matcher.scan_directory()
            results = matcher.match_links(self.links, self.manual_overrides)
            try:
                self.signals.finished.emit(results)
            except RuntimeError:
                pass
        except Exception as e:
            try:
                self.signals.error.emit(str(e))
            except RuntimeError:
                pass
