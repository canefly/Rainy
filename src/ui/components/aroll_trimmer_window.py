import os
import sys
import math
from typing import Optional, Dict, Any, List, Tuple
import numpy as np
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize, QTimer, QThread
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QKeySequence, QShortcut, QCursor,
    QIcon, QPixmap
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSizePolicy, QApplication
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, TransparentToolButton, ToolButton, PrimaryToolButton,
    FluentIcon, isDarkTheme, ComboBox, IconWidget
)

from ...core.models import ScriptItem
from ...core.database import db
from ...core.parser import format_smpte, parse_time_single, process_time_span
from ...core.engine_manager import is_mpv_available
from ...core.video_engine import MpvPlayer
from ...core.logger import get_logger
from ..theme import ThemeManager

logger = get_logger("aroll_trimmer")


def make_bracket_icon(kind: str, color: QColor, size: int = 32) -> QIcon:
    """Draws a '[' (kind='in') or ']' (kind='out') glyph icon matching the timeline In/Out brackets."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, size * 0.09)
    pen.setCapStyle(Qt.SquareCap)
    pen.setJoinStyle(Qt.MiterJoin)
    p.setPen(pen)
    top, bottom = size * 0.1, size * 0.9
    arm = size * 0.3
    if kind == "in":
        x = size * 0.28
        p.drawLine(QPoint(int(x), int(top)), QPoint(int(x), int(bottom)))
        p.drawLine(QPoint(int(x), int(top)), QPoint(int(x + arm), int(top)))
        p.drawLine(QPoint(int(x), int(bottom)), QPoint(int(x + arm), int(bottom)))
    else:
        x = size * 0.72
        p.drawLine(QPoint(int(x), int(top)), QPoint(int(x), int(bottom)))
        p.drawLine(QPoint(int(x - arm), int(top)), QPoint(int(x), int(top)))
        p.drawLine(QPoint(int(x - arm), int(bottom)), QPoint(int(x), int(bottom)))
    p.end()
    return QIcon(pm)


def format_ruler_timecode(sec: float, fps: float, step: float) -> str:
    """
    Smart simplified Premiere-style timecode formatting for ruler labels.
    - If hours == 0 and step >= 1.0s: MM:SS (e.g. 20:30)
    - If hours == 0 and step < 1.0s: MM:SS:FF (e.g. 20:25:00)
    - If hours > 0 and step >= 1.0s: HH:MM:SS (e.g. 01:20:30)
    - If hours > 0 and step < 1.0s: HH:MM:SS:FF (e.g. 01:20:25:00)
    """
    fps_val = max(1.0, fps)
    sec_val = max(0.0, sec)
    total_frames = int(round(sec_val * fps_val))
    int_fps = max(1, int(round(fps_val)))
    f = total_frames % int_fps
    total_sec = total_frames // int_fps
    s = total_sec % 60
    m = (total_sec // 60) % 60
    h = total_sec // 3600

    if step < 1.0:
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
        return f"{m:02d}:{s:02d}:{f:02d}"
    else:
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


class TimecodeScrubBar(QWidget):
    """
    Premiere Pro-style Two-Tier Timeline Scrubber & Precision Zoom Control.
    
    Tier 1 (Top, 44px):
      - Time ruler with adaptive dynamic SMPTE tick marks (subdividing into frames, seconds, minutes, hours based on zoom).
      - Draggable In ('[') and Out (']') brackets with shaded region.
      - Draggable Playhead cursor with inverted pentagon head and laser hairline.
      - Mouse wheel / Alt+wheel zooming anchored to playhead position.

    Tier 2 (Bottom, 24px):
      - Full-width zoom & pan overview bar representing 100% media duration.
      - Lighter viewport thumb representing visible time window.
      - Bullseye grip handles '(O)' on left and right edges for symmetric or asymmetric zooming.
      - Center-thumb drag for panning the visible window across the media.
    """
    seek_requested = Signal(float)       # target seconds
    in_point_changed = Signal(float)     # in seconds
    out_point_changed = Signal(float)    # out seconds
    drag_started = Signal(str)           # "in_bracket", "out_bracket", "playhead", "thumb_left", "thumb_right", "thumb_pan"
    drag_finished = Signal(str)          # drag mode that completed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        self.duration: float = 60.0
        self.playhead_pos: float = 0.0
        self.in_pos: float = 0.0
        self.out_pos: float = 10.0
        # When False (Loop/Player state), In/Out brackets are locked: track clicks only scrub.
        self.trim_enabled: bool = True

        self.view_start_sec: float = 0.0
        self.view_end_sec: float = 60.0
        self.min_view_span_sec: float = 0.25

        self._fps: float = 23.976
        self._drag_mode: Optional[str] = None
        self._drag_start_x: int = 0
        self._drag_start_view_start: float = 0.0
        self._drag_start_view_end: float = 60.0
        self._drag_start_span: float = 60.0
        self._drag_start_anchor: float = 0.0
        self._zoom_anchor_mode: str = "cut"
        self._waveform_peaks: Optional[np.ndarray] = None
        self._waveform_sample_rate: int = 100
        self.margin_x: int = 14

    def set_zoom_anchor_mode(self, mode: str):
        """Sets zoom anchor mode: 'cut' (anchors to In/Out cut) or 'playhead' (anchors to playhead)."""
        self._zoom_anchor_mode = mode

    def set_waveform_data(self, peaks: Optional[np.ndarray], sample_rate: int = 100):
        """Sets the audio waveform peaks to render across the entire timeline lane."""
        self._waveform_peaks = peaks
        self._waveform_sample_rate = max(1, sample_rate)
        self.update()

    def get_zoom_anchor(self) -> float:
        """
        Calculates zoom center anchor based on selected anchor mode.
        If mode is 'playhead', anchors directly to current playhead.
        If mode is 'cut', anchors to (in_pos + out_pos) / 2.0 (falling back to playhead if no cut).
        """
        if self._zoom_anchor_mode == "playhead":
            return self.playhead_pos
        cut_span = self.out_pos - self.in_pos
        has_cut = cut_span > 0.05 and (self.in_pos > 0.01 or self.out_pos < (self.duration - 0.01))
        if has_cut:
            return (self.in_pos + self.out_pos) / 2.0
        return self.playhead_pos

    def center_on(self, sec: float):
        """Centers the visible zoom viewport around the given time in seconds."""
        span = self.view_end_sec - self.view_start_sec
        if span >= (self.duration - 0.001):
            return
        new_start = sec - (span / 2.0)
        if new_start < 0.0:
            new_start = 0.0
        elif new_start + span > self.duration:
            new_start = max(0.0, self.duration - span)
        self.view_start_sec = new_start
        self.view_end_sec = min(self.duration, new_start + span)
        self.update()

    def is_playhead_in_view(self) -> bool:
        """Returns True if the current playhead position is visible within the zoomed time window."""
        return self.view_start_sec <= self.playhead_pos <= self.view_end_sec

    def ensure_playhead_in_view(self):
        """
        Only recenters the viewport around the playhead if it is currently out of frame.
        If the playhead is already visible within the current zoom view, the viewport position is unchanged.
        """
        if not self.is_playhead_in_view():
            self.center_on(self.playhead_pos)

    def frame_cut(self, in_sec: Optional[float] = None, out_sec: Optional[float] = None):
        """
        Automatically frames the zoom view around the cut:
        cut_center = (in_sec + out_sec) / 2.0, span = max(12.0, (out_sec - in_sec) * 3.0).
        Centers the view so the cut is immediately visible and centered on load.
        """
        if in_sec is not None:
            self.in_pos = max(0.0, min(in_sec, self.duration))
        if out_sec is not None:
            self.out_pos = max(self.in_pos + (1.0 / self._fps), min(out_sec, self.duration))

        cut_center = (self.in_pos + self.out_pos) / 2.0
        raw_span = (self.out_pos - self.in_pos) * 3.0
        span = max(12.0, raw_span)
        span = max(self.min_view_span_sec, min(self.duration, span))

        v_start = cut_center - (span / 2.0)
        v_end = v_start + span
        if v_start < 0.0:
            v_start = 0.0
            v_end = min(self.duration, v_start + span)
        elif v_end > self.duration:
            v_end = self.duration
            v_start = max(0.0, v_end - span)

        self.view_start_sec = v_start
        self.view_end_sec = v_end
        self.update()

    def check_playback_follow(self):
        """
        During playback, if playhead reaches near the right edge of the visible zoom view,
        automatically follow / scroll the viewport forward.
        """
        span = self.view_end_sec - self.view_start_sec
        if span >= (self.duration - 0.001):
            return

        threshold = self.view_end_sec - (0.12 * span)
        if self.playhead_pos >= threshold:
            new_start = self.playhead_pos - (0.15 * span)
            new_start = max(0.0, min(self.duration - span, new_start))
            self.view_start_sec = new_start
            self.view_end_sec = new_start + span
            self.update()
        elif self.playhead_pos < self.view_start_sec:
            new_start = max(0.0, self.playhead_pos - (0.15 * span))
            self.view_start_sec = new_start
            self.view_end_sec = min(self.duration, new_start + span)
            self.update()

    # Overview-handle zoom: once the thumb hits its minimum physical width it can't shrink
    # further, so each extra SQUEEZE_PX_PER_HALVING px of squeeze halves the span exponentially
    # (continuous with the linear regime) instead of making the thumb "glide".
    THUMB_MIN_PX = 54.0
    SQUEEZE_PX_PER_HALVING = 30.0

    def _span_to_squeeze_pos(self, span: float) -> float:
        """Maps a view span to a virtual 'thumb width in px' (can go below the physical minimum)."""
        w = max(1, self.width() - 2 * self.margin_x)
        t_min = (self.THUMB_MIN_PX / w) * self.duration
        if span >= t_min:
            return span / self.duration * w
        return self.THUMB_MIN_PX - self.SQUEEZE_PX_PER_HALVING * math.log2(t_min / max(span, 1e-9))

    def _squeeze_pos_to_span(self, pos: float) -> float:
        w = max(1, self.width() - 2 * self.margin_x)
        t_min = (self.THUMB_MIN_PX / w) * self.duration
        if pos >= self.THUMB_MIN_PX:
            return pos / w * self.duration
        return t_min * (2.0 ** (-(self.THUMB_MIN_PX - pos) / self.SQUEEZE_PX_PER_HALVING))

    def _apply_handle_zoom(self, squeeze_px: float):
        """
        Zooms the view while dragging an overview handle. Tracks the cursor 1:1 while the thumb
        is wider than its minimum, then keeps zooming exponentially (thumb stays put) down to
        the single-frame limit. Zoom is anchored on the cut/playhead like the mouse wheel.
        """
        start_span = max(self.min_view_span_sec, self._drag_start_span)
        pos = self._span_to_squeeze_pos(start_span) - squeeze_px
        new_span = self._squeeze_pos_to_span(pos)
        new_span = max(self.min_view_span_sec, min(self.duration, new_span))

        t_anchor = self._drag_start_anchor
        alpha = (t_anchor - self._drag_start_view_start) / max(0.001, self._drag_start_view_end - self._drag_start_view_start)
        alpha = max(0.0, min(1.0, alpha))

        new_start = t_anchor - alpha * new_span
        new_end = new_start + new_span
        if new_start < 0.0:
            new_start = 0.0
            new_end = min(self.duration, new_span)
        elif new_end > self.duration:
            new_end = self.duration
            new_start = max(0.0, self.duration - new_span)

        self.view_start_sec = new_start
        self.view_end_sec = new_end
        self.update()

    def _get_tier2_thumb_geometry(self) -> Tuple[int, int, int]:
        """Calculates Tier 2 thumb left, right, and width enforcing minimum 54px physical width."""
        w = self.width()
        min_thumb_w = 54
        max_right = w - self.margin_x

        raw_left = self._overview_sec_to_x(self.view_start_sec)
        raw_right = self._overview_sec_to_x(self.view_end_sec)

        if (raw_right - raw_left) < min_thumb_w:
            mid = (raw_left + raw_right) / 2.0
            th_left = int(round(mid - min_thumb_w / 2.0))
            th_right = th_left + min_thumb_w
            if th_left < self.margin_x:
                th_left = self.margin_x
                th_right = th_left + min_thumb_w
            elif th_right > max_right:
                th_right = max_right
                th_left = max(self.margin_x, th_right - min_thumb_w)
        else:
            th_left = raw_left
            th_right = raw_right

        th_w = max(min_thumb_w, th_right - th_left)
        return th_left, th_right, th_w

    def set_range(self, duration: float, in_sec: float, out_sec: float, fps: float = 23.976):
        self.duration = max(0.1, duration)
        self.in_pos = max(0.0, min(in_sec, self.duration))
        self.out_pos = max(self.in_pos + (1.0 / fps), min(out_sec, self.duration))
        self.playhead_pos = self.in_pos
        self._fps = fps
        self.min_view_span_sec = max(0.05, min(self.duration, 5.0 / fps))
        self.view_start_sec = 0.0
        self.view_end_sec = self.duration
        self.update()

    def set_playhead(self, seconds: float):
        # Ignore external updates while the user is actively dragging the playhead
        if self._drag_mode == "playhead":
            return
        self.playhead_pos = max(0.0, min(seconds, self.duration))
        self.update()

    def set_in_point(self, seconds: float):
        self.in_pos = max(0.0, min(seconds, self.out_pos - (1.0 / self._fps)))
        self.in_point_changed.emit(self.in_pos)
        self.update()

    def set_out_point(self, seconds: float):
        self.out_pos = max(self.in_pos + (1.0 / self._fps), min(seconds, self.duration))
        self.out_point_changed.emit(self.out_pos)
        self.update()

    def _sec_to_x(self, sec: float) -> int:
        span = max(0.001, self.view_end_sec - self.view_start_sec)
        w = max(1, self.width() - 2 * self.margin_x)
        return int(self.margin_x + ((sec - self.view_start_sec) / span) * w)

    def _x_to_sec(self, x: int) -> float:
        span = max(0.001, self.view_end_sec - self.view_start_sec)
        w = max(1, self.width() - 2 * self.margin_x)
        clamped_x = max(self.margin_x, min(x, self.width() - self.margin_x))
        return self.view_start_sec + ((clamped_x - self.margin_x) / w) * span

    def _overview_sec_to_x(self, sec: float) -> int:
        w = max(1, self.width() - 2 * self.margin_x)
        clamped = max(0.0, min(sec, self.duration))
        return int(self.margin_x + (clamped / max(0.001, self.duration)) * w)

    def _overview_x_to_sec(self, x: int) -> float:
        w = max(1, self.width() - 2 * self.margin_x)
        clamped_x = max(self.margin_x, min(x, self.width() - self.margin_x))
        return ((clamped_x - self.margin_x) / w) * self.duration

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        if delta == 0:
            return

        factor = 0.82 if delta > 0 else (1.0 / 0.82)
        current_span = max(self.min_view_span_sec, self.view_end_sec - self.view_start_sec)
        new_span = max(self.min_view_span_sec, min(self.duration, current_span * factor))

        t_anchor = self.get_zoom_anchor()

        alpha = (t_anchor - self.view_start_sec) / current_span
        alpha = max(0.0, min(1.0, alpha))

        new_view_start = t_anchor - alpha * new_span
        new_view_end = new_view_start + new_span

        if new_view_start < 0.0:
            new_view_start = 0.0
            new_view_end = min(self.duration, new_view_start + new_span)
        elif new_view_end > self.duration:
            new_view_end = self.duration
            new_view_start = max(0.0, new_view_end - new_span)

        self.view_start_sec = new_view_start
        self.view_end_sec = new_view_end
        self.update()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._draw_scrub_bar(painter)
        finally:
            painter.end()

    def _draw_scrub_bar(self, painter: QPainter):
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        accent = QColor(pal.get("accent", "#38bdf8"))

        # Background surfaces
        t1_bg = QColor(20, 20, 24) if is_dark else QColor(241, 245, 249)
        painter.fillRect(QRect(0, 0, w, 45), t1_bg)
        t2_bg = QColor(14, 14, 18) if is_dark else QColor(230, 235, 240)
        painter.fillRect(QRect(0, 45, w, h - 45), t2_bg)

        # -------------------------------------------------------------
        # TIER 1: Time Ruler with Adaptive Dynamic SMPTE Tick Marks
        # -------------------------------------------------------------
        span = max(0.001, self.view_end_sec - self.view_start_sec)
        pps = max(0.001, (w - 2 * self.margin_x) / span)
        fps = self._fps or 23.976
        frame_dur = 1.0 / fps

        # When fully zoomed out (view_span >= 0.7 * duration), hide all timecode numbers
        show_tc_numbers = (span < 0.7 * self.duration)

        candidate_intervals = [
            frame_dur, frame_dur * 2, frame_dur * 5, frame_dur * 10, frame_dur * 12,
            0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0,
            60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0, 7200.0
        ]
        # Require at least 120px between timecode labels when numbers are shown
        min_label_px = 120.0 if show_tc_numbers else 45.0
        major_step = candidate_intervals[-1]
        for interval in candidate_intervals:
            if interval * pps >= min_label_px:
                major_step = interval
                break

        if major_step <= frame_dur * 2:
            sub_count = 1
        elif major_step <= 1.0:
            sub_count = max(2, int(round(major_step / frame_dur)))
        elif major_step in (2.0, 5.0, 10.0, 15.0, 30.0):
            sub_count = 5
        elif major_step in (60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0, 7200.0):
            sub_count = 4
        else:
            sub_count = 4

        ruler_tick_col = QColor(255, 255, 255, 75) if is_dark else QColor(0, 0, 0, 75)
        ruler_minor_col = QColor(255, 255, 255, 30) if is_dark else QColor(0, 0, 0, 30)
        ruler_text_col = QColor("#94a3b8") if is_dark else QColor("#64748b")
        ruler_font = QFont("Consolas", 8)
        ruler_font.setStyleHint(QFont.Monospace)

        first_tick = (int(self.view_start_sec // major_step)) * major_step
        t = first_tick
        sub_step = major_step / sub_count
        draw_minor = (sub_step * pps >= 6.0)

        while t <= self.view_end_sec + major_step:
            tx = self._sec_to_x(t)
            if self.margin_x <= tx <= w - self.margin_x:
                painter.setPen(QPen(ruler_tick_col, 1))
                painter.drawLine(tx, 0, tx, 8)
                if show_tc_numbers:
                    tc_txt = format_ruler_timecode(t, fps, major_step)
                    painter.setPen(ruler_text_col)
                    painter.setFont(ruler_font)
                    painter.drawText(QRect(tx - 50, 8, 100, 11), Qt.AlignCenter, tc_txt)

            # Subdivisions
            if draw_minor:
                for s_idx in range(1, sub_count):
                    st = t + s_idx * sub_step
                    if self.view_start_sec <= st <= self.view_end_sec:
                        stx = self._sec_to_x(st)
                        if self.margin_x <= stx <= w - self.margin_x:
                            painter.setPen(QPen(ruler_minor_col, 1))
                            painter.drawLine(stx, 0, stx, 4)
            t += major_step

        # -------------------------------------------------------------
        # TIER 1 Track: Full-Width Audio Waveforms Across the Entire Lane (Clean Mirrored Spikes)
        # -------------------------------------------------------------
        in_x = self._sec_to_x(self.in_pos)
        out_x = self._sec_to_x(self.out_pos)
        cy = 31
        max_wave_h = 11

        if self._waveform_peaks is not None and len(self._waveform_peaks) > 0:
            total_peaks = len(self._waveform_peaks)
            sr = self._waveform_sample_rate
            peaks_arr = self._waveform_peaks

            outside_col = QColor(148, 163, 184, 110) if is_dark else QColor(100, 116, 139, 130)
            pen_inside = QPen(QColor(56, 189, 248, 220), 1)
            pen_outside = QPen(outside_col, 1)

            for px in range(self.margin_x, w - self.margin_x):
                t0 = self._x_to_sec(px)
                t1 = self._x_to_sec(px + 1)
                idx0 = max(0, min(total_peaks - 1, int(t0 * sr)))
                idx1 = max(idx0 + 1, min(total_peaks, int(t1 * sr)))
                if idx1 > idx0:
                    peak = float(np.max(peaks_arr[idx0:idx1]))
                else:
                    peak = float(peaks_arr[idx0])

                bar_h = int(round(peak * max_wave_h))
                if bar_h <= 0:
                    # Clean silence: no baseline line, no horizontal bar
                    continue

                if in_x <= px <= out_x:
                    painter.setPen(pen_inside)  # Active selection: cyan
                else:
                    painter.setPen(pen_outside)  # Outside: subtle slate blue
                painter.drawLine(px, cy - bar_h, px, cy + bar_h)

        # -------------------------------------------------------------
        # In / Out Bracket Range & Shaded Region (Tier 1)
        # -------------------------------------------------------------
        # Selected In/Out Shaded Region
        if out_x > self.margin_x and in_x < w - self.margin_x:
            reg_left = max(self.margin_x, in_x)
            reg_right = min(w - self.margin_x, out_x)
            region_color = QColor(accent.red(), accent.green(), accent.blue(), 35)
            painter.fillRect(QRect(reg_left, 20, max(1, reg_right - reg_left), 24), region_color)

        # In Bracket '['
        if self.margin_x - 10 <= in_x <= w - self.margin_x + 10:
            painter.setPen(QPen(accent, 2.5))
            painter.drawLine(in_x, 20, in_x, 44)
            painter.drawLine(in_x, 20, in_x + 6, 20)
            painter.drawLine(in_x, 44, in_x + 6, 44)

        # Out Bracket ']'
        if self.margin_x - 10 <= out_x <= w - self.margin_x + 10:
            painter.setPen(QPen(accent, 2.5))
            painter.drawLine(out_x, 20, out_x, 44)
            painter.drawLine(out_x - 6, 20, out_x, 20)
            painter.drawLine(out_x - 6, 44, out_x, 44)

        # Playhead Line & Pentagon Cap
        play_x = self._sec_to_x(self.playhead_pos)
        if self.margin_x - 8 <= play_x <= w - self.margin_x + 8:
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            head_pts = [
                QPoint(play_x - 5, 0),
                QPoint(play_x + 5, 0),
                QPoint(play_x + 5, 7),
                QPoint(play_x, 12),
                QPoint(play_x - 5, 7),
            ]
            painter.drawPolygon(head_pts)
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.drawLine(play_x, 12, play_x, 44)

        # -------------------------------------------------------------
        # TIER 2: Full-Width Overview Bar, Viewport Thumb & Grip Handles
        # -------------------------------------------------------------
        # Border divider line between Tier 1 and Tier 2
        painter.setPen(QPen(QColor(255, 255, 255, 25) if is_dark else QColor(0, 0, 0, 25), 1))
        painter.drawLine(0, 45, w, 45)

        # Full media background track
        track_rect = QRect(self.margin_x, 52, w - 2 * self.margin_x, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(36, 36, 40) if is_dark else QColor(220, 226, 234))
        painter.drawRoundedRect(track_rect, 3, 3)

        # Mini In/Out cut region on overview track
        ov_in = self._overview_sec_to_x(self.in_pos)
        ov_out = self._overview_sec_to_x(self.out_pos)
        ov_w = max(2, ov_out - ov_in)
        painter.fillRect(QRect(ov_in, 53, ov_w, 8), QColor(accent.red(), accent.green(), accent.blue(), 130))

        # Viewport thumb with guaranteed minimum physical width of 36px
        th_left, th_right, th_w = self._get_tier2_thumb_geometry()
        th_rect = QRect(th_left, 50, th_w, 14)

        thumb_col = QColor(63, 63, 70, 210) if is_dark else QColor(203, 213, 225, 210)
        painter.setPen(QPen(QColor(113, 113, 122), 1))
        painter.setBrush(thumb_col)
        painter.drawRoundedRect(th_rect, 3, 3)

        # Circular Bullseye Grip Handles '(O)' on left and right edges (radius 5px)
        cy = 57
        # Left bullseye
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QColor(24, 24, 27) if is_dark else QColor(255, 255, 255))
        painter.drawEllipse(QPoint(th_left, cy), 5, 5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPoint(th_left, cy), 2, 2)

        # Right bullseye
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QColor(24, 24, 27) if is_dark else QColor(255, 255, 255))
        painter.drawEllipse(QPoint(th_right, cy), 5, 5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPoint(th_right, cy), 2, 2)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        p = event.position().toPoint() if hasattr(event, "position") else event.pos()
        x, y = p.x(), p.y()

        if y >= 45:
            # Tier 2 interaction (Zoom / Pan)
            th_left, th_right, th_w = self._get_tier2_thumb_geometry()
            handle_hit = 8

            if abs(x - th_left) <= handle_hit:
                self._drag_mode = "thumb_left"
                self._drag_start_x = x
                self._drag_start_view_start = self.view_start_sec
                self._drag_start_view_end = self.view_end_sec
                self._drag_start_span = max(self.min_view_span_sec, self.view_end_sec - self.view_start_sec)
                self._drag_start_anchor = self.get_zoom_anchor()
                self.drag_started.emit("thumb_left")
            elif abs(x - th_right) <= handle_hit:
                self._drag_mode = "thumb_right"
                self._drag_start_x = x
                self._drag_start_view_start = self.view_start_sec
                self._drag_start_view_end = self.view_end_sec
                self._drag_start_span = max(self.min_view_span_sec, self.view_end_sec - self.view_start_sec)
                self._drag_start_anchor = self.get_zoom_anchor()
                self.drag_started.emit("thumb_right")
            elif th_left < x < th_right:
                self._drag_mode = "thumb_pan"
                self._drag_start_x = x
                self._drag_start_view_start = self.view_start_sec
                self._drag_start_view_end = self.view_end_sec
                self.setCursor(Qt.ClosedHandCursor)
                self.drag_started.emit("thumb_pan")
            else:
                # Click outside thumb: center viewport around clicked time
                click_sec = self._overview_x_to_sec(x)
                self.center_on(click_sec)
        else:
            # Tier 1 interaction (Time Ruler Header vs Track Brackets)
            in_x = self._sec_to_x(self.in_pos)
            out_x = self._sec_to_x(self.out_pos)
            fps = self._fps or 23.976
            frame_dur = 1.0 / fps

            if y < 18:
                # Top ruler header: scrub/drag playhead
                self._drag_mode = "playhead"
                raw_sec = max(0.0, min(self._x_to_sec(x), self.duration))
                sec = max(0.0, min(round(raw_sec / frame_dur) * frame_dur, self.duration))
                self.playhead_pos = sec
                self.seek_requested.emit(sec)
                self.drag_started.emit("playhead")
                self.update()
            else:
                # Track area: brackets have priority (Edit state only), then playhead
                if self.trim_enabled and abs(x - in_x) <= 8:
                    self._drag_mode = "in_bracket"
                    self.drag_started.emit("in_bracket")
                elif self.trim_enabled and abs(x - out_x) <= 8:
                    self._drag_mode = "out_bracket"
                    self.drag_started.emit("out_bracket")
                else:
                    self._drag_mode = "playhead"
                    raw_sec = max(0.0, min(self._x_to_sec(x), self.duration))
                    sec = max(0.0, min(round(raw_sec / frame_dur) * frame_dur, self.duration))
                    self.playhead_pos = sec
                    self.seek_requested.emit(sec)
                    self.drag_started.emit("playhead")
                    self.update()

    def mouseMoveEvent(self, event):
        p = event.position().toPoint() if hasattr(event, "position") else event.pos()
        x, y = p.x(), p.y()
        w = max(1, self.width() - 2 * self.margin_x)
        fps = self._fps or 23.976
        frame_dur = 1.0 / fps

        if self._drag_mode == "playhead":
            raw_sec = max(0.0, min(self._x_to_sec(x), self.duration))
            sec = max(0.0, min(round(raw_sec / frame_dur) * frame_dur, self.duration))
            if abs(sec - self.playhead_pos) >= (frame_dur * 0.5):
                self.playhead_pos = sec
                self.seek_requested.emit(sec)
                self.update()
        elif self._drag_mode == "in_bracket":
            raw_sec = max(0.0, min(self._x_to_sec(x), self.out_pos - frame_dur))
            sec = max(0.0, min(round(raw_sec / frame_dur) * frame_dur, self.out_pos - frame_dur))
            if abs(sec - self.in_pos) >= (frame_dur * 0.5):
                self.in_pos = sec
                self.in_point_changed.emit(sec)
                self.update()
        elif self._drag_mode == "out_bracket":
            raw_sec = max(self.in_pos + frame_dur, min(self._x_to_sec(x), self.duration))
            sec = max(self.in_pos + frame_dur, min(round(raw_sec / frame_dur) * frame_dur, self.duration))
            if abs(sec - self.out_pos) >= (frame_dur * 0.5):
                self.out_pos = sec
                self.out_point_changed.emit(sec)
                self.update()
        elif self._drag_mode in ("thumb_left", "thumb_right"):
            # Squeezing the handles together zooms in, pulling them apart zooms out.
            dx = x - self._drag_start_x
            squeeze_px = dx if self._drag_mode == "thumb_left" else -dx
            self._apply_handle_zoom(squeeze_px)
        elif self._drag_mode == "thumb_pan":
            dx = x - self._drag_start_x
            span = self._drag_start_view_end - self._drag_start_view_start
            d_sec = (dx / w) * self.duration
            new_start = self._drag_start_view_start + d_sec
            new_end = self._drag_start_view_end + d_sec
            if new_start < 0.0:
                new_start = 0.0
                new_end = min(self.duration, span)
            elif new_end > self.duration:
                new_end = self.duration
                new_start = max(0.0, self.duration - span)
            self.view_start_sec = new_start
            self.view_end_sec = new_end
            self.update()
        else:
            # Hover cursor update
            if y >= 45:
                th_left, th_right, th_w = self._get_tier2_thumb_geometry()
                handle_hit = 8
                if abs(x - th_left) <= handle_hit or abs(x - th_right) <= handle_hit:
                    self.setCursor(Qt.SizeHorCursor)
                elif th_left < x < th_right:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.PointingHandCursor)
            else:
                in_x = self._sec_to_x(self.in_pos)
                out_x = self._sec_to_x(self.out_pos)
                if self.trim_enabled and y >= 18 and (abs(x - in_x) <= 8 or abs(x - out_x) <= 8):
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            old_mode = self._drag_mode
            self._drag_mode = None
            self.setCursor(QCursor(Qt.PointingHandCursor))
            if old_mode:
                self.drag_finished.emit(old_mode)
            self.update()


class ARollTrimmerWindow(QWidget):
    """
    Floating, modeless Source Monitor window for precision A-Roll cut re-trimming.
    Syncs adjustments instantly with SQLite delta overrides and checklist UI.
    """
    timecode_changed = Signal(str, str, str)  # (item_id, start_timecode, end_timecode)
    request_prev_card = Signal()
    request_next_card = Signal()
    closed = Signal()
    edit_applied = Signal(str)  # item_id whose In/Out changed during an Edit session

    MODE_LOOP = "loop"   # Player state: loops In→Out, brackets locked
    MODE_EDIT = "edit"   # Editor state: full precision trimming controls

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ARollTrimmerWindow")
        self.setWindowTitle("Clip Player")
        self.resize(760, 520)
        self.setMinimumSize(560, 380)

        # Modeless floating window flags
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.current_item: Optional[ScriptItem] = None
        self.current_file: Optional[str] = None
        self.player: Optional[MpvPlayer] = None

        self._in_seconds: float = 0.0
        self._out_seconds: float = 0.0
        self._nominal_fps: float = 23.976
        self._shuttle_speed: float = 1.0
        self.doc_signature: str = ""
        self.probed_media = None
        self._is_closing: bool = False

        # Undo/Redo history stack for trims
        self._undo_stack: List[Tuple[float, float]] = []
        self._redo_stack: List[Tuple[float, float]] = []

        # Deferred In-Memory Overrides: Batch persisted on trimmer close/exit to eliminate UI lag
        self._pending_overrides: Dict[str, Tuple[str, str]] = {}
        self._trim_save_timer = QTimer(self)
        self._trim_save_timer.setSingleShot(True)
        self._trim_save_timer.setInterval(300)
        self._trim_save_timer.timeout.connect(self._persist_trim_overrides)

        # 60 Hz Monotonic UI Update Timer for anti-flicker playhead tracking
        self._is_playing: bool = False
        self._latest_player_pos: float = 0.0
        self._smooth_playhead_pos: float = 0.0
        self._ui_update_timer = QTimer(self)
        self._ui_update_timer.setInterval(16)
        self._ui_update_timer.timeout.connect(self._on_ui_tick)

        # Scrubbing exclusivity & throttled seek timer
        self._is_scrubbing: bool = False
        self._pending_scrub_sec: Optional[float] = None
        self._scrub_seek_timer = QTimer(self)
        self._scrub_seek_timer.setSingleShot(True)
        self._scrub_seek_timer.setInterval(25)
        self._scrub_seek_timer.timeout.connect(self._flush_scrub_seek)

        self._workspace_parent = None
        self._waveform_worker = None
        # Loop (player) vs Edit (trimmer) state; snapshot detects whether an edit session changed In/Out
        self._mode: str = self.MODE_LOOP
        self._edit_snapshot: Optional[Tuple[str, float, float]] = None
        self._setup_ui()
        self.set_mode(self.MODE_LOOP)
        # libmpv is lazy-initialized in _ensure_player() when media is loaded or playback requested

    def _setup_ui(self):
        pal = ThemeManager.get_palette()
        is_dark = pal["is_dark"]
        bg_col = "#111114" if is_dark else "#f8f9fa"
        self.setStyleSheet(f"""
            QWidget#ARollTrimmerWindow {{
                background-color: {bg_col};
                color: {pal['text']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # 1. Top Header Row (Clean: Filename + VFR Badge + Anchor Mode Dropdown)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.clip_name_lbl = QLabel("No Clip Loaded", self)
        self.clip_name_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffffff;")
        top_row.addWidget(self.clip_name_lbl)

        # Subtle Amber VFR Badge
        self.vfr_badge = QFrame(self)
        self.vfr_badge.setObjectName("VfrBadge")
        self.vfr_badge.setStyleSheet("""
            QFrame#VfrBadge {
                background-color: rgba(245, 158, 11, 0.14);
                border: 1px solid rgba(245, 158, 11, 0.4);
                border-radius: 4px;
            }
            QFrame#VfrBadge QLabel {
                color: #fbbf24;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
                border: none;
            }
        """)
        vfr_lay = QHBoxLayout(self.vfr_badge)
        vfr_lay.setContentsMargins(6, 2, 8, 2)
        vfr_lay.setSpacing(4)
        vfr_icon = IconWidget(FluentIcon.INFO, self.vfr_badge)
        vfr_icon.setFixedSize(12, 12)
        vfr_lay.addWidget(vfr_icon)
        vfr_lay.addWidget(QLabel("VFR", self.vfr_badge))
        self.vfr_badge.setToolTip(
            "Variable Frame Rate (VFR) Detected: Footage has dynamic frame timing.\n"
            "Audio/video may desync in Premiere Pro unless transcoded to Constant Frame Rate (CFR)."
        )
        self.vfr_badge.hide()
        top_row.addWidget(self.vfr_badge)

        # Zoom Anchor Dropdown (Cut vs Playhead)
        self.anchor_dropdown = ComboBox(self)
        self.anchor_dropdown.setFixedHeight(26)
        self.anchor_dropdown.addItems(["Anchor: Cut (In/Out)", "Anchor: Playhead"])
        self.anchor_dropdown.setToolTip("Target anchor for zoom magnification (Cut center vs Playhead position)")
        self.anchor_dropdown.currentTextChanged.connect(self._on_anchor_dropdown_changed)
        top_row.addWidget(self.anchor_dropdown)

        top_row.addStretch(1)

        # Loop ⇄ Edit state toggle (Edit enters trimming; Done applies and returns to loop)
        self.mode_btn = PushButton("Edit", self)
        self.mode_btn.setFixedSize(64, 28)
        self.mode_btn.setFocusPolicy(Qt.NoFocus)
        self.mode_btn.clicked.connect(self.toggle_mode)
        top_row.addWidget(self.mode_btn)
        layout.addLayout(top_row)

        # 2. Native Video Viewport Surface
        self.video_viewport = QFrame(self)
        self.video_viewport.setObjectName("VideoViewport")
        self.video_viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_viewport.setAttribute(Qt.WA_NativeWindow, True)
        self.video_viewport.setAttribute(Qt.WA_PaintOnScreen, True)
        self.video_viewport.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.video_viewport.setAttribute(Qt.WA_NoSystemBackground, True)
        self.video_viewport.setStyleSheet("""
            QFrame#VideoViewport {
                background-color: #000000;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
            }
        """)
        # Viewport empty message label
        vp_layout = QVBoxLayout(self.video_viewport)
        vp_layout.setAlignment(Qt.AlignCenter)
        self.vp_status_lbl = QLabel("libmpv Engine Ready. Load a clip to play.", self.video_viewport)
        self.vp_status_lbl.setStyleSheet("color: #64748b; font-size: 13px; font-weight: 600;")
        vp_layout.addWidget(self.vp_status_lbl)

        layout.addWidget(self.video_viewport, 1)

        # 3. Precision Timecode Bar (Directly beneath video viewport, above timeline)
        # Layout: LEFT = [ IN | CENTER = Playhead | RIGHT = OUT ]
        # No captions: the bracket glyphs mirror the In/Out brackets on the timeline.
        tc_bar = QHBoxLayout()
        tc_bar.setContentsMargins(4, 2, 4, 2)
        tc_bar.setSpacing(6)

        mono = "font-family: 'Consolas', 'Courier New', monospace;"
        val_style = mono + "font-size: 13px; font-weight: 600; color: #d4d4d8;"
        bracket_style = mono + "font-size: 16px; font-weight: 700; color: #38bdf8;"

        in_bracket_lbl = QLabel("[", self)
        in_bracket_lbl.setStyleSheet(bracket_style)
        self.in_tc_lbl = QLabel("00:00:00:00", self)
        self.in_tc_lbl.setStyleSheet(val_style)
        self.in_tc_lbl.setToolTip("In point")
        tc_bar.addWidget(in_bracket_lbl)
        tc_bar.addWidget(self.in_tc_lbl)

        tc_bar.addStretch(1)

        # Center: Current Playhead timestamp (only accent-colored element)
        self.smpte_hud_lbl = QLabel("00:00:00:00", self)
        self.smpte_hud_lbl.setStyleSheet("""
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 15px;
            font-weight: 700;
            color: #38bdf8;
        """)
        tc_bar.addWidget(self.smpte_hud_lbl)

        tc_bar.addStretch(1)

        # Right: OUT
        self.out_tc_lbl = QLabel("00:00:00:00", self)
        self.out_tc_lbl.setStyleSheet(val_style)
        self.out_tc_lbl.setToolTip("Out point")
        out_bracket_lbl = QLabel("]", self)
        out_bracket_lbl.setStyleSheet(bracket_style)
        tc_bar.addWidget(self.out_tc_lbl)
        tc_bar.addWidget(out_bracket_lbl)
        layout.addLayout(tc_bar)

        # 3. Interactive Scrub Bar
        self.scrub_bar = TimecodeScrubBar(self)
        self.scrub_bar.seek_requested.connect(self._on_scrub_seek)
        self.scrub_bar.in_point_changed.connect(self._on_in_changed_direct)
        self.scrub_bar.out_point_changed.connect(self._on_out_changed_direct)
        self.scrub_bar.drag_started.connect(self._on_trim_drag_started)
        self.scrub_bar.drag_finished.connect(self._on_trim_drag_finished)
        layout.addWidget(self.scrub_bar)

        # 4. Transport & Trimming Row (Premiere-style, icon-only)
        #    LEFT  = Mark In / Mark Out
        #    CENTER = [ < Play > ] transport cluster
        #    RIGHT = Prev / Next card | Undo / Reset
        is_dark_ui = pal["is_dark"]
        icon_col = QColor("#e4e4e7") if is_dark_ui else QColor("#27272a")
        btn_size = QSize(34, 32)

        def _tool_btn(icon, tooltip: str, slot, primary: bool = False):
            btn = PrimaryToolButton(icon, self) if primary else TransparentToolButton(icon, self)
            btn.setFixedSize(btn_size)
            btn.setIconSize(QSize(16, 16))
            btn.setToolTip(tooltip)
            btn.setFocusPolicy(Qt.NoFocus)  # never steal keyboard focus from window/transport
            btn.clicked.connect(slot)
            return btn

        def _divider():
            line = QFrame(self)
            line.setFixedSize(1, 18)
            line.setStyleSheet("background-color: rgba(255, 255, 255, 0.12);")
            return line

        # Center: [ In  <  Play  >  Out ] cluster (Premiere-style)
        self.mark_in_btn = _tool_btn(make_bracket_icon("in", icon_col), "Mark In point (I)", self.mark_in)
        self.mark_out_btn = _tool_btn(make_bracket_icon("out", icon_col), "Mark Out point (O)", self.mark_out)
        self.step_back_btn = _tool_btn(FluentIcon.LEFT_ARROW, "Step 1 frame backward (Left Arrow)", self._step_backward)
        self.play_btn = _tool_btn(FluentIcon.PLAY_SOLID, "Play / Pause (Space)", self._toggle_play, primary=True)
        self.play_btn.setFixedSize(QSize(46, 32))
        self.step_fwd_btn = _tool_btn(FluentIcon.RIGHT_ARROW, "Step 1 frame forward (Right Arrow)", self._step_forward)
        transport_group = QHBoxLayout()
        transport_group.setSpacing(4)
        transport_group.addWidget(self.mark_in_btn)
        transport_group.addWidget(self.step_back_btn)
        transport_group.addWidget(self.play_btn)
        transport_group.addWidget(self.step_fwd_btn)
        transport_group.addWidget(self.mark_out_btn)

        # Right: card navigation | undo / reset
        self.prev_btn = _tool_btn(FluentIcon.UP, "Previous card (Up Arrow / P)", self._navigate_prev)
        self.next_btn = _tool_btn(FluentIcon.DOWN, "Next card (Down Arrow / N)", self._navigate_next)
        self.undo_btn = _tool_btn(FluentIcon.HISTORY, "Undo last trim adjustment (Ctrl+Z)", self.undo_trim)
        self.reset_btn = _tool_btn(FluentIcon.SYNC, "Reset to script writer's original timecode (R)", self.reset_to_raw)
        right_group = QHBoxLayout()
        right_group.setSpacing(2)
        right_group.addWidget(self.prev_btn)
        right_group.addWidget(self.next_btn)
        self._edit_divider = _divider()
        right_group.addSpacing(6)
        right_group.addWidget(self._edit_divider)
        right_group.addSpacing(6)
        right_group.addWidget(self.undo_btn)
        right_group.addWidget(self.reset_btn)

        # 3-column grid keeps the transport cluster truly centered
        ctrl_row = QGridLayout()
        ctrl_row.setContentsMargins(0, 2, 0, 0)
        ctrl_row.setColumnStretch(0, 1)
        ctrl_row.setColumnStretch(1, 0)
        ctrl_row.setColumnStretch(2, 1)
        ctrl_row.addLayout(transport_group, 0, 1, Qt.AlignCenter)
        ctrl_row.addLayout(right_group, 0, 2, Qt.AlignRight | Qt.AlignVCenter)

        layout.addLayout(ctrl_row)

        # Global Spacebar shortcut for rock-solid Play/Pause toggle
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.setContext(Qt.WindowShortcut)
        self._space_shortcut.activated.connect(self._toggle_play)

    def _set_play_icon(self, playing: bool):
        """Swaps the transport button between Play and Pause Fluent icons."""
        self._play_icon_is_pause = playing
        self.play_btn.setIcon(FluentIcon.PAUSE_BOLD if playing else FluentIcon.PLAY_SOLID)

    # --- Loop (Player) / Edit (Trimmer) states ---
    @property
    def mode(self) -> str:
        return self._mode

    def is_edit_mode(self) -> bool:
        return self._mode == self.MODE_EDIT

    def toggle_mode(self):
        self.set_mode(self.MODE_LOOP if self.is_edit_mode() else self.MODE_EDIT)

    def set_mode(self, mode: str, resume: bool = True):
        """Switches between looping playback and precision trimming.

        Leaving Edit = Apply: persists overrides, emits edit_applied if In/Out changed,
        then resumes looping over the (possibly new) In→Out range.
        """
        mode = self.MODE_EDIT if mode == self.MODE_EDIT else self.MODE_LOOP
        prev = self._mode
        self._mode = mode
        is_edit = mode == self.MODE_EDIT

        for w in (self.mark_in_btn, self.mark_out_btn, self.undo_btn, self.reset_btn, self._edit_divider):
            w.setVisible(is_edit)
        self.scrub_bar.trim_enabled = is_edit
        self.scrub_bar.update()
        self.mode_btn.setText("Done" if is_edit else "Edit")
        self.mode_btn.setToolTip("Apply trim and return to loop (Ctrl+S / E / Esc)" if is_edit else "Edit this cut's In/Out (Ctrl+E / E)")
        if is_edit:
            self.mode_btn.setStyleSheet("""
                PushButton {
                    background-color: rgba(56, 189, 248, 0.20);
                    border: 1px solid #38bdf8;
                    color: #38bdf8;
                    font-size: 12px;
                    font-weight: 700;
                    border-radius: 5px;
                    padding: 0px 12px;
                }
                PushButton:hover {
                    background-color: rgba(56, 189, 248, 0.30);
                    border: 1px solid #7dd3fc;
                    color: #7dd3fc;
                }
            """)
        else:
            self.mode_btn.setStyleSheet("""
                PushButton {
                    background-color: rgba(255, 255, 255, 0.07);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    color: #e4e4e7;
                    font-size: 12px;
                    font-weight: 600;
                    border-radius: 5px;
                    padding: 0px 12px;
                }
                PushButton:hover {
                    background-color: rgba(255, 255, 255, 0.13);
                    border: 1px solid rgba(255, 255, 255, 0.22);
                    color: #ffffff;
                }
            """)
        self.setWindowTitle("Clip Editor — Trimming" if is_edit else "Clip Player — Looping")

        if prev == mode:
            return
        if is_edit:
            self._take_edit_snapshot()
            if self.player:
                self.player.set_ab_loop(None, None)
        else:
            self._apply_edit_session()
            if resume:
                self.start_loop_playback()

    def _take_edit_snapshot(self):
        if self.current_item:
            self._edit_snapshot = (self.current_item.id, self._in_seconds, self._out_seconds)
        else:
            self._edit_snapshot = None

    def _apply_edit_session(self):
        """Flushes trims and emits edit_applied once if the snapshotted card's In/Out changed."""
        snap = self._edit_snapshot
        self._edit_snapshot = None
        self.flush_trim_overrides()
        if not snap or not self.current_item or snap[0] != self.current_item.id:
            return
        _, s_in, s_out = snap
        if abs(s_in - self._in_seconds) > 1e-4 or abs(s_out - self._out_seconds) > 1e-4:
            self.edit_applied.emit(self.current_item.id)

    def _navigate_prev(self):
        if self.is_edit_mode():
            self._apply_edit_session()
        self.request_prev_card.emit()

    def _navigate_next(self):
        if self.is_edit_mode():
            self._apply_edit_session()
        self.request_next_card.emit()

    def start_loop_playback(self):
        """Loops In→Out via mpv's native A-B loop (seamless, no polling) and starts playback."""
        if self._mode != self.MODE_LOOP or not self.current_item:
            return
        if not self.current_file or not os.path.isfile(self.current_file):
            return
        self._ensure_player()
        if not self.player:
            return
        self.player.set_ab_loop(self._in_seconds, self._out_seconds)
        pos = self.scrub_bar.playhead_pos
        if not (self._in_seconds <= pos < self._out_seconds):
            self._on_scrub_seek(self._in_seconds)
        if not self._is_playing:
            self._toggle_play()

    def toggle_play(self):
        self._toggle_play()

    def _ensure_player(self):
        if self.player is None:
            self._init_player()

    def _init_player(self):
        if not is_mpv_available():
            self.vp_status_lbl.setText("libmpv Video Engine is not installed.\nOpen Settings -> Core Engines to install.")
            return

        wid = int(self.video_viewport.winId())
        self.player = MpvPlayer(wid=wid, parent=self)
        self.player.time_pos_changed.connect(self._on_player_time_pos)
        self.player.duration_changed.connect(self._on_player_duration)
        self.player.playback_state_changed.connect(self._on_playback_state)
        self.vp_status_lbl.hide()

    def load_item(self, item: ScriptItem, footage_path: Optional[str] = None, doc_signature: str = ""):
        """Loads a checklist card into the Source Monitor."""
        # Switching cards mid-edit applies the previous card's session (so it pulses too)
        if self.is_edit_mode() and self.current_item is not None:
            self._apply_edit_session()

        self.current_item = item
        self.current_file = footage_path
        self.doc_signature = doc_signature

        # 1. Probe footage with ffprobe / media_probe if available
        self.probed_media = None
        if footage_path and os.path.isfile(footage_path):
            try:
                from ...core.media_probe import probe_media_file
                self.probed_media = probe_media_file(footage_path, use_cache=True)
            except Exception as e:
                logger.warning("Could not probe media '%s': %s", footage_path, e)

        # 2. Update FPS
        if self.probed_media and self.probed_media.frame_rate > 0:
            self._nominal_fps = self.probed_media.frame_rate
        else:
            self._nominal_fps = 23.976

        # 3. Check for existing delta override in SQLite
        in_sec = item.start_seconds or 0.0
        out_sec = item.end_seconds or (in_sec + 5.0)
        if self.doc_signature:
            overrides = db.get_overrides(self.doc_signature)
            if item.id in overrides:
                ov = overrides[item.id]
                s_tc, s_sec, e_tc, e_sec, _, _, _, _ = process_time_span(f"{ov.get('start_tc', '')} - {ov.get('end_tc', '')}")
                in_sec = s_sec
                out_sec = e_sec

        self._in_seconds = in_sec
        self._out_seconds = out_sec

        # 4. Update HUD labels
        clip_name = os.path.basename(footage_path) if footage_path else "Unlinked Footage"
        if self.probed_media:
            res_str = f" | {self.probed_media.resolution}" if self.probed_media.resolution else ""
            fps_str = f" @ {self._nominal_fps:.2f} FPS"
            self.clip_name_lbl.setText(f"{clip_name}{res_str}{fps_str}")
            if self.probed_media.is_vfr:
                self.vfr_badge.show()
            else:
                self.vfr_badge.hide()
        else:
            self.clip_name_lbl.setText(clip_name)
            self.vfr_badge.hide()

        self._update_in_out_displays()

        # 5. Load or update player
        self._is_playing = False
        if hasattr(self, "_ui_update_timer") and self._ui_update_timer.isActive():
            self._ui_update_timer.stop()
        self._set_play_icon(False)
        self._latest_player_pos = in_sec
        self._smooth_playhead_pos = in_sec

        # Trigger background audio waveform extraction for full-lane rendering
        if hasattr(self, "_waveform_worker") and self._waveform_worker is not None:
            self._waveform_worker.cancel()
            self._waveform_worker = None

        has_audio = True
        if self.probed_media and not getattr(self.probed_media, "has_audio", True):
            has_audio = False

        if footage_path and os.path.isfile(footage_path) and has_audio:
            from ...core.waveform_engine import (
                load_cached_waveform,
                AudioWaveformWorker,
                waveform_precache_manager,
            )
            cached_peaks = load_cached_waveform(footage_path, target_fps=100)
            if cached_peaks is not None:
                # Instant zero-latency display: pre-cached peaks loaded on frame 1
                if len(cached_peaks) > 0:
                    self.scrub_bar.set_waveform_data(cached_peaks, 100)
                else:
                    self.scrub_bar.set_waveform_data(None)
            else:
                # Prioritize this file in the background queue and launch immediate worker
                waveform_precache_manager.prioritize_file(footage_path)
                self._waveform_worker = AudioWaveformWorker(footage_path, target_fps=100, parent=self)
                self._waveform_worker.waveform_ready.connect(self._on_waveform_ready)
                self._waveform_worker.start(QThread.LowestPriority)
        else:
            self.scrub_bar.set_waveform_data(None)

        if footage_path and os.path.isfile(footage_path):
            self._ensure_player()
            if self.player:
                self.vp_status_lbl.hide()
                self.player.load_clip(footage_path, start_seconds=in_sec)
        elif not footage_path:
            if self.player:
                self.player.pause()
            self.vp_status_lbl.setText("No local footage file linked to this timestamp card.")
            self.vp_status_lbl.show()

        # 6. Scrubber range
        clip_dur = (
            self.probed_media.duration_seconds
            if (self.probed_media and self.probed_media.duration_seconds > 0)
            else max(out_sec + 10.0, 60.0)
        )
        self.scrub_bar.set_range(
            duration=clip_dur,
            in_sec=in_sec,
            out_sec=out_sec,
            fps=self._nominal_fps
        )
        self.scrub_bar.frame_cut(in_sec, out_sec)
        self._update_smpte_display(in_sec)
        self._undo_stack.clear()

        # 7. State-specific follow-up: loop immediately, or start a fresh edit snapshot
        if self.is_edit_mode():
            self._take_edit_snapshot()
        else:
            self.start_loop_playback()

    def refresh_probed_metadata(self, updated_path: Optional[str] = None):
        """
        Re-probes the active footage file (or all files if updated_path is None) with use_cache=False
        and immediately updates the VFR badge and FPS HUD without losing the user's current playhead/trim state.
        """
        if not self.current_file or not os.path.isfile(self.current_file):
            return
        if updated_path:
            if os.path.normcase(os.path.abspath(updated_path)) != os.path.normcase(os.path.abspath(self.current_file)):
                return

        try:
            from ...core.media_probe import probe_media_file
            self.probed_media = probe_media_file(self.current_file, use_cache=False)
        except Exception as e:
            logger.warning("Failed to refresh probed media for '%s': %s", self.current_file, e)
            return

        if not self.probed_media:
            return

        if self.probed_media.frame_rate > 0:
            self._nominal_fps = self.probed_media.frame_rate

        clip_name = os.path.basename(self.current_file)
        res_str = f" | {self.probed_media.resolution}" if self.probed_media.resolution else ""
        fps_str = f" @ {self._nominal_fps:.2f} FPS"
        self.clip_name_lbl.setText(f"{clip_name}{res_str}{fps_str}")

        if self.probed_media.is_vfr:
            self.vfr_badge.show()
        else:
            self.vfr_badge.hide()

        self.scrub_bar._fps = self._nominal_fps
        self.scrub_bar.update()
        self._update_in_out_displays()
        self._update_smpte_display(self._smooth_playhead_pos)
        self._redo_stack.clear()

    def mark_in(self):
        if not self.current_item:
            return
        cur_pos = self.player.get_time_pos() if self.player else self.scrub_bar.playhead_pos
        self._push_undo()
        self._in_seconds = cur_pos
        if self._out_seconds <= self._in_seconds:
            self._out_seconds = self._in_seconds + (1.0 / self._nominal_fps)
        self._commit_trim()

    def mark_out(self):
        if not self.current_item:
            return
        cur_pos = self.player.get_time_pos() if self.player else self.scrub_bar.playhead_pos
        self._push_undo()
        self._out_seconds = max(self._in_seconds + (1.0 / self._nominal_fps), cur_pos)
        self._commit_trim()

    def _commit_trim(self):
        if not self.current_item:
            return
        new_in_tc = format_smpte(self._in_seconds, self._nominal_fps)
        new_out_tc = format_smpte(self._out_seconds, self._nominal_fps)
        dur = max(0.0, self._out_seconds - self._in_seconds)

        # 1. Update in-memory item (Zero lag 60+ FPS)
        self.current_item.start_seconds = self._in_seconds
        self.current_item.end_seconds = self._out_seconds
        self.current_item.start_timecode = new_in_tc
        self.current_item.end_timecode = new_out_tc
        self.current_item.duration_seconds = dur

        # 2. Update HUD and scrubber
        self._update_in_out_displays()
        self.scrub_bar.in_pos = self._in_seconds
        self.scrub_bar.out_pos = self._out_seconds
        self.scrub_bar.update()

        # 3. Notify main UI to update checklist card badge in memory
        self.timecode_changed.emit(self.current_item.id, new_in_tc, new_out_tc)

        # 4. Defer persistence: Record in pending overrides for batch flush on window exit/close
        if not hasattr(self, "_pending_overrides"):
            self._pending_overrides = {}
        self._pending_overrides[self.current_item.id] = (new_in_tc, new_out_tc)

    def flush_trim_overrides(self):
        """Batch-persists any pending trim modifications to SQLite when exiting trimmer."""
        if hasattr(self, "_trim_save_timer") and self._trim_save_timer.isActive():
            self._trim_save_timer.stop()

        if not hasattr(self, "_pending_overrides") or not self.doc_signature:
            return

        # Ensure current item's latest position is recorded if active
        if self.current_item:
            cur_in_tc = format_smpte(self._in_seconds, self._nominal_fps)
            cur_out_tc = format_smpte(self._out_seconds, self._nominal_fps)
            self._pending_overrides[self.current_item.id] = (cur_in_tc, cur_out_tc)

        if not self._pending_overrides:
            return

        for item_id, (in_tc, out_tc) in list(self._pending_overrides.items()):
            dur = 0.0
            try:
                from ...core.timecode import timecode_to_seconds
                dur = max(0.0, timecode_to_seconds(out_tc, self._nominal_fps) - timecode_to_seconds(in_tc, self._nominal_fps))
            except Exception:
                pass
            db.save_override(self.doc_signature, item_id, in_tc, out_tc)
            logger.info("Persisted trim card '%s' -> %s - %s (%.2fs)", item_id, in_tc, out_tc, dur)
        self._pending_overrides.clear()

    def _persist_trim_overrides(self):
        self.flush_trim_overrides()

    def _push_undo(self):
        self._undo_stack.append((self._in_seconds, self._out_seconds))
        self._redo_stack.clear()

    def undo_trim(self):
        if not self._undo_stack:
            return
        self._redo_stack.append((self._in_seconds, self._out_seconds))
        prev_in, prev_out = self._undo_stack.pop()
        self._in_seconds = prev_in
        self._out_seconds = prev_out
        self._commit_trim()

    def redo_trim(self):
        if not self._redo_stack:
            return
        self._undo_stack.append((self._in_seconds, self._out_seconds))
        next_in, next_out = self._redo_stack.pop()
        self._in_seconds = next_in
        self._out_seconds = next_out
        self._commit_trim()

    def reset_to_raw(self):
        """Reverts cut to the raw timecode parsed from docx."""
        if not self.current_item:
            return
        s_tc, s_sec, e_tc, e_sec, _, _, _, _ = process_time_span(self.current_item.raw_timestamp)
        self._push_undo()
        self._in_seconds = s_sec
        self._out_seconds = e_sec if e_sec > s_sec else (s_sec + 5.0)
        self._commit_trim()
        self.scrub_bar.frame_cut(self._in_seconds, self._out_seconds)

    def _step_forward(self):
        if self.player:
            self.player.step_frame_forward()
        else:
            new_pos = self.scrub_bar.playhead_pos + (1.0 / self._nominal_fps)
            self._latest_player_pos = new_pos
            self._smooth_playhead_pos = new_pos
            self.scrub_bar.set_playhead(new_pos)
            self._update_smpte_display(new_pos)

    def _step_backward(self):
        if self.player:
            self.player.step_frame_backward()
        else:
            new_pos = max(0.0, self.scrub_bar.playhead_pos - (1.0 / self._nominal_fps))
            self._latest_player_pos = new_pos
            self._smooth_playhead_pos = new_pos
            self.scrub_bar.set_playhead(new_pos)
            self._update_smpte_display(new_pos)

    def _toggle_play(self):
        if not self.player and self.current_file and os.path.isfile(self.current_file):
            self._ensure_player()
            if self.player:
                self.player.load_clip(self.current_file, start_seconds=self.scrub_bar.playhead_pos)

        if self._is_playing:
            # Video was playing -> pause video (only bring into view if playhead was out of frame)
            if self.player:
                self.player.pause()
            self._is_playing = False
            self._set_play_icon(False)
            if self._ui_update_timer.isActive():
                self._ui_update_timer.stop()
            if self.player:
                cur = self.player.get_time_pos()
                self._latest_player_pos = cur
                self._smooth_playhead_pos = cur
                self.scrub_bar.set_playhead(cur)
                self._update_smpte_display(cur)
            self.scrub_bar.ensure_playhead_in_view()
        else:
            # Video was paused -> start playback
            if self.player:
                self.player.play()
            self._is_playing = True
            self._set_play_icon(True)
            self._smooth_playhead_pos = self.scrub_bar.playhead_pos
            self._latest_player_pos = self.scrub_bar.playhead_pos
            if not self._ui_update_timer.isActive():
                self._ui_update_timer.start(16)

    def _flush_scrub_seek(self):
        if self._pending_scrub_sec is not None and self.player:
            self.player.seek(self._pending_scrub_sec, exact=True)
            self._pending_scrub_sec = None

    def _on_ui_tick(self):
        """Smooth 60 Hz display tick: updates playhead without backward jitter or paint flooding."""
        if self._is_scrubbing:
            return
        target = self._latest_player_pos
        if target < self._smooth_playhead_pos:
            # Allow jump only if significant delta (> 0.5s), else suppress micro-jitter backwards
            if (self._smooth_playhead_pos - target) > 0.5:
                self._smooth_playhead_pos = target
        else:
            self._smooth_playhead_pos = target

        self.scrub_bar.set_playhead(self._smooth_playhead_pos)
        self._update_smpte_display(self._smooth_playhead_pos)
        self.scrub_bar.check_playback_follow()

    def _on_player_time_pos(self, sec: float):
        self._latest_player_pos = sec
        # During user scrubbing, user drag has 100% exclusive authority over the playhead and HUD.
        # Background callbacks from libmpv seeking are completely suppressed to eliminate flicker.
        if self._is_scrubbing:
            return
        if not self._is_playing:
            self._smooth_playhead_pos = sec
            self.scrub_bar.set_playhead(sec)
            self._update_smpte_display(sec)

    def _on_player_duration(self, sec: float):
        self.scrub_bar.duration = max(sec, 1.0)
        self.scrub_bar.update()

    def _on_playback_state(self, is_playing: bool):
        was_playing = self._is_playing
        self._is_playing = is_playing
        self._set_play_icon(is_playing)

        if is_playing:
            if not self._ui_update_timer.isActive():
                self._smooth_playhead_pos = self._latest_player_pos
                self._ui_update_timer.start(16)
        else:
            if self._ui_update_timer.isActive():
                self._ui_update_timer.stop()
            if self.player:
                cur = self.player.get_time_pos()
                self._latest_player_pos = cur
                self._smooth_playhead_pos = cur
                self.scrub_bar.set_playhead(cur)
                self._update_smpte_display(cur)
            if was_playing:
                self.scrub_bar.ensure_playhead_in_view()

    def _on_scrub_seek(self, sec: float):
        if not self.player and self.current_file and os.path.isfile(self.current_file):
            self._ensure_player()
            if self.player:
                self.player.load_clip(self.current_file, start_seconds=sec)

        # Immediate HUD and playhead synchronization (zero-latency frame update)
        self._latest_player_pos = sec
        self._smooth_playhead_pos = sec
        self.scrub_bar.set_playhead(sec)
        self._update_smpte_display(sec)

        if self._is_scrubbing:
            self._pending_scrub_sec = sec
            if hasattr(self, "_scrub_seek_timer") and not self._scrub_seek_timer.isActive():
                self._scrub_seek_timer.start(25)
        else:
            if self.player:
                self.player.seek(sec, exact=True)

    def _on_trim_drag_started(self, mode: str):
        if mode == "playhead":
            self._is_scrubbing = True
            if self._is_playing:
                self._toggle_play()
        elif mode in ("in_bracket", "out_bracket"):
            self._push_undo()

    def _on_trim_drag_finished(self, mode: str):
        if mode == "playhead":
            self._is_scrubbing = False
            if hasattr(self, "_scrub_seek_timer") and self._scrub_seek_timer.isActive():
                self._scrub_seek_timer.stop()
            self._pending_scrub_sec = None
            if self.player:
                self.player.seek(self.scrub_bar.playhead_pos, exact=True)
        elif mode in ("in_bracket", "out_bracket"):
            # Defer disk writes until trimmer exit/close for butter-smooth responsiveness.
            pass

    def _on_in_changed_direct(self, sec: float):
        self._in_seconds = sec
        self._commit_trim()

    def _on_out_changed_direct(self, sec: float):
        self._out_seconds = sec
        self._commit_trim()

    def _update_smpte_display(self, sec: float):
        tc = format_smpte(sec, self._nominal_fps)
        self.smpte_hud_lbl.setText(tc)

    def _update_in_out_displays(self):
        in_tc = format_smpte(self._in_seconds, self._nominal_fps)
        out_tc = format_smpte(self._out_seconds, self._nominal_fps)
        self.in_tc_lbl.setText(in_tc)
        self.out_tc_lbl.setText(out_tc)

    def _on_anchor_dropdown_changed(self, text: str):
        if "Playhead" in text:
            self.scrub_bar.set_zoom_anchor_mode("playhead")
        else:
            self.scrub_bar.set_zoom_anchor_mode("cut")

    def _on_waveform_ready(self, file_path: str, peaks: Optional[np.ndarray], sample_rate: int):
        if self.current_file and os.path.normpath(file_path) == os.path.normpath(self.current_file):
            if peaks is not None and len(peaks) > 0:
                self.scrub_bar.set_waveform_data(peaks, sample_rate)
            else:
                self.scrub_bar.set_waveform_data(None)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        is_edit = self.is_edit_mode()

        if (key == Qt.Key_E and not (modifiers & Qt.AltModifier)):
            self.toggle_mode()
            event.accept()
        elif key == Qt.Key_S and (modifiers & Qt.ControlModifier):
            if is_edit:
                self.set_mode(self.MODE_LOOP)
            else:
                self.flush_trim_overrides()
                ws = getattr(self, "_workspace_parent", None) or self.parent()
                if ws and hasattr(ws, "flush_save"):
                    ws.flush_save()
            event.accept()
        elif key == Qt.Key_I and is_edit:
            self.mark_in()
            event.accept()
        elif key == Qt.Key_O and is_edit:
            self.mark_out()
            event.accept()
        elif key == Qt.Key_Space:
            self._toggle_play()
            event.accept()
        elif key == Qt.Key_Left:
            if modifiers & Qt.ShiftModifier:
                cur = self.player.get_time_pos() if self.player else self.scrub_bar.playhead_pos
                self._on_scrub_seek(max(0.0, cur - 1.0))
            else:
                self._step_backward()
            event.accept()
        elif key == Qt.Key_Right:
            if modifiers & Qt.ShiftModifier:
                cur = self.player.get_time_pos() if self.player else self.scrub_bar.playhead_pos
                self._on_scrub_seek(min(self.scrub_bar.duration, cur + 1.0))
            else:
                self._step_forward()
            event.accept()
        elif key == Qt.Key_J:
            # Reverse shuttle
            self._shuttle_speed = max(0.5, self._shuttle_speed * 1.5)
            if self.player:
                self.player.set_speed(self._shuttle_speed)
                self.player.play()
            event.accept()
        elif key == Qt.Key_K:
            # Stop shuttle / pause (only re-center if out of frame)
            self._shuttle_speed = 1.0
            if self.player:
                self.player.set_speed(1.0)
            if self._is_playing:
                self._toggle_play()
            else:
                if self.player:
                    self.player.pause()
                self.scrub_bar.ensure_playhead_in_view()
            event.accept()
        elif key == Qt.Key_L:
            # Forward shuttle
            self._shuttle_speed = min(8.0, self._shuttle_speed * 1.5)
            if self.player:
                self.player.set_speed(self._shuttle_speed)
                self.player.play()
            event.accept()
        elif key == Qt.Key_Q:
            # Jump to IN
            self._on_scrub_seek(self._in_seconds)
            event.accept()
        elif key == Qt.Key_W:
            # Jump to OUT
            self._on_scrub_seek(self._out_seconds)
            event.accept()
        elif key == Qt.Key_Z and (modifiers & Qt.ControlModifier) and is_edit:
            self.undo_trim()
            event.accept()
        elif key == Qt.Key_Y and (modifiers & Qt.ControlModifier) and is_edit:
            self.redo_trim()
            event.accept()
        elif key == Qt.Key_R and is_edit:
            self.reset_to_raw()
            event.accept()
        elif key == Qt.Key_P or key == Qt.Key_Up:
            self._navigate_prev()
            event.accept()
        elif key == Qt.Key_N or key == Qt.Key_Down:
            self._navigate_next()
            event.accept()
        elif key == Qt.Key_Escape:
            # Esc in Edit = Done (apply, back to loop); Esc in Loop = close the player
            if is_edit:
                self.set_mode(self.MODE_LOOP)
            else:
                self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        self.flush_trim_overrides()
        super().hideEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "_waveform_worker") and self._waveform_worker is not None:
            self._waveform_worker.cancel()
            self._waveform_worker = None
        if hasattr(self, "_ui_update_timer") and self._ui_update_timer.isActive():
            self._ui_update_timer.stop()
        if hasattr(self, "_trim_save_timer") and self._trim_save_timer.isActive():
            self._trim_save_timer.stop()
            self._persist_trim_overrides()
        if getattr(self, "_is_closing", False):
            event.accept()
            return
        # Closing mid-edit counts as Done: apply (pulse) and reopen next time in Loop state
        if self.is_edit_mode():
            self.set_mode(self.MODE_LOOP, resume=False)
        if self.player:
            self.player.pause()
        self.hide()
        self.closed.emit()
        event.ignore()

    def destroy_window(self):
        """Clean shutdown called when the main application terminates."""
        if hasattr(self, "_waveform_worker") and self._waveform_worker is not None:
            self._waveform_worker.cancel()
            self._waveform_worker = None
        if hasattr(self, "_ui_update_timer") and self._ui_update_timer.isActive():
            self._ui_update_timer.stop()
        if hasattr(self, "_trim_save_timer") and self._trim_save_timer.isActive():
            self._trim_save_timer.stop()
            self._persist_trim_overrides()
        self._is_closing = True
        if self.player:
            try:
                self.player.destroy()
            except Exception:
                pass
            self.player = None
        self.close()
        self.deleteLater()
