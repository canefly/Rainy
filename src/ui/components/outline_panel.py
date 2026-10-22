from typing import List, Dict, Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QScrollArea, QWidget
)
from qfluentwidgets import ToolButton, FluentIcon, IconWidget
from ..theme import ThemeManager

class OutlinePanel(QFrame):
    """Document Outline / Tabs navigation panel (matching React DocumentTabs)."""
    closed = Signal()
    heading_clicked = Signal(int)  # paragraph index / anchor id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(240)
        self.setObjectName("OutlinePanel")
        self._headings: List[Dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        header.setSpacing(8)

        icon = IconWidget(FluentIcon.TILES, self)
        icon.setFixedSize(14, 14)
        header.addWidget(icon)

        self.title_lbl = QLabel("Outline & Tabs", self)
        header.addWidget(self.title_lbl)
        header.addStretch(1)

        self.close_btn = ToolButton(FluentIcon.CLOSE, self)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setToolTip("Collapse outline")
        self.close_btn.clicked.connect(self.closed.emit)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # Scroll Area for headings
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_widget = QWidget(self.scroll)
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        border_col = pal.get("border", "#27272a")
        self.setStyleSheet(f"""
            QFrame#OutlinePanel {{
                border-right: 1px solid {border_col};
                background: transparent;
            }}
        """)
        if hasattr(self, "title_lbl") and self.title_lbl:
            self.title_lbl.setStyleSheet(f"color: {text_col}; background: transparent; font-weight: 700; font-size: 12px; letter-spacing: 0.5px;")
        if self._headings:
            self.load_headings(self._headings)

    def load_headings(self, headings: List[Dict[str, Any]]):
        self._headings = headings
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#e2e8f0")
        muted_col = pal.get("muted", "#64748b")
        surface_col = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border_col = pal.get("border", "#27272a")
        rgb = pal.get("accent_rgb", "56, 189, 248")

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not headings:
            empty_lbl = QLabel("No chapters or headings detected.")
            empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")
            self.list_layout.insertWidget(0, empty_lbl)
            return

        for h in headings:
            card = QFrame(self.list_widget)
            card.setObjectName("OutlineCard")
            card.setCursor(QCursor(Qt.PointingHandCursor))
            card.setStyleSheet(f"""
                QFrame#OutlineCard {{
                    background-color: {surface_col};
                    border: 1px solid {border_col};
                    border-radius: 6px;
                    padding: 6px 8px;
                }}
                QFrame#OutlineCard:hover {{
                    background-color: {surface_hover};
                    border-color: rgba({rgb}, 0.4);
                }}
            """)
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(6, 4, 6, 4)

            lbl = QLabel(h["text"], card)
            lbl.setStyleSheet(f"color: {text_col}; background: transparent; border: none; font-size: 11px; font-weight: 600;")
            lbl.setWordWrap(True)
            c_layout.addWidget(lbl)

            card.mousePressEvent = lambda e, idx=h["id"]: self.heading_clicked.emit(idx)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
