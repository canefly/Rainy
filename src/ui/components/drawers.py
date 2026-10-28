import os
import webbrowser
from typing import List, Dict, Optional, Set
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QCursor, QIcon, QPainter, QColor, QPalette
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QWidget, QTextEdit, QApplication, QSizePolicy
)
from qframelesswindow import FramelessWindow
from qfluentwidgets import (
    FluentTitleBar, PushButton, PrimaryPushButton, ToolButton, FluentIcon, 
    IconWidget, SearchLineEdit, CheckBox, ProgressBar, SmoothScrollArea, ComboBox
)
from qfluentwidgets.components.widgets.menu import MenuAnimationType
from .widgets import MiddleClickAutoScroller, safe_copy_to_clipboard
from .titlebar import CaptionPinButton
from ...core.models import EditorNote
from ..theme import ThemeManager


def format_domain_badge(link: str) -> str:
    """Returns a clean, compact domain identifier for badge display."""
    lower = link.lower()
    if "drive.google.com" in lower:
        return "DRIVE"
    elif "youtube.com" in lower or "youtu.be" in lower:
        return "YOUTUBE"
    elif "dropbox.com" in lower:
        return "DROPBOX"
    elif "vimeo.com" in lower:
        return "VIMEO"
    elif "onedrive" in lower:
        return "ONEDRIVE"
    else:
        domain = link.split('/')[2].upper() if '/' in link else "LINK"
        if domain.startswith("WWW."):
            domain = domain[4:]
        return domain[:10]


class ElidedUrlLabel(QLabel):
    """A URL label that cleanly elides middle text with '...' to fit available space,
    never forces its parent layout to expand or push buttons off-screen, and preserves full URL in tooltip."""
    
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self.setToolTip(url)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(60)

    def set_url(self, url: str):
        self._url = url
        self.setToolTip(url)
        self.update()

    def url(self) -> str:
        return self._url

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            webbrowser.open(self._url)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = painter.fontMetrics()
        avail_w = max(10, self.width() - 4)
        elided = metrics.elidedText(self._url, Qt.ElideMiddle, avail_w)
        painter.setPen(self.palette().color(QPalette.WindowText))
        painter.drawText(QRect(2, 0, avail_w, self.height()), Qt.AlignLeft | Qt.AlignVCenter, elided)


class ToolWindowTitleBar(FluentTitleBar):
    """Native frameless title bar matching the main window's caption styling, pin button, and Segoe UI Variable typography."""
    def __init__(self, title: str, icon_enum: FluentIcon, badge_color: str = None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setObjectName("ToolWindowTitleBar")
        self._custom_badge_color = badge_color
        self.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        self.hBoxLayout.setSpacing(0)

        if hasattr(self, 'iconLabel') and self.iconLabel:
            self.iconLabel.hide()
        if hasattr(self, 'titleLabel') and self.titleLabel:
            self.titleLabel.hide()

        # Left branding container (Icon + Title + Pill Badge)
        self.left_widget = QWidget(self)
        self.left_widget.setFixedHeight(32)
        l_layout = QHBoxLayout(self.left_widget)
        l_layout.setContentsMargins(0, 0, 4, 0)
        l_layout.setSpacing(6)

        # 1. Vector Icon
        self.icon_widget = IconWidget(icon_enum, self.left_widget)
        self.icon_widget.setFixedSize(16, 16)
        l_layout.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        # 2. Title Label with Segoe UI Variable typography
        self.title_label = QLabel(title, self.left_widget)
        l_layout.addWidget(self.title_label, 0, Qt.AlignVCenter)

        # 3. Refined Compact Pill Count Badge
        self.count_badge = QLabel("0", self.left_widget)
        self.count_badge.setFixedHeight(20)
        self.count_badge.setMinimumWidth(22)
        self.count_badge.setAlignment(Qt.AlignCenter)
        l_layout.addWidget(self.count_badge, 0, Qt.AlignVCenter)

        self.hBoxLayout.insertWidget(0, self.left_widget, 0, Qt.AlignTop)

        # 4. Pin Window Button (Flat 46x32 CaptionPinButton placed directly beside minimize button)
        self.pin_btn = CaptionPinButton(self)
        self.pin_btn.setToolTip("Always on top")
        self.buttonLayout.insertWidget(0, self.pin_btn)

        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        col = self._custom_badge_color if self._custom_badge_color else accent

        self.title_label.setStyleSheet(
            f"color: {text_col}; background: transparent; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            f"font-weight: 700; font-size: 13px; letter-spacing: 0.3px;"
        )
        self.count_badge.setStyleSheet(f"""
            QLabel {{
                background-color: rgba({rgb}, 0.14);
                color: {col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                padding: 0px 7px;
                border-radius: 10px;
                border: 1px solid rgba({rgb}, 0.28);
            }}
        """)
        self.pin_btn.update()

        # Match MainWindow caption button colors
        base = QColor(text_col)
        hover_bg = QColor(base.red(), base.green(), base.blue(), 28)
        press_bg = QColor(base.red(), base.green(), base.blue(), 46)

        def rgba(c: QColor) -> str:
            return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"

        for btn in (self.minBtn, self.maxBtn):
            btn.setStyleSheet(
                f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: {rgba(base)}; "
                f"qproperty-pressedColor: {rgba(base)}; "
                f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
                f"qproperty-hoverBackgroundColor: {rgba(hover_bg)}; "
                f"qproperty-pressedBackgroundColor: {rgba(press_bg)};"
            )
            btn.update()
        self.closeBtn.setStyleSheet(
            f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: rgba(255, 255, 255, 255); "
            f"qproperty-pressedColor: rgba(255, 255, 255, 255); "
            f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
            f"qproperty-hoverBackgroundColor: rgba(232, 17, 35, 255); "
            f"qproperty-pressedBackgroundColor: rgba(241, 112, 122, 255);"
        )
        self.closeBtn.update()


class DesktopToolWindow(FramelessWindow):
    """Independent modeless desktop tool window with window icon, always-on-top pin, and Segoe UI Variable typography."""
    closed = Signal()
    pin_toggled = Signal(bool)

    def __init__(self, title: str, icon_enum: FluentIcon, badge_color: str = None, parent=None):
        super().__init__(parent)
        self.is_pinned = False
        self.badge_color = badge_color
        self.setObjectName("DesktopToolWindow")
        self.setWindowTitle(f"{title} - Rainy Tools")
        self.resize(440, 640)
        self.setMinimumSize(340, 360)

        # Set Window Icon
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # TitleBar
        self.title_bar_widget = ToolWindowTitleBar(title, icon_enum, badge_color, self)
        self.setTitleBar(self.title_bar_widget)
        self.title_bar_widget.pin_btn.clicked.connect(self._toggle_pin)

        # Exposed attributes for callers/tests
        self.title_label = self.title_bar_widget.title_label
        self.count_badge = self.title_bar_widget.count_badge
        self.pin_btn = self.title_bar_widget.pin_btn

        # Content container layout offset for 40px titlebar
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(14, 46, 14, 12)
        self.main_layout.setSpacing(10)

    def _toggle_pin(self):
        self.is_pinned = not self.is_pinned
        flags = self.windowFlags()
        if self.is_pinned:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.pin_btn.set_pinned(self.is_pinned)
        self.setWindowFlags(flags)
        self.show()
        self.pin_toggled.emit(self.is_pinned)

    def set_badge_text(self, text: str):
        self.count_badge.setText(text)

    def paintEvent(self, e):
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e):
        super().moveEvent(e)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        ThemeManager.enable_window_backdrop(self)
        if hasattr(self, "apply_theme"):
            self.apply_theme()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.closed.emit()

DrawerFrame = DesktopToolWindow


class NotesDrawer(DrawerFrame):
    """Full-featured Editor Notes Checklist Drawer with search, progress tracking, and line jumps."""
    note_added = Signal(str)
    note_deleted = Signal(str)
    note_checked_changed = Signal(str, bool)
    jump_to_block_requested = Signal(int)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Editor Notes", FluentIcon.EDIT, badge_color=None, parent=parent)
        self.notes: List[EditorNote] = []
        self.note_cards: List[QFrame] = []
        self.checked_state: Dict[str, bool] = {}

        # 1. Toolbar: Dropdown Filter (All | Done | Pending) + "Mark all as done" PushButton
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.filter_combo = ComboBox(self)
        orig_create_menu = self.filter_combo._createComboMenu
        def _fast_menu():
            menu = orig_create_menu()
            orig_exec = menu.exec
            menu.exec = lambda pos, ani=True, aniType=MenuAnimationType.DROP_DOWN: orig_exec(pos, ani=False, aniType=MenuAnimationType.NONE)
            return menu
        self.filter_combo._createComboMenu = _fast_menu
        self.filter_combo.addItems(["All", "Done", "Pending"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(112)
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch(1)

        self.toggle_all_btn = PushButton("Mark all as done", self)
        self.toggle_all_btn.setFixedHeight(28)
        self.toggle_all_btn.clicked.connect(self._toggle_all)
        toolbar.addWidget(self.toggle_all_btn)

        self.main_layout.addLayout(toolbar)

        # 2. Notes List Smooth Scroll Area
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
        self.middle_scroller = MiddleClickAutoScroller(self.scroll, self)
        self.main_layout.addWidget(self.scroll)

        # 3. Add Note Input Row
        self.note_input = QTextEdit(self)
        self.note_input.setObjectName("DrawerNoteInput")
        self.note_input.setPlaceholderText("Add a custom note...")
        self.note_input.setFixedHeight(50)
        self.main_layout.addWidget(self.note_input)

        self.add_btn = PushButton("Add Note", self)
        self.add_btn.setIcon(FluentIcon.ADD)
        self.add_btn.setFixedHeight(32)
        self.add_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_btn.clicked.connect(self._on_add_note)
        self.main_layout.addWidget(self.add_btn)
        self.apply_theme()

    def apply_theme(self):
        if hasattr(self, "title_bar_widget") and self.title_bar_widget:
            self.title_bar_widget.apply_theme()
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")

        if hasattr(self, "empty_lbl") and self.empty_lbl:
            self.empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")

        if hasattr(self, "toggle_all_btn") and self.toggle_all_btn:
            self.toggle_all_btn.setStyleSheet(f"""
                PushButton {{
                    background-color: {surface};
                    color: {text_col};
                    border: 1px solid {border};
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 2px 10px;
                }}
                PushButton:hover {{
                    background-color: {surface_hover};
                    border-color: {accent};
                }}
            """)

        if hasattr(self, "note_input") and self.note_input:
            self.note_input.setStyleSheet(f"""
                QTextEdit#DrawerNoteInput {{
                    background-color: {surface};
                    color: {text_col};
                    border: 1px solid {border};
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 11px;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
                QTextEdit#DrawerNoteInput:focus {{
                    border: 1px solid {accent};
                }}
            """)

        if hasattr(self, "add_btn") and self.add_btn:
            self.add_btn.setStyleSheet(f"""
                PushButton {{
                    background-color: rgba({rgb}, 0.14);
                    color: {text_col};
                    border: 1px solid rgba({rgb}, 0.32);
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 5px 12px 6px 36px;
                }}
                PushButton:hover {{
                    background-color: rgba({rgb}, 0.22);
                    border: 1px solid {accent};
                }}
            """)
        self.apply_filter()
        for card in self.note_cards:
            is_checked = bool(card.property("is_checked"))
            self._apply_note_style(card, is_checked)

    def load_notes(self, notes: List[EditorNote], checked_notes: Optional[Dict[str, bool]] = None):
        self.notes = notes
        self.checked_state = checked_notes.copy() if checked_notes else {}
        self.note_cards.clear()
        self.empty_lbl = None

        # Clear existing widgets
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.set_badge_text(f"{len(notes)}")

        if not notes:
            pal = ThemeManager.get_palette()
            muted_col = pal.get("muted", "#94a3b8")
            self.empty_lbl = QLabel("No editor notes found in script.", self.list_widget)
            self.empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")
            self.list_layout.insertWidget(0, self.empty_lbl)
            self._update_all_done_button()
            self.apply_filter()
            return

        for idx, note in enumerate(notes, 1):
            is_checked = self.checked_state.get(note.id, note.is_checked)
            self.checked_state[note.id] = is_checked

            card = QFrame(self.list_widget)
            card.setObjectName("NoteCard")

            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(10, 8, 10, 8)
            c_layout.setSpacing(4)

            top_row = QHBoxLayout()
            top_row.setSpacing(6)

            cb = CheckBox("", card)
            cb.setStyleSheet("CheckBox { min-width: 18px; max-width: 18px; margin: 0px; spacing: 0px; padding: 0px; }")
            cb.setFixedSize(18, 18)
            cb.setChecked(is_checked)
            cb.stateChanged.connect(lambda state, n=note, c=card: self._on_note_check(n, state, c))
            top_row.addWidget(cb, 0, Qt.AlignVCenter)

            idx_lbl = QLabel(f"#{idx}", card)
            idx_lbl.setFixedHeight(20)
            idx_lbl.setAlignment(Qt.AlignCenter)
            top_row.addWidget(idx_lbl, 0, Qt.AlignVCenter)

            top_row.addStretch(1)

            # Jump to line in script
            jump_btn = ToolButton(FluentIcon.SEARCH, card)
            jump_btn.setFixedSize(24, 24)
            jump_btn.setToolTip(f"Jump to block #{note.block_id} in script")
            jump_btn.clicked.connect(lambda _, b=note.block_id: self.jump_to_block_requested.emit(b))
            top_row.addWidget(jump_btn)

            # Copy note text
            copy_btn = ToolButton(FluentIcon.COPY, card)
            copy_btn.setFixedSize(24, 24)
            copy_btn.setToolTip("Copy note text")
            copy_btn.clicked.connect(lambda _, t=note.text: self._copy_text(t))
            top_row.addWidget(copy_btn)

            c_layout.addLayout(top_row)

            # Note text
            lbl = QLabel(note.text, card)
            lbl.setWordWrap(True)
            c_layout.addWidget(lbl)

            card.setProperty("note_id", note.id)
            card.setProperty("note_text", note.text.lower())
            card.setProperty("is_checked", is_checked)
            card.setProperty("checkbox", cb)
            card.setProperty("idx_label", idx_lbl)
            card.setProperty("text_label", lbl)
            card.setProperty("jump_btn", jump_btn)
            card.setProperty("copy_btn", copy_btn)

            self._apply_note_style(card, is_checked)

            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.note_cards.append(card)

        self._update_all_done_button()
        self.apply_filter()

    def _apply_note_style(self, card: QFrame, is_checked: bool):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")
        text_col = pal.get("text", "#ffffff")

        idx_lbl = card.property("idx_label")
        if idx_lbl is not None:
            idx_lbl.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba({rgb}, 0.14);
                    color: {accent};
                    font-size: 10px;
                    font-weight: 700;
                    padding: 0px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba({rgb}, 0.28);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
            """)
        txt_lbl = card.property("text_label")
        if txt_lbl is not None:
            txt_lbl.setStyleSheet(f"color: {text_col}; background: transparent; border: none; font-size: 11px; line-height: 1.4;")

        btn_qss = f"""
            ToolButton {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: {text_col};
            }}
            ToolButton:hover {{
                background-color: {surface_hover};
            }}
            ToolButton:pressed {{
                background-color: rgba({rgb}, 0.22);
            }}
        """
        for b_name in ("jump_btn", "copy_btn"):
            btn = card.property(b_name)
            if btn is not None:
                btn.setStyleSheet(btn_qss)

        if is_checked:
            card.setStyleSheet("""
                QFrame#NoteCard {
                    background-color: rgba(16, 185, 129, 0.10);
                    border: 1px solid rgba(16, 185, 129, 0.38);
                    border-radius: 8px;
                }
                QFrame#NoteCard:hover {
                    border-color: #10b981;
                }
            """)
        else:
            card.setStyleSheet(f"""
                QFrame#NoteCard {{
                    background-color: {surface};
                    border: 1px solid {border};
                    border-radius: 8px;
                }}
                QFrame#NoteCard:hover {{
                    background-color: {surface_hover};
                    border-color: rgba({rgb}, 0.42);
                }}
            """)

    def _on_note_check(self, note: EditorNote, state: int, card: QFrame):
        is_checked = (state == Qt.Checked.value)
        self.checked_state[note.id] = is_checked
        card.setProperty("is_checked", is_checked)
        self._apply_note_style(card, is_checked)
        self.note_checked_changed.emit(note.id, is_checked)
        self._update_all_done_button()
        self.apply_filter()

    def _update_all_done_button(self):
        total = len(self.notes)
        checked = sum(1 for v in self.checked_state.values() if v)
        if total > 0 and checked == total:
            self.toggle_all_btn.setText("Mark all as not done")
        else:
            self.toggle_all_btn.setText("Mark all as done")

    def _update_progress(self):
        self._update_all_done_button()

    def _set_filter(self, filter_name: str):
        mapping = {"all": "All", "pending": "Pending", "checked": "Done"}
        text = mapping.get(filter_name.lower(), filter_name)
        self.filter_combo.setCurrentText(text)

    def _on_filter_changed(self, text: str = ""):
        self.apply_filter()

    def apply_filter(self):
        mode = self.filter_combo.currentText()
        for card in self.note_cards:
            is_checked = bool(card.property("is_checked"))
            if mode == "Done":
                card.setVisible(is_checked)
            elif mode in ("Pending", "Not Done"):
                card.setVisible(not is_checked)
            else:
                card.show()

    def _filter_notes(self):
        self.apply_filter()

    def _toggle_all(self):
        total = len(self.notes)
        if total == 0:
            return
        checked = sum(1 for v in self.checked_state.values() if v)
        new_state = (checked < total)

        for card in self.note_cards:
            cb = card.property("checkbox")
            if cb:
                cb.setChecked(new_state)
        self._update_all_done_button()
        self.apply_filter()

    def _copy_text(self, text: str):
        safe_copy_to_clipboard(text)
        self.copy_feedback_requested.emit("Copied Note!")

    def _on_add_note(self):
        text = self.note_input.toPlainText().strip()
        if text:
            self.note_added.emit(text)
            self.note_input.clear()


class LinksDrawer(DrawerFrame):
    """Full-featured Source Links Checklist Drawer with search, progress tracking, and batch copy."""
    link_checked_changed = Signal(str, bool)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Source Links", FluentIcon.LINK, badge_color=None, parent=parent)
        self.links: List[str] = []
        self.link_cards: List[QFrame] = []
        self.checked_links: Dict[str, bool] = {}
        self.active_filter: str = "all"

        # 1. Toolbar: Dropdown Filter (All | Done | Pending) + "Mark all as done" PushButton
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.filter_combo = ComboBox(self)
        orig_create_menu = self.filter_combo._createComboMenu
        def _fast_menu():
            menu = orig_create_menu()
            orig_exec = menu.exec
            menu.exec = lambda pos, ani=True, aniType=MenuAnimationType.DROP_DOWN: orig_exec(pos, ani=False, aniType=MenuAnimationType.NONE)
            return menu
        self.filter_combo._createComboMenu = _fast_menu
        self.filter_combo.addItems(["All", "Done", "Pending"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(112)
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch(1)

        self.toggle_all_btn = PushButton("Mark all as done", self)
        self.toggle_all_btn.setFixedHeight(28)
        self.toggle_all_btn.clicked.connect(self._toggle_all)
        toolbar.addWidget(self.toggle_all_btn)

        self.main_layout.addLayout(toolbar)

        # 2. Links List Smooth Scroll Area
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
        self.middle_scroller = MiddleClickAutoScroller(self.scroll, self)
        self.main_layout.addWidget(self.scroll)
        self.apply_theme()

    def apply_theme(self):
        if hasattr(self, "title_bar_widget") and self.title_bar_widget:
            self.title_bar_widget.apply_theme()
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")

        if hasattr(self, "empty_lbl") and self.empty_lbl:
            self.empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")

        if hasattr(self, "toggle_all_btn") and self.toggle_all_btn:
            self.toggle_all_btn.setStyleSheet(f"""
                PushButton {{
                    background-color: {surface};
                    color: {text_col};
                    border: 1px solid {border};
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 2px 10px;
                }}
                PushButton:hover {{
                    background-color: {surface_hover};
                    border-color: {accent};
                }}
            """)
        self.apply_filter()
        for card in self.link_cards:
            is_checked = bool(card.property("is_checked"))
            self._apply_link_style(card, is_checked)

    def load_links(self, links: List[str], checked_links: Optional[Dict[str, bool]] = None):
        self.links = sorted(list(set(links)))
        self.checked_links = checked_links.copy() if checked_links else {}
        self.link_cards.clear()
        self.empty_lbl = None

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.set_badge_text(f"{len(self.links)}")

        if not self.links:
            pal = ThemeManager.get_palette()
            muted_col = pal.get("muted", "#94a3b8")
            self.empty_lbl = QLabel("No external links found in script.", self.list_widget)
            self.empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")
            self.list_layout.insertWidget(0, self.empty_lbl)
            self._update_all_done_button()
            self.apply_filter()
            return

        for link in self.links:
            is_checked = self.checked_links.get(link, False)
            domain_text = format_domain_badge(link)

            card = QFrame(self.list_widget)
            card.setObjectName("LinkCard")

            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(10, 6, 10, 6)
            c_layout.setSpacing(6)

            cb = CheckBox("", card)
            cb.setStyleSheet("CheckBox { min-width: 18px; max-width: 18px; margin: 0px; spacing: 0px; padding: 0px; }")
            cb.setFixedSize(18, 18)
            cb.setChecked(is_checked)
            cb.stateChanged.connect(lambda state, l=link, c=card: self._on_link_check(l, state, c))
            c_layout.addWidget(cb, 0, Qt.AlignVCenter)

            # Domain badge (compact, neatly aligned)
            badge = QLabel(domain_text, card)
            badge.setFixedHeight(20)
            badge.setMinimumWidth(50)
            badge.setAlignment(Qt.AlignCenter)
            c_layout.addWidget(badge, 0, Qt.AlignVCenter)

            lbl = ElidedUrlLabel(link, card)
            c_layout.addWidget(lbl, 1, Qt.AlignVCenter)

            # Copy link button
            copy_btn = ToolButton(FluentIcon.COPY, card)
            copy_btn.setFixedSize(24, 24)
            copy_btn.setToolTip("Copy URL")
            copy_btn.clicked.connect(lambda _, l=link: self._copy_link(l))
            c_layout.addWidget(copy_btn, 0, Qt.AlignVCenter)

            open_btn = ToolButton(FluentIcon.SHARE, card)
            open_btn.setFixedSize(24, 24)
            open_btn.setToolTip("Open in browser")
            open_btn.clicked.connect(lambda _, l=link: webbrowser.open(l))
            c_layout.addWidget(open_btn, 0, Qt.AlignVCenter)

            card.setProperty("link_url", link.lower())
            card.setProperty("is_checked", is_checked)
            card.setProperty("checkbox", cb)
            card.setProperty("domain_badge", badge)
            card.setProperty("text_label", lbl)
            card.setProperty("copy_btn", copy_btn)
            card.setProperty("open_btn", open_btn)

            self._apply_link_style(card, is_checked)

            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.link_cards.append(card)

        self._update_all_done_button()
        self.apply_filter()

    def _apply_link_style(self, card: QFrame, is_checked: bool):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")
        text_col = pal.get("text", "#ffffff")

        badge = card.property("domain_badge")
        if badge is not None:
            badge.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba({rgb}, 0.14);
                    color: {accent};
                    font-size: 9px;
                    font-weight: 700;
                    padding: 0px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba({rgb}, 0.28);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
            """)
        txt_lbl = card.property("text_label")
        if txt_lbl is not None:
            p = txt_lbl.palette()
            p.setColor(QPalette.WindowText, QColor(text_col))
            txt_lbl.setPalette(p)
            txt_lbl.setStyleSheet(f"color: {text_col}; background: transparent; border: none; font-size: 11px;")

        btn_qss = f"""
            ToolButton {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: {text_col};
            }}
            ToolButton:hover {{
                background-color: {surface_hover};
            }}
            ToolButton:pressed {{
                background-color: rgba({rgb}, 0.22);
            }}
        """
        for b_name in ("copy_btn", "open_btn"):
            btn = card.property(b_name)
            if btn is not None:
                btn.setStyleSheet(btn_qss)

        if is_checked:
            card.setStyleSheet("""
                QFrame#LinkCard {
                    background-color: rgba(16, 185, 129, 0.10);
                    border: 1px solid rgba(16, 185, 129, 0.38);
                    border-radius: 8px;
                }
                QFrame#LinkCard:hover {
                    border-color: #10b981;
                }
            """)
        else:
            card.setStyleSheet(f"""
                QFrame#LinkCard {{
                    background-color: {surface};
                    border: 1px solid {border};
                    border-radius: 8px;
                }}
                QFrame#LinkCard:hover {{
                    background-color: {surface_hover};
                    border-color: rgba({rgb}, 0.42);
                }}
            """)

    def _on_link_check(self, link: str, state: int, card: QFrame):
        is_checked = (state == Qt.Checked.value)
        self.checked_links[link] = is_checked
        card.setProperty("is_checked", is_checked)
        self._apply_link_style(card, is_checked)
        self.link_checked_changed.emit(link, is_checked)
        self._update_all_done_button()
        self.apply_filter()

    def _update_all_done_button(self):
        total = len(self.links)
        checked = sum(1 for v in self.checked_links.values() if v)
        if total > 0 and checked == total:
            self.toggle_all_btn.setText("Mark all as not done")
        else:
            self.toggle_all_btn.setText("Mark all as done")

    def _update_progress(self):
        self._update_all_done_button()

    def _set_filter(self, filter_name: str):
        mapping = {"all": "All", "pending": "Pending", "checked": "Done"}
        text = mapping.get(filter_name.lower(), filter_name)
        self.filter_combo.setCurrentText(text)

    def _on_filter_changed(self, text: str = ""):
        self.apply_filter()

    def apply_filter(self):
        mode = self.filter_combo.currentText()
        for card in self.link_cards:
            is_checked = bool(card.property("is_checked"))
            if mode == "Done":
                card.setVisible(is_checked)
            elif mode in ("Pending", "Not Done"):
                card.setVisible(not is_checked)
            else:
                card.show()

    def _filter_links(self):
        self.apply_filter()

    def _toggle_all(self):
        total = len(self.links)
        if total == 0:
            return
        checked = sum(1 for v in self.checked_links.values() if v)
        new_state = (checked < total)

        for card in self.link_cards:
            cb = card.property("checkbox")
            if cb:
                cb.setChecked(new_state)
        self._update_all_done_button()
        self.apply_filter()

    def _copy_link(self, link: str):
        safe_copy_to_clipboard(link)
        self.copy_feedback_requested.emit("Copied Link!")

    def _copy_all(self):
        if self.links:
            safe_copy_to_clipboard("\n".join(self.links))
            self.copy_feedback_requested.emit("Copied All Links!")
            self.copy_all_btn.setText("Copied!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.copy_all_btn.setText("Copy All"))

EditorNotesWindow = NotesDrawer
SourceLinksWindow = LinksDrawer

