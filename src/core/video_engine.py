import os
import sys
from typing import Optional, Callable
from PySide6.QtCore import QObject, Signal

from .engine_manager import is_mpv_available, register_engine_dll_directories, get_mpv_path
from .logger import get_logger

logger = get_logger("video_engine")


class MpvPlayer(QObject):
    """
    Hardware-accelerated libmpv transport controller for the A-Roll Source Monitor.
    Binds directly to a native QWidget HWND surface via MPV's `wid` handle.
    """
    time_pos_changed = Signal(float)       # current position in seconds
    duration_changed = Signal(float)       # total clip duration in seconds
    playback_state_changed = Signal(bool)  # True = playing, False = paused
    file_loaded = Signal(str, float)       # (filepath, duration)

    def __init__(self, wid: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.wid = wid
        self._mpv = None
        self._current_file: Optional[str] = None
        self._current_speed: float = 1.0
        self._is_playing: bool = False
        self._duration: float = 0.0

        if is_mpv_available():
            self._init_mpv()

    def _init_mpv(self):
        try:
            register_engine_dll_directories()
            import mpv

            mpv_kwargs = {
                "keep_open": "yes",
                "idle": "yes",
                "hwdec": "auto-safe",
                "vo": "gpu",
                "gpu_context": "d3d11",
                "hr_seek": "yes",
                "hr_seek_framedrop": "no",
                "osd_level": 0,
            }
            if self.wid:
                mpv_kwargs["wid"] = str(self.wid)

            self._mpv = mpv.MPV(**mpv_kwargs)

            # Observe position changes
            @self._mpv.property_observer("time-pos")
            def _on_time_pos(name, value):
                if value is not None:
                    self.time_pos_changed.emit(float(value))

            @self._mpv.property_observer("duration")
            def _on_duration(name, value):
                if value is not None:
                    self._duration = float(value)
                    self.duration_changed.emit(float(value))

            @self._mpv.property_observer("pause")
            def _on_pause(name, value):
                is_paused = bool(value)
                self._is_playing = not is_paused
                self.playback_state_changed.emit(self._is_playing)

            logger.info("libmpv engine initialized successfully (wid=%s)", self.wid)
        except Exception as e:
            logger.error("Failed to initialize libmpv engine: %s", e)
            self._mpv = None

    @property
    def is_ready(self) -> bool:
        return self._mpv is not None

    def load_clip(self, file_path: str, start_seconds: float = 0.0):
        """Loads a video file and jumps immediately to the requested start offset."""
        if not self._mpv or not file_path or not os.path.isfile(file_path):
            return

        self._current_file = file_path
        # Normalize Windows path for mpv
        norm_path = os.path.normpath(file_path).replace("\\", "/")
        try:
            if start_seconds > 0.0:
                self._mpv.start = f"+{start_seconds:.3f}"
            else:
                self._mpv.start = "0"
            self._mpv.command("loadfile", norm_path, "replace")
            self._mpv.pause = True
            self.file_loaded.emit(file_path, self._duration)
        except Exception as e:
            logger.error("Error loading file into mpv: %s", e)

    def play(self):
        if self._mpv:
            self._mpv.pause = False

    def pause(self):
        if self._mpv:
            self._mpv.pause = True

    def toggle_play_pause(self):
        if self._mpv:
            self._mpv.pause = not self._mpv.pause

    def seek(self, seconds: float, exact: bool = True):
        """Frame-accurate absolute seek."""
        if not self._mpv:
            return
        flag = "exact" if exact else "relative"
        try:
            self._mpv.command("seek", seconds, "absolute+" + flag)
        except Exception as e:
            logger.warning("mpv seek warning: %s", e)

    def step_frame_forward(self):
        """Steps forward by exactly 1 video frame."""
        if not self._mpv:
            return
        try:
            self._mpv.pause = True
            self._mpv.command("frame-step")
        except Exception as e:
            logger.warning("mpv frame-step warning: %s", e)

    def step_frame_backward(self):
        """Steps backward by exactly 1 video frame."""
        if not self._mpv:
            return
        try:
            self._mpv.pause = True
            self._mpv.command("frame-back-step")
        except Exception as e:
            logger.warning("mpv frame-back-step warning: %s", e)

    def set_speed(self, speed: float):
        """Sets playback rate (0.25x to 8.0x) for J-K-L shuttling."""
        if self._mpv:
            try:
                self._current_speed = max(0.1, min(16.0, speed))
                self._mpv.speed = self._current_speed
            except Exception as e:
                logger.warning("mpv set speed warning: %s", e)

    def set_ab_loop(self, a_seconds: Optional[float], b_seconds: Optional[float]):
        """Native seamless A-B loop (mpv ab-loop-a/b). Pass None for both to disable."""
        if not self._mpv:
            return
        try:
            if a_seconds is None or b_seconds is None or b_seconds <= a_seconds:
                self._mpv["ab-loop-a"] = "no"
                self._mpv["ab-loop-b"] = "no"
            else:
                self._mpv["ab-loop-a"] = f"{max(0.0, a_seconds):.3f}"
                self._mpv["ab-loop-b"] = f"{b_seconds:.3f}"
        except Exception as e:
            logger.warning("mpv ab-loop warning: %s", e)

    def get_time_pos(self) -> float:
        if self._mpv:
            try:
                pos = self._mpv.time_pos
                return float(pos) if pos is not None else 0.0
            except Exception:
                pass
        return 0.0

    def get_duration(self) -> float:
        return self._duration

    def destroy(self):
        """Clean shutdown of mpv instance."""
        if self._mpv:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None
