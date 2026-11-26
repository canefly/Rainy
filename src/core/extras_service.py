"""Qt services for announcements, bug reports and opt-in analytics.

All network work runs on the thread pool; the UI thread only ever sees finished results through
signals. Failures here are never allowed to disturb the editor: announcements and analytics fail
quietly (logged), and a bug report tells the person plainly what went wrong.

See the private rainy-api plan and src/core/extras_core.py.
"""
import os
import time
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .extras_core import (
    ANALYTICS_BATCH, Announcement, AnnouncementError, AnalyticsQueue, build_bug_payload, current_user_context,
    friendly_announcement_error, install_crash_hook, parse_announcements, prepare_log,
)
from .license_core import ApiRejected, ApiUnreachable, ManifestError, SettingsStore
from .logger import get_log_file_path, get_logger

logger = get_logger("Extras")

KEY_ANALYTICS_ENABLED = "analytics_enabled"
KEY_ANALYTICS_ASKED = "analytics_asked"
KEY_ANALYTICS_FORGET = "analytics_forget_pending"

ANNOUNCEMENT_REFRESH_MS = 60 * 60_000
ANALYTICS_FLUSH_MS = 5 * 60_000
ANALYTICS_FLUSH_AT = 40          # also send as soon as this many events are waiting
LOG_READ_BYTES = 400_000


# ------------------------------------------------------------------------------ plumbing
class _Relay(QObject):
    done = Signal(object, object, object)  # (token, result, error)


class _Job(QRunnable):
    def __init__(self, token, fn: Callable, relay: _Relay):
        super().__init__()
        self._token, self._fn, self._relay = token, fn, relay

    def run(self):
        result = error = None
        try:
            result = self._fn()
        except Exception as e:  # a worker must never die silently or take the app with it
            error = e
        try:
            self._relay.done.emit(self._token, result, error)
        except RuntimeError:  # service already torn down (app closing)
            pass


def _start(pool: QThreadPool, relay: _Relay, token, fn: Callable) -> None:
    pool.start(_Job(token, fn, relay))


def read_recent_log_text(max_bytes: int = LOG_READ_BYTES) -> str:
    """The end of the active log file as text (empty if there isn't one)."""
    path = get_log_file_path()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------------------ announcements
class AnnouncementService(QObject):
    """Keeps the list of unread notices from the owner. Verified before anything is shown."""

    changed = Signal(object)  # list[Announcement], newest first

    _instance: Optional["AnnouncementService"] = None

    def __init__(self, controller=None, parent=None, clock: Callable[[], float] = time.time):
        super().__init__(parent)
        if controller is None:
            from .license_service import LicenseService
            controller = LicenseService.instance().controller
        self.controller = controller
        self._clock = clock
        self._pool = QThreadPool.globalInstance()
        self._relay = _Relay(self)
        self._relay.done.connect(self._on_done)
        self._busy = False
        self._again = False
        self.items: List[Announcement] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    @classmethod
    def instance(cls) -> "AnnouncementService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_periodic(self) -> None:
        self._timer.start(ANNOUNCEMENT_REFRESH_MS)

    def refresh(self) -> None:
        """Fetch and verify the list now. Safe to call from a live nudge, on unlock, or on a timer."""
        if self._busy:
            self._again = True
            return
        self._busy = True
        now = self._clock()
        _start(self._pool, self._relay, ("list", None),
               lambda: parse_announcements(self.controller.fetch_announcements(), self.controller.keys, now))

    def mark_read(self, announcement_id: str) -> None:
        """The editor dismissed it. It disappears at once; telling the server is best effort."""
        self.items = [a for a in self.items if a.id != announcement_id]
        self.changed.emit(list(self.items))
        _start(self._pool, self._relay, ("read", announcement_id),
               lambda: self.controller.signed_call("POST", f"/v1/announcements/{announcement_id}/read", b"{}"))

    def _on_done(self, token, result, error) -> None:
        kind = token[0]
        if kind == "read":
            if error is not None:
                logger.info("Couldn't mark an announcement as read: %s", type(error).__name__)
            return
        if kind != "list":
            return
        self._busy = False
        if error is None:
            self.items = list(result)
            self.changed.emit(list(self.items))
        elif isinstance(error, ApiRejected) and error.code in ("not_approved", "not_registered", "auth_failed"):
            pass  # not allowed to see notices (yet): nothing to show, nothing to complain about
        elif isinstance(error, (ManifestError, AnnouncementError)):
            logger.warning("Ignoring the announcements list: %s", friendly_announcement_error(error))
        elif isinstance(error, ApiUnreachable):
            logger.info("Announcements: server unreachable")
        else:
            logger.warning("Announcements check failed: %s", type(error).__name__)
        if self._again:
            self._again = False
            self.refresh()


# ------------------------------------------------------------------------------ bug reports
def friendly_bug_error(e: Exception) -> str:
    if isinstance(e, ApiRejected):
        return {
            "not_approved": "Your install needs to be approved before it can send reports.",
            "not_registered": "Your install isn't registered yet.",
            "rate_limited": "You've sent a lot of reports today. Please try again tomorrow.",
            "body_too_large": "The report was too large to send.",
            "invalid_request": "The server didn't accept this report. Check the title and try again.",
            "clock_skew": "Your computer's clock looks wrong, so the server refused the report. Fix the time and try again.",
        }.get(e.code, "The server didn't accept the report.")
    if isinstance(e, ApiUnreachable):
        return "Couldn't reach the server. Check your connection and try again."
    if isinstance(e, ValueError):
        return "The report was too large to send."
    return "Something went wrong sending the report."


class BugReportService(QObject):
    finished = Signal(bool, str, str)  # (ok, message for the person, report id)

    _instance: Optional["BugReportService"] = None

    def __init__(self, controller=None, parent=None, app_version: Optional[str] = None):
        super().__init__(parent)
        if controller is None:
            from .license_service import LicenseService
            controller = LicenseService.instance().controller
        if app_version is None:
            from .updater import APP_VERSION
            app_version = APP_VERSION
        self.controller = controller
        self.app_version = app_version
        self._pool = QThreadPool.globalInstance()
        self._relay = _Relay(self)
        self._relay.done.connect(self._on_done)
        self.sending = False

    @classmethod
    def instance(cls) -> "BugReportService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def preview_log() -> str:
        """Exactly the log text that would be attached (scrubbed), for the dialog to show."""
        home, user = current_user_context()
        return prepare_log(read_recent_log_text(), home=home, username=user)

    def send(self, title: str, description: str, include_log: bool) -> None:
        if self.sending:
            return
        self.sending = True

        def work():
            log = self.preview_log() if include_log else ""
            body = build_bug_payload(title, description, log, self.app_version)
            return self.controller.signed_call("POST", "/v1/bugs", body)

        _start(self._pool, self._relay, ("bug", None), work)

    def _on_done(self, token, result, error) -> None:
        self.sending = False
        if error is None:
            report_id = str(result.get("id", "")) if isinstance(result, dict) else ""
            self.finished.emit(True, "Thanks! Your report was sent.", report_id[:8])
        else:
            if not isinstance(error, (ApiRejected, ApiUnreachable, ValueError)):
                logger.warning("Bug report failed: %s", type(error).__name__, exc_info=error)
            self.finished.emit(False, friendly_bug_error(error), "")


# ------------------------------------------------------------------------------ analytics
class AnalyticsService(QObject):
    """Opt-in, off by default. Nothing is queued or sent unless the editor said yes."""

    enabled_changed = Signal(bool)

    _instance: Optional["AnalyticsService"] = None

    def __init__(self, controller=None, store: Optional[SettingsStore] = None, can_send: Optional[Callable[[], bool]] = None,
                 parent=None, queue: Optional[AnalyticsQueue] = None):
        super().__init__(parent)
        if controller is None:
            from .license_service import LicenseService
            controller = LicenseService.instance().controller
        if store is None:
            from .license_service import DbSettings
            store = DbSettings()
        if can_send is None:
            from .license_service import LicenseService
            can_send = lambda: LicenseService.instance().is_unlocked  # noqa: E731
        self.controller, self.store, self._can_send = controller, store, can_send
        self.queue = queue or AnalyticsQueue()
        self._pool = QThreadPool.globalInstance()
        self._relay = _Relay(self)
        self._relay.done.connect(self._on_done)
        self._flushing = False
        self._forgetting = False
        self._server_off = False
        self._enabled: Optional[bool] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.flush)
        self._undo_hook: Optional[Callable[[], None]] = None

    @classmethod
    def instance(cls) -> "AnalyticsService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- the editor's choice ---------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """Cached: track() asks on every event, and a setting that was never saved isn't cached by the store."""
        if self._enabled is None:
            self._enabled = self.store.get(KEY_ANALYTICS_ENABLED, "0") == "1"
        return self._enabled

    @property
    def asked(self) -> bool:
        return self.store.get(KEY_ANALYTICS_ASKED, "0") == "1"

    def set_enabled(self, on: bool) -> None:
        """Their answer. Turning it off also asks the server to delete what was sent before."""
        on = bool(on)
        was = self.enabled
        self.store.set(KEY_ANALYTICS_ASKED, "1")
        self.store.set(KEY_ANALYTICS_ENABLED, "1" if on else "0")
        self._enabled = on
        if on:
            self.store.set(KEY_ANALYTICS_FORGET, "0")
            self._server_off = False
        else:
            self.queue.clear()
            self.store.set(KEY_ANALYTICS_FORGET, "1")
            self.retry_forget()
        if on != was:
            self.enabled_changed.emit(on)

    # -- recording ---------------------------------------------------------------------
    def track(self, name: str, **props) -> None:
        """Record one event. Costs nothing and does nothing unless the editor opted in."""
        if not self.enabled:
            return
        self.queue.add(name, props)
        if len(self.queue) >= ANALYTICS_FLUSH_AT:
            self.flush()

    def start(self) -> None:
        """Begin periodic sending, report crashes, and finish any pending deletion request."""
        self._timer.start(ANALYTICS_FLUSH_MS)
        if self._undo_hook is None:
            self._undo_hook = install_crash_hook(lambda ev: self.track(ev["name"], **ev["props"]))
        self.retry_forget()

    def stop(self) -> None:
        self._timer.stop()
        if self._undo_hook is not None:
            self._undo_hook()
            self._undo_hook = None

    # -- sending -----------------------------------------------------------------------
    def flush(self) -> None:
        if self._flushing or not self.enabled or self._server_off or not self._can_send() or not len(self.queue):
            return
        taken = self.queue.take(ANALYTICS_BATCH)
        if not taken:
            return
        self._flushing = True
        body = self.queue.to_wire(taken)
        _start(self._pool, self._relay, ("send", taken),
               lambda: self.controller.signed_call("POST", "/v1/analytics", body))

    def flush_on_exit(self, wait_ms: int = 2500) -> None:
        """Best effort when the app closes: send what's waiting, but never hold the window open long."""
        self.flush()
        if self._flushing:              # nothing to wait for if opted out, empty, or locked
            self._pool.waitForDone(wait_ms)

    def retry_forget(self) -> None:
        if self._forgetting or self.store.get(KEY_ANALYTICS_FORGET, "0") != "1" or self.enabled:
            return
        self._forgetting = True
        _start(self._pool, self._relay, ("forget", None),
               lambda: self.controller.signed_call("POST", "/v1/analytics/forget", b"{}"))

    def _on_done(self, token, result, error) -> None:
        kind = token[0]
        if kind == "forget":
            self._forgetting = False
            if error is None:
                self.store.set(KEY_ANALYTICS_FORGET, "0")
            else:
                logger.info("Couldn't delete earlier usage data yet (%s); will retry", type(error).__name__)
            return
        if kind != "send":
            return
        self._flushing = False
        taken = token[1]
        if error is None:
            if isinstance(result, dict) and result.get("disabled"):
                self._server_off = True   # the owner switched analytics off on the server: stop for this session
                self.queue.clear()
                return
            if len(self.queue) >= ANALYTICS_BATCH:
                self.flush()
            return
        keep = not (isinstance(error, ApiRejected) and error.status in (400, 413, 422))  # a refused batch won't improve
        if keep and self.enabled:
            self.queue.put_back(taken)
        logger.info("Analytics batch not sent (%s)", type(error).__name__)


def stop_all_for_tests() -> None:
    """Drop the singletons (tests build their own services)."""
    AnnouncementService._instance = None
    BugReportService._instance = None
    AnalyticsService._instance = None
