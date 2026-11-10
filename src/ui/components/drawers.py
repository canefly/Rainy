import os
import webbrowser
from typing import List, Dict, Optional, Set
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QCursor, QIcon, QPainter, QColor, QPalette
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QWidget, QTextEdit, QApplication, QSizePolicy, QDialog
)
from qframelesswindow import FramelessWindow
from qfluentwidgets import (
    FluentTitleBar, MessageBoxBase, PushButton, PrimaryPushButton, ToolButton, FluentIcon, 
    IconWidget, LineEdit, CheckBox, ProgressBar, SmoothScrollArea, ComboBox
)
from .widgets import MiddleClickAutoScroller, safe_copy_to_clipboard, CompactCheckBox, StatusDot
from .titlebar import CaptionPinButton
from .link_embed_card import link_embed_controller
from .link_info_dialog import LinkMetadataDialog
from ...core.models import EditorNote
from ...core.parser import normalize_link
from ...core.unfurl_engine import unfurl_engine, LinkMetadata
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
    never forces its parent layout to expand or push buttons off-screen, and triggers the rich embed hover card."""
    
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._display_text = url
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(60)

    def set_url(self, url: str):
        self._url = url
        self._display_text = url
        self.update()

    def set_display_text(self, text: str):
        self._display_text = text or self._url
        self.update()

    def display_text(self) -> str:
        return self._display_text

    def url(self) -> str:
        return self._url

    def enterEvent(self, event):
        if self._url and self._url != "#":
            top_left = self.mapToGlobal(self.rect().topLeft())
            link_embed_controller.schedule_show(self._url, QRect(top_left, self.size()))
        super().enterEvent(event)

    def leaveEvent(self, event):
        link_embed_controller.schedule_hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            webbrowser.open(self._url)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = painter.fontMetrics()
        avail_w = max(10, self.width() - 4)
        elide_mode = Qt.ElideRight if self._display_text != self._url else Qt.ElideMiddle
        elided = metrics.elidedText(self._display_text, elide_mode, avail_w)
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


class DrawerStatusDot(StatusDot):
    """Vector status dot with dynamic color switching for modal checklists."""
    def set_color(self, color: str, halo_color: str = None):
        self._dot_color = QColor(color)
        if halo_color:
            self._halo_color = QColor(halo_color)
        else:
            c = QColor(color)
            self._halo_color = QColor(c.red(), c.green(), c.blue(), 90)
        self.update()


class FluentModalDrawer(MessageBoxBase):
    """Windows 11 Fluent Modal Checklist Drawer matching FootageRelinkDialog styling.
    Replaces detached DWM floating window with centered elevated card, smoke mask overlay,
    inline Top-Nav stats, and responsive Segoe UI Variable typography."""
    closed = Signal()
    pin_toggled = Signal(bool)

    def __init__(self, title: str, icon_enum: FluentIcon, subtitle: str = "", badge_color: str = None, parent=None):
        self._fallback_parent = None
        target_parent = None
        if parent:
            target_parent = parent.window() if (hasattr(parent, 'window') and parent.window()) else parent
        if target_parent is None:
            target_parent = QApplication.activeWindow()
        if target_parent is None:
            self._fallback_parent = QWidget()
            self._fallback_parent.resize(960, 680)
            target_parent = self._fallback_parent

        super().__init__(parent=target_parent)
        self._title = title
        self._subtitle = subtitle
        self._custom_badge_color = badge_color
        self.is_pinned = False

        self.viewLayout.setSpacing(10)
        self.viewLayout.setContentsMargins(20, 20, 20, 16)

        # 1. Dialog Header: Title & Inline Status & Stats
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.setAlignment(Qt.AlignVCenter)

        self.icon_widget = IconWidget(icon_enum, self.widget)
        self.icon_widget.setFixedSize(16, 16)
        title_row.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        self.header_title = QLabel(title, self.widget)
        self.header_title.setObjectName("DrawerDialogTitle")
        title_row.addWidget(self.header_title, 0, Qt.AlignVCenter)

        # Inline stats container matching Top Nav stats
        self.header_stats = QWidget(self.widget)
        self.header_stats.setObjectName("DrawerHeaderStats")
        hs_layout = QHBoxLayout(self.header_stats)
        hs_layout.setContentsMargins(0, 0, 0, 0)
        hs_layout.setSpacing(8)
        hs_layout.setAlignment(Qt.AlignVCenter)

        self.header_div1 = QFrame(self.header_stats)
        self.header_div1.setFrameShape(QFrame.VLine)
        self.header_div1.setFixedHeight(12)
        hs_layout.addWidget(self.header_div1)

        # Status dot (green if all done, amber/accent if pending)
        self.status_dot = DrawerStatusDot("#34d399", "rgba(52, 211, 153, 0.35)", self.header_stats)
        hs_layout.addWidget(self.status_dot)

        # Count badge: displays "0 / 0 Done"
        self.count_badge = QLabel("0 / 0 Done", self.header_stats)
        self.count_badge.setObjectName("DrawerHeaderCountBadge")
        hs_layout.addWidget(self.count_badge)

        title_row.addWidget(self.header_stats, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        header_layout.addLayout(title_row)

        if self._subtitle:
            self.header_desc = QLabel(self._subtitle, self.widget)
            self.header_desc.setObjectName("DrawerDialogSubtitle")
            self.header_desc.setWordWrap(True)
            header_layout.addWidget(self.header_desc)
        else:
            self.header_desc = None

        self.viewLayout.addLayout(header_layout)

        # Compatibility aliases for callers and tests
        self.title_label = self.header_title
        self.main_layout = self.viewLayout
        self.pin_btn = CaptionPinButton(self)
        self.pin_btn.hide()

        # Dialog Footer
        self.buttonGroup.setFixedHeight(64)
        self.buttonLayout.setContentsMargins(20, 10, 20, 14)
        self.yesButton.setText("Done")
        self.yesButton.setIcon(FluentIcon.ACCEPT)
        self.yesButton.clicked.connect(self.hide)
        self.cancelButton.hide()
        self.setClosableOnMaskClicked(True)

    def windowTitle(self) -> str:
        return self._title

    def setWindowTitle(self, title: str):
        self._title = title
        if hasattr(self, "header_title") and self.header_title:
            self.header_title.setText(title)

    def set_badge_text(self, text: str):
        self.count_badge.setText(text)

    def activateWindow(self):
        super().activateWindow()
        self.raise_()

    def showEvent(self, e):
        self.setGraphicsEffect(None)
        p = self.parentWidget() or self.window()
        if p and p.width() > 0 and p.height() > 0:
            self.resize(p.size())
        QDialog.showEvent(self, e)
        if hasattr(self, "apply_theme"):
            self.apply_theme()

    def done(self, code):
        self.setGraphicsEffect(None)
        if hasattr(self, "widget") and self.widget:
            self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)
        self.closed.emit()

    def close(self) -> bool:
        self.hide()
        self.closed.emit()
        return True

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.closed.emit()


DrawerFrame = FluentModalDrawer
DesktopToolWindow = FluentModalDrawer


class NotesDrawer(DrawerFrame):
    """Windows 11 Fluent Modal Checklist Dialog for Editor Notes.
    Features progress tracking, dropdown filtering (All | Done | Pending),
    jump to script line, copy note, and custom note addition.
    Strictly NO emojis are used in icons, labels, or badges."""
    note_added = Signal(str)
    note_deleted = Signal(str)
    note_checked_changed = Signal(str, bool)
    jump_to_block_requested = Signal(int)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(
            title="Editor Notes",
            icon_enum=FluentIcon.EDIT,
            subtitle="Review editorial feedback, script revisions, and production directives.",
            badge_color=None,
            parent=parent,
        )
        self.notes: List[EditorNote] = []
        self.note_cards: List[QFrame] = []
        self.checked_state: Dict[str, bool] = {}
        self.empty_lbl = None

        target_parent = self.parentWidget() or QApplication.activeWindow()
        parent_w = target_parent.width() if target_parent else 900
        self.widget.setFixedWidth(min(660, max(520, parent_w - 60)))

        # 1. Toolbar: Dropdown Filter (All | Done | Pending) + "Mark all as done" PushButton (NO SEARCH BAR)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.filter_combo = ComboBox(self.widget)
        self.filter_combo.addItems(["All", "Done", "Pending"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(112)
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch(1)

        self.toggle_all_btn = PushButton("Mark all as done", self.widget)
        self.toggle_all_btn.setFixedHeight(28)
        self.toggle_all_btn.clicked.connect(self._toggle_all)
        toolbar.addWidget(self.toggle_all_btn)

        self.main_layout.addLayout(toolbar)

        # 2. Notes List Smooth Scroll Area
        self.scroll = SmoothScrollArea(self.widget)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(340)
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

        # 3. Add Note Input Row (Clean Horizontal Layout)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 4, 0, 0)
        add_row.setSpacing(8)

        self.note_input = LineEdit(self.widget)
        self.note_input.toPlainText = lambda: self.note_input.text()
        self.note_input.setObjectName("DrawerNoteInput")
        self.note_input.setPlaceholderText("Add a custom note to checklist...")
        self.note_input.setFixedHeight(32)
        self.note_input.returnPressed.connect(self._on_add_note)
        add_row.addWidget(self.note_input, 1, Qt.AlignVCenter)

        self.add_btn = PushButton("Add Note", self.widget)
        self.add_btn.setIcon(FluentIcon.ADD)
        self.add_btn.setFixedHeight(32)
        self.add_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_btn.clicked.connect(self._on_add_note)
        add_row.addWidget(self.add_btn, 0, Qt.AlignVCenter)

        self.main_layout.addLayout(add_row)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")
        col = self._custom_badge_color if self._custom_badge_color else accent

        self.header_title.setStyleSheet(f"""
            QLabel#DrawerDialogTitle {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
        """)
        if self.header_desc:
            self.header_desc.setStyleSheet(f"""
                QLabel#DrawerDialogSubtitle {{
                    color: {muted_col};
                    font-size: 11px;
                }}
            """)
        self.header_div1.setStyleSheet(f"color: {border};")
        self.count_badge.setStyleSheet(f"""
            QLabel#DrawerHeaderCountBadge {{
                background-color: rgba({rgb}, 0.14);
                color: {col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                padding: 1px 8px;
                border-radius: 9px;
                border: 1px solid rgba({rgb}, 0.28);
            }}
        """)

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
                LineEdit#DrawerNoteInput {{
                    background-color: {surface};
                    color: {text_col};
                    border: 1px solid {border};
                    border-radius: 6px;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }}
                LineEdit#DrawerNoteInput:focus {{
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
                    font-size: 11px;
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

            cb = CompactCheckBox(card)
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
        checked = sum(1 for note in self.notes if self.checked_state.get(note.id, False))
        if total > 0 and checked == total:
            self.toggle_all_btn.setText("Mark all as not done")
            self.status_dot.set_color("#34d399", "rgba(52, 211, 153, 0.35)")
        else:
            self.toggle_all_btn.setText("Mark all as done")
            self.status_dot.set_color("#fbbf24" if total > 0 else "#94a3b8", "rgba(251, 191, 36, 0.35)" if total > 0 else "rgba(148, 163, 184, 0.25)")
        self.count_badge.setText(f"{checked} / {total} Done")

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
        checked = sum(1 for note in self.notes if self.checked_state.get(note.id, False))
        new_state = (checked < total)

        for card in self.note_cards:
            note_id = card.property("note_id")
            if note_id:
                self.checked_state[note_id] = new_state
                self.note_checked_changed.emit(note_id, new_state)
            card.setProperty("is_checked", new_state)
            self._apply_note_style(card, new_state)
            cb = card.property("checkbox")
            if cb and cb.isChecked() != new_state:
                cb.setChecked(new_state)
        self._update_all_done_button()
        self.apply_filter()

    def _copy_text(self, text: str):
        safe_copy_to_clipboard(text)
        self.copy_feedback_requested.emit("Copied Note!")

    def _on_add_note(self):
        text = self.note_input.text().strip() if hasattr(self.note_input, 'text') else self.note_input.toPlainText().strip()
        if text:
            self.note_added.emit(text)
            self.note_input.clear()


class LinksDrawer(DrawerFrame):
    """Windows 11 Fluent Modal Checklist Dialog for Source Links.
    Features progress tracking, dropdown filtering (All | Done | Pending),
    relink local footage, link metadata inspect, and batch copy.
    Strictly NO emojis are used in icons, labels, or badges."""
    link_checked_changed = Signal(str, bool)
    copy_feedback_requested = Signal(str)
    relink_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(
            title="Source Links",
            icon_enum=FluentIcon.LINK,
            subtitle="Verified external references, media assets, and research footage.",
            badge_color=None,
            parent=parent,
        )
        self.links: List[str] = []
        self.link_cards: List[QFrame] = []
        self.checked_links: Dict[str, bool] = {}
        self.linked_footage: Dict[str, str] = {}
        self.active_filter: str = "all"
        self.empty_lbl = None

        target_parent = self.parentWidget() or QApplication.activeWindow()
        parent_w = target_parent.width() if target_parent else 900
        self.widget.setFixedWidth(min(760, max(560, parent_w - 60)))

        # 1. Toolbar: Dropdown Filter + Relink Local Footage + Mark All Done Button (NO SEARCH BAR)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.filter_combo = ComboBox(self.widget)
        self.filter_combo.addItems(["All", "Done", "Pending"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(112)
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch(1)

        self.relink_btn = ToolButton(FluentIcon.FOLDER, self.widget)
        self.relink_btn.setFixedSize(28, 28)
        self.relink_btn.setToolTip("Relink Local Footage")
        self.relink_btn.clicked.connect(self.relink_requested.emit)
        toolbar.addWidget(self.relink_btn)

        self.toggle_all_btn = PushButton("Mark all as done", self.widget)
        self.toggle_all_btn.setFixedHeight(28)
        self.toggle_all_btn.clicked.connect(self._toggle_all)
        toolbar.addWidget(self.toggle_all_btn)

        self.main_layout.addLayout(toolbar)

        # 2. Links List Smooth Scroll Area
        self.scroll = SmoothScrollArea(self.widget)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(380)
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
        unfurl_engine.metadata_ready.connect(self._on_link_metadata_ready)
        self.apply_theme()

    def _on_link_metadata_ready(self, url: str, meta: LinkMetadata):
        if not meta or meta.loading or meta.is_fallback or not meta.title:
            return
        if not hasattr(self, "link_cards") or not self.link_cards:
            return
        norm = normalize_link(url)
        for card in list(self.link_cards):
            try:
                raw_url = card.property("raw_link_url") or card.property("link_url") or ""
                if raw_url and normalize_link(raw_url) == norm:
                    lbl = card.property("text_label")
                    if isinstance(lbl, ElidedUrlLabel):
                        lbl.set_display_text(meta.title)
                    sb = card.property("stat_badge")
                    if sb is not None:
                        self._update_stat_badge(sb, meta)
            except (RuntimeError, ReferenceError):
                continue

    def _update_stat_badge(self, badge: QLabel, meta: Optional[LinkMetadata]):
        if not meta or meta.loading or meta.is_fallback:
            badge.hide()
            return

        if meta.is_restricted:
            badge.setText("RESTRICTED")
            badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(245, 158, 11, 0.16);
                    color: #fbbf24;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 0px 5px;
                    border-radius: 4px;
                    border: 1px solid rgba(245, 158, 11, 0.32);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }
            """)
            badge.show()
        stat_parts = []
        if meta.resolution:
            stat_parts.append(meta.resolution)
        if meta.duration_display:
            stat_parts.append(meta.duration_display)
        if meta.file_size_display:
            stat_parts.append(meta.file_size_display)

        if stat_parts:
            badge.setText(" • ".join(stat_parts))
            badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(56, 189, 248, 0.12);
                    color: #38bdf8;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 0px 5px;
                    border-radius: 4px;
                    border: 1px solid rgba(56, 189, 248, 0.28);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }
            """)
            badge.show()
        elif meta.title:
            badge.setText("● FETCHED")
            badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(16, 185, 129, 0.12);
                    color: #34d399;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 0px 5px;
                    border-radius: 4px;
                    border: 1px solid rgba(16, 185, 129, 0.28);
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                }
            """)
            badge.show()
        else:
            badge.hide()

    def _show_link_info(self, link: str):
        link_embed_controller.hide_immediately()
        norm = normalize_link(link)
        local_path = self.linked_footage.get(norm) or self.linked_footage.get(link)
        dialog = LinkMetadataDialog(link, parent=self, local_path=local_path)
        dialog.exec()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        rgb = pal.get("accent_rgb", "56, 189, 248")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#27272a")
        col = self._custom_badge_color if self._custom_badge_color else accent

        self.header_title.setStyleSheet(f"""
            QLabel#DrawerDialogTitle {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
        """)
        if self.header_desc:
            self.header_desc.setStyleSheet(f"""
                QLabel#DrawerDialogSubtitle {{
                    color: {muted_col};
                    font-size: 11px;
                }}
            """)
        self.header_div1.setStyleSheet(f"color: {border};")
        self.count_badge.setStyleSheet(f"""
            QLabel#DrawerHeaderCountBadge {{
                background-color: rgba({rgb}, 0.14);
                color: {col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                padding: 1px 8px;
                border-radius: 9px;
                border: 1px solid rgba({rgb}, 0.28);
            }}
        """)

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

    def load_links(
        self,
        links: List[str],
        checked_links: Optional[Dict[str, bool]] = None,
        linked_footage: Optional[Dict[str, str]] = None
    ):
        self.links = sorted(list(set(links)))
        self.checked_links = checked_links.copy() if checked_links else {}
        if linked_footage is not None:
            self.linked_footage = linked_footage.copy()
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

            cb = CompactCheckBox(card)
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
            cached_meta = unfurl_engine.get_cached(link)
            if cached_meta and not cached_meta.loading and not cached_meta.is_fallback and cached_meta.title:
                lbl.set_display_text(cached_meta.title)
            c_layout.addWidget(lbl, 1, Qt.AlignVCenter)

            # Local footage indicator
            norm = normalize_link(link)
            local_path = self.linked_footage.get(norm) or self.linked_footage.get(link)
            if local_path and os.path.isfile(local_path):
                from ...core.media_probe import probe_media_file
                from ...core.unfurl_engine import format_bytes_display, format_duration_display
                probed = probe_media_file(local_path, use_cache=True)
                if probed:
                    if not cached_meta or not cached_meta.duration_display or not cached_meta.resolution:
                        if not cached_meta:
                            cached_meta = LinkMetadata(url=link, title=os.path.basename(local_path), domain="LOCAL", is_fallback=False, loading=False)
                        if not cached_meta.title or cached_meta.is_fallback:
                            cached_meta.title = os.path.basename(local_path)
                        if probed.resolution:
                            cached_meta.resolution = probed.resolution
                        if probed.duration_seconds > 0:
                            cached_meta.duration_seconds = probed.duration_seconds
                            cached_meta.duration_display = probed.duration_display or format_duration_display(probed.duration_seconds)
                        if probed.file_size > 0:
                            cached_meta.file_size_bytes = probed.file_size
                            cached_meta.file_size_display = format_bytes_display(probed.file_size)
                        cached_meta.is_fallback = False
                        cached_meta.loading = False
                        unfurl_engine.store_metadata(link, cached_meta, persist_sqlite=True)
                        if norm != link:
                            unfurl_engine.store_metadata(norm, cached_meta, persist_sqlite=True)
                        if hasattr(lbl, 'set_display_text') and cached_meta.title:
                            lbl.set_display_text(cached_meta.title)

                local_badge = QLabel("LOCAL", card)
                local_badge.setFixedHeight(18)
                tip_text = f"Linked Local File:\n{local_path}"
                if probed and abs(probed.start_time) > 0.001:
                    tip_text += f"\nOffset: +{probed.start_time:.3f}s"
                local_badge.setToolTip(tip_text)
                local_badge.setStyleSheet("""
                    QLabel {
                        background-color: rgba(16, 185, 129, 0.16);
                        color: #34d399;
                        border: 1px solid rgba(16, 185, 129, 0.38);
                        border-radius: 4px;
                        font-size: 9px;
                        font-weight: 700;
                        padding: 0px 5px;
                        font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    }
                """)
                c_layout.addWidget(local_badge, 0, Qt.AlignVCenter)

                if probed and probed.is_vfr:
                    vfr_badge = QLabel("VFR", card)
                    vfr_badge.setFixedHeight(18)
                    vfr_badge.setToolTip(
                        f"Variable Frame Rate ({probed.frame_rate:.2f} FPS avg)\n"
                        "Audio/video timecode will drift in Premiere Pro without CFR transcode or PTS compensation."
                    )
                    vfr_badge.setStyleSheet("""
                        QLabel {
                            background-color: rgba(245, 158, 11, 0.16);
                            color: #fbbf24;
                            border: 1px solid rgba(245, 158, 11, 0.42);
                            border-radius: 4px;
                            font-size: 9px;
                            font-weight: 700;
                            padding: 0px 5px;
                            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                        }
                    """)
                    c_layout.addWidget(vfr_badge, 0, Qt.AlignVCenter)
                elif probed and probed.frame_rate > 0:
                    cfr_badge = QLabel("CFR", card)
                    cfr_badge.setFixedHeight(18)
                    cfr_badge.setToolTip(
                        f"Constant Frame Rate ({probed.frame_rate:.3f} FPS)\n"
                        "Standard broadcast framerate. Clean 1:1 timecode alignment in Premiere Pro."
                    )
                    cfr_badge.setStyleSheet("""
                        QLabel {
                            background-color: rgba(56, 189, 248, 0.12);
                            color: #38bdf8;
                            border: 1px solid rgba(56, 189, 248, 0.32);
                            border-radius: 4px;
                            font-size: 9px;
                            font-weight: 700;
                            padding: 0px 5px;
                            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                        }
                    """)
                    c_layout.addWidget(cfr_badge, 0, Qt.AlignVCenter)

            # Metadata stat badge (shows 1080p, 142 MB, or FETCHED status)
            stat_badge = QLabel(card)
            stat_badge.setFixedHeight(18)
            stat_badge.setAlignment(Qt.AlignCenter)
            self._update_stat_badge(stat_badge, cached_meta)
            c_layout.addWidget(stat_badge, 0, Qt.AlignVCenter)

            # More info button
            info_btn = ToolButton(FluentIcon.INFO, card)
            info_btn.setFixedSize(24, 24)
            info_btn.setToolTip("View Metadata & File Details")
            info_btn.clicked.connect(lambda _, l=link: self._show_link_info(l))
            c_layout.addWidget(info_btn, 0, Qt.AlignVCenter)

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

            card.setProperty("raw_link_url", link)
            card.setProperty("link_url", link.lower())
            card.setProperty("is_checked", is_checked)
            card.setProperty("checkbox", cb)
            card.setProperty("domain_badge", badge)
            card.setProperty("stat_badge", stat_badge)
            card.setProperty("text_label", lbl)
            card.setProperty("info_btn", info_btn)
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
        for b_name in ("info_btn", "copy_btn", "open_btn"):
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
        checked = sum(1 for link in self.links if self.checked_links.get(link, False))
        if total > 0 and checked == total:
            self.toggle_all_btn.setText("Mark all as not done")
            self.status_dot.set_color("#34d399", "rgba(52, 211, 153, 0.35)")
        else:
            self.toggle_all_btn.setText("Mark all as done")
            self.status_dot.set_color("#fbbf24" if total > 0 else "#94a3b8", "rgba(251, 191, 36, 0.35)" if total > 0 else "rgba(148, 163, 184, 0.25)")
        self.count_badge.setText(f"{checked} / {total} Done")

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
        checked = sum(1 for link in self.links if self.checked_links.get(link, False))
        new_state = (checked < total)

        for card in self.link_cards:
            raw_url = card.property("raw_link_url") or card.property("link_url")
            if raw_url:
                self.checked_links[raw_url] = new_state
                self.link_checked_changed.emit(raw_url, new_state)
            card.setProperty("is_checked", new_state)
            self._apply_link_style(card, new_state)
            cb = card.property("checkbox")
            if cb and cb.isChecked() != new_state:
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


