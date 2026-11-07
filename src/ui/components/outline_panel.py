from typing import List, Dict, Any, Optional
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QCursor, QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QScrollArea, QWidget
)
from qfluentwidgets import ToolButton, FluentIcon, IconWidget
from ..theme import ThemeManager


class OutlineItemWidget(QWidget):
    """Clean, typography-driven outline item matching Google Docs & Fluent design."""
    clicked = Signal(int)

    def __init__(self, heading_id: int, text: str, level: int = 1, parent=None):
        super().__init__(parent)
        self.heading_id = heading_id
        self.raw_text = text.strip()
        self.level = level
        self.is_active = False
        self.is_hover = False
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip(self.raw_text)
        self.setFixedHeight(28)

        # Detect sub-level by level or leading '+'
        self.is_sub = (self.level >= 2) or self.raw_text.startswith("+")

    def set_active(self, active: bool):
        if self.is_active != active:
            self.is_active = active
            self.update()

    def enterEvent(self, e):
        self.is_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.is_hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.heading_id)
            e.accept()
            return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#f43f5e")
        rgb_parts = [int(x.strip()) for x in pal.get("accent_rgb", "244, 63, 94").split(",")]
        is_dark = ThemeManager.is_dark

        # 1. Background wash (Active tint or subtle Hover wash)
        if self.is_active:
            bg_col = QColor(rgb_parts[0], rgb_parts[1], rgb_parts[2], 28)
            painter.setBrush(bg_col)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(4, 2, w - 8, h - 4), 4, 4)
        elif self.is_hover:
            hover_alpha = 14 if is_dark else 10
            painter.setBrush(QColor(255, 255, 255, hover_alpha) if is_dark else QColor(0, 0, 0, hover_alpha))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(4, 2, w - 8, h - 4), 4, 4)

        # 2. Active Indicator Pill (3px wide on left edge)
        if self.is_active:
            painter.setBrush(QColor(accent))
            painter.setPen(Qt.NoPen)
            pill_h = 16.0
            pill_y = (h - pill_h) / 2.0
            painter.drawRoundedRect(QRectF(1, pill_y, 3, pill_h), 1.5, 1.5)

        # 3. Hierarchy Indentation
        indent_x = 10.0
        if self.is_sub:
            indent_x = 24.0
        if self.level >= 3:
            indent_x = 36.0

        # 4. Typography (Standard neutral Windows 11 / Google Docs colors decoupled from pink theme)
        font = QFont("Segoe UI Variable Text", 9)
        if is_dark:
            col_active = "#ffffff"
            col_root = "#f4f4f5"
            col_sub = "#a1a1aa"
            col_hover = "#ffffff"
        else:
            col_active = "#09090b"
            col_root = "#18181b"
            col_sub = "#71717a"
            col_hover = "#09090b"

        if self.is_active:
            font.setWeight(QFont.Bold)
            text_color = QColor(col_active)
        elif self.is_sub:
            font.setWeight(QFont.Normal)
            text_color = QColor(col_hover if self.is_hover else col_sub)
        else:
            font.setWeight(QFont.DemiBold)
            text_color = QColor(col_hover if self.is_hover else col_root)

        painter.setFont(font)
        painter.setPen(text_color)

        fm = painter.fontMetrics()
        avail_w = int(w - indent_x - 10)
        elided = fm.elidedText(self.raw_text, Qt.ElideRight, max(20, avail_w))
        text_rect = QRectF(indent_x, 0, avail_w, h)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)


class OutlineTreeContainer(QWidget):
    """Container widget that renders a subtle hairline vertical tree guide matching Google Docs."""
    def paintEvent(self, e):
        super().paintEvent(e)
        items = [w for w in self.findChildren(OutlineItemWidget) if w.isVisible()]
        if not items:
            return

        items.sort(key=lambda w: w.y())
        first_item = items[0]
        last_item = items[-1]

        y_start = float(first_item.y() + 4)
        y_end = float(last_item.y() + last_item.height() - 4)

        if y_end <= y_start:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        line_col = QColor(255, 255, 255, 45) if ThemeManager.is_dark else QColor(0, 0, 0, 36)
        painter.setPen(QPen(line_col, 1.0))
        painter.drawLine(QPointF(2.5, y_start), QPointF(2.5, y_end))


class OutlinePanel(QFrame):
    """Document Outline / Tabs navigation panel (matching Google Docs and Windows 11 Fluent Design)."""
    closed = Signal()
    heading_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(240)
        self.setObjectName("OutlinePanel")
        self._headings: List[Dict[str, Any]] = []
        self._items: Dict[int, OutlineItemWidget] = {}
        self._active_id: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 2, 2)
        header.setSpacing(6)

        self.icon = IconWidget(FluentIcon.DICTIONARY, self)
        self.icon.setFixedSize(14, 14)
        header.addWidget(self.icon)

        self.title_lbl = QLabel("Document Tabs", self)
        self.title_lbl.setStyleSheet(
            "font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
            "font-size: 12px; font-weight: 700;"
        )
        header.addWidget(self.title_lbl)
        header.addStretch(1)

        self.close_btn = ToolButton(FluentIcon.CLOSE, self)
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setToolTip("Collapse Document Tabs")
        self.close_btn.clicked.connect(self.closed.emit)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # Scroll Area with tree guide
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_widget = OutlineTreeContainer(self.scroll)
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 4, 2, 4)
        self.list_layout.setSpacing(1)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll)
        self.apply_theme()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = ThemeManager.get_palette()
        border_col = pal.get("border", "#27272a")
        bg_col = pal.get("surface", "#121212")
        painter.setPen(QColor(border_col))
        painter.setBrush(QColor(bg_col))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = "#ffffff" if ThemeManager.is_dark else "#18181b"
        muted_col = "#a1a1aa" if ThemeManager.is_dark else "#71717a"

        self.setStyleSheet("""
            QFrame#OutlinePanel {
                border: none;
                background: transparent;
            }
        """)
        self.title_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
            f"font-size: 12px; font-weight: 700; color: {text_col};"
        )
        for item in self._items.values():
            item.update()
        self.list_widget.update()
        self.update()

    def load_headings(self, headings: List[Dict[str, Any]]):
        self._headings = headings or []
        self._items.clear()
        self._active_id = None

        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self._headings:
            empty = QLabel("No headings in script", self.list_widget)
            empty.setStyleSheet("color: #71717a; font-size: 11px; font-style: italic; padding: 12px 8px;")
            self.list_layout.insertWidget(0, empty)
            return

        for idx, h in enumerate(self._headings):
            h_id = h.get("id", idx)
            h_text = h.get("text", "")
            h_level = h.get("level", 1)
            item = OutlineItemWidget(h_id, h_text, level=h_level, parent=self.list_widget)
            item.clicked.connect(self.heading_clicked.emit)
            self._items[h_id] = item
            self.list_layout.insertWidget(self.list_layout.count() - 1, item)

    def set_active_heading(self, heading_id: int):
        if self._active_id == heading_id:
            return
        self._active_id = heading_id
        target_item = None
        for hid, item in self._items.items():
            is_match = (hid == heading_id)
            item.set_active(is_match)
            if is_match:
                target_item = item

        if target_item:
            self.scroll.ensureWidgetVisible(target_item, 0, 30)
