"""First-run setup: make sure the helper programs Rainy needs are on this computer.

Rainy does not ship libmpv (the video player) or FFmpeg/FFprobe (transcoding and probing) inside the
installer. After the license check passes, whatever is missing is downloaded once into the app folder
(see app_paths) from the exact files the server's signed list names, and is verified before it is kept.

This service only decides *what* is missing and runs the downloads one after another; the engine
manager does the verified downloading, and the UI (ui/engine_setup_controller.py) shows the progress.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Signal

from .engine_manager import EngineDownloadWorker, is_mpv_available
from .ffmpeg_encoder import get_ffmpeg_path
from .logger import get_logger
from .media_probe import get_ffprobe_path

logger = get_logger("engine_setup")

# engine id -> what to call it when talking to the person using the app
LABELS = {"libmpv": "the video player", "ffmpeg": "the video tools"}

IDLE, RUNNING, DONE, FAILED, CANCELLED = "idle", "running", "done", "failed", "cancelled"


def missing_engines() -> List[str]:
    """Engine ids that are needed and not found anywhere the app looks."""
    out = []
    if not is_mpv_available():
        out.append("libmpv")
    if not (get_ffmpeg_path() and get_ffprobe_path()):
        out.append("ffmpeg")
    return out


@dataclass(frozen=True)
class SetupState:
    phase: str = IDLE
    engine: str = ""          # the one being worked on (or that failed)
    step: int = 0             # 1-based position in the queue
    total: int = 0
    percent: int = 0          # 0-100 over the whole queue
    message: str = ""         # a sentence fit to show

    @property
    def label(self) -> str:
        return LABELS.get(self.engine, self.engine)


class EngineSetupService(QObject):
    state_changed = Signal(object)  # SetupState

    def __init__(self, parent=None, *, missing: Callable[[], List[str]] = missing_engines,
                 worker_factory: Callable[[str], object] = EngineDownloadWorker):
        super().__init__(parent)
        self._missing = missing
        self._factory = worker_factory
        self._queue: List[str] = []
        self._index = 0
        self._worker = None
        self._state = SetupState()

    # -- state -----------------------------------------------------------------------
    @property
    def state(self) -> SetupState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state.phase == RUNNING

    def _set(self, **kw) -> None:
        self._state = SetupState(**{**self._state.__dict__, **kw})
        self.state_changed.emit(self._state)

    # -- control ---------------------------------------------------------------------
    def start(self) -> bool:
        """Begin downloading whatever is missing. Returns False when nothing is needed (or already running)."""
        if self.running:
            return False
        self._queue = list(self._missing())
        self._index = 0
        if not self._queue:
            self._state = SetupState(phase=DONE, message="Everything Rainy needs is already here.")
            return False
        self._begin_current()
        return True

    retry = start

    def cancel(self) -> None:
        if not self.running:
            return
        if self._worker is not None:
            self._worker.cancel()
        # the worker reports "cancelled" through its finished signal, which ends the run

    # -- the queue -------------------------------------------------------------------
    def _begin_current(self) -> None:
        engine = self._queue[self._index]
        total = len(self._queue)
        self._set(phase=RUNNING, engine=engine, step=self._index + 1, total=total,
                  percent=int(self._index * 100 / total),
                  message=f"Downloading {LABELS.get(engine, engine)}" + (f" ({self._index + 1} of {total})" if total > 1 else ""))
        worker = self._factory(engine)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _on_progress(self, engine: str, pct: int, text: str) -> None:
        if not self.running or engine != self._state.engine:
            return
        total = max(1, len(self._queue))
        overall = int((self._index * 100 + max(0, min(100, pct))) / total)
        self._set(percent=max(self._state.percent, overall),
                  message=(text or self._state.message))

    def _on_finished(self, engine: str, ok: bool, message: str) -> None:
        if not self.running or engine != self._state.engine:
            return
        self._worker = None
        if ok:
            self._index += 1
            if self._index < len(self._queue):
                self._begin_current()
                return
            self._set(phase=DONE, percent=100, message="Everything is ready.")
            return
        cancelled = "cancel" in (message or "").lower()
        logger.warning("Engine setup stopped at %s: %s", engine, message)
        self._set(phase=CANCELLED if cancelled else FAILED,
                  message="Setup was cancelled." if cancelled else (message or "The download failed."))

    def shutdown(self) -> None:
        """Window closing: stop any download and wait briefly for it."""
        w, self._worker = self._worker, None
        if w is not None:
            try:
                w.cancel()
                w.wait(3000)
            except Exception:  # noqa: BLE001
                pass
