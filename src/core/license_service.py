"""Qt wrapper around LicenseController: runs the blocking work off the UI thread, listens to the
live channel and tells the window when the license state changes.

Two ways the state moves:
- live channel (rainy-api phase 3): the server pushes approve / revoke / fresh tokens the moment they
  happen, so the app reacts in about a second. This is the normal path.
- HTTP polling: the fallback. While the live channel is healthy it only runs rarely as a safety net;
  when the channel is down it goes back to the faster schedule so nothing is missed.

See docs/rainy-api-plan.md.
"""
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .database import db
from .license_core import (
    KeyringStore, LicenseApi, LicenseController, LicenseSnapshot, LicenseState,
    api_base_url, trusted_public_keys, ws_url,
)
from .live_channel import AUTH_REJECTIONS, LiveChannel
from .logger import get_logger
from .system_specs import get_system_specs
from .updater import APP_VERSION

logger = get_logger("License")

# Polling schedule when the live channel is NOT connected
PENDING_POLL_MS = 10_000
APPROVED_POLL_MS = 10 * 60_000
RETRY_POLL_MS = 30_000
REVOKED_POLL_MS = 60_000
# Safety-net schedule while the live channel IS connected (pushes do the real work)
LIVE_PENDING_POLL_MS = 10 * 60_000
LIVE_APPROVED_POLL_MS = 60 * 60_000
LIVE_REVOKED_POLL_MS = 10 * 60_000

_NO_LIVE_NEEDED = (LicenseState.NEEDS_REGISTRATION, LicenseState.ERROR)
_DEFAULT = object()


class DbSettings:
    """SettingsStore backed by the app's SQLite settings table."""

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return db.get_setting(key, default)

    def set(self, key: str, value: str) -> None:
        db.set_setting(key, value)


def build_default_controller() -> LicenseController:
    try:
        keys = trusted_public_keys()
    except ValueError as e:
        logger.error("Ignoring malformed license key configuration: %s", e)
        keys = {}
    return LicenseController(
        LicenseApi(api_base_url(), app_version=APP_VERSION),
        KeyringStore(),
        DbSettings(),
        keys,
        specs=get_system_specs,
        app_version=APP_VERSION,
    )


class _JobSignals(QObject):
    done = Signal(object, object)  # (snapshot, kind)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], LicenseSnapshot], kind: str, signals: _JobSignals):
        super().__init__()
        self._fn = fn
        self._kind = kind
        self._signals = signals

    def run(self):
        try:
            snap = self._fn()
        except Exception as e:  # never let a worker die silently: surface it as a lock-screen error
            logger.error("License job failed: %s", e, exc_info=True)
            snap = LicenseSnapshot(LicenseState.ERROR, message=f"Unexpected error: {e}")
        try:
            self._signals.done.emit(snap, self._kind)
        except RuntimeError:  # service already torn down (app closing)
            pass


class LicenseService(QObject):
    state_changed = Signal(object)  # LicenseSnapshot
    update_nudged = Signal(str)     # the live channel says a newer version exists (version number only)
    announcements_nudged = Signal()  # the live channel says there is something new to read (no text)

    _instance: Optional["LicenseService"] = None

    def __init__(self, controller: Optional[LicenseController] = None, parent=None, live=_DEFAULT):
        super().__init__(parent)
        self.controller = controller or build_default_controller()
        self.snapshot = LicenseSnapshot(LicenseState.CHECKING)
        self._pool = QThreadPool.globalInstance()
        self._signals = _JobSignals(self)
        self._signals.done.connect(self._on_done)
        self._busy = False
        self._queued: Optional[tuple] = None
        self._push_gen = 0      # bumped whenever a live push changes state
        self._job_gen = 0       # value of _push_gen when the running job started
        self._live_ok = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._submit(self.controller.refresh, "refresh"))

        if live is _DEFAULT:
            live = LiveChannel(lambda: ws_url(api_base_url()), self.controller.build_hello, parent=self)
        self.live = live
        if self.live is not None:
            self.live.frame_received.connect(self._on_push)
            self.live.connected_changed.connect(self._on_live_state)
            self.live.rejected.connect(self._on_live_rejected)

    @classmethod
    def instance(cls) -> "LicenseService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- state -----------------------------------------------------------------------
    @property
    def is_unlocked(self) -> bool:
        return self.snapshot.unlocked

    @property
    def live_connected(self) -> bool:
        return self._live_ok

    def lock_reason(self) -> Optional[str]:
        """None when unlocked, else a short human reason (used to refuse MCP calls)."""
        if self.snapshot.unlocked:
            return None
        return "Rainy is locked: this install is not approved yet. Open the app to see its status."

    def _emit(self, snap: LicenseSnapshot) -> None:
        self.snapshot = snap
        self.state_changed.emit(snap)
        self._sync_live(snap)

    def _sync_live(self, snap: LicenseSnapshot) -> None:
        """The live channel runs whenever this install is registered, and not otherwise."""
        if self.live is None:
            return
        if snap.state in _NO_LIVE_NEEDED or not snap.device_id:
            self.live.stop()
        else:
            self.live.start()

    # -- actions ---------------------------------------------------------------------
    def start(self) -> None:
        snap = self.controller.load_local()
        self._emit(snap)
        if snap.state in (LicenseState.CHECKING, LicenseState.APPROVED):
            self._submit(self.controller.refresh, "refresh")

    def register(self, editor_name: str) -> None:
        self._emit(LicenseSnapshot(LicenseState.CHECKING, editor_name=editor_name.strip(),
                                   message="Sending your request..."))
        self._submit(lambda: self.controller.register(editor_name), "register")

    def check_now(self) -> None:
        if not self.snapshot.unlocked:
            self._emit(LicenseSnapshot(LicenseState.CHECKING, editor_name=self.snapshot.editor_name,
                                       device_id=self.snapshot.device_id, message="Checking..."))
        self._submit(self.controller.refresh, "refresh")

    # -- live channel ----------------------------------------------------------------
    def _on_push(self, frame: dict) -> None:
        if frame.get("t") == "update_available":
            version = frame.get("version")
            if isinstance(version, str):
                self.update_nudged.emit(version[:32])  # a hint only: the updater fetches the signed feed itself
            return
        if frame.get("t") == "announcements":
            self.announcements_nudged.emit()  # a hint only: the list is fetched and verified over HTTPS
            return
        snap = self.controller.apply_push(frame)
        if snap is None:
            return
        self._push_gen += 1  # any HTTP answer already in flight is now older than this
        if snap != self.snapshot:
            self._emit(snap)
        if not self._busy:
            self._reschedule(snap)

    def _on_live_state(self, ok: bool) -> None:
        self._live_ok = ok
        if not self._busy:
            self._reschedule(self.snapshot)

    def _on_live_rejected(self, code: str) -> None:
        # The server refused our hello. For signature/clock/identity problems the HTTP API explains
        # properly (clock screen, re-register...), so ask it once. Backoff stops this from looping.
        if code in AUTH_REJECTIONS and not self._busy:
            self.check_now()

    # -- plumbing --------------------------------------------------------------------
    def _submit(self, fn: Callable[[], LicenseSnapshot], kind: str) -> None:
        if self._busy:
            self._queued = (fn, kind)
            return
        self._busy = True
        self._job_gen = self._push_gen
        self._timer.stop()
        self._pool.start(_Job(fn, kind, self._signals))

    def _on_done(self, snap: LicenseSnapshot, kind: str) -> None:
        self._busy = False
        if kind == "refresh" and self._push_gen != self._job_gen:
            # A push arrived while this HTTP check was in flight. The push is newer, so this answer
            # could only drag the state backwards (e.g. "pending" after an instant approval).
            snap = self.snapshot
        else:
            self._emit(snap)
        if self._queued is not None:
            (fn, queued_kind), self._queued = self._queued, None
            self._submit(fn, queued_kind)
            return
        self._reschedule(snap)

    def _interval_ms(self, state: LicenseState) -> Optional[int]:
        if self._live_ok:
            live = {LicenseState.PENDING: LIVE_PENDING_POLL_MS, LicenseState.APPROVED: LIVE_APPROVED_POLL_MS,
                    LicenseState.REVOKED: LIVE_REVOKED_POLL_MS}.get(state)
            if live:
                return live
        return {
            LicenseState.PENDING: PENDING_POLL_MS,
            LicenseState.APPROVED: APPROVED_POLL_MS,
            LicenseState.REVOKED: REVOKED_POLL_MS,
            LicenseState.UNREACHABLE: RETRY_POLL_MS,
            LicenseState.EXPIRED: RETRY_POLL_MS,
            LicenseState.CLOCK_SKEW: RETRY_POLL_MS,
            LicenseState.CHECKING: RETRY_POLL_MS,
        }.get(state)

    def _reschedule(self, snap: LicenseSnapshot) -> None:
        interval = self._interval_ms(snap.state)
        if interval:
            self._timer.start(interval)
        else:
            self._timer.stop()
