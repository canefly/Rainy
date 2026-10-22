import os
import webbrowser
from typing import List, Dict, Optional, Set
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QWidget, QTextEdit, QApplication
)
from qframelesswindow import FramelessWindow, TitleBar
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, FluentIcon, 
    IconWidget, SearchLineEdit, CheckBox, ProgressBar, SmoothScrollArea, ComboBox
)
from .widgets import MiddleClickAutoScroller
from ...core.models import EditorNote
from ..theme import ThemeManager

class ToolWindowTitleBar(TitleBar):
    """Native frameless title bar with vector icon, title label, refined compact pill badge, and pin button."""
    def __init__(self, title: str, icon_enum: FluentIcon, badge_color: str = None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setObjectName("ToolWindowTitleBar")
        self._custom_badge_color = badge_color
        self.hBoxLayout.setContentsMargins(12, 0, 8, 0)
        self.hBoxLayout.setSpacing(8)

        # 1. Vector Icon
        self.icon_widget = IconWidget(icon_enum, self)
        self.icon_widget.setFixedSize(16, 16)
        self.hBoxLayout.insertWidget(0, self.icon_widget, 0, Qt.AlignVCenter)

        # 2. Title Label with Segoe UI Variable typography
        self.title_label = QLabel(title, self)
        self.hBoxLayout.insertWidget(1, self.title_label, 0, Qt.AlignVCenter)

        # 3. Refined Compact Pill Count Badge (vertically centered, typography-aligned)
        self.count_badge = QLabel("0", self)
        self.count_badge.setFixedHeight(20)
        self.count_badge.setMinimumWidth(22)
        self.count_badge.setAlignment(Qt.AlignCenter)
        self.hBoxLayout.insertWidget(2, self.count_badge, 0, Qt.AlignVCenter)

        self.hBoxLayout.insertStretch(3, 1)

        # 4. Pin / Stay on Top Button inserted before window buttons
        self.pin_btn = ToolButton(FluentIcon.PIN, self)
        self.pin_btn.setFixedSize(26, 26)
        self.pin_btn.setToolTip("Always on top")
        insert_idx = max(3, self.hBoxLayout.count() - 3)
        self.hBoxLayout.insertWidget(insert_idx, self.pin_btn, 0, Qt.AlignVCenter)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        accent = pal.get("accent", "#fb7185")
        rgb = pal.get("accent_rgb", "251, 113, 133")
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
        self.setMinimumSize(360, 420)

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

        # Content container layout offset for 38px titlebar
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(14, 44, 14, 12)
        self.main_layout.setSpacing(10)

    def _toggle_pin(self):
        self.is_pinned = not self.is_pinned
        flags = self.windowFlags()
        if self.is_pinned:
            flags |= Qt.WindowStaysOnTopHint
            pal = ThemeManager.get_palette()
            accent = pal.get("accent", "#fb7185")
            self.pin_btn.setStyleSheet(f"color: {accent};")
        else:
            flags &= ~Qt.WindowStaysOnTopHint
            self.pin_btn.setStyleSheet("")
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
        accent = pal.get("accent", "#fb7185")
        rgb = pal.get("accent_rgb", "251, 113, 133")
        text_col = pal.get("text", "#ffffff")

        if hasattr(self, "add_btn") and self.add_btn:
            self.add_btn.setStyleSheet(f"""
                PushButton {{
                    background-color: rgba({rgb}, 0.14);
                    color: {text_col};
                    border: 1px solid rgba({rgb}, 0.32);
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 4px 12px;
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

        # Clear existing widgets
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.set_badge_text(f"{len(notes)}")

        if not notes:
            pal = ThemeManager.get_palette()
            muted_col = pal.get("muted", "#64748b")
            empty_lbl = QLabel("No editor notes found in script.")
            empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")
            self.list_layout.insertWidget(0, empty_lbl)
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
            cb.setChecked(is_checked)
            cb.stateChanged.connect(lambda state, n=note, c=card: self._on_note_check(n, state, c))
            top_row.addWidget(cb)

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

            self._apply_note_style(card, is_checked)

            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.note_cards.append(card)

        self._update_all_done_button()
        self.apply_filter()

    def _apply_note_style(self, card: QFrame, is_checked: bool):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#fb7185")
        rgb = pal.get("accent_rgb", "251, 113, 133")
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
        QApplication.clipboard().setText(text)
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
        self.middle_scroller = MiddleClickAutoScroller(self.scroll, self)
        self.main_layout.addWidget(self.scroll)
        self.apply_theme()

    def apply_theme(self):
        if hasattr(self, "title_bar_widget") and self.title_bar_widget:
            self.title_bar_widget.apply_theme()
        self.apply_filter()
        for card in self.link_cards:
            is_checked = bool(card.property("is_checked"))
            self._apply_link_style(card, is_checked)

    def load_links(self, links: List[str], checked_links: Optional[Dict[str, bool]] = None):
        self.links = sorted(list(set(links)))
        self.checked_links = checked_links.copy() if checked_links else {}
        self.link_cards.clear()

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.set_badge_text(f"{len(self.links)}")

        if not self.links:
            pal = ThemeManager.get_palette()
            muted_col = pal.get("muted", "#64748b")
            empty_lbl = QLabel("No external links found in script.")
            empty_lbl.setStyleSheet(f"color: {muted_col}; background: transparent; font-size: 11px; font-style: italic;")
            self.list_layout.insertWidget(0, empty_lbl)
            self._update_all_done_button()
            self.apply_filter()
            return

        for link in self.links:
            is_checked = self.checked_links.get(link, False)
            domain = link.split('/')[2].upper() if '/' in link else "LINK"

            card = QFrame(self.list_widget)
            card.setObjectName("LinkCard")

            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(10, 6, 10, 6)
            c_layout.setSpacing(8)

            cb = CheckBox("", card)
            cb.setChecked(is_checked)
            cb.stateChanged.connect(lambda state, l=link, c=card: self._on_link_check(l, state, c))
            c_layout.addWidget(cb)

            # Domain badge (refined compact pill aligned with text)
            badge = QLabel(domain, card)
            badge.setFixedHeight(20)
            badge.setAlignment(Qt.AlignCenter)
            c_layout.addWidget(badge, 0, Qt.AlignVCenter)

            lbl = QLabel(link, card)
            lbl.setCursor(QCursor(Qt.PointingHandCursor))
            lbl.mousePressEvent = lambda e, l=link: webbrowser.open(l)
            c_layout.addWidget(lbl, 1)

            # Copy link button
            copy_btn = ToolButton(FluentIcon.COPY, card)
            copy_btn.setFixedSize(24, 24)
            copy_btn.setToolTip("Copy URL")
            copy_btn.clicked.connect(lambda _, l=link: self._copy_link(l))
            c_layout.addWidget(copy_btn)

            open_btn = ToolButton(FluentIcon.SHARE, card)
            open_btn.setFixedSize(24, 24)
            open_btn.setToolTip("Open in browser")
            open_btn.clicked.connect(lambda _, l=link: webbrowser.open(l))
            c_layout.addWidget(open_btn)

            card.setProperty("link_url", link.lower())
            card.setProperty("is_checked", is_checked)
            card.setProperty("checkbox", cb)
            card.setProperty("domain_badge", badge)
            card.setProperty("text_label", lbl)

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
                    padding: 0px 7px;
                    border-radius: 4px;
                    border: 1px solid rgba({rgb}, 0.28);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
            """)
        txt_lbl = card.property("text_label")
        if txt_lbl is not None:
            txt_lbl.setStyleSheet(f"color: {text_col}; background: transparent; border: none; font-size: 11px;")

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
        QApplication.clipboard().setText(link)
        self.copy_feedback_requested.emit("Copied Link!")

    def _copy_all(self):
        if self.links:
            QApplication.clipboard().setText("\n".join(self.links))
            self.copy_feedback_requested.emit("Copied All Links!")
            self.copy_all_btn.setText("Copied!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.copy_all_btn.setText("Copy All"))

EditorNotesWindow = NotesDrawer
SourceLinksWindow = LinksDrawer

