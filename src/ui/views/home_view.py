import os
import random
import time
from datetime import datetime
from typing import List, Dict, Any
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QCursor, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QFileDialog
)
from qfluentwidgets import (
    PushButton, FluentIcon, IconWidget, CardWidget, SmoothScrollArea
)
from ...core.database import db

TYPOS_POOL = ['WMLCOME', 'WLCOME', 'WELCOMP', 'WLEMCOM', 'WELACOME', 'WELCOMRE', 'WELCO0ME', 'WLEICME', 'WLCOM\\', 'WLECOME']
MULTILINGUAL_HELLOS = [
    'Hello', 'Hola', 'Bonjour', 'こんにちは', 'Ciao', '안녕하세요', 
    'Hallo', 'Olá', 'Привет', '你好', 'مرحباً', 'नमस्ते'
]
SPLASH_TEXTS = [
    'Reaady to edit??',
    'Cheeer up!! you can do that a roll and b roll!!',
    'ISANG SAHOD LANG YAAAN!!!',
    'wooohooo kirill the goated',
    'Heya femboy would you like to try femboy theme?',
    'did you know this backend is handled by python?',
    'sigh.. i swear premiere pro crashes always..',
    'B ROLL IS SO BOOOORING WAAA',
    'you can do it!',
    'get some good rest okay?',
    'hey dont forget to check for jarring cuts!',
    'dont forget to take a shower!',
    'YOU CAN DO THIS!',
    'THIS WAS MADE BY CANE BTWWW THE GOATED FURRY FOX!!!',
    'YAWA',
    'Dont forget to drink water',
    "you're a furry i swear...wait- no? ok..",
    'WE LOVE TV GUY',
    'HEY IS THAT A 100+ A ROLLS? WTF DUDE GOODLUCK',
    'im living in your computer now',
    "why not try light mode? cmonn its so gooood!",
    'finish your a rolls please istg',
    'kaze is gay',
    'ZAC OUR CLOSETED TWINK WAHAHA',
    'upgrade your ram please...',
    "it'll be alright",
    'goodluckies~',
    "i guess its lock-in time now huh?",
    'dont hesitate contacting cane for problems',
    "you can literally drop a file anywhere in this window btw",
    'Thanks John!',
    "What do you mean mizuu version? its- its deprecated!!",
    "dont just stare at me like that.. im- im gonna blush! >///<",
    "done with the a-roll? damn nice!",
    "i know you're reading that welcome text for 10 minutes now..",
    "i know that welcome animation is fascinating right?",
    "dont forget your skincare",
    "any thoughts of becoming a furry? OH OH A WOLF SUITS YOUU",
    "just you and i staring at each other yeah is that what you like?",
    "why are you even reading this messages go edit now you silly goose",
    "did you know Cane's fursona is an arctic fox?",
    "im glad that i exist here in your computer",
    "OwO, Whats this?",
    "Ding! is that the sound of paycheck?... no? awww",
    "im ready to crunch that timestamps!",
    "are you stress eating?... spare some food to me!!",
    "wipe your monitor please.. so you can see your timestamps i deliver for you",
]

def format_relative_time(timestamp_str: str) -> str:
    try:
        t = datetime.fromisoformat(timestamp_str).timestamp()
        diff = time.time() - t
        if diff < 60:
            return "JUST NOW"
        mins = int(diff // 60)
        if mins < 60:
            return f"{mins}M AGO"
        hrs = int(mins // 60)
        if hrs < 24:
            return f"{hrs}H AGO"
        days = int(hrs // 24)
        if days == 1:
            return "YESTERDAY"
        return f"{days}D AGO"
    except Exception:
        return "RECENTLY"


class TypewriterHeading(QLabel):
    """Recreates Cane's signature typewriter animation with typos & multilingual sequences."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self.apply_theme()
        self.current_text = ""
        self.target_text = ""
        self.char_idx = 0
        self.is_deleting = False
        self._sequence_index = 0
        self._active_sequence: List[str] = []

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self._init_sequence()
        self.timer.start(50)

    def apply_theme(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        self.setStyleSheet(f"""
            color: {text_col};
            background: transparent;
            font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Nirmala UI', 'Microsoft YaHei', sans-serif;
            font-size: 38px; 
            font-weight: 900; 
            letter-spacing: -0.5px;
        """)

    def _init_sequence(self):
        if random.random() < 0.3:
            shuffled = list(MULTILINGUAL_HELLOS)
            random.shuffle(shuffled)
            self._active_sequence = shuffled[:5] + ["WELCOME!! ... HI!"]
        else:
            shuffled = list(TYPOS_POOL)
            random.shuffle(shuffled)
            self._active_sequence = [shuffled[0], shuffled[1], "WELCOME!! ... HI!"]

        self._sequence_index = 0
        self._next_phrase()

    def _next_phrase(self):
        if self._sequence_index >= len(self._active_sequence):
            self._init_sequence()
            return

        self.target_text = self._active_sequence[self._sequence_index]
        self._sequence_index += 1
        self.char_idx = 0
        self.is_deleting = False

    def _step(self):
        if not self.is_deleting:
            if self.char_idx < len(self.target_text):
                self.char_idx += 1
                self.setText(self.target_text[:self.char_idx] + "▌")
            else:
                self.setText(self.target_text + " ")
                self.is_deleting = True
                is_final = (self.target_text == "WELCOME!! ... HI!")
                self.timer.setInterval(2800 if is_final else 800)
                return
        else:
            self.timer.setInterval(35)
            if self.char_idx > 0:
                self.char_idx -= 1
                self.setText(self.target_text[:self.char_idx] + "▌")
            else:
                self._next_phrase()
                self.timer.setInterval(60)


class ScriptParserDropCard(CardWidget):
    """Large interactive Script Parser card with drag & drop and browse file picker (stationary desktop hover)."""
    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFixedHeight(300)
        self.setObjectName("DropCard")
        self.setBorderRadius(8)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(14)

        # Centered Icon Box
        self.icon_box = QFrame(self)
        self.icon_box.setFixedSize(56, 56)
        ib_layout = QVBoxLayout(self.icon_box)
        ib_layout.setAlignment(Qt.AlignCenter)
        self.doc_icon = IconWidget(FluentIcon.DOCUMENT, self.icon_box)
        self.doc_icon.setFixedSize(24, 24)
        ib_layout.addWidget(self.doc_icon)
        layout.addWidget(self.icon_box, 0, Qt.AlignCenter)

        # Title & Subtitle
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        self.title_lbl = QLabel("Script Parser", self)
        self.title_lbl.setAlignment(Qt.AlignCenter)
        text_box.addWidget(self.title_lbl)

        self.sub_lbl = QLabel("EXTRACT A-ROLLS & DURATIONS", self)
        self.sub_lbl.setAlignment(Qt.AlignCenter)
        text_box.addWidget(self.sub_lbl)

        self.hint_lbl = QLabel("Drop .docx or .clickproj anywhere or click to browse", self)
        self.hint_lbl.setAlignment(Qt.AlignCenter)
        text_box.addWidget(self.hint_lbl)

        layout.addLayout(text_box)
        self.update_accent()

    def _normalBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("surface", "#251f27"))

    def _hoverBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("surface_hover", "#312933"))

    def paintEvent(self, e):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        painter.setBrush(self.backgroundColor)
        painter.setPen(QPen(QColor(pal.get("border", "#3d3240")), 1))
        r = self.borderRadius
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)

    def update_accent(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        rgb = pal.get("accent_rgb", "251, 113, 133")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#64748b")
        self.icon_box.setStyleSheet(f"""
            background-color: rgba({rgb}, 0.12);
            border: 1px solid rgba({rgb}, 0.3);
            border-radius: 12px;
        """)
        if hasattr(self, "title_lbl"):
            self.title_lbl.setStyleSheet(f"color: {text_col}; background: transparent; font-size: 18px; font-weight: 800;")
        if hasattr(self, "sub_lbl"):
            self.sub_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 9px; font-weight: 800; letter-spacing: 1.5px;")
        if hasattr(self, "hint_lbl"):
            self.hint_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; margin-top: 4px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Script or Project",
                "",
                "Supported Files (*.docx *.clickproj);;Clickables Projects (*.clickproj);;Word Documents (*.docx);;All Files (*.*)"
            )
            if path:
                self.file_selected.emit(path)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                lf = url.toLocalFile().lower()
                if lf.endswith('.docx') or lf.endswith('.clickproj'):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            lf = local_path.lower()
            if lf.endswith('.docx') or lf.endswith('.clickproj'):
                self.file_selected.emit(local_path)
                event.acceptProposedAction()
                return


class RecentItemRow(CardWidget):
    """Clean solid row for recent scripts with theme contrast and stationary desktop hover."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RecentItemRow")
        self.setFixedHeight(40)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setBorderRadius(6)
        self.name_lbl: QLabel = None

    def _normalBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("surface_hover", "#312933"))

    def _hoverBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("border", "#3d3240"))

    def update_theme(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        self.setBackgroundColor(self._normalBackgroundColor())
        if self.name_lbl is not None:
            self.name_lbl.setStyleSheet(f"color: {text_col}; background: transparent; font-size: 12px; font-weight: 600;")
        self.update()

    def paintEvent(self, e):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        painter.setBrush(self.backgroundColor)
        painter.setPen(QPen(QColor(pal.get("border", "#3d3240")), 1))
        r = self.borderRadius
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)


class RecentScriptsCard(QFrame):
    """Sleek native recent scripts panel matching Adobe Premiere Pro / After Effects start screens."""
    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(300)
        self.setObjectName("RecentScriptsCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Flat Section Header (No nested pills)
        header = QHBoxLayout()
        header.setSpacing(8)
        hist_icon = IconWidget(FluentIcon.HISTORY, self)
        hist_icon.setFixedSize(14, 14)
        header.addWidget(hist_icon)

        self.title_lbl = QLabel("RECENT SCRIPTS", self)
        header.addWidget(self.title_lbl)
        header.addStretch(1)
        layout.addLayout(header)

        # Smooth Kinetic Scroll Area for files with zero clipping
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_widget = QWidget(self.scroll)
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_widget)
        if hasattr(self.scroll, "delegate") and self.scroll.delegate:
            from qfluentwidgets.common.smooth_scroll import SmoothMode
            self.scroll.delegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        layout.addWidget(self.scroll)

        self.update_theme()
        self.refresh()

    def update_theme(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        muted_col = pal.get("muted", "#64748b")
        self.setStyleSheet(f"""
            QFrame#RecentScriptsCard {{
                background-color: {pal.get('surface', '#251f27')};
                border: 1px solid {pal.get('border', '#3d3240')};
                border-radius: 8px;
            }}
        """)
        if hasattr(self, "title_lbl") and self.title_lbl:
            self.title_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 10px; font-weight: 800; letter-spacing: 1.5px;")
        if hasattr(self, 'list_layout'):
            for i in range(self.list_layout.count()):
                it = self.list_layout.itemAt(i)
                if it and it.widget() and isinstance(it.widget(), RecentItemRow):
                    it.widget().update_theme()

    def refresh(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#64748b")

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        recent_files = db.get_recent_files(6)
        if not recent_files:
            empty_lbl = QLabel("No recent scripts opened yet.")
            empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic; padding: 12px;")
            self.list_layout.insertWidget(0, empty_lbl)
            return

        for rf in recent_files:
            item_frame = RecentItemRow(self.list_widget)
            i_layout = QHBoxLayout(item_frame)
            i_layout.setContentsMargins(12, 0, 10, 0)

            name_lbl = QLabel(rf["name"], item_frame)
            name_lbl.setStyleSheet(f"color: {text_col}; background: transparent; font-size: 12px; font-weight: 600;")
            name_lbl.setToolTip(rf.get("path", ""))
            item_frame.name_lbl = name_lbl
            i_layout.addWidget(name_lbl, 1)

            def _on_row_clicked(e, p=rf["path"], frame=item_frame):
                if e.button() == Qt.LeftButton:
                    self.file_selected.emit(p)
                RecentItemRow.mousePressEvent(frame, e)

            item_frame.mousePressEvent = _on_row_clicked
            self.list_layout.insertWidget(self.list_layout.count() - 1, item_frame)


class HomeActionCard(CardWidget):
    """Sleek bottom action cards matching HomeDashboard's Updates & Copyright Helper (stationary desktop hover)."""
    clicked = Signal()

    def __init__(self, icon_enum, title_text: str, subtitle_text: str, badge_text: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("ActionCard")
        self.setFixedHeight(74)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setBorderRadius(8)
        self.badge = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(14)

        self.icon_box = QFrame(self)
        self.icon_box.setFixedSize(40, 40)
        ib_layout = QVBoxLayout(self.icon_box)
        ib_layout.setAlignment(Qt.AlignCenter)
        icon = IconWidget(icon_enum, self.icon_box)
        icon.setFixedSize(18, 18)
        ib_layout.addWidget(icon)
        layout.addWidget(self.icon_box)

        tb = QVBoxLayout()
        tb.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self.title_lbl = QLabel(title_text, self)
        title_row.addWidget(self.title_lbl)

        if badge_text:
            self.badge = QLabel(badge_text, self)
            title_row.addWidget(self.badge)
        title_row.addStretch(1)
        tb.addLayout(title_row)

        self.sub_lbl = QLabel(subtitle_text, self)
        tb.addWidget(self.sub_lbl)
        layout.addLayout(tb, 1)

        arrow = IconWidget(FluentIcon.CHEVRON_RIGHT, self)
        arrow.setFixedSize(14, 14)
        layout.addWidget(arrow)

        self.update_accent()

    def update_accent(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        rgb = pal.get("accent_rgb", "56, 189, 248")
        accent = pal.get("accent", "#38bdf8")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#64748b")
        self.icon_box.setStyleSheet(f"""
            background-color: rgba({rgb}, 0.12);
            border: 1px solid rgba({rgb}, 0.25);
            border-radius: 8px;
        """)
        if hasattr(self, "title_lbl") and self.title_lbl:
            self.title_lbl.setStyleSheet(f"color: {text_col}; background: transparent; font-size: 13px; font-weight: 700;")
        if hasattr(self, "sub_lbl") and self.sub_lbl:
            self.sub_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 10px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase;")
        if self.badge:
            self.badge.setStyleSheet(f"""
                background-color: rgba({rgb}, 0.15);
                color: {accent};
                font-size: 9px;
                font-weight: 800;
                padding: 1px 6px;
                border-radius: 4px;
                border: 1px solid rgba({rgb}, 0.3);
            """)

    def _normalBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("surface", "#251f27"))

    def _hoverBackgroundColor(self):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        return QColor(pal.get("surface_hover", "#312933"))

    def paintEvent(self, e):
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        painter.setBrush(self.backgroundColor)
        painter.setPen(QPen(QColor(pal.get("border", "#3d3240")), 1))
        r = self.borderRadius
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class HomeView(QWidget):
    """The Home Dashboard with Typewriter heading, single Settings button, and dual start cards."""
    file_selected = Signal(str)
    settings_requested = Signal()
    updates_requested = Signal()
    copyright_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomeView")
        self.setStyleSheet("QWidget#HomeView { background: transparent; }")

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        # Centered container matching React max-w-7xl
        content_container = QWidget(self)
        content_container.setMaximumWidth(1080)
        content_container.setStyleSheet("background: transparent;")
        main_layout = QVBoxLayout(content_container)
        main_layout.setContentsMargins(32, 28, 32, 20)
        main_layout.setSpacing(22)

        # 1. Header Row: Left Typewriter & Splash + Right [Settings] Button ONLY
        header_row = QHBoxLayout()
        header_left = QVBoxLayout()
        header_left.setSpacing(6)

        self.typewriter = TypewriterHeading(self)
        header_left.addWidget(self.typewriter)

        self.splash_lbl = QLabel(random.choice(SPLASH_TEXTS), self)
        self.splash_lbl.setFixedHeight(22)
        self.splash_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #94a3b8;")
        header_left.addWidget(self.splash_lbl)
        header_row.addLayout(header_left, 1)

        # Right Action: Settings Button ONLY (Data Vault is accessed in Settings / Sidebar)
        self.settings_btn = PushButton("Settings", self)
        self.settings_btn.setIcon(FluentIcon.SETTING)
        self.settings_btn.setFixedSize(110, 36)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header_row.addWidget(self.settings_btn, 0, Qt.AlignTop)

        main_layout.addLayout(header_row)

        # 2. Main 2-Column Split: Script Parser Card + Recent Scripts Card
        grid_row = QHBoxLayout()
        grid_row.setSpacing(20)

        self.drop_card = ScriptParserDropCard(self)
        self.drop_card.file_selected.connect(self.file_selected.emit)
        grid_row.addWidget(self.drop_card, 1)

        self.recents_card = RecentScriptsCard(self)
        self.recents_card.file_selected.connect(self.file_selected.emit)
        grid_row.addWidget(self.recents_card, 1)

        main_layout.addLayout(grid_row)

        # 3. Bottom Row: Check for Updates + Copyright Helper (matching React HomeDashboard)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(20)

        from ...core.updater import APP_VERSION
        self.updates_card = HomeActionCard(
            FluentIcon.INFO, 
            "Check for Updates", 
            "About this app & Canefly", 
            badge_text=f"v{APP_VERSION}",
            parent=self
        )
        self.updates_card.clicked.connect(self.updates_requested.emit)
        bottom_row.addWidget(self.updates_card, 1)

        self.copyright_card = HomeActionCard(
            FluentIcon.CERTIFICATE, 
            "Copyright Helper", 
            "Audio Visual & Segment Heatmaps", 
            badge_text="BETA", 
            parent=self
        )
        self.copyright_card.clicked.connect(self.copyright_requested.emit)
        bottom_row.addWidget(self.copyright_card, 1)

        main_layout.addLayout(bottom_row)

        main_layout.addStretch(1)
        outer_layout.addWidget(content_container)

    def apply_theme(self, variant=None, dark=True):
        """Update hero typewriter, splash label, cards, and accent-tinted icon boxes to match active theme variant."""
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        muted_col = pal.get("muted", "#94a3b8")
        if hasattr(self, 'typewriter') and self.typewriter:
            self.typewriter.apply_theme()
        if hasattr(self, 'splash_lbl') and self.splash_lbl:
            self.splash_lbl.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {muted_col}; background: transparent;")
        if hasattr(self, 'drop_card'):
            self.drop_card.update_accent()
            self.drop_card.setBackgroundColor(self.drop_card._normalBackgroundColor())
            self.drop_card.update()
        if hasattr(self, 'recents_card'):
            self.recents_card.update_theme()
        if hasattr(self, 'updates_card'):
            self.updates_card.update_accent()
            self.updates_card.setBackgroundColor(self.updates_card._normalBackgroundColor())
            self.updates_card.update()
        if hasattr(self, 'copyright_card'):
            self.copyright_card.update_accent()
            self.copyright_card.setBackgroundColor(self.copyright_card._normalBackgroundColor())
            self.copyright_card.update()

    def refresh_recents(self):
        self.recents_card.refresh()
