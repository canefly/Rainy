from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget
)
from qfluentwidgets import TransparentToolButton, ToolButton, FluentIcon

class BadgedIconButton(QWidget):
    """A 40x40 square dock button with a vector icon and a 4px Fluent micro-chip badge counter."""
    clicked = Signal()

    def __init__(self, icon_enum: FluentIcon, tooltip: str = "", badge_bg=None, badge_fg=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self._custom_badge_bg = badge_bg
        self._custom_badge_fg = badge_fg
        self._count = 0

        # Base TransparentToolButton with native hover states
        self.btn = TransparentToolButton(icon_enum, self)
        self.btn.setFixedSize(40, 40)
        self.btn.move(0, 0)
        self.btn.setToolTip(tooltip)
        self.btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn.setStyleSheet("""
            TransparentToolButton {
                border-radius: 4px;
            }
        """)
        self.btn.clicked.connect(self.clicked.emit)

        # 4px Fluent Micro-chip Badge Overlay
        self.badge = QLabel("", self)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setCursor(QCursor(Qt.PointingHandCursor))
        self.badge.mousePressEvent = lambda e: self.clicked.emit() if e.button() == Qt.LeftButton else None
        self.badge.move(24, 0)
        self.badge.hide()
        self.apply_theme()

    def apply_theme(self):
        from ..theme import ThemeManager
        from PySide6.QtGui import QColor
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#fb7185")
        rgb = pal.get("accent_rgb", "251, 113, 133")

        bg = self._custom_badge_bg if self._custom_badge_bg else accent
        if self._custom_badge_fg:
            fg = self._custom_badge_fg
        else:
            c = QColor(bg)
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            fg = "#09090b" if lum > 175 else "#ffffff"

        self.badge.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 9px;
                font-weight: 800;
                padding: 1px 4px;
                border-radius: 4px;
                border: 1px solid rgba({rgb}, 0.5);
            }}
        """)
        if self._count > 0:
            self.set_badge_count(self._count)

    def set_badge_count(self, count: int):
        self._count = count
        if count > 0:
            self.badge.setText(str(count))
            self.badge.adjustSize()
            badge_x = max(0, self.width() - self.badge.width())
            self.badge.move(badge_x, 0)
            self.badge.show()
            self.badge.raise_()
        else:
            self.badge.hide()


class SidebarDock(QFrame):
    """The compact sidebar dock strictly displayed in the Workspace view."""
    home_clicked = Signal()
    outline_clicked = Signal()
    notes_clicked = Signal()
    links_clicked = Signal()
    vault_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(53)
        self.setObjectName("SidebarDock")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 14, 5, 14)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignHCenter)

        # 1. Top Section
        # Home button (Go Home / New Import)
        self.home_btn = BadgedIconButton(FluentIcon.HOME, "Go Home / New Import", parent=self)
        self.home_btn.clicked.connect(self.home_clicked.emit)
        layout.addWidget(self.home_btn)

        # Document Tabs
        self.outline_btn = BadgedIconButton(FluentIcon.DICTIONARY, "Document Tabs", parent=self)
        self.outline_btn.clicked.connect(self.outline_clicked.emit)
        layout.addWidget(self.outline_btn)

        # Editor Notes with theme accent badge
        self.notes_btn = BadgedIconButton(
            FluentIcon.EDIT, 
            "Editor Notes", 
            parent=self
        )
        self.notes_btn.clicked.connect(self.notes_clicked.emit)
        layout.addWidget(self.notes_btn)

        # Source Links with theme accent badge
        self.links_btn = BadgedIconButton(
            FluentIcon.LINK, 
            "Source Links", 
            parent=self
        )
        self.links_btn.clicked.connect(self.links_clicked.emit)
        layout.addWidget(self.links_btn)

        layout.addStretch(1)

        # 2. Bottom Section
        # Settings
        self.settings_btn = BadgedIconButton(FluentIcon.SETTING, "Settings", parent=self)
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.settings_btn)

    def set_notes_count(self, count: int):
        self.notes_btn.set_badge_count(count)

    def set_links_count(self, count: int):
        self.links_btn.set_badge_count(count)

    def apply_theme(self):
        for btn in (self.home_btn, self.outline_btn, self.notes_btn, self.links_btn, self.settings_btn):
            btn.apply_theme()
