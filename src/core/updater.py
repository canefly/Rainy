"""
Rainy Tools Native - Asynchronous Update Service
Implements the No-OTA update policy: checks remote endpoints, detects new releases,
and provides download links and changelogs without disruptive forced auto-installations.
"""

import re
import json
import urllib.request
from typing import Optional, List, Tuple
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

from .logger import get_logger

logger = get_logger("Updater")

APP_VERSION = "1.2.0"
VERSION_ENDPOINT = "https://rainy.canefly.xyz/api/version.php"


def parse_version(v_str: str) -> Tuple[int, int, int]:
    """Parse semantic version string like '1.2.0' or 'v1.2.3' into a comparable tuple."""
    clean = re.sub(r'^[^\d]*', '', (v_str or "").strip())
    parts = []
    for segment in clean.split('.'):
        digits = re.match(r'^\d+', segment)
        parts.append(int(digits.group(0)) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


class UpdateSignals(QObject):
    update_available = Signal(str, str, list)  # (latest_version, download_url, changelog)
    up_to_date = Signal(str)                  # (current_version)
    check_failed = Signal(str)                # (error_message)


class UpdateCheckWorker(QRunnable):
    """Background worker to query Cane's version endpoint without blocking GUI."""

    def __init__(self, endpoint: str = VERSION_ENDPOINT):
        super().__init__()
        self.endpoint = endpoint
        self.signals = UpdateSignals()

    def run(self):
        req = urllib.request.Request(
            self.endpoint,
            headers={
                "User-Agent": "RainyTools/2.0 Native",
                "Accept": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)

                latest_version = data.get("latest_version", APP_VERSION)
                download_url = data.get("download_url", "")
                changelog = data.get("changelog", [])

                curr_tuple = parse_version(APP_VERSION)
                latest_tuple = parse_version(latest_version)

                def _safe_emit(signal, *args):
                    try:
                        signal.emit(*args)
                    except RuntimeError:
                        pass

                if latest_tuple > curr_tuple:
                    logger.info(f"[Updater] New version detected: v{latest_version} (Current: v{APP_VERSION})")
                    _safe_emit(self.signals.update_available, latest_version, download_url, changelog)
                else:
                    logger.debug(f"[Updater] Application is up to date: v{APP_VERSION}")
                    _safe_emit(self.signals.up_to_date, APP_VERSION)

        except Exception as e:
            logger.warning(f"[Updater] Failed to check for updates: {e}")
            try:
                self.signals.check_failed.emit(str(e))
            except RuntimeError:
                pass


class UpdateService(QObject):
    """Singleton service managing application update state and background polling."""
    _instance: Optional['UpdateService'] = None

    update_available = Signal(str, str, list)
    up_to_date = Signal(str)
    check_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.thread_pool = QThreadPool.globalInstance()
        self.current_version = APP_VERSION
        self.latest_version: Optional[str] = None
        self.download_url: Optional[str] = None
        self.changelog: List[str] = []
        self.is_checking = False
        self.has_update = False

    @classmethod
    def instance(cls) -> 'UpdateService':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def check_for_updates(self, on_available=None, on_latest=None, on_failed=None):
        """Dispatches an asynchronous update check."""
        if self.is_checking:
            return

        self.is_checking = True
        worker = UpdateCheckWorker()

        def _on_available(latest_v, url, chlog):
            self.is_checking = False
            self.has_update = True
            self.latest_version = latest_v
            self.download_url = url
            self.changelog = chlog
            self.update_available.emit(latest_v, url, chlog)
            if on_available:
                on_available(latest_v, url, chlog)

        def _on_latest(curr_v):
            self.is_checking = False
            self.has_update = False
            self.latest_version = curr_v
            self.up_to_date.emit(curr_v)
            if on_latest:
                on_latest(curr_v)

        def _on_failed(err):
            self.is_checking = False
            self.check_failed.emit(err)
            if on_failed:
                on_failed(err)

        worker.signals.update_available.connect(_on_available)
        worker.signals.up_to_date.connect(_on_latest)
        worker.signals.check_failed.connect(_on_failed)

        self.thread_pool.start(worker)
