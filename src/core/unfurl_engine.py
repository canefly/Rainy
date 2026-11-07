import re
import time
import json
import random
import uuid
import html as html_lib
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, QMutex, QMutexLocker, QTimer

from .cookie_manager import cookie_manager
from .database import db
from .parser import normalize_link
from .media_inspector import (
    probe_google_drive_stream,
    inspect_image_dimensions,
    inspect_mp4_metadata,
    format_resolution,
)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) "
    "Gecko/20100101 Firefox/131.0"
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".rtf", ".xlsx", ".csv", ".pptx"}


@dataclass
class LinkMetadata:
    url: str
    title: str = ""
    description: str = ""
    author: str = ""
    provider: str = ""
    domain: str = ""
    file_type: str = "LINK"          # VIDEO | AUDIO | DOC | IMAGE | FOLDER | LINK
    thumbnail_url: Optional[str] = None
    thumbnail_bytes: Optional[bytes] = None
    is_restricted: bool = False
    is_fallback: bool = False
    loading: bool = False
    file_size_bytes: Optional[int] = None
    file_size_display: str = ""
    resolution: str = ""
    duration_seconds: Optional[float] = None
    duration_display: str = ""
    updated_at: float = field(default_factory=time.time)


def format_bytes_display(size_bytes: Optional[int]) -> str:
    """Formats exact byte integer into human-readable string (e.g. 1548482012 -> 1.44 GB)."""
    if not size_bytes or size_bytes <= 0:
        return ""
    b = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{int(b)} {unit}"
            val_str = f"{b:.2f}".rstrip('0').rstrip('.')
            return f"{val_str} {unit}"
        b /= 1024.0
    return ""


def format_duration_display(duration_sec: Optional[float]) -> str:
    """Formats seconds into compact timecode string (e.g. 862.0 -> 14m 22s)."""
    if duration_sec is None or duration_sec <= 0:
        return ""
    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    hrs = mins // 60
    rem_mins = mins % 60
    if hrs > 0:
        return f"{hrs}h {rem_mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _extract_drive_file_stats(html_text: str) -> Tuple[Optional[int], str, str, Optional[float], str]:
    """
    Extracts (file_size_bytes, file_size_display, resolution, duration_seconds, duration_display)
    from Google Drive bootstrap JSON embedded in the HTML payload.
    """
    if not html_text:
        return None, "", "", None, ""

    size_bytes = None
    # 1. Exact raw bytes integer
    m_size = re.search(r'["\'](?:sizeBytes|fileSize)["\']\s*[:=,]\s*["\']?(\d{4,})["\']?', html_text)
    if m_size:
        try:
            size_bytes = int(m_size.group(1))
        except Exception:
            pass

    size_display = format_bytes_display(size_bytes)
    if not size_display:
        m_fmt = re.search(r'["\']formattedFileSize["\']\s*[:=]\s*["\']([^"\']+)["\']', html_text)
        if m_fmt:
            size_display = m_fmt.group(1).strip()

    # 2. Resolution / Dimensions
    resolution = ""
    m_res = re.search(r'["\'](?:width|videoWidth)["\']\s*[:=]\s*(\d+)[^}]*?["\'](?:height|videoHeight)["\']\s*[:=]\s*(\d+)', html_text)
    if not m_res:
        m_res = re.search(r'["\']videoMediaMetadata["\']\s*:\s*\{[^}]*?"height"\s*:\s*(\d+)[^}]*?"width"\s*:\s*(\d+)', html_text)
        if m_res:
            h, w = int(m_res.group(1)), int(m_res.group(2))
        else:
            h, w = 0, 0
    else:
        w, h = int(m_res.group(1)), int(m_res.group(2))

    if w > 0 and h > 0:
        if (w, h) in ((3840, 2160), (4096, 2160)) or h >= 2160:
            resolution = "4K"
        elif (w, h) in ((2560, 1440),) or h >= 1440:
            resolution = "1440p"
        elif (w, h) in ((1920, 1080), (1080, 1920)) or h >= 1080:
            resolution = "1080p"
        elif (w, h) in ((1280, 720), (720, 1280)) or h >= 720:
            resolution = "720p"
        elif h >= 480:
            resolution = "480p"
        else:
            resolution = f"{w}x{h}"

    # 3. Video Duration
    duration_sec = None
    m_dur = re.search(r'["\']durationMillis["\']\s*[:=]\s*["\']?(\d{3,})["\']?', html_text)
    if m_dur:
        try:
            duration_sec = float(m_dur.group(1)) / 1000.0
        except Exception:
            pass

    dur_display = format_duration_display(duration_sec)
    return size_bytes, size_display, resolution, duration_sec, dur_display


def extract_domain(url: str) -> str:
    if not url or url == "#":
        return "link"
    try:
        raw = url if url.startswith(("http://", "https://")) else f"https://{url}"
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "link").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return "link"


def extract_gdrive_id(url: str) -> Tuple[Optional[str], bool]:
    """
    Extracts (drive_id, is_folder) from Google Drive / Google Docs URLs.
    """
    if not url:
        return None, False

    folder_match = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", url)
    if folder_match:
        return folder_match.group(1), True

    file_match = re.search(r"/(?:file|document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]{10,})", url)
    if file_match:
        return file_match.group(1), False

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0], ("/folders" in parsed.path)

    return None, False


def infer_file_type_from_title(title: str, is_folder: bool = False, og_type: str = "") -> str:
    if is_folder:
        return "FOLDER"
    lower_title = (title or "").lower().strip()
    for ext in VIDEO_EXTENSIONS:
        if lower_title.endswith(ext):
            return "VIDEO"
    for ext in AUDIO_EXTENSIONS:
        if lower_title.endswith(ext):
            return "AUDIO"
    for ext in IMAGE_EXTENSIONS:
        if lower_title.endswith(ext):
            return "IMAGE"
    for ext in DOC_EXTENSIONS:
        if lower_title.endswith(ext):
            return "DOC"
    lower_og = (og_type or "").lower()
    if "video" in lower_og:
        return "VIDEO"
    if "audio" in lower_og or "music" in lower_og:
        return "AUDIO"
    return "LINK"


def _extract_meta_tag(html_text: str, keys: List[str]) -> str:
    """Extracts content from <meta property/name/itemprop='...'> tags."""
    for key in keys:
        escaped = re.escape(key)
        patterns = [
            rf'<meta[^>]+(?:property|name|itemprop)\s*=\s*["\']{escaped}["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
            rf'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)\s*=\s*["\']{escaped}["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html_text, flags=re.IGNORECASE)
            if m and m.group(1).strip():
                return html_lib.unescape(m.group(1).strip())
    return ""


def _extract_html_title(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if m and m.group(1):
        cleaned = re.sub(r"\s+", " ", m.group(1)).strip()
        return html_lib.unescape(cleaned)
    return ""


def _is_google_login_or_restricted(final_url: str, html_text: str) -> bool:
    lower_url = (final_url or "").lower()
    if "accounts.google.com/servicelogin" in lower_url or "accounts.google.com/v3/signin" in lower_url:
        return True
    title = _extract_html_title(html_text).lower()
    if title in (
        "sign in - google accounts",
        "google drive: sign-in",
        "meet google drive – one place for all your files",
        "access denied",
    ):
        return True
    if "you need access" in html_text.lower() and "request access" in html_text.lower():
        return True
    return False


class UnfurlWorkerSignals(QObject):
    finished = Signal(str, object, str)   # (normalized_url, LinkMetadata, session_id)
    error = Signal(str, str, str)         # (normalized_url, error_msg, session_id)


class UnfurlWorker(QRunnable):
    """Background QRunnable that unfurls a single link off the GUI thread."""
    def __init__(
        self,
        url: str,
        norm_url: str,
        engine: "UnfurlEngine",
        fetch_thumbnail: bool = True,
        session_id: str = "",
    ):
        super().__init__()
        self.url = url
        self.norm_url = norm_url
        self.engine = engine
        self.fetch_thumbnail = fetch_thumbnail
        self.session_id = session_id
        self.signals = UnfurlWorkerSignals()

    def run(self):
        try:
            meta = self.engine.unfurl_sync(self.url, fetch_thumbnail=self.fetch_thumbnail)
            self.signals.finished.emit(self.norm_url, meta, self.session_id)
        except RuntimeError:
            pass
        except Exception as e:
            try:
                self.signals.error.emit(self.norm_url, str(e), self.session_id)
            except RuntimeError:
                pass


class UnfurlEngine(QObject):
    """
    Central asynchronous Link Unfurl Engine powered by QThreadPool and CookieManager.
    Extracts Open Graph / oEmbed metadata, real Google Drive filenames & file types,
    detects restricted links requiring cookies, and pre-fetches authenticated thumbnails.
    """
    metadata_ready = Signal(str, object)    # (url, LinkMetadata)
    batch_progress = Signal(int, int)       # (completed_count, total_count)
    cache_cleared = Signal(bool)            # (restricted_only)

    _instance: Optional["UnfurlEngine"] = None

    @classmethod
    def instance(cls) -> "UnfurlEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._cache: Dict[str, LinkMetadata] = {}
        self._in_flight: Set[str] = set()
        self._batch_total = 0
        self._batch_done = 0
        self._active_session_id: str = ""

        # Dedicated clamped thread pool: strictly capped at 3 concurrent workers
        # to prevent overwhelming Google Drive / YouTube endpoints with connection bursts
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(3)

        # Paced FIFO dispatch queue with micro-jitter
        self._queue: List[Tuple[str, str, bool, str]] = []  # [(url, norm_url, fetch_thumbnail, session_id)]
        self._queued_norms: Set[str] = set()
        self._backoff_until: float = 0.0

        # High-precision dispatch timer for randomized micro-jitter (140ms - 260ms)
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setSingleShot(True)
        self._dispatch_timer.timeout.connect(self._dispatch_next)

    def set_active_session(self, session_id: str):
        """Sets the active document session generation token. Stale callbacks will be dropped."""
        with QMutexLocker(self._mutex):
            self._active_session_id = session_id

    def cancel_pending(self):
        """Flushes the pending queue and invalidates in-flight callbacks when closing or switching documents."""
        with QMutexLocker(self._mutex):
            self._queue.clear()
            self._queued_norms.clear()
            self._batch_total = 0
            self._batch_done = 0
            self._active_session_id = uuid.uuid4().hex
        if self._dispatch_timer.isActive():
            self._dispatch_timer.stop()

    def has_cached(self, url: str) -> bool:
        """Instant O(1) check if link metadata is already cached in RAM or SQLite."""
        if not url or url == "#":
            return False
        cached = self.get_cached(url)
        return (cached is not None and not cached.loading and not cached.is_fallback and bool(cached.title))

    def trigger_rate_limit_backoff(self, backoff_sec: float = 6.0):
        """
        Pauses queue dispatch when receiving HTTP 429 Too Many Requests or HTTP 503
        from upstream services (Google Drive / YouTube). Staggers subsequent requests.
        """
        now = time.time()
        self._backoff_until = max(self._backoff_until, now + backoff_sec)
        delay_ms = int(max(0.5, self._backoff_until - now) * 1000)
        if self._dispatch_timer.isActive():
            self._dispatch_timer.stop()
        self._dispatch_timer.start(delay_ms)

    def _schedule_dispatch(self, jitter: bool = True):
        """
        Schedules next queue pop. If jitter=True, applies a randomized 140ms - 260ms micro-jitter
        to space out HTTP pings and simulate natural human browsing patterns.
        If priority=True (jitter=False), dispatches immediately.
        """
        with QMutexLocker(self._mutex):
            if not self._queue:
                return
            has_slots = self.thread_pool.activeThreadCount() < self.thread_pool.maxThreadCount()

        if not has_slots:
            return

        now = time.time()
        if self._backoff_until > now:
            delay_ms = int((self._backoff_until - now) * 1000) + random.randint(100, 300)
            if not self._dispatch_timer.isActive():
                self._dispatch_timer.start(delay_ms)
            return

        if not jitter:
            delay_ms = 0
        else:
            delay_ms = random.randint(140, 260)

        if delay_ms == 0:
            if self._dispatch_timer.isActive():
                self._dispatch_timer.stop()
            self._dispatch_next()
        else:
            if not self._dispatch_timer.isActive():
                self._dispatch_timer.start(delay_ms)

    def _dispatch_next(self):
        """Pops and launches the next queued unfurl worker within the clamped 3-thread pool."""
        now = time.time()
        if self._backoff_until > now:
            self._schedule_dispatch(jitter=False)
            return

        with QMutexLocker(self._mutex):
            if not self._queue:
                return
            if self.thread_pool.activeThreadCount() >= self.thread_pool.maxThreadCount():
                return
            url, norm, fetch_thumbnail, session_id = self._queue.pop(0)
            self._queued_norms.discard(norm)
            self._in_flight.add(norm)

        worker = UnfurlWorker(url, norm, self, fetch_thumbnail=fetch_thumbnail, session_id=session_id)
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.error.connect(self._on_worker_error)
        self.thread_pool.start(worker)

        # If more items queued and worker slots still open, schedule next with micro-jitter
        with QMutexLocker(self._mutex):
            more_queued = bool(self._queue)
            has_slots = self.thread_pool.activeThreadCount() < self.thread_pool.maxThreadCount()

        if more_queued and has_slots:
            self._schedule_dispatch(jitter=True)

    # --- Cache Management (RAM + SQLite 0ms Persistence) ---
    def get_cached(self, url: str) -> Optional[LinkMetadata]:
        if not url or url == "#":
            return None
        norm = normalize_link(url)
        with QMutexLocker(self._mutex):
            cached = self._cache.get(norm) or self._cache.get(url)
            if cached is not None:
                return cached

        # Check SQLite persistent cache (0ms startup lookup)
        row = db.get_link_metadata(norm)
        if row:
            meta = LinkMetadata(
                url=row.get("url") or url,
                title=row.get("title", ""),
                description=row.get("description", ""),
                author=row.get("author", ""),
                provider=row.get("provider", ""),
                domain=row.get("domain") or extract_domain(url),
                file_type=row.get("file_type", "LINK"),
                thumbnail_url=row.get("thumbnail_url"),
                thumbnail_bytes=row.get("thumbnail_bytes"),
                is_restricted=bool(row.get("is_restricted", False)),
                is_fallback=False,
                loading=False,
                updated_at=float(row.get("updated_at", time.time())),
                file_size_bytes=row.get("file_size_bytes"),
                file_size_display=row.get("file_size_display", ""),
                resolution=row.get("resolution", ""),
                duration_seconds=row.get("duration_seconds"),
                duration_display=row.get("duration_display", ""),
            )
            with QMutexLocker(self._mutex):
                self._cache[norm] = meta
                self._cache[url] = meta
            return meta
        return None

    def store_metadata(self, url: str, meta: LinkMetadata, persist_sqlite: bool = True):
        if not url or url == "#":
            return
        norm = normalize_link(url)
        with QMutexLocker(self._mutex):
            self._cache[norm] = meta
            self._cache[url] = meta
        if persist_sqlite and not meta.loading and not meta.is_fallback:
            try:
                db.save_link_metadata(norm, meta.__dict__)
            except Exception:
                pass

    def clear_cache(self, restricted_only: bool = False):
        """Clears cached link metadata in RAM and SQLite (or only restricted entries when cookies are updated)."""
        with QMutexLocker(self._mutex):
            if restricted_only:
                to_remove = [k for k, v in self._cache.items() if v.is_restricted]
                for k in to_remove:
                    del self._cache[k]
            else:
                self._cache.clear()
                self._queue.clear()
                self._queued_norms.clear()
        try:
            db.delete_link_metadata(restricted_only=restricted_only)
        except Exception:
            pass
        self.cache_cleared.emit(restricted_only)

    # --- Async QThreadPool Dispatch ---
    def request_unfurl(
        self,
        url: str,
        force_refresh: bool = False,
        fetch_thumbnail: bool = True,
        priority: bool = False,
        session_id: str = "",
    ):
        if not url or url == "#":
            return
        norm = normalize_link(url)
        sess = session_id or self._active_session_id

        if force_refresh:
            try:
                db.delete_link_metadata(norm_url=norm)
            except Exception:
                pass
            with QMutexLocker(self._mutex):
                self._cache.pop(norm, None)
                self._cache.pop(url, None)
        else:
            existing = self.get_cached(url)
            if existing is not None and not existing.loading:
                self.metadata_ready.emit(url, existing)
                return

        with QMutexLocker(self._mutex):
            if norm in self._in_flight:
                return

            if norm in self._queued_norms:
                if priority:
                    # Move to front of queue for immediate dispatch
                    self._queue = [item for item in self._queue if item[1] != norm]
                    self._queue.insert(0, (url, norm, fetch_thumbnail, sess))
                    self._schedule_dispatch(jitter=False)
                return

            self._queued_norms.add(norm)
            self._cache[norm] = LinkMetadata(
                url=url,
                domain=extract_domain(url),
                loading=True
            )
            if priority:
                self._queue.insert(0, (url, norm, fetch_thumbnail, sess))
            else:
                self._queue.append((url, norm, fetch_thumbnail, sess))

        self._schedule_dispatch(jitter=(not priority))

    def request_batch_unfurl(
        self,
        urls: List[str],
        force_refresh: bool = False,
        fetch_thumbnail: bool = True,
        session_id: str = "",
    ):
        unique_urls = []
        seen = set()
        sess = session_id or self._active_session_id
        for u in urls:
            if not u or u == "#":
                continue
            norm = normalize_link(u)
            if norm not in seen:
                seen.add(norm)
                unique_urls.append(u)

        with QMutexLocker(self._mutex):
            self._batch_total = len(unique_urls)
            self._batch_done = 0

        for u in unique_urls:
            self.request_unfurl(u, force_refresh=force_refresh, fetch_thumbnail=fetch_thumbnail, session_id=sess)

    def _on_worker_finished(self, norm_url: str, meta: LinkMetadata, session_id: str = ""):
        with QMutexLocker(self._mutex):
            self._in_flight.discard(norm_url)
            if session_id and session_id != self._active_session_id:
                # Stale thread from previously closed document session — discard cleanly
                return
            self._cache[norm_url] = meta
            self._cache[meta.url] = meta
            if self._batch_total > 0:
                self._batch_done += 1
                done, total = self._batch_done, self._batch_total
            else:
                done, total = 0, 0

        if not meta.loading and not meta.is_fallback:
            try:
                db.save_link_metadata(norm_url, meta.__dict__)
            except Exception:
                pass

        self.metadata_ready.emit(meta.url, meta)
        if total > 0:
            self.batch_progress.emit(done, total)

        # Stagger next dispatch in queue with micro-jitter
        self._schedule_dispatch(jitter=True)

    def _on_worker_error(self, norm_url: str, err_msg: str, session_id: str = ""):
        with QMutexLocker(self._mutex):
            if session_id and session_id != self._active_session_id:
                return
        fallback = self._build_fallback_metadata(norm_url)
        self._on_worker_finished(norm_url, fallback, session_id=session_id)

    # --- HTTP Helpers with CookieManager Integration ---
    def _build_opener(self) -> urllib.request.OpenerDirector:
        jar = cookie_manager.get_cookie_jar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def _fetch_url_text(
        self, url: str, timeout: float = 6.0, max_bytes: int = 196608, use_cookies: bool = True
    ) -> Tuple[int, str, str]:
        """
        Performs an HTTP GET with active CookieManager cookies (via HTTPCookieProcessor)
        and returns: (status_code, final_url, decoded_html_text)
        """
        opener = self._build_opener() if use_cookies else urllib.request.build_opener()
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200) or 200
                final_url = resp.geturl() or url
                raw_bytes = resp.read(max_bytes)
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw_bytes.decode(charset, errors="replace")
                return status, final_url, text
        except urllib.error.HTTPError as he:
            if he.code in (429, 503):
                self.trigger_rate_limit_backoff(random.uniform(6.0, 8.0))
            body = ""
            try:
                body = he.read(65536).decode("utf-8", errors="replace")
            except Exception:
                pass
            return he.code, url, body

    def fetch_thumbnail_bytes(self, thumb_url: str, timeout: float = 5.0, max_bytes: int = 2_097_152) -> Optional[bytes]:
        """Downloads thumbnail bytes using the authenticated CookieManager session (handling 302 Set-Cookie redirects)."""
        if not thumb_url or not thumb_url.startswith(("http://", "https://")):
            return None
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        for use_cookies in (True, False):
            opener = self._build_opener() if use_cookies else urllib.request.build_opener()
            req = urllib.request.Request(thumb_url, headers=headers, method="GET")
            try:
                with opener.open(req, timeout=timeout) as resp:
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if "text/html" in content_type:
                        # Redirected to an HTML sign-in page instead of an image
                        continue
                    data = resp.read(max_bytes)
                    if data:
                        return data
            except urllib.error.HTTPError as he:
                if he.code in (429, 503):
                    self.trigger_rate_limit_backoff(random.uniform(6.0, 8.0))
                if not use_cookies or cookie_manager.get_mode() == "disabled":
                    break
            except Exception:
                if not use_cookies or cookie_manager.get_mode() == "disabled":
                    break
        return None

    # --- Synchronous Unfurl Core (Runs inside Worker Thread) ---
    def unfurl_sync(self, url: str, fetch_thumbnail: bool = True) -> LinkMetadata:
        if not url or url == "#":
            return LinkMetadata(
                url=url or "#",
                title="No Link Assigned",
                description="No external URL bound to this item.",
                provider="Link",
                domain="link",
                file_type="LINK",
                loading=False,
            )

        domain = extract_domain(url)

        # 1. Google Drive / Google Docs Handler
        if "drive.google.com" in domain or "docs.google.com" in domain:
            return self._unfurl_google_drive(url, domain, fetch_thumbnail=fetch_thumbnail)

        # 2. YouTube Handler
        if "youtube.com" in domain or "youtu.be" in domain:
            return self._unfurl_youtube(url, domain, fetch_thumbnail=fetch_thumbnail)

        # 3. General Open Graph Web Handler
        return self._unfurl_general_web(url, domain, fetch_thumbnail=fetch_thumbnail)

    def _unfurl_google_drive(self, url: str, domain: str, fetch_thumbnail: bool = True) -> LinkMetadata:
        drive_id, is_folder = extract_gdrive_id(url)
        target_url = url
        if drive_id and not is_folder and "drive.google.com" in domain:
            target_url = f"https://drive.google.com/file/d/{drive_id}/view"

        try:
            status, final_url, html_text = self._fetch_url_text(target_url, use_cookies=True)
            if (status in (401, 403) or _is_google_login_or_restricted(final_url, html_text)) and cookie_manager.get_mode() != "disabled":
                # Try secondary signed-in Google account (u/1) if primary (u/0) lacks permission
                if drive_id and not is_folder and "drive.google.com" in domain:
                    u1_url = f"https://drive.google.com/file/u/1/d/{drive_id}/view"
                    s1, f1, h1 = self._fetch_url_text(u1_url, use_cookies=True)
                    if s1 == 200 and not _is_google_login_or_restricted(f1, h1):
                        status, final_url, html_text = s1, f1, h1
                # Fallback retry without cookies so public Google Drive files still unfurl even if local cookies are stale
                if status in (401, 403):
                    status, final_url, html_text = self._fetch_url_text(target_url, use_cookies=False)
        except Exception:
            return self._build_fallback_metadata(url)

        # Check if private / restricted
        if status in (401, 403) or _is_google_login_or_restricted(final_url, html_text):
            return LinkMetadata(
                url=url,
                title="Restricted — Configure Cookies",
                description="Private Google Drive folder or file. Enable Firefox or cookies.txt in Settings > Cookies & Auth.",
                provider="Google Drive",
                domain=domain,
                file_type="FOLDER" if is_folder else "VIDEO",
                is_restricted=True,
                loading=False,
            )

        og_title = _extract_meta_tag(html_text, ["og:title", "twitter:title", "name"])
        raw_title = og_title or _extract_html_title(html_text)
        # Strip trailing " - Google Drive" or " - Google Docs"
        clean_title = re.sub(r"\s*[-–—]\s*Google\s+(?:Drive|Docs|Sheets|Slides)\s*$", "", raw_title, flags=re.IGNORECASE).strip()

        if not clean_title or clean_title.lower() in ("google drive", "meet google drive"):
            clean_title = "Google Drive Folder" if is_folder else "Google Drive Asset"

        og_desc = _extract_meta_tag(html_text, ["og:description", "description"])
        og_type = _extract_meta_tag(html_text, ["og:type"])
        og_img = _extract_meta_tag(html_text, ["og:image", "twitter:image"])
        og_url = _extract_meta_tag(html_text, ["og:url", "url"])

        file_type = infer_file_type_from_title(clean_title, is_folder=is_folder, og_type=og_type)

        if not og_desc:
            type_label = {
                "VIDEO": "Google Drive Video Clip",
                "AUDIO": "Google Drive Audio Clip",
                "IMAGE": "Google Drive Image Asset",
                "DOC": "Google Drive Document",
                "FOLDER": "Shared Google Drive Folder",
            }.get(file_type, "Shared Google Drive File")
            og_desc = f"{type_label} • {clean_title}"

        acct_match = re.search(r"/u/(\d+)/", og_url or final_url or "")
        acct_prefix = f"/u/{acct_match.group(1)}" if acct_match else ""

        thumb_url = og_img
        if not thumb_url and drive_id and not is_folder:
            thumb_url = f"https://drive.google.com{acct_prefix}/thumbnail?id={drive_id}&sz=w320"

        thumb_bytes = None
        if fetch_thumbnail and thumb_url:
            thumb_bytes = self.fetch_thumbnail_bytes(thumb_url)
            if not thumb_bytes and acct_prefix and drive_id and not is_folder:
                fallback_thumb = f"https://drive.google.com/thumbnail?id={drive_id}&sz=w320"
                thumb_bytes = self.fetch_thumbnail_bytes(fallback_thumb)

        size_bytes, size_display, resolution, dur_sec, dur_display = _extract_drive_file_stats(html_text)

        # Probe Google Drive uc download stream for exact byte range, image dimensions, duration & virus warning size
        if drive_id and not is_folder:
            probe_opener = self._build_opener()
            p_size, p_disp, p_res, p_dur, p_dur_disp, p_name = probe_google_drive_stream(drive_id, probe_opener)
            if p_size is None and not p_disp:
                # Retry probe without cookies in case public file
                p_size, p_disp, p_res, p_dur, p_dur_disp, p_name = probe_google_drive_stream(
                    drive_id, urllib.request.build_opener()
                )

            if p_size is not None:
                size_bytes = p_size
            if p_disp:
                size_display = p_disp
            if p_res:
                resolution = p_res
            if p_dur is not None:
                dur_sec = p_dur
            if p_dur_disp:
                dur_display = p_dur_disp
            if p_name:
                if clean_title in ("Google Drive Asset", "Google Drive File", "Google Drive Folder") or not clean_title:
                    clean_title = p_name
                file_type = infer_file_type_from_title(p_name, is_folder=False, og_type=og_type)

        return LinkMetadata(
            url=url,
            title=clean_title,
            description=og_desc,
            provider="Google Drive",
            domain=domain,
            file_type=file_type,
            thumbnail_url=thumb_url,
            thumbnail_bytes=thumb_bytes,
            is_restricted=False,
            loading=False,
            file_size_bytes=size_bytes,
            file_size_display=size_display,
            resolution=resolution,
            duration_seconds=dur_sec,
            duration_display=dur_display,
        )

    def _unfurl_youtube(self, url: str, domain: str, fetch_thumbnail: bool = True) -> LinkMetadata:
        # 1. Try fast YouTube oEmbed endpoint first
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url, safe='')}&format=json"
        try:
            status, _, body = self._fetch_url_text(oembed_url, timeout=4.5)
            if status == 200 and body:
                data = json.loads(body)
                title = (data.get("title") or "YouTube Video").strip()
                author = (data.get("author_name") or "YouTube Creator").strip()
                thumb_url = data.get("thumbnail_url")
                thumb_bytes = self.fetch_thumbnail_bytes(thumb_url) if (fetch_thumbnail and thumb_url) else None
                return LinkMetadata(
                    url=url,
                    title=title,
                    description=f"YouTube video uploaded by {author}.",
                    author=author,
                    provider="YouTube",
                    domain=domain,
                    file_type="VIDEO",
                    thumbnail_url=thumb_url,
                    thumbnail_bytes=thumb_bytes,
                    is_restricted=False,
                    loading=False,
                )
        except Exception:
            pass

        # 2. Fallback to full YouTube page fetch with cookies (handles channels, playlists, or restricted videos)
        try:
            status, final_url, html_text = self._fetch_url_text(url)
            if status in (401, 403) or _is_google_login_or_restricted(final_url, html_text):
                return LinkMetadata(
                    url=url,
                    title="Restricted YouTube Video — Configure Cookies",
                    description="Private or age-restricted YouTube content. Configure cookies in Settings > Cookies & Auth.",
                    provider="YouTube",
                    domain=domain,
                    file_type="VIDEO",
                    is_restricted=True,
                    loading=False,
                )

            title = _extract_meta_tag(html_text, ["og:title", "twitter:title"]) or _extract_html_title(html_text)
            title = re.sub(r"\s*-\s*YouTube\s*$", "", title, flags=re.IGNORECASE).strip()
            if not title or title.lower() == "youtube":
                title = "YouTube Video"

            desc = _extract_meta_tag(html_text, ["og:description", "description"]) or "Online video reference hosted on YouTube."
            author = _extract_meta_tag(html_text, ["author"])
            thumb_url = _extract_meta_tag(html_text, ["og:image", "twitter:image"])
            thumb_bytes = self.fetch_thumbnail_bytes(thumb_url) if (fetch_thumbnail and thumb_url) else None

            return LinkMetadata(
                url=url,
                title=title,
                description=desc,
                author=author,
                provider="YouTube",
                domain=domain,
                file_type="VIDEO",
                thumbnail_url=thumb_url,
                thumbnail_bytes=thumb_bytes,
                is_restricted=False,
                loading=False,
            )
        except Exception:
            return self._build_fallback_metadata(url)

    def _unfurl_general_web(self, url: str, domain: str, fetch_thumbnail: bool = True) -> LinkMetadata:
        try:
            status, final_url, html_text = self._fetch_url_text(url)
            if status in (401, 403):
                return LinkMetadata(
                    url=url,
                    title=f"Restricted ({domain}) — Configure Cookies",
                    description=f"Access to {domain} requires authentication cookies.",
                    provider=domain,
                    domain=domain,
                    file_type="LINK",
                    is_restricted=True,
                    loading=False,
                )

            if not html_text:
                return self._build_fallback_metadata(url)

            title = _extract_meta_tag(html_text, ["og:title", "twitter:title"]) or _extract_html_title(html_text)
            if not title:
                title = f"{domain} Page"

            desc = _extract_meta_tag(html_text, ["og:description", "description", "twitter:description"])
            if not desc:
                desc = f"Web page content hosted on {domain}."

            provider = _extract_meta_tag(html_text, ["og:site_name"]) or domain
            author = _extract_meta_tag(html_text, ["author", "article:author"])
            og_type = _extract_meta_tag(html_text, ["og:type"])
            thumb_url = _extract_meta_tag(html_text, ["og:image", "twitter:image"])
            if thumb_url and not thumb_url.startswith(("http://", "https://")):
                thumb_url = urllib.parse.urljoin(final_url or url, thumb_url)

            file_type = infer_file_type_from_title(title, is_folder=False, og_type=og_type)
            thumb_bytes = self.fetch_thumbnail_bytes(thumb_url) if (fetch_thumbnail and thumb_url) else None

            return LinkMetadata(
                url=url,
                title=title,
                description=desc,
                author=author,
                provider=provider,
                domain=domain,
                file_type=file_type,
                thumbnail_url=thumb_url,
                thumbnail_bytes=thumb_bytes,
                is_restricted=False,
                loading=False,
            )
        except Exception:
            return self._build_fallback_metadata(url)

    @staticmethod
    def _build_fallback_metadata(url: str) -> LinkMetadata:
        domain = extract_domain(url)
        title = f"{domain} Reference"
        description = f"External website link pointing to {domain}. Click to view full content."
        provider = domain
        file_type = "LINK"

        if "drive.google.com" in domain:
            _, is_folder = extract_gdrive_id(url)
            title = "Google Drive Folder" if is_folder else "Google Drive Asset"
            description = "Shared document or cloud storage folder hosted on Google Drive."
            provider = "Google Drive"
            file_type = "FOLDER" if is_folder else "VIDEO"
        elif "youtube.com" in domain or "youtu.be" in domain:
            title = "YouTube Video Stream"
            description = "Online video reference hosted on YouTube."
            provider = "YouTube"
            file_type = "VIDEO"
        elif "dropbox.com" in domain:
            title = "Dropbox Cloud Asset"
            description = "Shared media file or folder hosted on Dropbox."
            provider = "Dropbox"
        elif "vimeo.com" in domain:
            title = "Vimeo Video Stream"
            description = "Online video reference hosted on Vimeo."
            provider = "Vimeo"
            file_type = "VIDEO"

        return LinkMetadata(
            url=url,
            title=title,
            description=description,
            provider=provider,
            domain=domain,
            file_type=file_type,
            is_restricted=False,
            is_fallback=True,
            loading=False,
        )


unfurl_engine = UnfurlEngine.instance()
