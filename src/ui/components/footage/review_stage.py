"""
Review tab of the Footage & Media dialog.

Shows the deterministic VO flags (duplicate takes, false starts, wordless sounds,
breaths) for the current transcript, the noise-sensitivity slider, a waveform strip
of the selected part with the deleted silence greyed out, and keep/drop choices.

Cut model: every speech island is a VO clip and all silence between islands is
deleted. Nothing here is required: undecided flags keep their waveform.
Decisions are saved into the transcript JSON.
"""
import math
import os
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    ComboBox, FluentIcon, PushButton, SegmentedWidget, SmoothScrollArea, SwitchButton, TransparentToolButton,
)

from ....core import vo_review as R
from ....core.logger import get_logger
from ....core.vo_cut_preview import (
    TimeMap, build_time_map, cut_to_source, preview_path_for, render_cut_preview, source_to_cut,
)
from ....core.vo_engine import load_vo_transcript_json, save_vo_transcript_json
from ....core.vo_islands import HOP_S, DetectionSettings, estimate_noise_floor_db, slim_segments
from ..widgets import MagneticDetentSlider

logger = get_logger("ReviewStage")

TEXT = "#e8e9ec"
MUTED = "#94a3b8"
ACCENT = "#60a5fa"

TYPE_STYLE = {
    R.DUPLICATE_TAKE: ("Duplicate take", "#fbbf24"),
    R.FALSE_START: ("False start", "#fbbf24"),
    R.NONSPEECH: ("No words", "#c084fc"),
    R.BREATH: ("Breath", "#c084fc"),
}

FILTERS = (
    ("all", "All", None),
    ("takes", "Takes", (R.DUPLICATE_TAKE, R.FALSE_START)),
    ("nowords", "No words", (R.NONSPEECH,)),
    ("breaths", "Breaths", (R.BREATH,)),
)


def fmt_t(sec: float) -> str:
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    return f"{m:02d}:{sec - m * 60:05.2f}"


def decision_options(flag: Dict[str, Any]) -> List[tuple]:
    t = flag["type"]
    if t == R.DUPLICATE_TAKE:
        n = len(flag.get("takes") or [])
        return [(f"{R.PICK_PREFIX}{i}", f"Take {i + 1}") for i in range(n)] + [(R.KEEP_ALL, "Keep all")]
    return [(R.KEEP, "Keep"), (R.DROP, "Drop")]


# ---------------------------------------------------------------------------
# Background envelope loading
# ---------------------------------------------------------------------------
class _EnvelopeSignals(QObject):
    done = Signal(int, object)   # (token, {part_id: env})
    failed = Signal(int, str)


class _EnvelopeLoader(QRunnable):
    def __init__(self, token: int, manifest: Dict[str, Any]):
        super().__init__()
        self.token = token
        self.manifest = manifest
        self.signals = _EnvelopeSignals()

    def run(self):
        try:
            self.signals.done.emit(self.token, R.load_part_envelopes(self.manifest))
        except Exception as e:
            logger.warning("Envelope load failed: %s", e, exc_info=True)
            self.signals.failed.emit(self.token, str(e))


# ---------------------------------------------------------------------------
# Audio preview: one lazy QMediaPlayer for raw playback, flag context and the
# rendered cut preview. Positions are reported in SOURCE seconds of the part.
# ---------------------------------------------------------------------------
class _AudioPreview(QObject):
    position = Signal(float)        # source seconds while playing
    state_changed = Signal(bool)    # playing?

    TICK_MS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._out = None
        self._end_ms = 0
        self._pending_ms: Optional[int] = None
        self._source = ""
        self._tmap: Optional[TimeMap] = None
        self._playing = False
        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self._on_tick)

    def _ensure(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self._player = QMediaPlayer(self)
            self._out = QAudioOutput(self)
            self._player.setAudioOutput(self._out)
            self._player.mediaStatusChanged.connect(self._on_status)
            self._player.playbackStateChanged.connect(self._on_state)

    def play(self, path: str, start: float, end: Optional[float] = None, tmap: Optional[TimeMap] = None):
        """Plays `path` from `start` (media seconds) to `end` (or the end of the file).
        With a time map the file is a cut preview and positions are mapped back to source time."""
        if not path or not os.path.isfile(path):
            return
        self._ensure()
        from PySide6.QtMultimedia import QMediaPlayer
        self._tmap = tmap
        self._end_ms = int(end * 1000) if end is not None else 0
        start_ms = max(0, int(start * 1000))
        if path != self._source:
            self._source = path
            self._pending_ms = start_ms
            self._player.setSource(QUrl.fromLocalFile(path))
            if self._player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                self._on_status(self._player.mediaStatus())
        else:
            self._player.setPosition(start_ms)
            self._player.play()

    def pause(self):
        if self._player is not None:
            self._player.pause()

    def stop(self):
        self._pending_ms = None
        if self._player is not None:
            self._player.stop()

    def release(self):
        """Lets go of the current file (so a cut preview render can be deleted)."""
        self.stop()
        if self._player is not None:
            self._player.setSource(QUrl())
        self._source = ""

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def is_cut_preview(self) -> bool:
        return self._tmap is not None

    def _to_source(self, ms: int) -> float:
        t = ms / 1000.0
        return cut_to_source(t, self._tmap) if self._tmap else t

    def _on_tick(self):
        if self._player is None:
            return
        ms = self._player.position()
        if 0 < self._end_ms <= ms:
            self._player.pause()
            ms = self._end_ms
        self.position.emit(self._to_source(ms))

    def _on_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._pending_ms is not None:
            ms, self._pending_ms = self._pending_ms, None
            self._player.setPosition(ms)
            self._player.play()

    def _on_state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        playing = state == QMediaPlayer.PlayingState
        if playing:
            self._tick.start()
        else:
            self._tick.stop()
        if playing != self._playing:
            self._playing = playing
            self.state_changed.emit(playing)


class _RenderSignals(QObject):
    done = Signal(int, str)
    failed = Signal(int, str)


class _CutRenderer(QRunnable):
    """Builds the cut preview WAV off the UI thread."""

    def __init__(self, token: int, src_path: str, tmap: TimeMap):
        super().__init__()
        self.token, self.src_path, self.tmap = token, src_path, tmap
        self.signals = _RenderSignals()

    def run(self):
        try:
            self.signals.done.emit(self.token, render_cut_preview(self.src_path, self.tmap))
        except Exception as e:
            logger.warning("Cut preview render failed: %s", e)
            self.signals.failed.emit(self.token, str(e))


# ---------------------------------------------------------------------------
# Waveform strip: zoomable envelope + playhead + zoom/pan overview bar
# ---------------------------------------------------------------------------
class WaveformStrip(QWidget):
    """
    Loudness envelope of one part.

    Top: the waveform of the visible window. Deleted audio is greyed, flags are tinted,
    and once zoomed in each VO clip's edges ([ ]) and its speech island (dashed) are drawn,
    so the handles between them can be checked. Click or drag to move the playhead (silent).

    Bottom: overview of the whole part. Squeeze the two grip handles to zoom in, drag the
    middle to pan, click elsewhere to centre there, double-click to show the whole part.
    The mouse wheel zooms around the playhead; a horizontal wheel pans.
    """
    flag_clicked = Signal(str)
    seek_requested = Signal(float)    # while pressing/dragging on the waveform
    scrub_finished = Signal(float)    # mouse released

    WAVE_H = 96
    OV_TOP = 104
    OV_H = 16
    MARGIN = 8
    MIN_SPAN_S = 0.5
    EDGE_SPAN_S = 30.0        # draw clip edges once the view is this short
    THUMB_MIN_PX = 54.0
    SQUEEZE_PX_PER_HALVING = 30.0
    CLICK_SLOP_PX = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.OV_TOP + self.OV_H + 2)
        self.setMouseTracking(True)
        self._env: Optional[np.ndarray] = None
        self._cs: Optional[np.ndarray] = None      # cumulative linear amplitude
        self._global_ref = 1.0
        self._duration = 0.0
        self._flags: List[Dict[str, Any]] = []
        self._selected = ""
        self._playhead: Optional[float] = 0.0
        self._kept: List[Dict[str, Any]] = []
        self._islands: List[Dict[str, Any]] = []
        self._message = "Reading waveform…"
        self.view_start = 0.0
        self.view_end = 0.0
        self._drag: Optional[str] = None
        self._press_x = 0
        self._press_view = (0.0, 0.0)
        self._press_anchor = 0.0
        self._moved = False

    # ---------------------------------------------------------------- data
    def set_message(self, text: str):
        self._message = text
        self._env = None
        self.update()

    def set_data(self, env: Optional[np.ndarray], duration: float, flags: List[Dict[str, Any]],
                 selected: str = "", kept: Optional[List[Dict[str, Any]]] = None,
                 islands: Optional[List[Dict[str, Any]]] = None):
        """kept: this part's VO clip ranges; everything outside them is drawn as deleted.
        A different envelope (another part) resets the zoom; the same one keeps it."""
        new_part = env is not self._env
        self._env = env
        self._duration = float(duration or (len(env) * HOP_S if env is not None else 0.0))
        if new_part:
            if env is not None and len(env):
                lin = np.power(10.0, env.astype(np.float64) / 20.0)
                self._cs = np.concatenate(([0.0], np.cumsum(lin)))
                self._global_ref = float(np.percentile(lin, 99)) or 1.0
            else:
                self._cs = None
            self.reset_view()
        self._flags = flags
        self._selected = selected
        self._kept = list(kept or [])
        self._islands = list(islands or [])
        self.update()

    def set_selected(self, flag_id: str):
        self._selected = flag_id
        self.update()

    def set_playhead(self, t: Optional[float], follow: bool = False):
        self._playhead = t
        if follow and t is not None and self.zoomed:
            span = self.view_span
            if t > self.view_end - 0.05 * span or t < self.view_start:
                self._set_view(t - 0.1 * span, t + 0.9 * span)
        self.update()

    # ---------------------------------------------------------------- view
    @property
    def view_span(self) -> float:
        return max(1e-6, self.view_end - self.view_start)

    @property
    def zoomed(self) -> bool:
        return self._duration > 0 and self.view_span < self._duration - 1e-6

    def reset_view(self):
        self.view_start, self.view_end = 0.0, max(0.0, self._duration)
        self.update()

    def _set_view(self, a: float, b: float):
        d = self._duration
        if d <= 0:
            return
        span = min(d, max(min(self.MIN_SPAN_S, d), b - a))
        if a < 0:
            a = 0.0
        if a + span > d:
            a = d - span
        self.view_start, self.view_end = max(0.0, a), max(0.0, a) + span
        self.update()

    def frame(self, t0: float, t1: float, min_span: float = 4.0):
        """Zooms so [t0, t1] sits in the middle with room around it."""
        span = max(min_span, (t1 - t0) * 1.8)
        mid = (t0 + t1) / 2.0
        self._set_view(mid - span / 2.0, mid + span / 2.0)

    def zoom_by(self, factor: float, anchor: Optional[float] = None):
        if self._duration <= 0:
            return
        if anchor is None:
            ph = self._playhead
            anchor = ph if ph is not None and self.view_start <= ph <= self.view_end else (self.view_start + self.view_end) / 2
        span = self.view_span
        new_span = min(self._duration, max(self.MIN_SPAN_S, span * factor))
        alpha = min(1.0, max(0.0, (anchor - self.view_start) / span))
        a = anchor - alpha * new_span
        self._set_view(a, a + new_span)

    # ---------------------------------------------------------------- geometry
    def _x(self, t: float) -> float:
        return (t - self.view_start) / self.view_span * self.width()

    def _t(self, x: float) -> float:
        return self.view_start + max(0.0, min(float(self.width()), x)) / max(1, self.width()) * self.view_span

    def _ov_w(self) -> float:
        return max(1.0, self.width() - 2 * self.MARGIN)

    def _ox(self, t: float) -> float:
        return self.MARGIN + max(0.0, min(t, self._duration)) / max(1e-6, self._duration) * self._ov_w()

    def _ot(self, x: float) -> float:
        return max(0.0, min(1.0, (x - self.MARGIN) / self._ov_w())) * self._duration

    def _thumb(self) -> tuple:
        left, right = self._ox(self.view_start), self._ox(self.view_end)
        if right - left < self.THUMB_MIN_PX:
            mid = (left + right) / 2.0
            left = min(max(self.MARGIN, mid - self.THUMB_MIN_PX / 2), self.width() - self.MARGIN - self.THUMB_MIN_PX)
            right = left + self.THUMB_MIN_PX
        return left, right

    # Squeeze zoom, as in the A-roll refiner: 1:1 while the thumb is wider than its minimum,
    # then every SQUEEZE_PX_PER_HALVING px of extra squeeze halves the span.
    def _span_to_pos(self, span: float) -> float:
        t_min = self.THUMB_MIN_PX / self._ov_w() * self._duration
        if span >= t_min:
            return span / self._duration * self._ov_w()
        return self.THUMB_MIN_PX - self.SQUEEZE_PX_PER_HALVING * math.log2(t_min / max(span, 1e-9))

    def _pos_to_span(self, pos: float) -> float:
        t_min = self.THUMB_MIN_PX / self._ov_w() * self._duration
        if pos >= self.THUMB_MIN_PX:
            return pos / self._ov_w() * self._duration
        return t_min * (2.0 ** (-(self.THUMB_MIN_PX - pos) / self.SQUEEZE_PX_PER_HALVING))

    # ---------------------------------------------------------------- input
    def _hit_flag(self, x: float) -> Optional[str]:
        t = self._t(x)
        pad = self.view_span / max(1, self.width()) * 3  # 3 px slop for thin flags
        hits = [f for f in self._flags if f["in"] - pad <= t <= f["out"] + pad]
        return min(hits, key=lambda f: f["out"] - f["in"])["id"] if hits else None

    def mousePressEvent(self, e):
        if self._duration <= 0 or self._env is None or e.button() != Qt.LeftButton:
            return
        x, y = e.position().x(), e.position().y()
        self._press_x, self._moved = x, False
        self._press_view = (self.view_start, self.view_end)
        if y >= self.OV_TOP - 4:
            left, right = self._thumb()
            if abs(x - left) <= 8 or abs(x - right) <= 8:
                self._drag = "squeeze_left" if abs(x - left) <= abs(x - right) else "squeeze_right"
                ph = self._playhead
                self._press_anchor = ph if ph is not None and self.view_start <= ph <= self.view_end \
                    else (self.view_start + self.view_end) / 2
            elif left < x < right:
                self._drag = "pan"
                self.setCursor(Qt.ClosedHandCursor)
            else:
                t = self._ot(x)
                self._set_view(t - self.view_span / 2, t + self.view_span / 2)
            return
        self._drag = "scrub"
        t = self._t(x)
        self._playhead = t
        self.seek_requested.emit(t)
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if abs(x - self._press_x) > self.CLICK_SLOP_PX:
            self._moved = True
        if self._drag == "scrub":
            t = self._t(x)
            if t != self._playhead:
                self._playhead = t
                self.seek_requested.emit(t)
                self.update()
        elif self._drag in ("squeeze_left", "squeeze_right"):
            dx = x - self._press_x
            squeeze = dx if self._drag == "squeeze_left" else -dx
            v0, v1 = self._press_view
            span = max(self.MIN_SPAN_S, v1 - v0)
            new_span = min(self._duration, max(self.MIN_SPAN_S, self._pos_to_span(self._span_to_pos(span) - squeeze)))
            alpha = min(1.0, max(0.0, (self._press_anchor - v0) / max(1e-6, v1 - v0)))
            a = self._press_anchor - alpha * new_span
            self._set_view(a, a + new_span)
        elif self._drag == "pan":
            v0, v1 = self._press_view
            d = (x - self._press_x) / self._ov_w() * self._duration
            self._set_view(v0 + d, v1 + d)
        elif y >= self.OV_TOP - 4:
            left, right = self._thumb()
            if abs(x - left) <= 8 or abs(x - right) <= 8:
                self.setCursor(Qt.SizeHorCursor)
            elif left < x < right:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.IBeamCursor)

    def mouseReleaseEvent(self, e):
        mode, self._drag = self._drag, None
        self.setCursor(Qt.ArrowCursor)
        if mode == "scrub":
            if not self._moved:
                fid = self._hit_flag(e.position().x())
                if fid:
                    self.flag_clicked.emit(fid)
            self.scrub_finished.emit(self._playhead if self._playhead is not None else 0.0)

    def mouseDoubleClickEvent(self, e):
        if e.position().y() >= self.OV_TOP - 4:
            self.reset_view()
        else:
            self.mousePressEvent(e)

    def wheelEvent(self, e):
        if self._duration <= 0:
            return
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        if dx and not dy:
            shift = -dx / 120.0 * 0.15 * self.view_span
            self._set_view(self.view_start + shift, self.view_end + shift)
        elif dy:
            self.zoom_by(0.8 if dy > 0 else 1.25)
        e.accept()

    # ---------------------------------------------------------------- paint
    def _column_amps(self, cols: int) -> np.ndarray:
        n = len(self._cs) - 1
        t = self.view_start + np.arange(cols + 1) * (self.view_span / cols)
        f = np.clip((t / HOP_S).astype(np.int64), 0, n)
        a, b = f[:-1], np.maximum(f[1:], f[:-1] + 1)
        b = np.minimum(b, n)
        a = np.minimum(a, b - 1)
        return (self._cs[b] - self._cs[a]) / np.maximum(1, b - a)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width()
        wave_rect = QRectF(0, 0, w, self.WAVE_H)
        p.fillRect(wave_rect, QColor(255, 255, 255, 8))
        if self._env is None or self._cs is None or self._duration <= 0:
            p.setPen(QColor(MUTED))
            p.drawText(wave_rect, Qt.AlignCenter, self._message)
            return
        h = self.WAVE_H
        p.setClipRect(wave_rect)

        for f in self._flags:
            if f["out"] < self.view_start or f["in"] > self.view_end:
                continue
            col = QColor(TYPE_STYLE[f["type"]][1])
            col.setAlpha(80 if f["id"] == self._selected else 34)
            x0, x1 = self._x(f["in"]), self._x(f["out"])
            p.fillRect(QRectF(x0, 0, max(2.0, x1 - x0), h), col)

        # Envelope: mean linear amplitude per pixel column (a max draws a solid wall when
        # zoomed out). Scaled to the visible audio, but never louder than half the file's
        # peak, so zooming into silence doesn't blow room tone up into "speech".
        cols = max(1, w)
        amps = self._column_amps(cols)
        ref = max(float(np.percentile(amps, 98)), 0.5 * self._global_ref) or 1.0
        mid = h / 2.0
        p.setPen(QPen(QColor("#86c06c"), 1))
        for x in range(cols):
            amp = min(1.0, amps[x] / ref)
            if amp <= 0.02:
                p.drawPoint(x, int(mid))
                continue
            half = amp * (mid - 3)
            p.drawLine(QPointF(x + 0.5, mid - half), QPointF(x + 0.5, mid + half))

        # Deleted audio (silence + anything dropped): dim it so the cut points read at a glance
        dim = QColor(20, 20, 20, 170)
        cursor = 0.0
        kept_sorted = sorted(self._kept, key=lambda r: r["in"])
        for k in kept_sorted + [{"in": self._duration, "out": self._duration}]:
            if k["in"] > cursor:
                x0, x1 = self._x(cursor), self._x(k["in"])
                if x1 >= 0 and x0 <= w and x1 - x0 >= 0.5:
                    p.fillRect(QRectF(x0, 0, x1 - x0, h), dim)
            cursor = max(cursor, k["out"])

        # Zoomed in: the speech islands (dashed) and each VO clip's in/out ([ ]). The space
        # between a dashed line and its bracket is the handle that keeps word edges intact.
        if self.view_span <= self.EDGE_SPAN_S:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.DashLine))
            for isl in self._islands:
                for t in (isl["in"], isl["out"]):
                    if self.view_start <= t <= self.view_end:
                        x = round(self._x(t)) + 0.5
                        p.drawLine(QPointF(x, 4), QPointF(x, h - 4))
            p.setPen(QPen(QColor(232, 233, 236, 200), 1))
            for k in kept_sorted:
                for t, tick in ((k["in"], 5), (k["out"], -5)):
                    if self.view_start <= t <= self.view_end:
                        x = round(self._x(t)) + 0.5
                        p.drawLine(QPointF(x, 1), QPointF(x, h - 1))
                        p.drawLine(QPointF(x, 1), QPointF(x + tick, 1))
                        p.drawLine(QPointF(x, h - 1), QPointF(x + tick, h - 1))

        if self._selected:
            for f in self._flags:
                if f["id"] == self._selected:
                    p.setPen(QPen(QColor(TYPE_STYLE[f["type"]][1]), 1))
                    p.drawRect(QRectF(self._x(f["in"]), 0.5, max(2.0, self._x(f["out"]) - self._x(f["in"])), h - 1))

        if self._playhead is not None and self.view_start <= self._playhead <= self.view_end:
            x = round(self._x(self._playhead))
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x + 5, 5),
                                     QPointF(x, 10), QPointF(x - 5, 5)]))
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(QPen(QColor(255, 255, 255), 1))
            p.drawLine(QPointF(x + 0.5, 8), QPointF(x + 0.5, h))
        p.setClipping(False)

        self._paint_overview(p)

    def _paint_overview(self, p: QPainter):
        w = self.width()
        cy = self.OV_TOP + self.OV_H / 2.0
        p.setRenderHint(QPainter.Antialiasing, True)
        track = QRectF(self.MARGIN, cy - 4, w - 2 * self.MARGIN, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(36, 36, 40))
        p.drawRoundedRect(track, 3, 3)
        # Where the VO clips are, so you can find your way around the whole part
        clip_col = QColor("#86c06c")
        clip_col.setAlpha(90)
        for k in self._kept:
            x0, x1 = self._ox(k["in"]), self._ox(k["out"])
            p.fillRect(QRectF(x0, cy - 3, max(1.0, x1 - x0), 6), clip_col)
        if self._playhead is not None:
            p.fillRect(QRectF(self._ox(self._playhead) - 0.5, cy - 6, 1.5, 12), QColor(ACCENT))

        left, right = self._thumb()
        p.setPen(QPen(QColor(113, 113, 122), 1))
        p.setBrush(QColor(63, 63, 70, 150))
        p.drawRoundedRect(QRectF(left, cy - 7, right - left, 14), 3, 3)
        for hx in (left, right):
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.setBrush(QColor(24, 24, 27))
            p.drawEllipse(QPointF(hx, cy), 5, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(QPointF(hx, cy), 2, 2)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
CHOICE_QSS = """
QPushButton#ReviewChoice {
    background: transparent; color: #cbd5e1; font-size: 11.5px;
    border: 1px solid rgba(255,255,255,0.12); border-radius: 0px; padding: 3px 9px; min-height: 18px;
}
QPushButton#ReviewChoice:hover { background: rgba(255,255,255,0.06); }
QPushButton#ReviewChoice[state="default"] { border: 1px dashed rgba(96,165,250,0.6); color: #93c5fd; }
QPushButton#ReviewChoice[state="chosen"] { background: rgba(96,165,250,0.20); border: 1px solid rgba(96,165,250,0.7); color: #bfdbfe; }
QPushButton#ReviewChoice[state="mixed"] { border: 1px dashed rgba(148,163,184,0.5); }
"""


class ChoiceGroup(QWidget):
    """Joined option buttons. Shows the chosen value, or the default (dashed) when undecided."""
    chosen = Signal(str)

    def __init__(self, options: List[tuple], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(-1)
        self._buttons: Dict[str, QPushButton] = {}
        for value, label in options:
            b = QPushButton(label, self)
            b.setObjectName("ReviewChoice")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, v=value: self.chosen.emit(v))
            lay.addWidget(b)
            self._buttons[value] = b
        self.setStyleSheet(CHOICE_QSS)

    def set_state(self, decided: Optional[str], default: Optional[str], mixed: bool = False):
        for value, b in self._buttons.items():
            if mixed:
                state = "mixed"
            elif decided == value:
                state = "chosen"
            elif decided is None and default == value:
                state = "default"
            else:
                state = ""
            if b.property("state") != state:
                b.setProperty("state", state)
                b.style().unpolish(b)
                b.style().polish(b)


class _Tag(QLabel):
    def __init__(self, ftype: str, text: Optional[str] = None, parent=None):
        label, color = TYPE_STYLE[ftype]
        super().__init__(text or label, parent)
        c = QColor(color)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},0.14);"
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600; }"
        )
        self.setFixedHeight(20)


ROW_QSS = """
QFrame#ReviewRow { background: transparent; border: none; border-bottom: 1px solid rgba(255,255,255,0.05); }
QFrame#ReviewRow[selected="true"] { background: rgba(96,165,250,0.08); }
QFrame#ReviewRow[child="true"] { padding-left: 18px; }
"""


class FlagRow(QFrame):
    selected = Signal(str)
    play_requested = Signal(str)
    decided = Signal(str, str)

    def __init__(self, flag: Dict[str, Any], multi_part: bool, child: bool = False, parent=None):
        super().__init__(parent)
        self.flag = flag
        self.setObjectName("ReviewRow")
        self.setProperty("child", "true" if child else "false")
        self.setStyleSheet(ROW_QSS)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28 if child else 4, 6, 4, 6)
        lay.setSpacing(10)

        self.open_dot = QLabel("●", self)
        self.open_dot.setFixedWidth(10)
        self.open_dot.setStyleSheet(f"color: {ACCENT}; font-size: 9px; background: transparent;")
        self.open_dot.setToolTip("Not reviewed yet: the dashed choice will be used")
        lay.addWidget(self.open_dot, 0, Qt.AlignVCenter)

        if not child:
            tag = _Tag(flag["type"], parent=self)
            tag.setFixedWidth(104)
            tag.setAlignment(Qt.AlignCenter)
            lay.addWidget(tag, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        part = f"P{flag['part_id']} · " if multi_part else ""
        tc = QLabel(f"{part}{fmt_t(flag['global_in'])} → {fmt_t(flag['global_out'])}"
                    + (f"   {flag.get('detail', '')}" if flag.get("detail") else ""), self)
        tc.setFont(QFont("Consolas", 9))
        tc.setStyleSheet(f"color: {MUTED}; background: transparent;")
        col.addWidget(tc)
        body = self._body_text(flag)
        if body:
            txt = QLabel(body, self)
            txt.setWordWrap(True)
            txt.setStyleSheet(f"color: {TEXT}; font-size: 12px; background: transparent;")
            txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            col.addWidget(txt)
        lay.addLayout(col, 1)

        self.play_btn = TransparentToolButton(FluentIcon.PLAY, self)
        self.play_btn.setFixedSize(26, 26)
        self.play_btn.setToolTip("Play with 2 s of context (Space)")
        self.play_btn.clicked.connect(lambda: self.play_requested.emit(flag["id"]))
        lay.addWidget(self.play_btn, 0, Qt.AlignVCenter)

        self.choices = ChoiceGroup(decision_options(flag), self)
        self.choices.chosen.connect(lambda v: self.decided.emit(flag["id"], v))
        lay.addWidget(self.choices, 0, Qt.AlignVCenter)

    @staticmethod
    def _body_text(flag: Dict[str, Any]) -> str:
        t = flag["type"]
        if t == R.DUPLICATE_TAKE:
            return "\n".join(f"{i + 1}. “{tk['text']}”" for i, tk in enumerate(flag.get("takes") or []))
        if t == R.FALSE_START:
            return f"“{flag.get('text', '')}”" + ("" if flag.get("inline") else f"  {flag.get('detail', '')}")
        if t == R.NONSPEECH:
            return "Sound with nothing transcribed (laugh, shout, sigh?)"
        return ""

    def mousePressEvent(self, e):
        self.selected.emit(self.flag["id"])
        super().mousePressEvent(e)

    def set_decision(self, decisions: Dict[str, str]):
        is_open = R.is_open(self.flag, decisions)
        self.open_dot.setVisible(is_open)
        self.choices.set_state(None if is_open else decisions[self.flag["id"]], R.DEFAULT_DECISION[self.flag["type"]])

    def set_selected(self, on: bool):
        if self.property("selected") != ("true" if on else "false"):
            self.setProperty("selected", "true" if on else "false")
            self.style().unpolish(self)
            self.style().polish(self)


class GroupRow(QFrame):
    """One collapsible row for all breaths, with bulk choices."""
    toggled = Signal(str)          # flag type
    bulk_decided = Signal(str, str)  # (flag type, value)

    def __init__(self, ftype: str, parent=None):
        super().__init__(parent)
        self.ftype = ftype
        self.setObjectName("ReviewRow")
        self.setStyleSheet(ROW_QSS)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(10)

        self.open_dot = QLabel("●", self)
        self.open_dot.setFixedWidth(10)
        self.open_dot.setStyleSheet(f"color: {ACCENT}; font-size: 9px; background: transparent;")
        lay.addWidget(self.open_dot, 0, Qt.AlignVCenter)

        tag = _Tag(ftype, "Breaths", self)
        tag.setFixedWidth(104)
        tag.setAlignment(Qt.AlignCenter)
        lay.addWidget(tag, 0, Qt.AlignVCenter)

        self.summary = QLabel("", self)
        self.summary.setStyleSheet(f"color: {TEXT}; font-size: 12px; background: transparent;")
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.summary, 1)

        self.expand_btn = TransparentToolButton(FluentIcon.CHEVRON_RIGHT_MED, self)
        self.expand_btn.setFixedSize(26, 26)
        self.expand_btn.setToolTip("Show each one")
        self.expand_btn.clicked.connect(lambda: self.toggled.emit(ftype))
        lay.addWidget(self.expand_btn, 0, Qt.AlignVCenter)

        opts = [(R.KEEP, "Keep all"), (R.DROP, "Drop all")]
        self.choices = ChoiceGroup(opts, self)
        self.choices.chosen.connect(lambda v: self.bulk_decided.emit(ftype, v))
        lay.addWidget(self.choices, 0, Qt.AlignVCenter)

    def mousePressEvent(self, e):
        self.toggled.emit(self.ftype)
        super().mousePressEvent(e)

    def update_state(self, flags: List[Dict[str, Any]], decisions: Dict[str, str], expanded: bool, extra: str = ""):
        n = len(flags)
        noun = "short quiet sound"
        self.summary.setText(f"{n} {noun}{'s' if n != 1 else ''}{extra}")
        values = {decisions.get(f["id"]) if not R.is_open(f, decisions) else None for f in flags}
        any_open = None in values
        self.open_dot.setVisible(any_open)
        if len(values) == 1:
            v = next(iter(values))
            self.choices.set_state(v, R.DEFAULT_DECISION[self.ftype])
        else:
            self.choices.set_state(None, None, mixed=True)
        self.expand_btn.setIcon(FluentIcon.CHEVRON_DOWN_MED if expanded else FluentIcon.CHEVRON_RIGHT_MED)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------
class ReviewStage(QWidget):
    open_count_changed = Signal(int)
    timing_changed = Signal(str, list)   # (transcript path, slim segments) after a re-snap
    open_transcribe_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, transcript_path: str = ""):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.transcript_path = ""
        self.manifest: Optional[Dict[str, Any]] = None
        self.envs: Dict[int, np.ndarray] = {}
        self.detection = DetectionSettings()
        self.review = R.ReviewSettings()
        self.decisions: Dict[str, str] = {}
        self.flags: List[Dict[str, Any]] = []
        self.filter_key = "all"
        self.selected_id = ""
        self.expanded: set = set()
        self._rows: Dict[str, FlagRow] = {}
        self._group_rows: Dict[str, GroupRow] = {}
        self._load_token = 0
        self._timing_dirty = False
        self._suppress_sliders = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save)
        self._detect_timer = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._detect_timer.setInterval(120)
        self._detect_timer.timeout.connect(self._apply_slider_settings)

        self.preview = _AudioPreview(self)
        self.preview.position.connect(self._on_play_pos)
        self.preview.state_changed.connect(self._on_play_state)
        self._play_part = 0
        self.playhead = 0.0                 # source seconds in the current part
        self._render_token = 0
        self._want_play_after_render = False
        self._resume_after_scrub = False
        self._renders: set = set()          # cut preview files made this session

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget(self)
        outer.addWidget(self.pages)
        self._build_empty_page()
        self._build_main_page()
        self.pages.setCurrentWidget(self.empty_page)

        if transcript_path:
            self.load(transcript_path)

    # ------------------------------------------------------------------ build
    def _build_empty_page(self):
        self.empty_page = QWidget(self.pages)
        lay = QVBoxLayout(self.empty_page)
        lay.addStretch(1)
        title = QLabel("Transcribe your VO to review it", self.empty_page)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {TEXT}; font-size: 14px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel("Duplicate takes, false starts and wordless sounds show up here.", self.empty_page)
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(sub)
        btn = PushButton("Open Transcribe", self.empty_page)
        btn.clicked.connect(self.open_transcribe_requested.emit)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addSpacing(8)
        lay.addLayout(row)
        lay.addStretch(2)
        self.pages.addWidget(self.empty_page)

    def _build_main_page(self):
        self.main_page = QWidget(self.pages)
        lay = QVBoxLayout(self.main_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Top bar: part picker, filters, open count
        top = QHBoxLayout()
        top.setSpacing(8)
        self.part_combo = ComboBox(self.main_page)
        self.part_combo.setFixedHeight(32)
        self.part_combo.setMinimumWidth(220)
        self.part_combo.currentIndexChanged.connect(self._on_part_changed)
        top.addWidget(self.part_combo, 0, Qt.AlignVCenter)
        self.filter_bar = SegmentedWidget(self.main_page)
        for key, text, _ in FILTERS:
            self.filter_bar.addItem(routeKey=key, text=text, onClick=lambda *_, k=key: self._set_filter(k))
        self.filter_bar.setCurrentItem("all")
        top.addWidget(self.filter_bar, 0, Qt.AlignVCenter)
        top.addStretch(1)
        self.open_lbl = QLabel("", self.main_page)
        self.open_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        top.addWidget(self.open_lbl, 0, Qt.AlignVCenter)
        lay.addLayout(top)

        # Detection sliders
        strip = QFrame(self.main_page)
        strip.setObjectName("ReviewSliders")
        strip.setStyleSheet("QFrame#ReviewSliders { background-color: rgba(255,255,255,0.03); border-radius: 6px; }")
        s_lay = QVBoxLayout(strip)
        s_lay.setContentsMargins(12, 6, 12, 8)
        s_lay.setSpacing(0)

        def slider_row(label, lo, hi, default, cap_lo, cap_hi, tip):
            row = QHBoxLayout()
            row.setSpacing(10)
            name = QLabel(label, strip)
            name.setFixedWidth(130)
            name.setToolTip(tip)
            name.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
            sl = MagneticDetentSlider([(lo, cap_lo), (default, "Default"), (hi, cap_hi)], snap_radius=max(1, (hi - lo) // 40), parent=strip)
            sl.setRange(lo, hi)
            sl.setToolTip(tip)
            val = QLabel("", strip)
            val.setFixedWidth(64)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setStyleSheet(f"color: {TEXT}; font-size: 12px; background: transparent;")
            row.addWidget(name, 0, Qt.AlignVCenter)
            row.addWidget(sl, 1)
            row.addWidget(val, 0, Qt.AlignVCenter)
            s_lay.addLayout(row)
            sl.valueChanged.connect(self._on_slider_moved)
            return sl, val

        self.th_slider, self.th_val = slider_row(
            "Noise sensitivity", 6, 40, 12, "Sensitive", "Strict",
            "How much louder than the room tone a sound must be to count as VO. Everything quieter is "
            "silence and gets deleted. Raise it if hiss or room tone is being kept; lower it if quiet "
            "word tails are being cut.")

        self.readout = QLabel("", strip)
        self.readout.setFont(QFont("Consolas", 9))
        self.readout.setStyleSheet(f"color: {MUTED}; background: transparent;")
        s_lay.addSpacing(4)
        s_lay.addWidget(self.readout)
        lay.addWidget(strip)

        self.wave = WaveformStrip(self.main_page)
        self.wave.flag_clicked.connect(self._on_wave_clicked)
        self.wave.seek_requested.connect(self._on_seek)
        self.wave.scrub_finished.connect(self._on_scrub_finished)
        lay.addWidget(self.wave)

        # Transport: play/pause, cut preview vs raw file, show whole part
        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.play_btn = TransparentToolButton(FluentIcon.PLAY, self.main_page)
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setToolTip("Play / pause from the playhead (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        transport.addWidget(self.play_btn, 0, Qt.AlignVCenter)
        self.cut_switch = SwitchButton(self.main_page)
        self.cut_switch.setOnText("Cut preview")
        self.cut_switch.setOffText("Cut preview")
        self.cut_switch.setChecked(True)
        self.cut_switch.setToolTip("On: hear the VO clips butted together, exactly as the timeline gets them "
                                   "(silence and drops skipped).\nOff: play the original file straight through.")
        self.cut_switch.checkedChanged.connect(self._on_cut_mode_changed)
        transport.addWidget(self.cut_switch, 0, Qt.AlignVCenter)
        self.transport_lbl = QLabel("", self.main_page)
        self.transport_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        transport.addWidget(self.transport_lbl, 0, Qt.AlignVCenter)
        transport.addStretch(1)
        self.fit_btn = TransparentToolButton(FluentIcon.ZOOM_OUT, self.main_page)
        self.fit_btn.setFixedSize(30, 30)
        self.fit_btn.setToolTip("Show the whole part (or double-click the zoom bar)")
        self.fit_btn.clicked.connect(self.wave.reset_view)
        transport.addWidget(self.fit_btn, 0, Qt.AlignVCenter)
        lay.addLayout(transport)

        # Flag list
        self.scroll = SmoothScrollArea(self.main_page)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        lay.addWidget(self.scroll, 1)

        self.empty_list_lbl = QLabel("Nothing to review: no duplicate takes, false starts or wordless sounds found.", self.list_host)
        self.empty_list_lbl.setAlignment(Qt.AlignCenter)
        self.empty_list_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; padding: 24px;")
        self.empty_list_lbl.hide()

        hint = QLabel("Space play/pause · click or drag the waveform to move the playhead · wheel or the zoom bar to zoom · "
                      "↑↓ next/previous · K keep · D drop · 1–9 pick take. Greyed-out audio gets deleted; "
                      "anything you skip uses the dashed choice.", self.main_page)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: #64748b; font-size: 11px;")
        lay.addWidget(hint)
        self.pages.addWidget(self.main_page)

    # ------------------------------------------------------------------ load / save
    def load(self, transcript_path: str, envelopes: Optional[Dict[int, np.ndarray]] = None):
        """Loads a transcript JSON. Envelopes are read in the background unless given."""
        self.preview.release()
        self._render_token += 1
        self.playhead = 0.0
        self._load_token += 1
        manifest = load_vo_transcript_json(transcript_path) if transcript_path else None
        if not manifest or not manifest.get("segments"):
            self.transcript_path, self.manifest, self.flags = transcript_path or "", None, []
            self.pages.setCurrentWidget(self.empty_page)
            self.open_count_changed.emit(0)
            return

        self.transcript_path = transcript_path
        self.manifest = manifest
        self.envs = {}
        self.expanded = set()
        self.selected_id = ""
        block = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
        self.detection = DetectionSettings.from_dict(manifest.get("detection"))
        self.review = R.ReviewSettings.from_dict(block.get("settings"))
        self.decisions = dict(block.get("decisions") or {})
        self._sync_sliders()

        self._suppress_part = True
        self.part_combo.clear()
        for f in manifest.get("files") or []:
            self.part_combo.addItem(f"Part {f.get('part_id', 1)} · {f.get('filename') or os.path.basename(f.get('path', ''))}")
        self.part_combo.setVisible(len(manifest.get("files") or []) > 0)
        self._suppress_part = False

        self.pages.setCurrentWidget(self.main_page)
        if envelopes is not None:
            self._on_envelopes(self._load_token, envelopes)
        else:
            self.wave.set_message("Reading waveform…")
            self._recompute(save=False)
            loader = _EnvelopeLoader(self._load_token, manifest)
            loader.signals.done.connect(self._on_envelopes)
            loader.signals.failed.connect(self._on_envelopes_failed)
            QThreadPool.globalInstance().start(loader)

    def _on_envelopes(self, token: int, envs: Dict[int, np.ndarray]):
        if token != self._load_token or self.manifest is None:
            return
        self.envs = dict(envs or {})
        if R.needs_timing_rebuild(self.manifest) and self.envs:
            R.rebuild_timing(self.manifest, self.detection, self.envs)
            self._timing_dirty = True
        # Re-measure room tone from the waveform: older saves stored a value clamped at -90 dB.
        tone_changed = False
        for f in self.manifest.get("files") or []:
            env = self.envs.get(int(f.get("part_id", 1)))
            if env is not None:
                measured = round(estimate_noise_floor_db(env), 1)
                if f.get("noise_floor_db") != measured:
                    f["noise_floor_db"] = measured
                    tone_changed = True
        self._recompute(save=self._timing_dirty or tone_changed or "review" not in self.manifest)

    def _on_envelopes_failed(self, token: int, err: str):
        if token == self._load_token:
            self.wave.set_message("Couldn't read the audio waveform")
            self.readout.setText(f"Waveform unavailable: {err[:120]}")

    def save(self):
        if not self.manifest or not self.transcript_path:
            return
        self._save_timer.stop()
        self.manifest["review"] = R.build_review_block(self.manifest, self.flags, self.decisions, self.review, self.detection)
        self.decisions = dict(self.manifest["review"]["decisions"])
        try:
            save_vo_transcript_json(self.manifest, self.transcript_path)
        except Exception as e:
            logger.error("Failed to save review decisions: %s", e, exc_info=True)
            return
        if self._timing_dirty:
            self._timing_dirty = False
            self.timing_changed.emit(self.transcript_path, slim_segments(self.manifest.get("segments", [])))

    def cleanup(self):
        if self._save_timer.isActive():
            self.save()
        self.preview.release()
        self._render_token += 1
        for path in self._renders:
            try:
                os.remove(path)
            except OSError:
                pass
        self._renders.clear()

    # ------------------------------------------------------------------ settings
    def _sync_sliders(self):
        self._suppress_sliders = True
        self.th_slider.setValue(int(round(self.detection.threshold_db)))
        self._suppress_sliders = False
        self._update_slider_labels()

    def _update_slider_labels(self):
        self.th_val.setText(f"+{self.th_slider.value()} dB")

    def _on_slider_moved(self, _v: int):
        self._update_slider_labels()
        if not self._suppress_sliders:
            self._detect_timer.start()

    def _apply_slider_settings(self):
        if not self.manifest:
            return
        new_th = float(self.th_slider.value())
        if abs(new_th - self.detection.threshold_db) > 1e-6:
            self.detection.threshold_db = new_th
            if self.envs:
                R.rebuild_timing(self.manifest, self.detection, self.envs)
                self._timing_dirty = True
        self._recompute(save=True)

    # ------------------------------------------------------------------ flags
    def _recompute(self, save: bool):
        if not self.manifest:
            return
        self.flags = R.detect_flags(self.manifest, self.review)
        self._rebuild_list()
        self._refresh_wave()
        self._update_counts()
        if save:
            self._save_timer.start()

    def _visible_types(self):
        return next(t for k, _, t in FILTERS if k == self.filter_key)

    def _rebuild_list(self):
        for w in list(self._rows.values()) + list(self._group_rows.values()):
            w.setParent(None)
            w.deleteLater()
        self._rows, self._group_rows = {}, {}
        while self.list_lay.count() > 1:
            self.list_lay.takeAt(0)

        types = self._visible_types()
        multi = len((self.manifest or {}).get("files") or []) > 1
        shown = [f for f in self.flags if types is None or f["type"] in types]
        singles = [f for f in shown if f["type"] not in R.GROUPED_TYPES]
        pos = 0
        for f in singles:
            row = self._make_row(f, multi, child=False)
            self.list_lay.insertWidget(pos, row)
            pos += 1
        for gtype in R.GROUPED_TYPES:
            members = [f for f in shown if f["type"] == gtype]
            if not members:
                continue
            g = GroupRow(gtype, self.list_host)
            g.toggled.connect(self._toggle_group)
            g.bulk_decided.connect(self._decide_group)
            self._group_rows[gtype] = g
            self.list_lay.insertWidget(pos, g)
            pos += 1
            # A filter showing only this group opens it
            if gtype in self.expanded or (types is not None and len(types) == 1):
                for f in members:
                    row = self._make_row(f, multi, child=True)
                    self.list_lay.insertWidget(pos, row)
                    pos += 1
        self.empty_list_lbl.setVisible(not shown)
        if not shown:
            self.list_lay.insertWidget(0, self.empty_list_lbl)
        self._refresh_decisions()

    def _make_row(self, f, multi, child) -> FlagRow:
        row = FlagRow(f, multi, child=child, parent=self.list_host)
        row.selected.connect(self.select_flag)
        row.play_requested.connect(self.play_flag)
        row.decided.connect(self.decide)
        row.set_selected(f["id"] == self.selected_id)
        self._rows[f["id"]] = row
        return row

    def _refresh_decisions(self):
        for row in self._rows.values():
            row.set_decision(self.decisions)
        for gtype, g in self._group_rows.items():
            members = [f for f in self.flags if f["type"] == gtype]
            g.update_state(members, self.decisions, gtype in self.expanded)

    def _update_counts(self):
        n = R.open_count(self.flags, self.decisions)
        self.open_lbl.setText(f"{n} open" if n else "All reviewed")
        files = (self.manifest or {}).get("files") or []
        floors = [f.get("noise_floor_db") for f in files if f.get("noise_floor_db") is not None]
        summary = R.cut_summary(self.manifest or {}, self.flags, self.decisions, self.detection)
        parts = []
        if floors:
            parts.append(f"room tone {min(floors):.0f} dB")
        parts.append(f"{summary['clips']} VO clips")
        parts.append(f"deletes {summary['silence_deleted_sec']:.1f} s of silence")
        if summary["dropped_sec"] > 0:
            parts.append(f"drops {summary['dropped_sec']:.1f} s you rejected")
        self.readout.setText(" · ".join(parts))
        self.open_count_changed.emit(n)

    def _set_filter(self, key: str):
        self.filter_key = key
        self.filter_bar.setCurrentItem(key)
        self._rebuild_list()

    def _toggle_group(self, ftype: str):
        self.expanded ^= {ftype}
        self._rebuild_list()

    # ------------------------------------------------------------------ decisions
    def decide(self, flag_id: str, value: str):
        flag = next((f for f in self.flags if f["id"] == flag_id), None)
        if not flag or value not in R.valid_decisions(flag):
            return
        self.decisions[flag_id] = value
        self.select_flag(flag_id, focus=False)
        self._refresh_decisions()
        self._refresh_wave()
        self._update_counts()
        self._save_timer.start()

    def _decide_group(self, ftype: str, value: str):
        for f in self.flags:
            if f["type"] == ftype and value in R.valid_decisions(f):
                self.decisions[f["id"]] = value
        self._refresh_decisions()
        self._update_counts()
        self._save_timer.start()

    # ------------------------------------------------------------------ selection / playback
    def _ordered_ids(self) -> List[str]:
        ids = []
        for i in range(self.list_lay.count()):
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, FlagRow):
                ids.append(w.flag["id"])
        return ids

    def select_flag(self, flag_id: str, focus: bool = True):
        """Selects a flag. With focus, the waveform zooms to it and the playhead moves to
        just before it, so Space plays it in context."""
        self.selected_id = flag_id
        for fid, row in self._rows.items():
            row.set_selected(fid == flag_id)
        flag = next((f for f in self.flags if f["id"] == flag_id), None)
        if flag:
            idx = self._part_index(flag["part_id"])
            if idx is not None and idx != self.part_combo.currentIndex():
                self.part_combo.setCurrentIndex(idx)
            row = self._rows.get(flag_id)
            if row:
                self.scroll.ensureWidgetVisible(row, 0, 24)
        self.wave.set_selected(flag_id)
        if flag and focus:
            self.wave.frame(flag["in"], flag["out"])
            self._move_playhead(max(0.0, flag["in"] - 1.0))
        self.setFocus()

    def _on_wave_clicked(self, flag_id: str):
        flag = next((f for f in self.flags if f["id"] == flag_id), None)
        if flag and flag_id not in self._rows and flag["type"] in R.GROUPED_TYPES:
            self.expanded.add(flag["type"])
            if self._visible_types() is not None and flag["type"] not in self._visible_types():
                self._set_filter("all")
            else:
                self._rebuild_list()
        # The click already put the playhead where you clicked: keep it and the zoom.
        self.select_flag(flag_id, focus=False)

    def play_flag(self, flag_id: str):
        """Row play button: the flagged audio from the original file with 2 s either side
        (a dropped take isn't in the cut preview, so this always plays the raw file)."""
        flag = next((f for f in self.flags if f["id"] == flag_id), None)
        if not flag:
            return
        self.select_flag(flag_id, focus=False)
        part = self._part(flag["part_id"])
        if not part:
            return
        if self.preview.is_playing:
            self.preview.pause()
            return
        self._play_part = flag["part_id"]
        dur = float(part.get("duration_sec") or flag["out"] + 2)
        start, end = max(0.0, flag["in"] - 2.0), min(dur, flag["out"] + 2.0)
        self.wave.frame(start, end)
        self.playhead = start
        self.wave.set_playhead(start)
        self.preview.play(part.get("path", ""), start, end)

    # ------------------------------------------------------------------ transport
    def _part_kept(self, pid: int) -> List[Dict[str, Any]]:
        return [k for k in R.compute_kept_ranges(self.manifest, self.flags, self.decisions, self.detection)
                if k["part_id"] == pid]

    def toggle_play(self):
        if self.preview.is_playing or self._want_play_after_render:
            self._want_play_after_render = False
            self.preview.pause()
            return
        self._start_playback()

    def _start_playback(self):
        part = self._current_part()
        if not part or not self.manifest:
            return
        pid = int(part.get("part_id", 1))
        path = part.get("path", "")
        if not path or not os.path.isfile(path):
            self.transport_lbl.setText("VO file not found")
            return
        self._play_part = pid
        dur = float(part.get("duration_sec") or 0.0)
        if not self.cut_switch.isChecked():
            start = self.playhead if self.playhead < dur - 0.05 else 0.0
            self.transport_lbl.setText("")
            self.preview.play(path, start)
            return
        tmap = build_time_map(self._part_kept(pid))
        if not tmap:
            self.transport_lbl.setText("No VO clips in this part")
            return
        out = preview_path_for(path, tmap)
        if os.path.isfile(out):
            self._play_cut(out, tmap)
            return
        self._render_token += 1
        self._want_play_after_render = True
        self.transport_lbl.setText("Building cut preview…")
        job = _CutRenderer(self._render_token, path, tmap)
        job.signals.done.connect(self._on_render_done)
        job.signals.failed.connect(self._on_render_failed)
        QThreadPool.globalInstance().start(job)

    def _play_cut(self, out: str, tmap: TimeMap):
        self._renders.add(out)
        self.transport_lbl.setText("")
        c = source_to_cut(self.playhead, tmap)
        if c is None:  # playhead after the last clip: start over
            c = 0.0
        self.preview.play(out, c, tmap=tmap)

    def _on_render_done(self, token: int, out: str):
        self._renders.add(out)
        if token != self._render_token:
            return
        self.transport_lbl.setText("")
        if self._want_play_after_render:
            self._want_play_after_render = False
            self._start_playback()  # re-checks the ranges: they may have changed meanwhile

    def _on_render_failed(self, token: int, err: str):
        if token == self._render_token:
            self._want_play_after_render = False
            self.transport_lbl.setText(f"Cut preview failed: {err[:80]}")

    def _on_cut_mode_changed(self, _checked: bool):
        if self.preview.is_playing:
            self._start_playback()

    def _move_playhead(self, t: float):
        self.playhead = t
        self.wave.set_playhead(t)
        if self.preview.is_playing:
            self._start_playback()

    def _on_seek(self, t: float):
        """Scrubbing is silent: playback pauses while you drag and resumes on release."""
        if self.preview.is_playing:
            self._resume_after_scrub = True
            self.preview.pause()
        self.playhead = t

    def _on_scrub_finished(self, t: float):
        self.playhead = t
        if self._resume_after_scrub:
            self._resume_after_scrub = False
            self._start_playback()

    def _on_play_state(self, playing: bool):
        self.play_btn.setIcon(FluentIcon.PAUSE if playing else FluentIcon.PLAY)

    def _on_play_pos(self, t: float):
        cur = self._current_part()
        if cur and int(cur.get("part_id", 1)) == self._play_part and not self._resume_after_scrub:
            self.playhead = t
            self.wave.set_playhead(t, follow=True)

    def keyPressEvent(self, e):
        key = e.key()
        ids = self._ordered_ids()
        if key in (Qt.Key_Down, Qt.Key_Up) and ids:
            cur = ids.index(self.selected_id) if self.selected_id in ids else -1
            nxt = min(len(ids) - 1, cur + 1) if key == Qt.Key_Down else max(0, cur - 1)
            self.select_flag(ids[nxt])
            return
        if key == Qt.Key_Space:
            self.toggle_play()
            return
        flag = next((f for f in self.flags if f["id"] == self.selected_id), None)
        if flag:
            if key in (Qt.Key_K, Qt.Key_D):
                if flag["type"] == R.DUPLICATE_TAKE:
                    value = R.KEEP_ALL if key == Qt.Key_K else None
                else:
                    value = R.KEEP if key == Qt.Key_K else R.DROP
                if value:
                    self.decide(flag["id"], value)
                return
            if Qt.Key_1 <= key <= Qt.Key_9 and flag["type"] == R.DUPLICATE_TAKE:
                self.decide(flag["id"], f"{R.PICK_PREFIX}{key - Qt.Key_1}")
                return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ parts / waveform
    def _part(self, part_id: int) -> Optional[Dict[str, Any]]:
        return next((f for f in (self.manifest or {}).get("files") or [] if int(f.get("part_id", 1)) == int(part_id)), None)

    def _part_index(self, part_id: int) -> Optional[int]:
        for i, f in enumerate((self.manifest or {}).get("files") or []):
            if int(f.get("part_id", 1)) == int(part_id):
                return i
        return None

    def _current_part(self) -> Optional[Dict[str, Any]]:
        files = (self.manifest or {}).get("files") or []
        i = self.part_combo.currentIndex()
        return files[i] if 0 <= i < len(files) else (files[0] if files else None)

    def _on_part_changed(self, _i: int):
        if not getattr(self, "_suppress_part", False):
            self.preview.stop()
            self._want_play_after_render = False
            self.playhead = 0.0
            self._refresh_wave()

    def _refresh_wave(self):
        part = self._current_part()
        if not part:
            return
        pid = int(part.get("part_id", 1))
        env = self.envs.get(pid)
        if env is None:
            if self.envs:
                self.wave.set_message("No waveform for this part")
            return
        islands = [i for i in self.manifest.get("islands") or [] if int(i.get("part_id", 1)) == pid]
        self.wave.set_data(env, float(part.get("duration_sec") or 0.0),
                           [f for f in self.flags if f["part_id"] == pid], self.selected_id,
                           self._part_kept(pid), islands)
        self.wave.set_playhead(self.playhead)
