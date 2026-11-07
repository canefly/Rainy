import os
import webbrowser
from typing import Dict, Optional, Tuple
from PySide6.QtCore import Qt, Signal, QTimer, QElapsedTimer, QEvent, QRect
from PySide6.QtGui import QCursor, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QGridLayout, QStackedLayout, QLabel, QWidget, QApplication
)
from qfluentwidgets import (
    CheckBox, FluentIcon, IconWidget, ToolButton, CardWidget, isDarkTheme
)

from ...core.models import ScriptItem
from ...core.database import db
from ...core.unfurl_engine import unfurl_engine, LinkMetadata
from ..theme import ThemeManager
from .widgets import get_display_frame_interval_ms, safe_copy_to_clipboard, CompactCheckBox
from .link_embed_card import link_embed_controller

# Subtle pastel palette for meta tags matching original React linkColors
META_PALETTE = [
    ("rgba(56, 189, 248, 0.18)", "#38bdf8"),   # Sky
    ("rgba(168, 85, 247, 0.18)", "#c084fc"),  # Purple
    ("rgba(244, 114, 182, 0.18)", "#f472b6"), # Pink
    ("rgba(52, 211, 153, 0.18)", "#34d399"),  # Emerald
    ("rgba(251, 191, 36, 0.18)", "#fbbf24"),  # Amber
    ("rgba(96, 165, 250, 0.18)", "#60a5fa"),  # Blue
]

def get_meta_palette(meta_text: str):
    if not meta_text:
        return META_PALETTE[0]
    hash_val = sum(ord(c) for c in meta_text)
    return META_PALETTE[hash_val % len(META_PALETTE)]


class InlineTimecodeLabel(QLabel):
    """Click to copy timecode with instant visual feedback."""
    copied = Signal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("Click to copy timecode")
        self._restore_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            text_to_copy = self.text().strip()
            self.copied.emit(text_to_copy)
            # Subtle flash feedback
            self.setStyleSheet("""
                QLabel {
                    background-color: rgba(34, 197, 94, 0.25);
                    color: #4ade80;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 12px;
                    font-weight: 700;
                    padding: 0px 4px;
                    border-radius: 4px;
                    border: none;
                }
            """)
            QTimer.singleShot(250, self._restore_style)
            event.accept()
            return
        super().mousePressEvent(event)

    def _restore_style(self):
        pal = ThemeManager.get_palette()
        text_col = pal["text"]
        accent = pal["accent"]
        self.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                border: none;
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: 600;
                padding: 0px 4px;
                border-radius: 4px;
            }}
            QLabel:hover {{
                background-color: rgba(56, 189, 248, 0.15);
                color: {accent};
                border: none;
                border-radius: 4px;
            }}
        """)


class TimelineCueCard(QFrame):
    """Native Fluent card with automatic hover styling and 1px subtle stroke."""
    item_checked_changed = Signal(str, bool)
    item_timecode_changed = Signal(str, str)
    sync_script_requested = Signal(int, str)
    copy_feedback_requested = Signal(str)

    def __init__(self, item: ScriptItem, doc_signature: str = "", link_colors: Dict[str, str] = None, linked_footage: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        self.item = item
        self.doc_signature = doc_signature
        self.link_colors = link_colors or {}
        self.linked_footage = linked_footage or {}
        self.is_highlighted = False
        self._highlight_alpha = 0.0
        self._hl_duration_ms = 1100.0
        self._hl_clock = QElapsedTimer()
        self._hl_timer: Optional[QTimer] = None
        self._surface_cache: Optional[QPixmap] = None
        self._surface_cache_key = None
        self.is_compact = False
        self._cached_surface: Optional[QPixmap] = None
        self._cached_surface_key = None
        self.isHover = False
        self._is_reflow_held = False
        self.borderRadius = 8
        self.setFixedHeight(44)
        self.setObjectName("TimelineCard")

        self._update_card_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(5)

        # 1. Custom Checkbox + Index Number (e.g. [x] 1.)
        self.cb = CompactCheckBox(self)
        self.cb.setChecked(item.is_checked)
        self.cb.stateChanged.connect(self._on_check_changed)
        layout.addWidget(self.cb)

        self.index_lbl = QLabel(f"{item.row_index}.", self)
        self.index_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.index_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        layout.addWidget(self.index_lbl)

        # 2. Tag Pill (e.g. CLIP / S10E20)
        # Use link_colors mapped by normalized link (single source of truth for uniqueness)
        first_link = item.links[0] if item.links else ""
        if first_link and first_link in self.link_colors:
            bg_col = self.link_colors[first_link]
            text_col = "#0b0f1a"
        else:
            bg_col, text_col = get_meta_palette(item.meta)

        display_meta = item.meta if len(item.meta) <= 8 else "CLIP"
        self.meta_pill = QLabel(display_meta, self)
        self.meta_pill.setFixedHeight(22)
        self.meta_pill.setAlignment(Qt.AlignCenter)
        self.meta_pill.setCursor(QCursor(Qt.PointingHandCursor))
        self.meta_pill.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_col};
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                padding: 1px 8px;
                border-radius: 6px;
                border: none;
            }}
            QLabel:hover {{
                border: 1px solid rgba(255, 255, 255, 0.4);
            }}
        """)
        self.meta_pill.setToolTip(f"Sync script: {item.meta}")
        self.meta_pill.mousePressEvent = self._on_meta_clicked
        layout.addWidget(self.meta_pill)

        # 3. Source Domain / Inline Unfurled Title Chip (e.g. DRIVE.GOOGLE.COM or Final_Cut_v3.mp4)
        first_link = item.links[0] if item.links else ""
        self.first_link = first_link if (first_link and first_link != "#") else ""
        self._link_meta: Optional[LinkMetadata] = None
        if self.first_link:
            domain = self.first_link.split('/')[2].upper() if '/' in self.first_link else "LINK"
            self.domain_lbl = QLabel(domain, self)
            self.domain_lbl.setStyleSheet("color: #475569; font-size: 9px; font-weight: 700; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; letter-spacing: 0.5px;")
            self.domain_lbl.setCursor(QCursor(Qt.PointingHandCursor))
            self.domain_lbl.mousePressEvent = lambda e: webbrowser.open(self.first_link)
            self.domain_lbl.installEventFilter(self)
            layout.addWidget(self.domain_lbl)
            cached_init = unfurl_engine.get_cached(self.first_link)
            if cached_init and not cached_init.loading and not cached_init.is_fallback and cached_init.title:
                self.update_link_metadata(cached_init)
        layout.addStretch(1)

        # 4. Clean Single Timestamps Pill: [00:20:48:00 | DUR 2s | 00:20:50:00]
        # In compact mode (narrow split view), squashes into 2 stacked rows without DUR:
        # [In  00:23:44:00]
        # [Out 00:23:47:00]
        # Uses QStackedLayout for instantaneous O(1) layout switching without UI stutter.
        self.tc_container = QFrame(self)
        self.tc_container.setObjectName("TimecodeContainer")
        pal = ThemeManager.get_palette()
        tc_bg = "rgba(255, 255, 255, 0.03)" if pal["is_dark"] else "rgba(0, 0, 0, 0.04)"
        tc_border = "rgba(255, 255, 255, 0.08)" if pal["is_dark"] else "rgba(0, 0, 0, 0.08)"
        self.tc_container.setStyleSheet(f"""
            QFrame#TimecodeContainer {{
                background-color: {tc_bg};
                border: 1px solid {tc_border};
                border-radius: 6px;
            }}
        """)
        self.tc_stack = QStackedLayout(self.tc_container)
        self.tc_stack.setContentsMargins(0, 0, 0, 0)

        if not item.is_single and item.end_timecode:
            # Page 0: Wide horizontal row
            self.wide_widget = QWidget(self.tc_container)
            wide_layout = QHBoxLayout(self.wide_widget)
            wide_layout.setContentsMargins(6, 4, 6, 4)
            wide_layout.setSpacing(5)

            self.start_btn = InlineTimecodeLabel(item.start_timecode, self.wide_widget)
            self.start_btn.setFixedWidth(86)
            self.start_btn.setAlignment(Qt.AlignCenter)
            self.start_btn.copied.connect(self._on_start_timecode_copied)
            wide_layout.addWidget(self.start_btn)

            dur_col = "#ef4444" if item.duration_seconds == 0 else "#38bdf8"
            self.dur_lbl = QLabel(f"DUR {int(item.duration_seconds)}s", self.wide_widget)
            self.dur_lbl.setFixedWidth(54)
            self.dur_lbl.setAlignment(Qt.AlignCenter)
            self.dur_lbl.setStyleSheet(f"""
                QLabel {{
                    background: transparent;
                    border: none;
                    color: {dur_col};
                    font-size: 12px;
                    font-weight: 700;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
            """)
            wide_layout.addWidget(self.dur_lbl)

            self.end_btn = InlineTimecodeLabel(item.end_timecode, self.wide_widget)
            self.end_btn.setFixedWidth(86)
            self.end_btn.setAlignment(Qt.AlignCenter)
            self.end_btn.copied.connect(self._on_end_timecode_copied)
            wide_layout.addWidget(self.end_btn)
            self.tc_stack.addWidget(self.wide_widget)

            # Page 1: Compact 2-row layout
            self.compact_widget = QWidget(self.tc_container)
            compact_layout = QGridLayout(self.compact_widget)
            compact_layout.setContentsMargins(4, 1, 4, 1)
            compact_layout.setHorizontalSpacing(3)
            compact_layout.setVerticalSpacing(0)

            lbl_col = "#94a3b8" if pal["is_dark"] else "#64748b"
            self.in_lbl = QLabel("In", self.compact_widget)
            self.in_lbl.setFixedWidth(20)
            self.in_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.in_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
            compact_layout.addWidget(self.in_lbl, 0, 0)

            self.start_btn_c = InlineTimecodeLabel(item.start_timecode, self.compact_widget)
            self.start_btn_c.setFixedWidth(82)
            self.start_btn_c.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.start_btn_c.copied.connect(self._on_start_timecode_copied)
            compact_layout.addWidget(self.start_btn_c, 0, 1)

            self.out_lbl = QLabel("Out", self.compact_widget)
            self.out_lbl.setFixedWidth(20)
            self.out_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.out_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
            compact_layout.addWidget(self.out_lbl, 1, 0)

            self.end_btn_c = InlineTimecodeLabel(item.end_timecode, self.compact_widget)
            self.end_btn_c.setFixedWidth(82)
            self.end_btn_c.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.end_btn_c.copied.connect(self._on_end_timecode_copied)
            compact_layout.addWidget(self.end_btn_c, 1, 1)

            self.tc_stack.addWidget(self.compact_widget)

            self.tc_container.setFixedSize(248, 32)
            self.tc_stack.setCurrentIndex(0)
        else:
            self.wide_widget = QWidget(self.tc_container)
            wide_layout = QHBoxLayout(self.wide_widget)
            wide_layout.setContentsMargins(6, 4, 6, 4)
            wide_layout.setSpacing(5)
            wide_layout.setAlignment(Qt.AlignCenter)

            self.start_btn = InlineTimecodeLabel(item.start_timecode, self.wide_widget)
            self.start_btn.setFixedWidth(86)
            self.start_btn.setAlignment(Qt.AlignCenter)
            self.start_btn.copied.connect(self._on_start_timecode_copied)
            wide_layout.addWidget(self.start_btn)

            still_icon = IconWidget(FluentIcon.INFO, self.wide_widget)
            still_icon.setFixedSize(12, 12)
            still_icon.setToolTip("Still image / single timestamp")
            wide_layout.addWidget(still_icon)

            self.tc_stack.addWidget(self.wide_widget)
            self.tc_container.setFixedSize(115, 32)
            self.tc_stack.setCurrentIndex(0)

        layout.addWidget(self.tc_container, 0, Qt.AlignVCenter)

        # 5. External Link Launch Button
        if self.first_link:
            self.ext_btn = ToolButton(FluentIcon.SHARE, self)
            self.ext_btn.setFixedSize(24, 24)
            self.ext_btn.clicked.connect(lambda: webbrowser.open(self.first_link))
            self.ext_btn.installEventFilter(self)
            layout.addWidget(self.ext_btn, 0, Qt.AlignVCenter)
            # Hydrate immediately if link metadata is already in RAM or SQLite cache
            cached_meta = unfurl_engine.get_cached(self.first_link)
            if cached_meta is not None:
                self.update_link_metadata(cached_meta)
        else:
            spacer = QWidget(self)
            spacer.setFixedWidth(24)
            layout.addWidget(spacer, 0, Qt.AlignVCenter)

        self.update_displayed_timecodes()

    def get_linked_footage_path(self) -> str:
        from ...core.parser import normalize_link
        for lk in getattr(self.item, "links", []):
            norm = normalize_link(lk)
            p = self.linked_footage.get(norm) or self.linked_footage.get(lk)
            if p and os.path.isfile(p):
                return p
        return ""

    def has_linked_footage(self) -> bool:
        return bool(self.get_linked_footage_path())

    def is_auto_conform_active(self) -> bool:
        return db.get_setting("auto_conform_smpte", "true").lower() == "true"

    def set_linked_footage(self, linked_footage: Dict[str, str]):
        self.linked_footage = linked_footage or {}
        self.update_displayed_timecodes()

    def update_displayed_timecodes(self):
        """Updates the button labels and tooltips based on whether auto-conform is active and footage is linked."""
        auto_on = self.is_auto_conform_active()
        footage_linked = self.has_linked_footage()

        if auto_on and footage_linked:
            smpte_start, _ = self.get_conformed_timecode(self.item.start_timecode)
            display_start = smpte_start
        else:
            display_start = self.item.start_timecode

        if hasattr(self, 'start_btn') and self.start_btn:
            self.start_btn.setText(display_start)
        if hasattr(self, 'start_btn_c') and self.start_btn_c:
            self.start_btn_c.setText(display_start)

        if not self.item.is_single and self.item.end_timecode:
            if auto_on and footage_linked:
                smpte_end, _ = self.get_conformed_timecode(self.item.end_timecode)
                display_end = smpte_end
            else:
                display_end = self.item.end_timecode

            if hasattr(self, 'end_btn') and self.end_btn:
                self.end_btn.setText(display_end)
            if hasattr(self, 'end_btn_c') and self.end_btn_c:
                self.end_btn_c.setText(display_end)

        self.update_timecode_tooltips()
        self.invalidate_cache()
        self.update()

    def update_timecode_tooltips(self):
        if hasattr(self, 'item') and self.item:
            auto_on = self.is_auto_conform_active()
            footage_linked = self.has_linked_footage()
            smpte_start, _ = self.get_conformed_timecode(self.item.start_timecode)

            if footage_linked and auto_on:
                tip_start = f"SMPTE Conformed: {smpte_start}\nRaw Script: {self.item.start_timecode}\nClick to copy for Premiere Pro"
            elif footage_linked:
                tip_start = f"Raw Script PTS: {self.item.start_timecode} (Footage bound, Conforming OFF)\nClick to copy"
            else:
                tip_start = f"Raw Script PTS: {self.item.start_timecode} (No footage linked)\nClick to copy"

            if hasattr(self, 'start_btn') and self.start_btn:
                self.start_btn.setToolTip(tip_start)
            if hasattr(self, 'start_btn_c') and self.start_btn_c:
                self.start_btn_c.setToolTip(tip_start)

            if not self.item.is_single and self.item.end_timecode:
                smpte_end, _ = self.get_conformed_timecode(self.item.end_timecode)
                if footage_linked and auto_on:
                    tip_end = f"SMPTE Conformed: {smpte_end}\nRaw Script: {self.item.end_timecode}\nClick to copy for Premiere Pro"
                elif footage_linked:
                    tip_end = f"Raw Script PTS: {self.item.end_timecode} (Footage bound, Conforming OFF)\nClick to copy"
                else:
                    tip_end = f"Raw Script PTS: {self.item.end_timecode} (No footage linked)\nClick to copy"

                if hasattr(self, 'end_btn') and self.end_btn:
                    self.end_btn.setToolTip(tip_end)
                if hasattr(self, 'end_btn_c') and self.end_btn_c:
                    self.end_btn_c.setToolTip(tip_end)

    def get_conformed_timecode(self, raw_ts: str) -> Tuple[str, str]:
        """
        Conforms the given script timestamp to SMPTE timecode (HH:MM:SS:FF)
        ONLY IF local footage is bound to this item's source link AND auto_conform_smpte is enabled.
        Otherwise, returns the raw timestamp untouched.
        """
        if not raw_ts:
            return "", "Copied"

        auto_conform = self.is_auto_conform_active()
        if not auto_conform:
            return raw_ts, f"Copied: {raw_ts}"

        linked_path = self.get_linked_footage_path()
        if not linked_path:
            # No footage bound: do NOT alter things, return raw PTS timestamp
            return raw_ts, f"Copied: {raw_ts}"

        # Local footage is linked: conform with probed properties
        from ...core.timecode_engine import conform_timestamp_for_clip
        from ...core.media_probe import probe_media_file

        probed = probe_media_file(linked_path, use_cache=True)
        if probed and probed.frame_rate > 0:
            smpte, meta = conform_timestamp_for_clip(
                raw_ts,
                probed_fps=probed.frame_rate,
                container_start_time=probed.start_time,
                embedded_timecode=probed.start_timecode,
                is_vfr=probed.is_vfr
            )
            feedback = f"Copied: {smpte} ({meta['nominal_fps']} FPS"
            if abs(meta['start_time_offset']) > 0.05:
                feedback += f", +{meta['start_time_offset']:.2f}s offset"
            if meta['is_vfr']:
                feedback += ", VFR alert"
            feedback += ")"
            return smpte, feedback

        return raw_ts, f"Copied: {raw_ts}"

    def set_compact_mode(self, compact: bool):
        if self.is_compact == compact:
            return
        self.is_compact = compact
        self.index_lbl.setVisible(True)
        if hasattr(self, 'domain_lbl') and self.domain_lbl:
            self.domain_lbl.setVisible(not compact and self.width() >= 560)
        if hasattr(self, 'wide_widget') and self.wide_widget:
            self.wide_widget.setVisible(not compact)
        if hasattr(self, 'compact_widget') and self.compact_widget:
            self.compact_widget.setVisible(compact)

        if not self.item.is_single and self.item.end_timecode:
            if compact:
                self.setFixedHeight(44)
                self.tc_container.setFixedSize(110, 32)
                self.tc_stack.setCurrentIndex(1)
                if hasattr(self, 'dur_lbl'):
                    self.dur_lbl.hide()
                if hasattr(self, 'in_lbl'):
                    self.in_lbl.show()
                if hasattr(self, 'out_lbl'):
                    self.out_lbl.show()
            else:
                self.setFixedHeight(44)
                self.tc_container.setFixedSize(248, 32)
                self.tc_stack.setCurrentIndex(0)
                if hasattr(self, 'dur_lbl'):
                    self.dur_lbl.show()
                if hasattr(self, 'in_lbl'):
                    self.in_lbl.hide()
                if hasattr(self, 'out_lbl'):
                    self.out_lbl.hide()
        else:
            self.setFixedHeight(44)
            self.tc_container.setFixedSize(115, 32)
            self.tc_stack.setCurrentIndex(0)

        self.updateGeometry()
        self.invalidate_cache()

    _set_compact_mode = set_compact_mode

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'domain_lbl') and self.domain_lbl:
            self.domain_lbl.setVisible(not self.is_compact and self.width() >= 560)
        # Standalone check only when unparented (in unit tests) to avoid high-frequency drag overhead
        if self.parent() is None:
            self.set_compact_mode(self.width() < 420)

    def set_highlighted(self, val: bool):
        if self._hl_timer and self._hl_timer.isActive():
            self._hl_timer.stop()
        self.is_highlighted = val
        self._highlight_alpha = 1.0 if val else 0.0
        self.update()

    def pulse_highlight(self, duration_ms: int = 1100):
        """High-refresh-rate eased glow fade when scrolled to via timestamp click."""
        self.is_highlighted = True
        self._highlight_alpha = 1.0
        self._hl_duration_ms = float(max(150, duration_ms))
        self._hl_clock.restart()
        if self._hl_timer is None:
            self._hl_timer = QTimer(self)
            self._hl_timer.setTimerType(Qt.PreciseTimer)
            self._hl_timer.timeout.connect(self._step_highlight_fade)
        interval = max(16, get_display_frame_interval_ms(self))
        self._hl_timer.start(interval)
        self.update()

    def _step_highlight_fade(self):
        elapsed = self._hl_clock.nsecsElapsed() / 1_000_000.0
        if elapsed >= self._hl_duration_ms:
            self.is_highlighted = False
            self._highlight_alpha = 0.0
            if self._hl_timer:
                self._hl_timer.stop()
            self.update()
            return
        # Hold full brightness briefly (first 20%), then smooth cubic ease-out fade
        progress = elapsed / self._hl_duration_ms
        if progress <= 0.2:
            new_alpha = 1.0
        else:
            fade_t = (progress - 0.2) / 0.8
            inv = 1.0 - fade_t
            new_alpha = inv * inv * inv

        # Only repaint when alpha visibly changes (prevents redundant repaints during initial hold)
        if abs(new_alpha - self._highlight_alpha) > 0.01:
            self._highlight_alpha = new_alpha
            self.update()

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        is_dark = isDarkTheme()
        r = self.borderRadius
        dpr = self.devicePixelRatioF() or 1.0

        # 1. Blit cached base card surface (eliminates per-frame path rasterization during fast scrolling)
        cache_key = (w, h, int(round(dpr * 100)), is_dark, bool(self.item.is_checked), bool(self.isHover), r)
        if self._surface_cache is None or self._surface_cache_key != cache_key:
            pix = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.transparent)
            cp = QPainter(pix)
            cp.setRenderHints(QPainter.Antialiasing)
            if self.item.is_checked:
                bg_alpha = 32 if self.isHover else 20
                border_alpha = 180 if self.isHover else 85
                cp.setBrush(QColor(16, 185, 129, bg_alpha))
                cp.setPen(QColor(16, 185, 129, border_alpha))
            else:
                if is_dark:
                    card_bg = QColor(48, 48, 52) if self.isHover else QColor(36, 36, 38)
                    pen_color = QColor(255, 255, 255, 55 if self.isHover else 22)
                else:
                    card_bg = QColor(241, 245, 249) if self.isHover else QColor(248, 249, 250)
                    pen_color = QColor(0, 0, 0, 45 if self.isHover else 18)
                cp.setBrush(card_bg)
                cp.setPen(pen_color)
            cp.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)
            cp.end()
            self._surface_cache = pix
            self._surface_cache_key = cache_key

        painter.drawPixmap(0, 0, self._surface_cache)

        # 2. Composite smooth animated highlight glow on top when active
        if self.is_highlighted and self._highlight_alpha > 0.005:
            pal = ThemeManager.get_palette()
            rgb_parts = [int(x.strip()) for x in pal.get("accent_rgb", "251, 113, 133").split(",")]
            alpha_fill = max(1, int(round(65 * self._highlight_alpha)))
            alpha_pen = max(1, int(round(230 * self._highlight_alpha)))
            painter.setBrush(QColor(rgb_parts[0], rgb_parts[1], rgb_parts[2], alpha_fill))
            painter.setPen(QColor(rgb_parts[0], rgb_parts[1], rgb_parts[2], alpha_pen))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)

    def enterEvent(self, event):
        self.isHover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.isHover = False
        if self.first_link:
            link_embed_controller.schedule_hide()
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.isHover = False
        if self.first_link:
            link_embed_controller.schedule_hide()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if self.first_link and watched in (getattr(self, "domain_lbl", None), getattr(self, "ext_btn", None)):
            if event.type() == QEvent.Enter:
                top_left = watched.mapToGlobal(watched.rect().topLeft())
                global_rect = QRect(top_left, watched.size())
                link_embed_controller.schedule_show(self.first_link, global_rect)
            elif event.type() == QEvent.Leave:
                link_embed_controller.schedule_hide()
        return super().eventFilter(watched, event)

    def update_link_metadata(self, meta: LinkMetadata):
        """Updates the inline domain/title chip when background link unfurl finishes."""
        self._link_meta = meta
        if not hasattr(self, "domain_lbl") or not self.domain_lbl:
            return
        if meta and not meta.loading and not meta.is_fallback and meta.title:
            pal = ThemeManager.get_palette()
            is_dark = pal.get("is_dark", True)
            if meta.is_restricted:
                self.domain_lbl.setText("RESTRICTED")
                self.domain_lbl.setStyleSheet("""
                    QLabel {
                        background-color: rgba(245, 158, 11, 0.14);
                        color: #fbbf24;
                        border: 1px solid rgba(245, 158, 11, 0.30);
                        border-radius: 4px;
                        padding: 1px 6px;
                        font-size: 10px;
                        font-weight: 700;
                        font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    }
                """)
            else:
                raw_t = meta.title.strip()
                short_t = (raw_t[:24] + "…") if len(raw_t) > 25 else raw_t
                self.domain_lbl.setText(short_t)
                chip_bg = "rgba(255, 255, 255, 0.05)" if is_dark else "rgba(0, 0, 0, 0.05)"
                chip_brd = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(0, 0, 0, 0.10)"
                chip_col = "#cbd5e1" if is_dark else "#334155"
                self.domain_lbl.setStyleSheet(f"""
                    QLabel {{
                        background-color: {chip_bg};
                        color: {chip_col};
                        border: 1px solid {chip_brd};
                        border-radius: 4px;
                        padding: 1px 6px;
                        font-size: 10px;
                        font-weight: 600;
                        font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    }}
                """)
            self.invalidate_cache()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_start_timecode_copied()
        super().mousePressEvent(event)

    def _update_card_style(self):
        pal = ThemeManager.get_palette()
        self.update()
        self.invalidate_cache()
        if hasattr(self, 'index_lbl'):
            self.index_lbl.setStyleSheet(f"color: {pal['muted']}; font-size: 12px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        if hasattr(self, 'dur_lbl') and hasattr(self, 'item') and not self.item.is_single and self.item.end_timecode:
            dur_col = "#ef4444" if self.item.duration_seconds == 0 else "#38bdf8"
            self.dur_lbl.setStyleSheet(f"""
                QLabel {{
                    background: transparent;
                    border: none;
                    color: {dur_col};
                    font-size: 12px;
                    font-weight: 700;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
            """)
        lbl_col = "#94a3b8" if pal["is_dark"] else "#64748b"
        if hasattr(self, 'in_lbl'):
            self.in_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        if hasattr(self, 'out_lbl'):
            self.out_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        if hasattr(self, 'tc_container'):
            tc_bg = "rgba(255, 255, 255, 0.03)" if pal["is_dark"] else "rgba(0, 0, 0, 0.04)"
            tc_border = "rgba(255, 255, 255, 0.08)" if pal["is_dark"] else "rgba(0, 0, 0, 0.08)"
            self.tc_container.setStyleSheet(f"""
                QFrame#TimecodeContainer {{
                    background-color: {tc_bg};
                    border: 1px solid {tc_border};
                    border-radius: 6px;
                }}
            """)
        if hasattr(self, 'start_btn') and hasattr(self.start_btn, '_restore_style'):
            self.start_btn._restore_style()
        if hasattr(self, 'start_btn_c') and hasattr(self.start_btn_c, '_restore_style'):
            self.start_btn_c._restore_style()
        if hasattr(self, 'end_btn') and hasattr(self.end_btn, '_restore_style'):
            self.end_btn._restore_style()
        if hasattr(self, 'end_btn_c') and hasattr(self.end_btn_c, '_restore_style'):
            self.end_btn_c._restore_style()
        if getattr(self, '_link_meta', None) is not None:
            self.update_link_metadata(self._link_meta)

    def set_checked(self, checked: bool):
        self.cb.setChecked(checked)
        self.invalidate_cache()

    def _on_check_changed(self, state: int):
        is_checked = (state == Qt.Checked.value)
        self.item.is_checked = is_checked
        self._update_card_style()
        self.invalidate_cache()
        self.item_checked_changed.emit(self.item.id, is_checked)

    def _on_start_timecode_copied(self, _=None):
        raw_val = self.item.start_timecode or self.item.raw_timestamp
        conformed, feedback = self.get_conformed_timecode(raw_val)
        safe_copy_to_clipboard(conformed)
        self.copy_feedback_requested.emit(feedback)
        sync_target = self.item.raw_timestamp if self.item.raw_timestamp else raw_val
        self.sync_script_requested.emit(self.item.sync_id, sync_target)

    def _on_end_timecode_copied(self, _=None):
        raw_val = self.item.end_timecode
        conformed, feedback = self.get_conformed_timecode(raw_val)
        safe_copy_to_clipboard(conformed)
        self.copy_feedback_requested.emit(feedback)
        sync_target = self.item.raw_timestamp if self.item.raw_timestamp else raw_val
        self.sync_script_requested.emit(self.item.sync_id, sync_target)

    def _on_meta_clicked(self, event):
        if event.button() == Qt.LeftButton:
            self._on_start_timecode_copied()
            event.accept()

    def _on_timecode_copied(self, tc: str):
        self._on_start_timecode_copied()

    def isHidden(self) -> bool:
        if getattr(self, "_filter_hidden", False):
            return True
        if getattr(self, "_virtual_visible", False):
            return False
        return super().isHidden()

    def set_virtual_visible(self, val: bool):
        self._virtual_visible = val
        self._filter_hidden = not val

    def set_hold_reflow(self, hold: bool):
        self._is_reflow_held = hold

    def get_cached_surface(self, w: int, h: int, dpr: float, hold_cache: bool = False) -> QPixmap:
        """Returns a high-DPI GPU/RAM rasterized texture surface of this card."""
        if (hold_cache or getattr(self, "_is_reflow_held", False)) and self._cached_surface is not None:
            return self._cached_surface

        cache_key = (w, h, int(round(dpr * 100)), bool(self.item.is_checked), bool(self.is_compact))
        if self._cached_surface is None or self._cached_surface_key != cache_key:
            if getattr(self, "_is_rendering_surface", False):
                return self._cached_surface or QPixmap()
            self._is_rendering_surface = True
            pix = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.transparent)
            try:
                if self.width() != w or self.height() != h:
                    self.resize(w, h)
                orig_hover = self.isHover
                self.isHover = False
                self.render(pix)
                self.isHover = orig_hover
            finally:
                self._is_rendering_surface = False
            self._cached_surface = pix
            self._cached_surface_key = cache_key
        return self._cached_surface

    def invalidate_cache(self):
        """Invalidates pre-baked texture surface so next paint pass re-renders."""
        self._cached_surface = None
        self._cached_surface_key = None

TimelineCard = TimelineCueCard
