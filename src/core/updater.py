"""
Rainy Tools Native - Update Service (Qt).

Checks the *signed* update feed from rainy-api, downloads the installer on request, verifies it
against the signed SHA-256 before anything runs it, and launches it when the user says so. There is
no silent OTA: the user always chooses to install, except when this version is below the minimum
the server says is still supported, in which case the window locks until they update.

The trust decisions live in update_core.py; this file is the thread and signal plumbing.
"""
import os
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, QTimer, Signal

from .license_core import (
    ApiRejected, ApiUnreachable, LicenseApi, ManifestError, api_base_url, dev_overrides_allowed,
    trusted_public_keys,
)
from .logger import get_logger
from .update_core import (
    DownloadError, UpdateManifest, UpdateStatus, download_verified, evaluate, find_verified_download,
    friendly_download_error, installer_path_for, launch_installer, parse_feed, parse_version, prune_updates,
    updates_dir,
)

logger = get_logger("Updater")

APP_VERSION = "2.0.0"
CHECK_INTERVAL_MS = 6 * 3600 * 1000   # re-check every 6 hours while the app stays open

__all__ = ["APP_VERSION", "UpdateService", "UpdateStatus", "parse_version", "update_checks_disabled"]


def update_checks_disabled() -> bool:
    """Dev/test switch (KNOT_NO_UPDATE_CHECK=1). Never honored in compiled builds."""
    return dev_overrides_allowed() and os.environ.get("KNOT_NO_UPDATE_CHECK") == "1"


class _Signals(QObject):
    checked = Signal(object, str, object)      # (UpdateManifest | None, error, Path | None already downloaded)
    progress = Signal(int, int)                # (bytes so far, total bytes)
    downloaded = Signal(object, str)           # (Path | None, error message)


class _CheckJob(QRunnable):
    def __init__(self, fetch: Callable[[], Optional[UpdateManifest]], current: str, directory: Path, signals: _Signals):
        super().__init__()
        self._fetch, self._current, self._dir, self._signals = fetch, current, directory, signals

    def run(self):
        manifest, error, existing = None, "", None
        try:
            manifest = self._fetch()
            if manifest is not None and evaluate(self._current, manifest) is not UpdateStatus.UP_TO_DATE:
                existing = find_verified_download(manifest, self._dir)   # hashes a big file: keep it off the UI thread
        except ManifestError as e:
            logger.warning("Update feed failed verification: %s", e.code)
            error = "The update information from the server failed verification, so it was ignored."
        except (ApiUnreachable, ApiRejected) as e:
            logger.info("Update check couldn't reach the server: %s", e)
            error = "Couldn't reach the update server."
        except Exception as e:
            logger.warning("Update check failed: %s", e)
            error = f"Update check failed: {e}"
        try:
            self._signals.checked.emit(manifest, error, existing)
        except RuntimeError:  # service torn down while we ran
            pass


class _DownloadJob(QRunnable):
    def __init__(self, manifest: UpdateManifest, directory: Path, cancelled: Callable[[], bool], signals: _Signals,
                 opener: Optional[Callable] = None):
        super().__init__()
        self._m, self._dir, self._cancelled, self._signals, self._opener = manifest, directory, cancelled, signals, opener

    def run(self):
        m = self._m
        last_pct = -1

        def progress(got: int, total: int):
            nonlocal last_pct
            pct = int(got * 100 / total) if total else -1
            if pct != last_pct:            # one signal per percent, not per 64 KB chunk
                last_pct = pct
                try:
                    self._signals.progress.emit(got, total)
                except RuntimeError:
                    pass

        path: Optional[Path] = None
        error = ""
        try:
            dest = installer_path_for(m, self._dir)
            prune_updates(self._dir, keep=None)
            path = download_verified(m.url, dest, sha256=m.sha256, size=m.size, progress=progress,
                                     cancelled=self._cancelled, opener=self._opener)
        except DownloadError as e:
            logger.warning("Update download rejected: %s", e.code)
            error = friendly_download_error(e)
        except Exception as e:  # never let the worker die silently
            logger.error("Update download failed: %s", e, exc_info=True)
            error = f"Download failed: {e}"
        try:
            self._signals.downloaded.emit(path, error)
        except RuntimeError:
            pass


class UpdateService(QObject):
    """Singleton: the app's update state. The window, About page, Preferences and title bar read it."""
    _instance: Optional["UpdateService"] = None

    # Kept from the old service so existing wiring (title-bar badge, About) keeps working
    update_available = Signal(str, str, list)   # (latest_version, download_url, changelog)
    up_to_date = Signal(str)
    check_failed = Signal(str)
    # New
    state_changed = Signal()                    # anything the About card shows changed
    required_changed = Signal(bool)             # this version fell below (or rose above) the minimum supported
    download_progress = Signal(int, int)
    download_finished = Signal(bool, str)       # (ok, message)
    install_started = Signal()

    def __init__(self, *, api: Optional[LicenseApi] = None, keys=None, clock: Callable[[], float] = time.time,
                 directory: Optional[Path] = None, opener: Optional[Callable] = None,
                 starter: Optional[Callable[[Path], None]] = None, current_version: str = APP_VERSION,
                 parent=None):
        super().__init__(parent)
        self.thread_pool = QThreadPool.globalInstance()
        self.current_version = current_version
        self._api, self._keys, self._clock = api, keys, clock
        self._dir = directory or updates_dir()
        self._opener, self._starter = opener, starter

        self.manifest: Optional[UpdateManifest] = None
        self.status = UpdateStatus.UP_TO_DATE
        self.error = ""
        self.last_checked: Optional[float] = None
        self.is_checking = False
        self.is_downloading = False
        self.downloaded_path: Optional[Path] = None
        self.download_error = ""
        self.progress: Tuple[int, int] = (0, 0)
        self._cancel = False
        self._callbacks: List[tuple] = []

        self._signals = _Signals(self)
        self._signals.checked.connect(self._on_checked)
        self._signals.progress.connect(self._on_progress)
        self._signals.downloaded.connect(self._on_downloaded)
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.check_for_updates())

    @classmethod
    def instance(cls) -> "UpdateService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- derived -----------------------------------------------------------------------
    # Legacy attribute names kept for existing callers
    @property
    def latest_version(self) -> Optional[str]:
        return self.manifest.version if self.manifest else None

    @property
    def download_url(self) -> Optional[str]:
        return self.manifest.url if self.manifest else None

    @property
    def changelog(self) -> List[str]:
        return list(self.manifest.notes) if self.manifest else []

    @property
    def has_update(self) -> bool:
        return self.status is not UpdateStatus.UP_TO_DATE

    @property
    def required(self) -> bool:
        return self.status is UpdateStatus.REQUIRED

    # -- checking ----------------------------------------------------------------------
    def _make_fetch(self) -> Callable[[], Optional[UpdateManifest]]:
        def fetch():
            api = self._api or LicenseApi(api_base_url(), app_version=self.current_version)
            keys = self._keys if self._keys is not None else trusted_public_keys()
            return parse_feed(api.get_version(), keys, self._clock())
        return fetch

    def start_periodic(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def on_nudge(self, version: str) -> None:
        """The live channel said a newer version exists. Only the version number came over the
        socket, so confirm it by fetching the signed feed (the socket is never trusted for a URL)."""
        if parse_version(version) > parse_version(self.current_version):
            self.check_for_updates()

    def check_for_updates(self, on_available=None, on_latest=None, on_failed=None) -> None:
        if update_checks_disabled():
            return
        if on_available or on_latest or on_failed:
            self._callbacks.append((on_available, on_latest, on_failed))
        if self.is_checking:
            return
        self.is_checking = True
        self.state_changed.emit()
        self.thread_pool.start(_CheckJob(self._make_fetch(), self.current_version, self._dir, self._signals))

    def _on_checked(self, manifest: Optional[UpdateManifest], error: str, existing: Optional[Path]) -> None:
        self.is_checking = False
        callbacks, self._callbacks = self._callbacks, []
        if error:
            self.error = error
            self.check_failed.emit(error)
            for _, _, failed in callbacks:
                if failed:
                    failed(error)
            self.state_changed.emit()
            return

        was_required = self.required
        self.error = ""
        self.last_checked = self._clock()
        self.manifest = manifest
        self.status = evaluate(self.current_version, manifest)
        if manifest is None or self.status is UpdateStatus.UP_TO_DATE:
            self.downloaded_path = None
        elif existing is not None:
            self.downloaded_path = existing        # already on disk and verified: skip straight to install
        elif self.downloaded_path is not None and not self.downloaded_path.is_file():
            self.downloaded_path = None

        if self.status is UpdateStatus.UP_TO_DATE:
            self.up_to_date.emit(self.current_version)
            for _, latest, _ in callbacks:
                if latest:
                    latest(self.current_version)
        else:
            assert manifest is not None
            self.update_available.emit(manifest.version, manifest.url, list(manifest.notes))
            for available, _, _ in callbacks:
                if available:
                    available(manifest.version, manifest.url, list(manifest.notes))
        if was_required != self.required:
            self.required_changed.emit(self.required)
        self.state_changed.emit()

    # -- downloading -------------------------------------------------------------------
    def start_download(self) -> None:
        if self.manifest is None or self.status is UpdateStatus.UP_TO_DATE or self.is_downloading:
            return
        if self.downloaded_path is not None:
            self.download_finished.emit(True, "Update downloaded and verified.")
            return
        self.is_downloading = True
        self.download_error = ""
        self._cancel = False
        self.progress = (0, self.manifest.size or 0)
        self.thread_pool.start(_DownloadJob(self.manifest, self._dir, lambda: self._cancel, self._signals, self._opener))
        self.state_changed.emit()

    def cancel_download(self) -> None:
        self._cancel = True

    def _on_progress(self, got: int, total: int) -> None:
        self.progress = (got, total)
        self.download_progress.emit(got, total)

    def _on_downloaded(self, path: Optional[Path], error: str) -> None:
        self.is_downloading = False
        if path is not None:
            self.downloaded_path = Path(path)
            self.download_error = ""
            self.download_finished.emit(True, "Update downloaded and verified.")
        else:
            self.download_error = error
            self.download_finished.emit(False, error)
        self.state_changed.emit()

    # -- installing --------------------------------------------------------------------
    def install(self, quit_app: bool = True) -> Tuple[bool, str]:
        """Launch the downloaded installer (after re-verifying it) and close the app so it can be replaced."""
        if self.manifest is None or self.downloaded_path is None:
            return False, "There's no downloaded update to install."
        try:
            launch_installer(self.downloaded_path, self.manifest, directory=self._dir, starter=self._starter)
        except DownloadError as e:
            self.downloaded_path = None
            self.download_error = friendly_download_error(e)
            self.state_changed.emit()
            return False, self.download_error
        except OSError as e:
            return False, f"Couldn't start the installer: {e}"
        self.install_started.emit()
        if quit_app:
            QTimer.singleShot(1500, QCoreApplication.quit)
        return True, "Installer started."
