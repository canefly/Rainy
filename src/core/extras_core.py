"""Announcements, bug reports and opt-in analytics: the parts that need no Qt.

Talks to rainy-api (the private rainy-api plan, "Announcements, bug reports and analytics").

Principles
- Announcements are text the owner wants editors to read. They arrive over HTTPS in a manifest signed
  with the server key (the live channel only nudges), are checked like the update feed, and are only
  ever shown as plain text: no links, no HTML.
- Bug reports are sent only when the editor presses Send, and show exactly what will be included.
  Personal paths, the Windows user name and e-mail addresses are removed from the log first.
- Analytics are opt-in and off by default. An event is a short name plus a few flat values. There is
  deliberately no way to attach free text, file names or project titles: validation here mirrors the
  server, which drops anything else.
"""
import json
import math
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Mapping, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .license_core import ManifestError, verify_manifest

# ------------------------------------------------------------------------------ announcements
ANNOUNCEMENT_MAX_AGE_SECONDS = 2 * 86400   # a replayed old list can't hide a new notice for long
SEVERITIES = ("info", "warning", "critical")
TITLE_MAX = 120
BODY_MAX = 2000
MAX_ITEMS = 20
_ID_RE = re.compile(r"[0-9a-f]{32}")
_TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # everything except \t \n \r


class AnnouncementError(Exception):
    """A signed list that is nonetheless malformed. Treated like a failed check: nothing is shown."""


@dataclass(frozen=True)
class Announcement:
    id: str
    title: str
    body: str
    severity: str
    created_at: int
    expires_at: Optional[int]


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def announcement_from_payload(raw) -> Announcement:
    if not isinstance(raw, dict):
        raise AnnouncementError("item is not an object")
    id_, title, body, severity = raw.get("id"), raw.get("title"), raw.get("body"), raw.get("severity")
    created, expires = raw.get("created_at"), raw.get("expires_at")
    if not isinstance(id_, str) or not _ID_RE.fullmatch(id_):
        raise AnnouncementError("bad id")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX or "\n" in title or _TEXT_CONTROL_RE.search(title):
        raise AnnouncementError("bad title")
    if not isinstance(body, str) or len(body) > BODY_MAX or _TEXT_CONTROL_RE.search(body):
        raise AnnouncementError("bad body")
    if severity not in SEVERITIES:
        raise AnnouncementError("bad severity")
    if not _is_int(created) or not (expires is None or _is_int(expires)):
        raise AnnouncementError("bad time")
    return Announcement(id_, title, body, severity, created, expires)


def parse_announcements(reply, keys: Mapping[str, Ed25519PublicKey], now: float) -> List[Announcement]:
    """Verify the signed list and return the notices that are live right now, newest first.

    Raises ManifestError (bad signature, wrong kind, too old...) or AnnouncementError."""
    token = reply.get("manifest") if isinstance(reply, dict) else None
    if not isinstance(token, str):
        raise ManifestError("malformed")
    payload = verify_manifest(token, keys, now, kind="announcements", max_age_seconds=ANNOUNCEMENT_MAX_AGE_SECONDS)
    items = payload.get("announcements")
    if not isinstance(items, list) or len(items) > 50:
        raise AnnouncementError("bad list")
    out = [announcement_from_payload(i) for i in items]
    out = [a for a in out if a.expires_at is None or a.expires_at > now]   # the server hides expired ones; so do we
    out.sort(key=lambda a: (-a.created_at, a.id))
    return out[:MAX_ITEMS]


def friendly_announcement_error(e: Exception) -> str:
    if isinstance(e, ManifestError):
        return {"bad_signature": "The announcements list failed verification.",
                "stale": "The announcements list was out of date."}.get(e.code, "The announcements list wasn't valid.")
    return "The announcements list wasn't valid."


# ------------------------------------------------------------------------------ bug reports
BUG_TITLE_MAX = 120
BUG_DESCRIPTION_MAX = 4000
BUG_LOG_MAX_CHARS = 100_000        # well under the server's cap, so even escaped text fits its body limit
BUG_BODY_MAX_BYTES = 300_000       # the server accepts 320,000

_USER_DIR_RE = re.compile(r"(?i)((?:[A-Za-z]:)?[\\/]+(?:Users|home)[\\/]+)[^\\/\s\"'<>|:]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_SECRETISH_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}(?:\.[A-Za-z0-9_-]{20,})?\b")   # tokens, long ids and hashes


def scrub_text(text: str, *, home: Optional[str] = None, username: Optional[str] = None) -> str:
    """Remove what identifies the person from a log before it leaves the machine.

    - the home folder becomes ~ and any C:\\Users\\<name> becomes C:\\Users\\<user>
    - the Windows user name, wherever else it appears, becomes <user>
    - e-mail addresses become <email>
    - long token-like strings (licence tokens, keys) become <redacted>
    Project and media file names are left alone on purpose, since they are often what a bug is about;
    the editor sees the final text before sending and can untick the log."""
    text = text or ""
    text = _EMAIL_RE.sub("<email>", text)          # first, so a user name inside an address doesn't leave the domain behind
    if home:
        for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            if len(variant) > 3:
                text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE)
    text = _USER_DIR_RE.sub(lambda m: m.group(1) + "<user>", text)
    if username and len(username) >= 3:
        text = re.sub(re.escape(username), "<user>", text, flags=re.IGNORECASE)
    text = _SECRETISH_RE.sub("<redacted>", text)
    return text


def current_user_context() -> Tuple[str, str]:
    """(home folder, user name) of whoever is running the app."""
    home = os.path.expanduser("~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or os.path.basename(home.rstrip("\\/")) or ""
    return home, user


def prepare_log(raw_log: str, *, home: Optional[str] = None, username: Optional[str] = None,
                max_chars: int = BUG_LOG_MAX_CHARS) -> str:
    """Newest part of the log (that's where the problem is), scrubbed."""
    raw_log = raw_log or ""
    if len(raw_log) > max_chars:
        raw_log = raw_log[-max_chars:]
        nl = raw_log.find("\n")
        if 0 <= nl < 400:                       # start on a whole line
            raw_log = raw_log[nl + 1:]
    return scrub_text(raw_log, home=home, username=username)


def build_bug_payload(title: str, description: str, log: str, app_version: str,
                      max_body_bytes: int = BUG_BODY_MAX_BYTES) -> bytes:
    """The exact request body. If it would be too big, the oldest part of the log is dropped."""
    title = (title or "").strip()[:BUG_TITLE_MAX]
    description = (description or "").strip()[:BUG_DESCRIPTION_MAX]

    def encode(log_text: str) -> bytes:
        data = {"title": title, "description": description, "log": log_text, "app_version": (app_version or "")[:32]}
        if not data["app_version"]:
            del data["app_version"]
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    body = encode(log)
    while len(body) > max_body_bytes and log:
        log = log[len(log) // 4 + 1:]           # keep the newest three quarters, try again
        body = encode(log)
    if len(body) > max_body_bytes:              # description alone is too big: cannot happen within its cap, but never send it
        raise ValueError("report too large")
    return body


# ------------------------------------------------------------------------------ analytics
ANALYTICS_BATCH = 50
ANALYTICS_QUEUE_MAX = 200
ANALYTICS_MAX_PROPS = 8
ANALYTICS_MAX_STRING = 64
ANALYTICS_MAX_AGE = 24 * 3600
_NAME_RE = re.compile(r"[a-z][a-z0-9_.]{0,47}")
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
# String values are identifiers, not text: letters, digits and _ . : + - only. That rules out spaces,
# slashes and backslashes, so a file path, a project title or a sentence can't be sent by accident.
_VALUE_RE = re.compile(r"[A-Za-z0-9_.:+-]{0,64}")


def make_event(name: str, props: Optional[Mapping] = None) -> Optional[dict]:
    """A valid event, or None. Never raises, so calling code can't be broken by a typo in a name."""
    try:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            return None
        clean: Dict[str, object] = {}
        for k, v in (props or {}).items():
            if not isinstance(k, str) or not _KEY_RE.fullmatch(k) or len(clean) >= ANALYTICS_MAX_PROPS:
                return None
            if isinstance(v, bool):
                clean[k] = v
            elif isinstance(v, int):
                if abs(v) > 2 ** 53:
                    return None
                clean[k] = v
            elif isinstance(v, float):
                if not math.isfinite(v):
                    return None
                clean[k] = v
            elif isinstance(v, str):
                if len(v) > ANALYTICS_MAX_STRING or not _VALUE_RE.fullmatch(v):
                    return None
                clean[k] = v
            else:
                return None
        return {"name": name, "props": clean}
    except Exception:
        return None


class AnalyticsQueue:
    """Events waiting to be sent. Bounded, thread safe, never grows without limit."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, maxlen: int = ANALYTICS_QUEUE_MAX):
        self._clock = clock
        self._items: Deque[Tuple[float, dict]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def add(self, name: str, props: Optional[Mapping] = None) -> bool:
        ev = make_event(name, props)
        if ev is None:
            return False
        with self._lock:
            self._items.append((self._clock(), ev))
        return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def take(self, n: int = ANALYTICS_BATCH) -> List[Tuple[float, dict]]:
        """Remove up to n events (oldest first), dropping any that are too old to be useful."""
        now = self._clock()
        with self._lock:
            while self._items and now - self._items[0][0] > ANALYTICS_MAX_AGE:
                self._items.popleft()
            out = []
            while self._items and len(out) < n:
                out.append(self._items.popleft())
            return out

    def put_back(self, taken: List[Tuple[float, dict]]) -> None:
        """A send failed: return the events to the front, in order, within the size limit."""
        with self._lock:
            room = (self._items.maxlen or ANALYTICS_QUEUE_MAX) - len(self._items)
            for item in reversed(taken[-room:] if room > 0 else []):
                self._items.appendleft(item)

    def to_wire(self, taken: List[Tuple[float, dict]]) -> bytes:
        now = self._clock()
        events = [{"name": ev["name"], "props": ev["props"], "age": max(0, min(ANALYTICS_MAX_AGE, int(now - t)))}
                  for t, ev in taken]
        return json.dumps({"events": events}, separators=(",", ":")).encode("utf-8")


_IDENT_RE = re.compile(r"[^A-Za-z0-9_.]")


def crash_event(exc_type, exc_tb) -> Optional[dict]:
    """What went wrong and roughly where: the exception's class name and the innermost function.
    Never the message (it can hold file names or text), never a file path."""
    try:
        kind = _IDENT_RE.sub("", getattr(exc_type, "__name__", "") or "Error")[:48] or "Error"
        where = "unknown"
        tb = exc_tb
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        if tb is not None:
            module = _IDENT_RE.sub("", str(tb.tb_frame.f_globals.get("__name__", "") or "?"))
            func = _IDENT_RE.sub("", tb.tb_frame.f_code.co_name)
            where = f"{module}:{func}"[:ANALYTICS_MAX_STRING] or "unknown"
        return make_event("crash", {"kind": kind, "where": where})
    except Exception:
        return None


def install_crash_hook(sink: Callable[[dict], None]) -> Callable[[], None]:
    """Report uncaught exceptions through `sink` (and still run the previous hook). Returns an undo."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
                ev = crash_event(exc_type, tb)
                if ev is not None:
                    sink(ev)
        except Exception:
            pass
        previous(exc_type, exc, tb)

    sys.excepthook = hook

    def undo():
        if sys.excepthook is hook:
            sys.excepthook = previous

    return undo
