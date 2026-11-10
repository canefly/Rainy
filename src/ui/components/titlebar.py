import os
from PySide6.QtCore import Qt, Signal, QRectF, QPoint
from PySide6.QtGui import QPixmap, QCursor, QPainter, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame, QAbstractButton, QPushButton
from qframelesswindow.titlebar.title_bar_buttons import TitleBarButton
from qfluentwidgets import ToolButton, TransparentToolButton, FluentIcon, RoundMenu, Action

from .widgets import StatusDot, ClickableLabel, CopiedToastIndicator
from ..theme import ThemeManager


class CaptionPinButton(TitleBarButton):
    """Flat 46x32 square pin button that matches the native min/max/close caption buttons
    (same size, square hover/press fill, no rounded box). Inherits TitleBarButton so it seamlessly
    integrates with frameless window dragging and titlebar hit testing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 32)
        self.setCursor(Qt.ArrowCursor)
        self._pinned = False
        self._hover = False

    def set_pinned(self, pinned: bool):
        self._pinned = pinned
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        base = 255 if is_dark else 0

        if self.isDown():
            p.fillRect(self.rect(), QColor(base, base, base, 38))
        elif self._hover:
            p.fillRect(self.rect(), QColor(base, base, base, 26))

        if self._pinned:
            col = QColor(pal.get("accent", "#38bdf8"))
        else:
            col = QColor(base, base, base, 230)
        icon = FluentIcon.PIN.icon(color=col) if hasattr(FluentIcon.PIN, "icon") else None
        if icon is not None:
            icon.paint(p, QRectF(15, 8, 16, 16).toRect())
        p.end()

class TitleBarBrandingWidget(QWidget):
    """Clean flush branding on the top-left corner matching modern IDEs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        # 1. Native Flush Title Text
        self.title_lbl = QLabel("Clickables Tool", self)
        self.title_lbl.setObjectName("TitleBrandingText")
        layout.addWidget(self.title_lbl)

        # 2. Made By Canefly link
        self.author_link = ClickableLabel("Made By Canefly", url="https://www.instagram.com/yow_ph/", parent=self)
        layout.addWidget(self.author_link)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#64748b")
        accent_col = pal.get("accent", "#38bdf8")
        self.title_lbl.setStyleSheet(f"""
            QLabel#TitleBrandingText {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 600;
                color: {text_col};
            }}
        """)
        self.author_link.setStyles(
            default_style=f"color: {muted_col}; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 11px; font-weight: 500;",
            hover_style=f"color: {accent_col}; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 11px; font-weight: 500; text-decoration: underline;"
        )


class TitleBarMenuButton(QPushButton):
    """Subtle transparent top-bar menu button matching VS Code / Office Fluent titlebar."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(26)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        text_col = pal.get("text", "#ffffff")
        hover_bg = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.06)"
        press_bg = "rgba(255, 255, 255, 0.14)" if is_dark else "rgba(0, 0, 0, 0.10)"
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 500;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background: {hover_bg};
            }}
            QPushButton:pressed, QPushButton:checked {{
                background: {press_bg};
            }}
        """)


class TitleBarMenuWidget(QWidget):
    """Inline VS Code style menubar: File | Edit | Export | Help inside the 40px TitleBar (0px added height)."""
    # File actions
    open_doc_requested = Signal()
    open_project_requested = Signal()
    reimport_script_requested = Signal()
    save_project_as_requested = Signal()
    set_footage_requested = Signal()
    open_projects_folder_requested = Signal()
    close_workspace_requested = Signal()
    exit_requested = Signal()

    # Edit actions
    find_requested = Signal()
    mark_all_done_requested = Signal()
    reset_checklists_requested = Signal()

    # Export actions
    export_xml_requested = Signal()
    export_timestamps_requested = Signal()

    # Help actions
    shortcuts_requested = Signal()
    settings_requested = Signal()
    about_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_document = False

        self.menu_layout = QHBoxLayout(self)
        self.menu_layout.setContentsMargins(4, 3, 8, 3)
        self.menu_layout.setSpacing(2)
        self.menu_layout.setAlignment(Qt.AlignVCenter)

        self.file_btn = TitleBarMenuButton("File", self)
        self.edit_btn = TitleBarMenuButton("Edit", self)
        self.export_btn = TitleBarMenuButton("Export", self)
        self.help_btn = TitleBarMenuButton("Help", self)

        self.menu_layout.addWidget(self.file_btn)
        self.menu_layout.addWidget(self.edit_btn)
        self.menu_layout.addWidget(self.export_btn)
        self.menu_layout.addWidget(self.help_btn)

        self.file_btn.clicked.connect(self._show_file_menu)
        self.edit_btn.clicked.connect(self._show_edit_menu)
        self.export_btn.clicked.connect(self._show_export_menu)
        self.help_btn.clicked.connect(self._show_help_menu)

    def set_has_document(self, has_doc: bool):
        self._has_document = has_doc

    def apply_theme(self):
        for btn in (self.file_btn, self.edit_btn, self.export_btn, self.help_btn):
            btn.apply_theme()

    def _show_file_menu(self):
        menu = RoundMenu(parent=self)

        open_doc_act = Action(FluentIcon.DOCUMENT, "Open Document...", self)
        open_doc_act.setShortcut("Ctrl+O")
        open_doc_act.triggered.connect(self.open_doc_requested.emit)
        menu.addAction(open_doc_act)

        open_proj_act = Action(FluentIcon.FOLDER, "Open Project (.clickproj)...", self)
        open_proj_act.triggered.connect(self.open_project_requested.emit)
        menu.addAction(open_proj_act)

        reimport_act = Action(FluentIcon.SYNC, "Re-import / Refresh Script from DOCX...", self)
        reimport_act.setEnabled(self._has_document)
        reimport_act.triggered.connect(self.reimport_script_requested.emit)
        menu.addAction(reimport_act)

        save_proj_act = Action(FluentIcon.SAVE_AS, "Save Project Copy As...", self)
        save_proj_act.setShortcut("Ctrl+Shift+S")
        save_proj_act.setEnabled(self._has_document)
        save_proj_act.triggered.connect(self.save_project_as_requested.emit)
        menu.addAction(save_proj_act)

        menu.addSeparator()

        folder_act = Action(FluentIcon.ZIP_FOLDER, "Open Projects Folder (Explorer)", self)
        folder_act.triggered.connect(self.open_projects_folder_requested.emit)
        menu.addAction(folder_act)

        menu.addSeparator()

        close_ws_act = Action(FluentIcon.CLOSE, "Close Workspace", self)
        close_ws_act.setEnabled(self._has_document)
        close_ws_act.triggered.connect(self.close_workspace_requested.emit)
        menu.addAction(close_ws_act)

        exit_act = Action(FluentIcon.CLOSE, "Exit", self)
        exit_act.setShortcut("Alt+F4")
        exit_act.triggered.connect(self.exit_requested.emit)
        menu.addAction(exit_act)

        pos = self.file_btn.mapToGlobal(QPoint(0, self.file_btn.height() + 2))
        menu.exec(pos, ani=False)

    def _show_edit_menu(self):
        menu = RoundMenu(parent=self)

        find_act = Action(FluentIcon.SEARCH, "Find in Script...", self)
        find_act.setShortcut("Ctrl+F")
        find_act.setEnabled(self._has_document)
        find_act.triggered.connect(self.find_requested.emit)
        menu.addAction(find_act)

        menu.addSeparator()

        mark_all_act = Action(FluentIcon.CHECKBOX, "Mark All Items Done", self)
        mark_all_act.setEnabled(self._has_document)
        mark_all_act.triggered.connect(self.mark_all_done_requested.emit)
        menu.addAction(mark_all_act)

        reset_act = Action(FluentIcon.SYNC, "Reset All Checklists", self)
        reset_act.setEnabled(self._has_document)
        reset_act.triggered.connect(self.reset_checklists_requested.emit)
        menu.addAction(reset_act)

        pos = self.edit_btn.mapToGlobal(QPoint(0, self.edit_btn.height() + 2))
        menu.exec(pos, ani=False)

    def _show_export_menu(self):
        menu = RoundMenu(parent=self)

        xml_act = Action(FluentIcon.SHARE, "Premiere Pro XML Sequence (.xml)...", self)
        xml_act.setShortcut("Ctrl+E")
        xml_act.setEnabled(self._has_document)
        xml_act.triggered.connect(self.export_xml_requested.emit)
        menu.addAction(xml_act)

        ts_act = Action(FluentIcon.DOCUMENT, "Export Timestamps (.txt / .csv)...", self)
        ts_act.setEnabled(self._has_document)
        ts_act.triggered.connect(self.export_timestamps_requested.emit)
        menu.addAction(ts_act)

        pos = self.export_btn.mapToGlobal(QPoint(0, self.export_btn.height() + 2))
        menu.exec(pos, ani=False)

    def _show_help_menu(self):
        menu = RoundMenu(parent=self)

        sc_act = Action(FluentIcon.HELP, "Keyboard Shortcuts", self)
        sc_act.setShortcut("F1")
        sc_act.triggered.connect(self.shortcuts_requested.emit)
        menu.addAction(sc_act)

        pref_act = Action(FluentIcon.SETTING, "Cookie & Auth Settings...", self)
        pref_act.triggered.connect(self.settings_requested.emit)
        menu.addAction(pref_act)

        menu.addSeparator()

        about_act = Action(FluentIcon.INFO, "About Clickables...", self)
        about_act.triggered.connect(self.about_requested.emit)
        menu.addAction(about_act)

        pos = self.help_btn.mapToGlobal(QPoint(0, self.help_btn.height() + 2))
        menu.exec(pos, ani=False)


class TitleBarStatsWidget(QWidget):
    """Contextual header stats on the right side of the title bar without redundant box borders."""
    pin_toggled = Signal(bool)
    render_queue_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_pinned = False
        self._has_document = False
        self._last_editor_name = "CANEEE"
        self._last_is_offline = False
        self._last_time_left = ""
        self._last_total = 0
        self._last_checked = 0
        self._last_seconds = 0.0

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(6)
        self.main_layout.setAlignment(Qt.AlignVCenter)

        # Borderless, clean readout container (no redundant enclosing box)
        self.status_container = QFrame(self)
        self.status_container.setObjectName("TitleBarStatusContainer")
        self.status_container.setStyleSheet("""
            QFrame#TitleBarStatusContainer {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        s_layout = QHBoxLayout(self.status_container)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(6)

        # 1. User Identity: ● CANEEE (no box)
        self.status_dot = StatusDot("#4ade80", "rgba(74, 222, 128, 0.35)", self.status_container)
        s_layout.addWidget(self.status_dot)

        self.user_name_lbl = QLabel("CANEEE", self.status_container)
        s_layout.addWidget(self.user_name_lbl)

        # 2. Workspace Contextual Stats (Hidden on Home Screen)
        self.workspace_stats = QWidget(self.status_container)
        ws_layout = QHBoxLayout(self.workspace_stats)
        ws_layout.setContentsMargins(0, 0, 0, 0)
        ws_layout.setSpacing(6)

        self.div1 = QFrame(self.workspace_stats)
        self.div1.setFrameShape(QFrame.VLine)
        self.div1.setFixedHeight(12)
        ws_layout.addWidget(self.div1)

        self.copied_toast = CopiedToastIndicator(self)
        self.copied_toast.hide()

        self.a_roll_pill = QLabel("A-Roll: 0 / 0 (0%)", self.workspace_stats)
        ws_layout.addWidget(self.a_roll_pill)

        self.div2 = QFrame(self.workspace_stats)
        self.div2.setFrameShape(QFrame.VLine)
        self.div2.setFixedHeight(12)
        ws_layout.addWidget(self.div2)

        self.est_total_pill = QLabel("Est. Total: 0s", self.workspace_stats)
        ws_layout.addWidget(self.est_total_pill)

        s_layout.addWidget(self.workspace_stats)
        self.workspace_stats.hide() # Hidden by default until document is loaded

        # 2b. Background Render Queue Indicator Pill (Hidden when idle)
        self.render_pill = QPushButton("Render 0/0", self.status_container)
        self.render_pill.setObjectName("TitleBarRenderPill")
        self.render_pill.setCursor(QCursor(Qt.PointingHandCursor))
        self.render_pill.setFixedHeight(22)
        self.render_pill.setToolTip("Background Render Queue Active — Click to open Footage Library")
        self.render_pill.clicked.connect(self.render_queue_clicked.emit)
        self.render_pill.hide()
        s_layout.addWidget(self.render_pill)

        self.main_layout.addWidget(self.status_container)

        # 3. Pin Window Button (clean, borderless transparent tool button)
        self.pin_btn = CaptionPinButton(self)
        self.pin_btn.setToolTip("Keep window on top")
        self.pin_btn.clicked.connect(self._toggle_pin)
        self.main_layout.addWidget(self.pin_btn)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        muted_col = pal.get("muted", "#64748b")
        border_col = pal.get("border", "#334155")
        accent_col = pal.get("accent", "#38bdf8")

        self.div1.setStyleSheet(f"color: {border_col};")
        self.div2.setStyleSheet(f"color: {border_col};")
        self.est_total_pill.setStyleSheet(f"""
            color: {muted_col}; 
            font-size: 11px; 
            font-weight: 600; 
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
        """)
        self.set_editor_identity(self._last_editor_name, self._last_is_offline, self._last_time_left)
        self.update_stats(self._last_total, self._last_checked, self._last_seconds)
        self.pin_btn.set_pinned(self.is_pinned)
        if hasattr(self, "render_pill"):
            self.render_pill.setStyleSheet("""
                QPushButton#TitleBarRenderPill {
                    background-color: rgba(56, 189, 248, 0.16);
                    color: #38bdf8;
                    border: 1px solid rgba(56, 189, 248, 0.38);
                    border-radius: 4px;
                    font-size: 10.5px;
                    font-weight: 700;
                    padding: 0px 8px;
                }
                QPushButton#TitleBarRenderPill:hover {
                    background-color: rgba(56, 189, 248, 0.26);
                }
            """)

    def update_render_queue_indicator(self, active: bool, text: str = ""):
        if not hasattr(self, "render_pill"):
            return
        if active and text:
            self.render_pill.setText(text)
            self.render_pill.show()
        else:
            self.render_pill.hide()

    def set_has_document(self, has_doc: bool):
        self._has_document = has_doc
        if has_doc:
            self.workspace_stats.show()
        else:
            self.workspace_stats.hide()

    def set_editor_identity(self, name: str, is_offline: bool = False, time_left: str = ""):
        self._last_editor_name = name
        self._last_is_offline = is_offline
        self._last_time_left = time_left
        is_dark = ThemeManager.is_dark
        self.user_name_lbl.setText(name.upper())
        if is_offline:
            amber = "#f59e0b" if is_dark else "#d97706"
            self.user_name_lbl.setStyleSheet(f"color: {amber}; font-size: 10px; font-weight: 800; letter-spacing: 1px;")
            self.status_dot._dot_color = Qt.yellow
            if time_left:
                self.user_name_lbl.setText(f"{name.upper()} • {time_left}")
        else:
            green = "#4ade80" if is_dark else "#16a34a"
            self.user_name_lbl.setStyleSheet(f"color: {green}; font-size: 10px; font-weight: 800; letter-spacing: 1px;")

    def trigger_copied(self, text: str = "Copied!"):
        # Redundant top notification suppressed per Cane's instruction
        pass

    def update_stats(self, total: int, checked: int, total_seconds: float):
        self._last_total = total
        self._last_checked = checked
        self._last_seconds = total_seconds
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        accent_col = pal.get("accent", "#38bdf8")

        pct = int((checked / total) * 100) if total > 0 else 0
        self.a_roll_pill.setText(f"A-Roll: {checked} / {total} ({pct}%)")

        if total > 0 and checked == total:
            done_col = "#34d399" if is_dark else "#16a34a"
            self.a_roll_pill.setStyleSheet(f"color: {done_col}; font-size: 11px; font-weight: 800; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
        else:
            self.a_roll_pill.setStyleSheet(f"color: {accent_col}; font-size: 11px; font-weight: 700; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")

        mins = int(total_seconds // 60)
        secs = int(total_seconds % 60)
        hrs = mins // 60
        rem_mins = mins % 60
        
        if hrs > 0:
            time_str = f"{hrs}h {rem_mins}m"
        elif mins > 0:
            time_str = f"{mins}m {secs}s"
        else:
            time_str = f"{secs}s"

        self.est_total_pill.setText(f"Est. Total: {time_str}")

    def _toggle_pin(self):
        self.is_pinned = not self.is_pinned
        pal = ThemeManager.get_palette()
        accent_col = pal.get("accent", "#38bdf8")
        self.pin_btn.set_pinned(self.is_pinned)
        self.pin_toggled.emit(self.is_pinned)
