"""Qt wrapper around LicenseController: runs the blocking work off the UI thread, keeps polling
on a timer and tells the window when the license state changes.

Polling is a stopgap until the live WebSocket channel exists (rainy-api phase 3): while waiting
for approval we ask every few seconds, and once approved we re-confirm every few minutes so a
revoke lands reasonably fast. See docs/rainy-api-plan.md.
"""
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .database import db
from .license_core import (
    KeyringStore, LicenseApi, LicenseController, LicenseSnapshot, LicenseState,
    api_base_url, trusted_public_keys,
)
from .logger import get_logger
from .system_specs import get_system_specs
from .updater import APP_VERSION

logger = get_logger("License")

PENDING_POLL_MS = 10_000
APPROVED_POLL_MS = 10 * 60_000
RETRY_POLL_MS = 30_000
REVOKED_POLL_MS = 60_000


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
    done = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], LicenseSnapshot], signals: _JobSignals):
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self):
        try:
            snap = self._fn()
        except Exception as e:  # never let a worker die silently: surface it as a lock-screen error
            logger.error("License job failed: %s", e, exc_info=True)
            snap = LicenseSnapshot(LicenseState.ERROR, message=f"Unexpected error: {e}")
        try:
            self._signals.done.emit(snap)
        except RuntimeError:  # service already torn down (app closing)
            pass


class LicenseService(QObject):
    state_changed = Signal(object)  # LicenseSnapshot

    _instance: Optional["LicenseService"] = None

    def __init__(self, controller: Optional[LicenseController] = None, parent=None):
        super().__init__(parent)
        self.controller = controller or build_default_controller()
        self.snapshot = LicenseSnapshot(LicenseState.CHECKING)
        self._pool = QThreadPool.globalInstance()
        self._signals = _JobSignals(self)
        self._signals.done.connect(self._on_done)
        self._busy = False
        self._queued: Optional[Callable[[], LicenseSnapshot]] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._submit(self.controller.refresh))

    @classmethod
    def instance(cls) -> "LicenseService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- state -----------------------------------------------------------------------
    @property
    def is_unlocked(self) -> bool:
        return self.snapshot.unlocked

    def lock_reason(self) -> Optional[str]:
        """None when unlocked, else a short human reason (used to refuse MCP calls)."""
        if self.snapshot.unlocked:
            return None
        return "Rainy is locked: this install is not approved yet. Open the app to see its status."

    def _emit(self, snap: LicenseSnapshot) -> None:
        self.snapshot = snap
        self.state_changed.emit(snap)

    # -- actions ---------------------------------------------------------------------
    def start(self) -> None:
        snap = self.controller.load_local()
        self._emit(snap)
        if snap.state in (LicenseState.CHECKING, LicenseState.APPROVED):
            self._submit(self.controller.refresh)

    def register(self, editor_name: str) -> None:
        self._emit(LicenseSnapshot(LicenseState.CHECKING, editor_name=editor_name.strip(),
                                   message="Sending your request..."))
        self._submit(lambda: self.controller.register(editor_name))

    def check_now(self) -> None:
        if not self.snapshot.unlocked:
            self._emit(LicenseSnapshot(LicenseState.CHECKING, editor_name=self.snapshot.editor_name,
                                       device_id=self.snapshot.device_id, message="Checking..."))
        self._submit(self.controller.refresh)

    # -- plumbing --------------------------------------------------------------------
    def _submit(self, fn: Callable[[], LicenseSnapshot]) -> None:
        if self._busy:
            self._queued = fn
            return
        self._busy = True
        self._timer.stop()
        self._pool.start(_Job(fn, self._signals))

    def _on_done(self, snap: LicenseSnapshot) -> None:
        self._busy = False
        self._emit(snap)
        if self._queued is not None:
            fn, self._queued = self._queued, None
            self._submit(fn)
            return
        interval = {
            LicenseState.PENDING: PENDING_POLL_MS,
            LicenseState.APPROVED: APPROVED_POLL_MS,
            LicenseState.REVOKED: REVOKED_POLL_MS,
            LicenseState.UNREACHABLE: RETRY_POLL_MS,
            LicenseState.EXPIRED: RETRY_POLL_MS,
            LicenseState.CLOCK_SKEW: RETRY_POLL_MS,
            LicenseState.CHECKING: RETRY_POLL_MS,
        }.get(snap.state)
        if interval:
            self._timer.start(interval)
