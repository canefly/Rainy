import webbrowser
from typing import Dict, Optional
from PySide6.QtCore import Qt, Signal, QTimer, QElapsedTimer
from PySide6.QtGui import QCursor, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QGridLayout, QStackedLayout, QLabel, QLineEdit, QWidget, QApplication
)
from qfluentwidgets import (
    CheckBox, FluentIcon, IconWidget, ToolButton, CardWidget, isDarkTheme
)

from ...core.models import ScriptItem
from ...core.database import db
from ..theme import ThemeManager
from .widgets import get_display_frame_interval_ms

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
    """Click to copy timecode with instant feedback, double-click to edit inline."""
    copied = Signal(str)
    edited = Signal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("Click to copy • Double-click to edit")
        self._restore_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            text_to_copy = self.text().strip()
            QApplication.clipboard().setText(text_to_copy)
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
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, self._restore_style)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start_inline_edit()

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

    def _start_inline_edit(self):
        parent_layout = self.parent().layout()
        idx = parent_layout.indexOf(self)

        self.editor = QLineEdit(self.text(), self.parent())
        self.editor.setStyleSheet("""
            background-color: #0b0f1a;
            color: #38bdf8;
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            font-size: 12px;
            font-weight: 600;
            border: 1px solid #38bdf8;
            border-radius: 4px;
            padding: 0px 4px;
        """)
        self.editor.setFixedWidth(85)
        self.editor.returnPressed.connect(self._finish_edit)
        self.editor.editingFinished.connect(self._finish_edit)

        self.hide()
        parent_layout.insertWidget(idx, self.editor)
        self.editor.setFocus()
        self.editor.selectAll()

    def _finish_edit(self):
        if hasattr(self, 'editor') and self.editor:
            new_val = self.editor.text().strip()
            if new_val:
                self.setText(new_val)
                self.edited.emit(new_val)
            self.editor.deleteLater()
            self.editor = None
            self.show()


class TimelineCueCard(CardWidget):
    """Native Fluent card with automatic hover styling and 1px subtle stroke."""
    item_checked_changed = Signal(str, bool)
    item_timecode_changed = Signal(str, str)
    sync_script_requested = Signal(int, str)
    copy_feedback_requested = Signal(str)

    def __init__(self, item: ScriptItem, doc_signature: str = "", link_colors: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.item = item
        self.doc_signature = doc_signature
        self.link_colors = link_colors or {}
        self.is_highlighted = False
        self._highlight_alpha = 0.0
        self._hl_duration_ms = 1100.0
        self._hl_clock = QElapsedTimer()
        self._hl_timer: Optional[QTimer] = None
        self._surface_cache: Optional[QPixmap] = None
        self._surface_cache_key = None
        self.is_compact = False
        self.setFixedHeight(44)
        self.setObjectName("TimelineCard")
        self.setBorderRadius(8)

        self._update_card_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        # 1. Custom Checkbox + Index Number (e.g. [x] 1.)
        self.cb = CheckBox("", self)
        self.cb.setStyleSheet("CheckBox { min-width: 18px; max-width: 18px; margin: 0px; spacing: 0px; padding: 0px; }")
        self.cb.setFixedSize(18, 18)
        self.cb.setChecked(item.is_checked)
        self.cb.stateChanged.connect(self._on_check_changed)
        layout.addWidget(self.cb)

        self.index_lbl = QLabel(f"{item.row_index}.", self)
        self.index_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.index_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        self.index_lbl.setFixedWidth(26)
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

        # 3. Source Domain (e.g. DRIVE.GOOGLE.COM or WWW.YOUTUBE.COM)
        first_link = item.links[0] if item.links else ""
        if first_link and first_link != "#":
            domain = first_link.split('/')[2].upper() if '/' in first_link else "LINK"
            self.domain_lbl = QLabel(domain, self)
            self.domain_lbl.setStyleSheet("color: #475569; font-size: 9px; font-weight: 700; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; letter-spacing: 0.5px;")
            self.domain_lbl.setCursor(QCursor(Qt.PointingHandCursor))
            self.domain_lbl.setToolTip(first_link)
            self.domain_lbl.mousePressEvent = lambda e: webbrowser.open(first_link)
            layout.addWidget(self.domain_lbl)
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
            self.start_btn.copied.connect(self._on_timecode_copied)
            self.start_btn.edited.connect(self._on_start_tc_edited)
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
            self.end_btn.copied.connect(self._on_timecode_copied)
            self.end_btn.edited.connect(self._on_end_tc_edited)
            wide_layout.addWidget(self.end_btn)
            self.tc_stack.addWidget(self.wide_widget)

            # Page 1: Compact 2-row layout
            self.compact_widget = QWidget(self.tc_container)
            compact_layout = QGridLayout(self.compact_widget)
            compact_layout.setContentsMargins(8, 3, 8, 3)
            compact_layout.setHorizontalSpacing(4)
            compact_layout.setVerticalSpacing(1)

            lbl_col = "#94a3b8" if pal["is_dark"] else "#64748b"
            self.in_lbl = QLabel("In", self.compact_widget)
            self.in_lbl.setFixedWidth(24)
            self.in_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.in_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
            compact_layout.addWidget(self.in_lbl, 0, 0)

            self.start_btn_c = InlineTimecodeLabel(item.start_timecode, self.compact_widget)
            self.start_btn_c.setFixedWidth(84)
            self.start_btn_c.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.start_btn_c.copied.connect(self._on_timecode_copied)
            self.start_btn_c.edited.connect(self._on_start_tc_edited)
            compact_layout.addWidget(self.start_btn_c, 0, 1)

            self.out_lbl = QLabel("Out", self.compact_widget)
            self.out_lbl.setFixedWidth(24)
            self.out_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.out_lbl.setStyleSheet(f"background: transparent; border: none; color: {lbl_col}; font-size: 11px; font-weight: 600; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
            compact_layout.addWidget(self.out_lbl, 1, 0)

            self.end_btn_c = InlineTimecodeLabel(item.end_timecode, self.compact_widget)
            self.end_btn_c.setFixedWidth(84)
            self.end_btn_c.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.end_btn_c.copied.connect(self._on_timecode_copied)
            self.end_btn_c.edited.connect(self._on_end_tc_edited)
            compact_layout.addWidget(self.end_btn_c, 1, 1)

            self.tc_stack.addWidget(self.compact_widget)

            self.tc_container.setFixedSize(248, 32)
            self.tc_stack.setCurrentIndex(0)
        else:
            self.wide_widget = QWidget(self.tc_container)
            wide_layout = QHBoxLayout(self.wide_widget)
            wide_layout.setContentsMargins(6, 4, 6, 4)
            wide_layout.setSpacing(5)

            self.start_btn = InlineTimecodeLabel(item.start_timecode, self.wide_widget)
            self.start_btn.setFixedWidth(86)
            self.start_btn.setAlignment(Qt.AlignCenter)
            self.start_btn.copied.connect(self._on_timecode_copied)
            self.start_btn.edited.connect(self._on_start_tc_edited)
            wide_layout.addWidget(self.start_btn)

            still_icon = IconWidget(FluentIcon.INFO, self.wide_widget)
            still_icon.setFixedSize(12, 12)
            still_icon.setToolTip("Still image / single timestamp")
            wide_layout.addWidget(still_icon)

            self.tc_stack.addWidget(self.wide_widget)
            self.tc_container.setFixedSize(115, 32)
            self.tc_stack.setCurrentIndex(0)

        layout.addWidget(self.tc_container)

        # 5. External Link Launch Button
        if first_link and first_link != "#":
            self.ext_btn = ToolButton(FluentIcon.SHARE, self)
            self.ext_btn.setFixedSize(24, 24)
            self.ext_btn.setToolTip("Open in browser")
            self.ext_btn.clicked.connect(lambda: webbrowser.open(first_link))
            layout.addWidget(self.ext_btn)
        else:
            spacer = QWidget(self)
            spacer.setFixedWidth(24)
            layout.addWidget(spacer)

    def set_compact_mode(self, compact: bool):
        if self.is_compact == compact:
            return
        self.is_compact = compact
        self.index_lbl.setVisible(not compact)

        if not self.item.is_single and self.item.end_timecode:
            if compact:
                self.setFixedHeight(50)
                self.tc_container.setFixedSize(130, 40)
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

    _set_compact_mode = set_compact_mode

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'domain_lbl') and self.domain_lbl:
            self.domain_lbl.setVisible(self.width() >= 550)
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
        interval = get_display_frame_interval_ms(self)
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
            self._highlight_alpha = 1.0
        else:
            fade_t = (progress - 0.2) / 0.8
            inv = 1.0 - fade_t
            self._highlight_alpha = inv * inv * inv
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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            copy_text = self.item.start_timecode or self.item.meta
            QApplication.clipboard().setText(copy_text)
            self.copy_feedback_requested.emit(copy_text)
            sync_target = self.item.raw_timestamp if self.item.raw_timestamp else (self.item.start_timecode or self.item.meta)
            self.sync_script_requested.emit(self.item.sync_id, sync_target)
        super().mousePressEvent(event)

    def _update_card_style(self):
        pal = ThemeManager.get_palette()
        self.update()
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

    def set_checked(self, checked: bool):
        self.cb.setChecked(checked)

    def _on_check_changed(self, state: int):
        is_checked = (state == Qt.Checked.value)
        self.item.is_checked = is_checked
        self._update_card_style()
        self.item_checked_changed.emit(self.item.id, is_checked)

    def _on_meta_clicked(self, event):
        if event.button() == Qt.LeftButton:
            QApplication.clipboard().setText(self.item.meta)
            self.copy_feedback_requested.emit(self.item.meta)
            sync_target = self.item.raw_timestamp if self.item.raw_timestamp else self.item.meta
            self.sync_script_requested.emit(self.item.sync_id, sync_target)
            event.accept()

    def _on_timecode_copied(self, tc: str):
        self.copy_feedback_requested.emit(tc)
        sync_target = self.item.raw_timestamp if self.item.raw_timestamp else tc
        self.sync_script_requested.emit(self.item.sync_id, sync_target)

    def _on_start_tc_edited(self, new_tc: str):
        self.item.start_timecode = new_tc
        if hasattr(self, 'start_btn'):
            self.start_btn.setText(new_tc)
        if hasattr(self, 'start_btn_c'):
            self.start_btn_c.setText(new_tc)
        if self.doc_signature:
            db.save_override(self.doc_signature, self.item.id, new_tc, self.item.end_timecode, self.item.text)
        self.item_timecode_changed.emit(self.item.id, new_tc)

    def _on_end_tc_edited(self, new_tc: str):
        self.item.end_timecode = new_tc
        if hasattr(self, 'end_btn'):
            self.end_btn.setText(new_tc)
        if hasattr(self, 'end_btn_c'):
            self.end_btn_c.setText(new_tc)
        if self.doc_signature:
            db.save_override(self.doc_signature, self.item.id, self.item.start_timecode, new_tc, self.item.text)
        self.item_timecode_changed.emit(self.item.id, new_tc)

TimelineCard = TimelineCueCard
