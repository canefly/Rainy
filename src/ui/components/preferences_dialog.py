import os
import webbrowser
from typing import Optional
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QThreadPool
from PySide6.QtGui import QCursor, QColor, QIcon, QPainter, QLinearGradient, QFont
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QFrame, QStackedWidget, QListWidget, QListWidgetItem,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QRadioButton, QButtonGroup, QAbstractItemView, QFileDialog, QTextEdit, QProgressBar,
    QSizePolicy
)
from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, SwitchButton, ComboBox,
    FluentIcon, IconWidget, MessageBox, Dialog, Slider, Theme as FluentTheme,
    MessageBoxBase, SearchLineEdit, InfoBar, InfoBarPosition, SmoothScrollArea
)

from ..theme import ThemeManager, ThemeVariant
from .widgets import MagneticDetentSlider, safe_copy_to_clipboard
from ...core.database import db
from ...core.cookie_manager import cookie_manager
from ...core.unfurl_engine import unfurl_engine
from ...core.logger import (
    get_logger, get_log_file_path, get_log_dir, get_log_size_display,
    read_recent_logs, clear_logs, export_diagnostics_bundle, set_log_level
)
from ...core.media_probe import get_ffprobe_status
from ...core.ffmpeg_encoder import (
    get_ffmpeg_path,
    get_ffmpeg_status,
    detect_available_encoders,
    get_recommended_encoder,
    get_hardware_status_summary,
    get_encoder_catalog,
)


def _set_qss_if_changed(widget: QWidget, qss: str):
    """Avoids expensive Qt unpolish/repolish cycles when stylesheet text is unchanged."""
    if getattr(widget, "_cached_qss", None) != qss:
        widget._cached_qss = qss
        widget.setStyleSheet(qss)


class PrefSidebarFrame(QFrame):
    """GPU-painted sidebar frame styled in authentic Windows 11 neutral gray."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PrefSidebar")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        is_dark = ThemeManager.is_dark
        side_col = QColor(32, 32, 32) if is_dark else QColor(243, 243, 243)
        painter.fillRect(self.rect(), side_col)


class PrefCardFrame(QFrame):
    """GPU-painted card container styled in authentic Windows 11 neutral card gray."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PrefCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        is_dark = ThemeManager.is_dark
        card_col = QColor(43, 43, 43) if is_dark else QColor(255, 255, 255)
        border_col = QColor(255, 255, 255, 22) if is_dark else QColor(0, 0, 0, 18)
        painter.setPen(border_col)
        painter.setBrush(card_col)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)


class UserAvatarWidget(QWidget):
    """Circular profile avatar badge with Windows blue gradient and antialiased initials."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._initials = "E"

    def set_initials(self, initials: str) -> None:
        self._initials = initials or "E"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        is_dark = ThemeManager.is_dark
        accent = QColor("#0284c7") if is_dark else QColor("#0078d4")

        grad = QLinearGradient(0, 0, rect.width(), rect.height())
        grad.setColorAt(0.0, accent)
        grad.setColorAt(1.0, accent.darker(140))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))

        painter.setPen(QColor(255, 255, 255))
        font = QFont("Segoe UI Variable Display", 12, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, self._initials)


def initials_for(name: str) -> str:
    """Up to two capital letters for the avatar: the first letters of the first two words, or one letter for
    a one-word name. Anything that isn't a letter or digit is skipped, so "  *Cane fly*" is still "CF"."""
    words = [w for w in (name or "").split() if any(c.isalnum() for c in w)]
    letters = []
    for w in words[:2]:
        first = next(c for c in w if c.isalnum())
        letters.append(first.upper())
    return "".join(letters) or "E"


class UserProfileCard(QFrame):
    """Windows 11 Settings left-rail profile: the editor's avatar circle and name, nothing else."""
    FALLBACK_NAME = "Editor"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UserProfileCard")
        self.setFixedHeight(60)
        self._name = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(12)

        self.avatar = UserAvatarWidget(self)
        layout.addWidget(self.avatar, 0, Qt.AlignVCenter)

        # The name sits on the avatar's centre line. Names are typed by people, so never read them as rich text.
        self.name_lbl = QLabel(self)
        self.name_lbl.setTextFormat(Qt.PlainText)
        self.name_lbl.setWordWrap(False)
        self.name_lbl.setMinimumWidth(0)
        self.name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self.name_lbl, 1, Qt.AlignVCenter)

        self.apply_theme()
        self.set_name(self._signed_in_name())
        from ...core.license_service import LicenseService
        service = LicenseService.peek()
        if service is not None:
            service.state_changed.connect(self._on_license_state)

    # -- who is signed in ------------------------------------------------------------
    @staticmethod
    def _signed_in_name() -> str:
        from ...core.license_service import LicenseService
        service = LicenseService.peek()
        return (service.snapshot.editor_name if service is not None else "") or ""

    def _on_license_state(self, snap) -> None:
        self.set_name(getattr(snap, "editor_name", "") or "")

    def set_name(self, name: str) -> None:
        self._name = (name or "").strip() or self.FALLBACK_NAME
        self.avatar.set_initials(initials_for(self._name))
        self.name_lbl.setText(self._name)
        self.name_lbl.setToolTip("")
        self._elide()

    def _elide(self) -> None:
        """A long name is cut with an ellipsis (full name in the tooltip) instead of pushing the layout around.
        Only once the card is on screen and laid out: before that its width means nothing."""
        width = self.name_lbl.width()
        if not self.isVisible() or width <= 1:
            return
        shown = self.name_lbl.fontMetrics().elidedText(self._name, Qt.ElideRight, width)
        self.name_lbl.setText(shown)
        self.name_lbl.setToolTip(self._name if shown != self._name else "")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.layout() is not None:
            self.layout().activate()                  # so the label has its real width
        self.name_lbl.setText(self._name)
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.name_lbl.setText(self._name)
        self._elide()

    def apply_theme(self):
        is_dark = ThemeManager.is_dark
        text_col = "#ffffff" if is_dark else "#18181b"
        self.name_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
            f"font-size: 14.5px; font-weight: 700; color: {text_col};"
        )
        self.avatar.update()


class HomeHeroCard(PrefCardFrame):
    """Windows 11 Settings Home top hero status card."""
    def __init__(self, icon: FluentIcon, title: str, subtitle: str, status_text: str, is_accent: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(78)
        self._icon = icon

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(14)

        f_theme = FluentTheme.DARK if ThemeManager.is_dark else FluentTheme.LIGHT
        self.icon_widget = IconWidget(icon.icon(f_theme), self)
        self.icon_widget.setFixedSize(30, 30)
        layout.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setStyleSheet(
            "font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
            "font-size: 14.5px; font-weight: 700; color: #ffffff;"
        )
        col.addWidget(self.title_lbl)

        self.sub_lbl = QLabel(subtitle, self)
        self.sub_lbl.setStyleSheet(
            "font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            "font-size: 12px; color: #a1a1aa;"
        )
        col.addWidget(self.sub_lbl)

        self.status_lbl = QLabel(status_text, self)
        self._is_accent = is_accent
        accent = "#60cdff" if ThemeManager.is_dark else "#0078d4"
        stat_col = accent if is_accent else "#34d399"
        self.status_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            f"font-size: 11.5px; font-weight: 600; color: {stat_col};"
        )
        col.addWidget(self.status_lbl)

        for lbl in (self.title_lbl, self.sub_lbl, self.status_lbl):
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl.setMinimumWidth(0)

        layout.addLayout(col, 1)

    def apply_theme(self):
        is_dark = ThemeManager.is_dark
        text_col = "#ffffff" if is_dark else "#18181b"
        muted = "#a1a1aa" if is_dark else "#52525b"
        accent = "#60cdff" if is_dark else "#0078d4"
        self.title_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
            f"font-size: 14.5px; font-weight: 700; color: {text_col};"
        )
        self.sub_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            f"font-size: 12px; color: {muted};"
        )
        stat_col = accent if self._is_accent else "#34d399"
        self.status_lbl.setStyleSheet(
            f"font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            f"font-size: 11.5px; font-weight: 600; color: {stat_col};"
        )
        f_theme = FluentTheme.DARK if is_dark else FluentTheme.LIGHT
        if hasattr(self, "icon_widget") and self.icon_widget and hasattr(self, "_icon"):
            self.icon_widget.setIcon(self._icon.icon(f_theme))


class LogViewerDialog(MessageBoxBase):
    """Clean Fluent modal dialog displaying recent diagnostic log events with real-time text filtering."""
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.widget.setFixedWidth(740)
        self.viewLayout.setSpacing(10)
        self.viewLayout.setContentsMargins(20, 20, 20, 16)

        # 1. Header
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel("Activity & Diagnostics Log Viewer", self.widget)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        header.addWidget(title_lbl, 1)

        badge = QLabel("LIVE RING BUFFER", self.widget)
        badge.setStyleSheet("""
            background-color: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 8px;
        """)
        header.addWidget(badge, 0)
        self.viewLayout.addLayout(header)

        # 2. Filter Bar
        filter_row = QHBoxLayout()
        self.search_edit = SearchLineEdit(self.widget)
        self.search_edit.setPlaceholderText("Filter log entries (e.g. ProjectManager, ERROR, ffprobe)...")
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)

        refresh_btn = ToolButton(FluentIcon.SYNC, self.widget)
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setToolTip("Reload Logs")
        refresh_btn.clicked.connect(self._reload_logs)
        filter_row.addWidget(refresh_btn, 0)

        copy_btn = PushButton(FluentIcon.COPY, "Copy All", self.widget)
        copy_btn.setFixedHeight(32)
        copy_btn.clicked.connect(self._copy_all)
        filter_row.addWidget(copy_btn, 0)

        self.viewLayout.addLayout(filter_row)

        # 3. Text Viewer
        self.log_text = QTextEdit(self.widget)
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(360)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-family: 'Consolas', 'Cascadia Code', 'Segoe UI Mono', monospace;
                font-size: 11px;
                line-height: 1.4;
                padding: 8px;
            }
        """)
        self.viewLayout.addWidget(self.log_text)

        # Buttons
        self.cancelButton.hide()
        self.yesButton.setText("Close")

        self._raw_lines = []
        self._reload_logs()

    def _reload_logs(self):
        self._raw_lines = read_recent_logs(250)
        self._apply_filter()

    def _apply_filter(self):
        query = self.search_edit.text().strip().lower()
        if not query:
            filtered = self._raw_lines
        else:
            filtered = [line for line in self._raw_lines if query in line.lower()]
        self.log_text.setPlainText("\n".join(filtered))
        sb = self.log_text.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _copy_all(self):
        text = self.log_text.toPlainText()
        if text:
            safe_copy_to_clipboard(text)
            InfoBar.success(
                title="Logs Copied",
                content="Log entries copied to clipboard.",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=2000
            )

    def showEvent(self, e):
        self.setGraphicsEffect(None)
        p = self.parentWidget() or self.window()
        if p and p.width() > 0 and p.height() > 0:
            self.resize(p.size())
        QDialog.showEvent(self, e)

    def done(self, code):
        self.setGraphicsEffect(None)
        if hasattr(self, "widget") and self.widget:
            self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)


class SettingsTitleBar(StandardTitleBar):
    """Windows 11 Settings custom titlebar with an authentic horizontally centered 'Find a setting' search pill."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setObjectName("PrefHeader")

        self.search_box = SearchLineEdit(self)
        self.search_box.setPlaceholderText("Find a setting")
        self.search_box.setClearButtonEnabled(True)

    def canDrag(self, pos):
        if hasattr(self, "search_box") and self.search_box.isVisible() and self.search_box.geometry().contains(pos):
            return False
        return super().canDrag(pos)

    def _update_search_box_geometry(self):
        # Calculate responsive centered width between 320px and 460px
        sb_w = min(460, max(300, self.width() - 400))
        sb_h = 30
        sb_x = (self.width() - sb_w) // 2
        sb_y = (self.height() - sb_h) // 2
        self.search_box.setGeometry(sb_x, sb_y, sb_w, sb_h)
        self.search_box.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_search_box_geometry()


class PreferencesWindow(FramelessWindow):
    """Unified native modeless preferences window following the Windows 11 Fluent Settings architecture."""
    theme_changed = Signal(object, bool) # (ThemeVariant, is_dark)
    document_opened = Signal(str)
    smpte_conforming_changed = Signal(bool)
    mcp_bridge_toggled = Signal(bool)

    def __init__(self, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        self.setObjectName("PreferencesDialog")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("Preferences — Rainy")

        # Responsive spawn dimensions with SQLite persistence
        saved_w = db.get_setting("pref_window_width")
        saved_h = db.get_setting("pref_window_height")
        try:
            w = int(saved_w) if saved_w else 1180
            h = int(saved_h) if saved_h else 700
        except (ValueError, TypeError):
            w, h = 1180, 700
        w = max(1120, min(1920, w))
        h = max(640, min(1080, h))
        self.resize(w, h)
        self.setMinimumSize(1000, 580)

        # Set Window Icon
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # TitleBar setup with centered search bar
        self.setTitleBar(SettingsTitleBar(self))
        if hasattr(self.titleBar, "titleLabel") and self.titleBar.titleLabel:
            self.titleBar.titleLabel.setText("Settings")
            self.titleBar.titleLabel.setStyleSheet(
                "font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 12px; font-weight: 600;"
            )

        self.search_box = self.titleBar.search_box
        self.search_box.textChanged.connect(self._on_search_text_changed)

        # Main Layout (offset by 42px for titlebar)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 42, 0, 0)
        root_layout.setSpacing(0)

        # 2. Body: Left Category Sidebar + Right Content Stack
        body = QWidget(self)
        b_layout = QHBoxLayout(body)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(0)

        # Left Navigation Pane (230px fixed width, Windows 11 rail proportions)
        self.sidebar_frame = PrefSidebarFrame(body)
        self.sidebar_frame.setFixedWidth(230)
        s_layout = QVBoxLayout(self.sidebar_frame)
        s_layout.setContentsMargins(10, 10, 10, 12)
        s_layout.setSpacing(4)

        # Profile card at top of sidebar
        self.user_profile = UserProfileCard(self.sidebar_frame)
        s_layout.addWidget(self.user_profile)
        s_layout.addSpacing(4)

        self.nav_list = QListWidget(self.sidebar_frame)
        self.nav_list.setObjectName("PrefNavList")
        self.nav_list.setIconSize(QSize(20, 20))
        self.nav_list.setSpacing(2)
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._categories = [
            ("Home", FluentIcon.HOME),
            ("Data Vault", FluentIcon.FOLDER),
            ("Media & Transcoding", FluentIcon.MOVIE),
            ("Core Engines", FluentIcon.DEVELOPER_TOOLS),
            ("Personalization", FluentIcon.PALETTE),
            ("Shortcuts", FluentIcon.COMMAND_PROMPT),
            ("Cookies & Accounts", FluentIcon.GLOBE),
            ("AI Models", FluentIcon.ROBOT),
            ("MCP", FluentIcon.CONNECT),
            ("About & Updates", FluentIcon.INFO),
        ]

        f_theme = FluentTheme.DARK if ThemeManager.is_dark else FluentTheme.LIGHT
        for text, icon in self._categories:
            item = QListWidgetItem(icon.icon(f_theme), f"   {text}")
            item.setSizeHint(QSize(210, 42))
            self.nav_list.addItem(item)

        s_layout.addWidget(self.nav_list)
        b_layout.addWidget(self.sidebar_frame)

        # Right Stacked Content Area (28px horizontal, 20px vertical Fluent page margins)
        self.content_frame = QFrame(body)
        self.content_frame.setObjectName("PrefContent")
        c_layout = QVBoxLayout(self.content_frame)
        c_layout.setContentsMargins(28, 16, 28, 16)
        c_layout.setSpacing(0)

        self.stack = QStackedWidget(self.content_frame)
        self.stack.addWidget(self._wrap_in_scroll(self._build_home_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_vault_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_transcode_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_core_engines_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_appearance_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_shortcuts_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_cookies_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_ai_mcp_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_mcp_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._build_about_tab()))

        c_layout.addWidget(self.stack)
        b_layout.addWidget(self.content_frame, 1)
        root_layout.addWidget(body, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(db.flush_settings)

        self.titleBar.raise_()
        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.switch_tab(initial_tab)

    def _wrap_in_scroll(self, widget: QWidget) -> SmoothScrollArea:
        """Wraps any tab page in a smooth-scrolling, transparent area for high refresh rate display."""
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setWidget(widget)
        return scroll

    def switch_tab(self, key_or_index):
        """Switches to the requested category by key string or integer index."""
        tab_map = {
            "home": 0,
            "vault": 1,
            "storage": 1,
            "system": 1,
            "transcode": 2,
            "transcoding": 2,
            "media": 2,
            "footage": 2,
            "encoder": 2,
            "ffmpeg": 2,
            "engine": 3,
            "engines": 3,
            "core_engines": 3,
            "libmpv": 3,
            "mpv": 3,
            "binaries": 3,
            "bin": 3,
            "appearance": 4,
            "personalization": 4,
            "theme": 4,
            "themes": 4,
            "shortcuts": 5,
            "keys": 5,
            "keyboard": 5,
            "cookies": 6,
            "auth": 6,
            "accounts": 6,
            "ai": 7,
            "models": 7,
            "whisper": 7,
            "mcp": 8,
            "bridge": 8,
            "about": 9,
            "updates": 9,
            "license": 9,
        }
        if isinstance(key_or_index, str):
            target = tab_map.get(key_or_index.lower().strip(), 0)
        else:
            try:
                target = int(key_or_index)
            except (ValueError, TypeError):
                target = 0
        target = max(0, min(self.nav_list.count() - 1, target))
        self.nav_list.setCurrentRow(target)

    def _on_search_text_changed(self, text: str):
        """Real-time filter routing for the 'Find a setting' search bar."""
        query = text.strip().lower()
        if not query:
            return

        routes = [
            (["home", "quick", "recommended"], 0),
            (["vault", "storage", "memory", "project", ".rainyproj", "stats", "telemetry", "log", "diagnostics", "probe", "ffprobe", "script", "scripts"], 1),
            (["media", "transcode", "transcoding", "encoder", "nvenc", "vfr", "cfr", "compress", "compression", "bodycam", "interrogation", "framerate", "crf", "interlace", "interlaced", "deinterlace", "decomb", "bwdif", "yadif"], 2),
            (["engine", "engines", "libmpv", "mpv", "ffmpeg", "binaries", "binary", "dependencies", "shinchiro", "btbn", "download engine"], 3),
            (["personalization", "appearance", "theme", "palette", "femboy", "pink", "blue", "green", "gray", "dark", "mode", "orb", "glow", "glass", "mica", "transparency", "dwm"], 4),
            (["shortcut", "shortcuts", "keyboard", "key", "timecode", "double click", "space", "meta", "drag"], 5),
            (["cookie", "cookies", "auth", "session", "firefox", "profile", "cookies.txt", "netscape", "paste", "restricted", "drive", "youtube"], 6),
            (["ai", "whisper", "faster-whisper", "model", "models", "voiceover", "vo", "transcribe", "cuda", "censor", "photo"], 7),
            (["mcp", "bridge", "antigravity", "claude", "codex", "cursor", "gemini", "pipe", "activity", "ai app"], 8),
            (["about", "update", "updates", "version", "cane", "canefly", "discord", "tiktok", "instagram", "legal", "notice"], 9),
        ]

        for keywords, tab_idx in routes:
            for kw in keywords:
                if query in kw or kw in query:
                    self.nav_list.setCurrentRow(tab_idx)
                    return

    def _create_tab_header(self, title_text: str, subtitle_text: str, badge_text: str = None) -> QVBoxLayout:
        """Builds a consistent Windows 11 Fluent Settings page header block."""
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 8)
        header_layout.setSpacing(4)
        is_dark = ThemeManager.is_dark
        text_col = "#ffffff" if is_dark else "#18181b"
        muted_col = "#a1a1aa" if is_dark else "#71717a"

        if badge_text:
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(10)

            title = QLabel(title_text)
            title.setObjectName("PrefTabTitle")
            title.setStyleSheet(
                f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
                f"font-size: 28px; font-weight: 700; color: {text_col};"
            )
            title_row.addWidget(title)

            # Standard Windows 11 Blue badge (NOT theme accent)
            badge_bg = "rgba(96, 205, 255, 0.15)" if is_dark else "rgba(0, 120, 212, 0.12)"
            badge_fg = "#60cdff" if is_dark else "#0078d4"
            badge_bd = "rgba(96, 205, 255, 0.35)" if is_dark else "rgba(0, 120, 212, 0.25)"
            badge = QLabel(badge_text)
            badge.setStyleSheet(f"""
                background-color: {badge_bg};
                color: {badge_fg};
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid {badge_bd};
            """)
            title_row.addWidget(badge, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            header_layout.addLayout(title_row)
        else:
            title = QLabel(title_text)
            title.setObjectName("PrefTabTitle")
            title.setStyleSheet(
                f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; "
                f"font-size: 28px; font-weight: 700; color: {text_col};"
            )
            header_layout.addWidget(title)

        sub = QLabel(subtitle_text)
        sub.setObjectName("PrefTabSubtitle")
        sub.setStyleSheet(
            f"font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; "
            f"font-size: 13.5px; color: {muted_col};"
        )
        sub.setWordWrap(True)
        header_layout.addWidget(sub)
        return header_layout

    # --- 0. Home Overview Tab (Windows 11 Settings Style) ---
    def _build_home_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # 1. Page Title ("Home")
        layout.addLayout(self._create_tab_header("Home", "System status, quick toggles, and common workflows."))

        # 2. Hero Status Cards Row (Windows 11 top row)
        hero_row = QHBoxLayout()
        hero_row.setContentsMargins(0, 0, 0, 0)
        hero_row.setSpacing(10)

        self.hero_device = HomeHeroCard(
            FluentIcon.APPLICATION,
            "Canefly",
            "Rainy",
            "canefly-server: Connected",
            is_accent=True,
            parent=widget
        )
        hero_row.addWidget(self.hero_device, 1)

        self.hero_vault = HomeHeroCard(
            FluentIcon.FOLDER,
            "Project Vault",
            "Memory Files (.rainyproj)",
            "SQLite Cache: Active",
            is_accent=False,
            parent=widget
        )
        hero_row.addWidget(self.hero_vault, 1)

        is_avail, ver_label, _ = get_ffprobe_status()
        ver_display = ver_label
        if "version" in ver_label:
            try:
                parts = ver_label.split("version")
                v_num = parts[1].strip().split("-")[0].strip().split(" ")[0].strip()
                ver_display = f"ffprobe v{v_num}"
            except Exception:
                ver_display = "ffprobe active"
        self.hero_engine = HomeHeroCard(
            FluentIcon.SYNC,
            "Media Engine",
            ver_display,
            "Engine Ready" if is_avail else "Fallback Mode",
            is_accent=False,
            parent=widget
        )
        self.hero_engine.setToolTip(ver_label)
        hero_row.addWidget(self.hero_engine, 1)

        layout.addLayout(hero_row)

        # 3. "Recommended settings" Section
        rec_header = QVBoxLayout()
        rec_header.setContentsMargins(0, 4, 0, 0)
        rec_header.setSpacing(2)
        rec_title = QLabel("Recommended settings")
        rec_title.setObjectName("PrefTabTitle")
        rec_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        rec_sub = QLabel("Recent and commonly used settings")
        rec_sub.setObjectName("PrefTabSubtitle")
        rec_sub.setStyleSheet("font-size: 13px;")
        rec_header.addWidget(rec_title)
        rec_header.addWidget(rec_sub)
        layout.addLayout(rec_header)

        # Grouped Card Frame containing settings rows with dividers
        rec_card = PrefCardFrame(widget)
        rc_layout = QVBoxLayout(rec_card)
        rc_layout.setContentsMargins(16, 10, 16, 10)
        rc_layout.setSpacing(8)

        # Row 1: Color Palette Preset
        r1 = QHBoxLayout()
        r1.setContentsMargins(0, 4, 0, 4)
        r1.setSpacing(12)
        r1_icon = IconWidget(FluentIcon.PALETTE, rec_card)
        r1_icon.setFixedSize(20, 20)
        r1.addWidget(r1_icon, 0, Qt.AlignVCenter)
        r1_col = QVBoxLayout()
        r1_col.setContentsMargins(0, 0, 0, 0)
        r1_col.setSpacing(1)
        r1_title = QLabel("Color Palette Preset")
        r1_title.setObjectName("PrefSectionTitle")
        self.r1_sub = QLabel(f"Current preset: {ThemeManager.current_variant.value.title()}")
        self.r1_sub.setObjectName("PrefCaption")
        self.r1_sub.setWordWrap(True)
        self.r1_sub.setMinimumWidth(0)
        r1_col.addWidget(r1_title)
        r1_col.addWidget(self.r1_sub)
        r1.addLayout(r1_col, 1)

        pal_btn = PushButton("Personalize", rec_card)
        pal_btn.setFixedHeight(32)
        pal_btn.setMinimumWidth(115)
        pal_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        pal_btn.setIcon(FluentIcon.CHEVRON_RIGHT)
        pal_btn.clicked.connect(lambda: self.switch_tab("appearance"))
        r1.addWidget(pal_btn, 0, Qt.AlignVCenter)
        rc_layout.addLayout(r1)

        # Divider 1
        d1 = QFrame(rec_card)
        d1.setFixedHeight(1)
        d1.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d1)

        # Row 2: Dark Mode Switch
        r2 = QHBoxLayout()
        r2.setContentsMargins(0, 4, 0, 4)
        r2.setSpacing(12)
        r2_icon = IconWidget(FluentIcon.CONSTRACT, rec_card)
        r2_icon.setFixedSize(20, 20)
        r2.addWidget(r2_icon, 0, Qt.AlignVCenter)
        r2_col = QVBoxLayout()
        r2_col.setContentsMargins(0, 0, 0, 0)
        r2_col.setSpacing(1)
        r2_title = QLabel("Dark Mode")
        r2_title.setObjectName("PrefSectionTitle")
        r2_sub = QLabel("Use low-glare dark surfaces optimized for video editing environments")
        r2_sub.setObjectName("PrefCaption")
        r2_sub.setWordWrap(True)
        r2_sub.setMinimumWidth(0)
        r2_col.addWidget(r2_title)
        r2_col.addWidget(r2_sub)
        r2.addLayout(r2_col, 1)

        self.home_dark_switch = SwitchButton(rec_card)
        self.home_dark_switch.setChecked(ThemeManager.is_dark)
        self.home_dark_switch.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.home_dark_switch.checkedChanged.connect(self._on_dark_mode_changed)
        r2.addWidget(self.home_dark_switch, 0, Qt.AlignVCenter)
        rc_layout.addLayout(r2)

        # Divider 2
        d2 = QFrame(rec_card)
        d2.setFixedHeight(1)
        d2.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d2)

        # Row 3: Live Activity & Diagnostics Logs
        r3 = QHBoxLayout()
        r3.setContentsMargins(0, 4, 0, 4)
        r3.setSpacing(12)
        r3_icon = IconWidget(FluentIcon.DOCUMENT, rec_card)
        r3_icon.setFixedSize(20, 20)
        r3.addWidget(r3_icon, 0, Qt.AlignVCenter)
        r3_col = QVBoxLayout()
        r3_col.setContentsMargins(0, 0, 0, 0)
        r3_col.setSpacing(1)
        r3_title = QLabel("Activity & Diagnostics Logs")
        r3_title.setObjectName("PrefSectionTitle")
        r3_sub = QLabel("Silently records atomic project saves, SQLite transactions, and system events")
        r3_sub.setObjectName("PrefCaption")
        r3_sub.setWordWrap(True)
        r3_sub.setMinimumWidth(0)
        r3_col.addWidget(r3_title)
        r3_col.addWidget(r3_sub)
        r3.addLayout(r3_col, 1)

        log_btn = PushButton("View Live Log...", rec_card)
        log_btn.setFixedHeight(32)
        log_btn.setMinimumWidth(135)
        log_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        log_btn.setIcon(FluentIcon.DOCUMENT)
        log_btn.clicked.connect(self._view_live_log_clicked)
        r3.addWidget(log_btn, 0, Qt.AlignVCenter)
        rc_layout.addLayout(r3)

        # Divider 3
        d3 = QFrame(rec_card)
        d3.setFixedHeight(1)
        d3.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d3)

        # Row 4: Cookie Auth Manager
        r4 = QHBoxLayout()
        r4.setContentsMargins(0, 4, 0, 4)
        r4.setSpacing(12)
        r4_icon = IconWidget(FluentIcon.GLOBE, rec_card)
        r4_icon.setFixedSize(20, 20)
        r4.addWidget(r4_icon, 0, Qt.AlignVCenter)
        r4_col = QVBoxLayout()
        r4_col.setContentsMargins(0, 0, 0, 0)
        r4_col.setSpacing(1)
        r4_title = QLabel("Cookie Auth Manager")
        r4_title.setObjectName("PrefSectionTitle")
        r4_sub = QLabel("Authenticate private Google Drive folders and restricted media links locally")
        r4_sub.setObjectName("PrefCaption")
        r4_sub.setWordWrap(True)
        r4_sub.setMinimumWidth(0)
        r4_col.addWidget(r4_title)
        r4_col.addWidget(r4_sub)
        r4.addLayout(r4_col, 1)

        cookie_btn = PushButton("Configure", rec_card)
        cookie_btn.setFixedHeight(32)
        cookie_btn.setMinimumWidth(110)
        cookie_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        cookie_btn.setIcon(FluentIcon.CHEVRON_RIGHT)
        cookie_btn.clicked.connect(lambda: self.switch_tab("cookies"))
        r4.addWidget(cookie_btn, 0, Qt.AlignVCenter)
        rc_layout.addLayout(r4)

        layout.addWidget(rec_card)

        # 4. "Timecode Conforming Engine" Group
        tc_header = QVBoxLayout()
        tc_header.setContentsMargins(0, 6, 0, 0)
        tc_header.setSpacing(2)
        tc_title = QLabel("Timecode Conforming Engine")
        tc_title.setObjectName("PrefTabTitle")
        tc_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        tc_sub = QLabel("Eliminate Google Drive player drift and container start_time offsets for Premiere Pro")
        tc_sub.setObjectName("PrefTabSubtitle")
        tc_sub.setStyleSheet("font-size: 13px;")
        tc_sub.setWordWrap(True)
        tc_sub.setMinimumWidth(0)
        tc_header.addWidget(tc_title)
        tc_header.addWidget(tc_sub)
        layout.addLayout(tc_header)

        tc_card = PrefCardFrame(widget)
        tcc_layout = QVBoxLayout(tc_card)
        tcc_layout.setContentsMargins(16, 10, 16, 10)
        tcc_layout.setSpacing(8)

        # Row TC1: Auto-Conform SMPTE on Copy
        tc1 = QHBoxLayout()
        tc1.setContentsMargins(0, 4, 0, 4)
        tc1.setSpacing(12)
        tc1_icon = IconWidget(FluentIcon.SYNC, tc_card)
        tc1_icon.setFixedSize(20, 20)
        tc1.addWidget(tc1_icon, 0, Qt.AlignVCenter)
        tc1_col = QVBoxLayout()
        tc1_col.setContentsMargins(0, 0, 0, 0)
        tc1_col.setSpacing(1)
        tc1_title = QLabel("Auto-Conform to SMPTE Timecode")
        tc1_title.setObjectName("PrefSectionTitle")
        tc1_sub = QLabel("Converts Google Drive PTS to exact frames (HH:MM:SS:FF) and compensates audio multiplex delay on copy")
        tc1_sub.setObjectName("PrefCaption")
        tc1_sub.setWordWrap(True)
        tc1_sub.setMinimumWidth(0)
        tc1_col.addWidget(tc1_title)
        tc1_col.addWidget(tc1_sub)
        tc1.addLayout(tc1_col, 1)

        self.smpte_switch = SwitchButton(tc_card)
        is_auto = db.get_setting("auto_conform_smpte", "true").lower() == "true"
        self.smpte_switch.setChecked(is_auto)
        self.smpte_switch.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.smpte_switch.checkedChanged.connect(self._on_smpte_toggle_changed)
        tc1.addWidget(self.smpte_switch, 0, Qt.AlignVCenter)
        tcc_layout.addLayout(tc1)

        # Divider TC1
        tcd1 = QFrame(tc_card)
        tcd1.setFixedHeight(1)
        tcd1.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        tcc_layout.addWidget(tcd1)

        # Row TC2: Fallback Sequence Framerate
        tc2 = QHBoxLayout()
        tc2.setContentsMargins(0, 4, 0, 4)
        tc2.setSpacing(12)
        tc2_icon = IconWidget(FluentIcon.MOVIE, tc_card)
        tc2_icon.setFixedSize(20, 20)
        tc2.addWidget(tc2_icon, 0, Qt.AlignVCenter)
        tc2_col = QVBoxLayout()
        tc2_col.setContentsMargins(0, 0, 0, 0)
        tc2_col.setSpacing(1)
        tc2_title = QLabel("Fallback Sequence Framerate")
        tc2_title.setObjectName("PrefSectionTitle")
        tc2_sub = QLabel("Sequence timebase for Premiere Pro XML sequence export (Ctrl+E) when media is unlinked")
        tc2_sub.setObjectName("PrefCaption")
        tc2_sub.setWordWrap(True)
        tc2_sub.setMinimumWidth(0)
        tc2_col.addWidget(tc2_title)
        tc2_col.addWidget(tc2_sub)
        tc2.addLayout(tc2_col, 1)

        self.fps_combo = ComboBox(tc_card)
        self.fps_combo.addItems([
            "23.976 fps (Standard)",
            "24.0 fps (Cinema)",
            "25.0 fps (PAL)",
            "29.97 fps (NTSC)",
            "30.0 fps (Web)",
            "59.94 fps (High NTSC)",
            "60.0 fps (High Speed)"
        ])
        current_fps = db.get_setting("timeline_framerate", "23.976")
        fps_map = {
            "23.976": 0, "24": 1, "24.0": 1, "25": 2, "25.0": 2,
            "29.97": 3, "30": 4, "30.0": 4, "59.94": 5, "60": 6, "60.0": 6
        }
        self.fps_combo.setCurrentIndex(fps_map.get(str(current_fps), 0))
        self.fps_combo.currentTextChanged.connect(self._on_fallback_fps_changed)
        self.fps_combo.setFixedWidth(180)
        self.fps_combo.setFixedHeight(32)
        tc2.addWidget(self.fps_combo, 0, Qt.AlignVCenter)
        tcc_layout.addLayout(tc2)

        layout.addWidget(tc_card)

        # 5. "Quick Navigation & Workflows" Group
        qw_header = QVBoxLayout()
        qw_header.setContentsMargins(0, 6, 0, 0)
        qw_header.setSpacing(2)
        qw_title = QLabel("Quick Navigation & Workflows")
        qw_title.setObjectName("PrefTabTitle")
        qw_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        qw_header.addWidget(qw_title)
        layout.addLayout(qw_header)

        qw_card = PrefCardFrame(widget)
        qwc_layout = QVBoxLayout(qw_card)
        qwc_layout.setContentsMargins(16, 10, 16, 10)
        qwc_layout.setSpacing(8)

        # Row Q1: Keyboard Shortcuts
        q1 = QHBoxLayout()
        q1.setContentsMargins(0, 4, 0, 4)
        q1.setSpacing(12)
        q1_icon = IconWidget(FluentIcon.COMMAND_PROMPT, qw_card)
        q1_icon.setFixedSize(20, 20)
        q1.addWidget(q1_icon, 0, Qt.AlignVCenter)
        q1_col = QVBoxLayout()
        q1_col.setContentsMargins(0, 0, 0, 0)
        q1_col.setSpacing(1)
        q1_title = QLabel("NLE Keyboard Shortcuts")
        q1_title.setObjectName("PrefSectionTitle")
        q1_sub = QLabel("Mouse and keyboard bindings for rapid NLE timeline conforming")
        q1_sub.setObjectName("PrefCaption")
        q1_col.addWidget(q1_title)
        q1_col.addWidget(q1_sub)
        q1.addLayout(q1_col, 1)

        sc_btn = PushButton("View Shortcuts", qw_card)
        sc_btn.setFixedHeight(32)
        sc_btn.setIcon(FluentIcon.CHEVRON_RIGHT)
        sc_btn.clicked.connect(lambda: self.switch_tab("shortcuts"))
        q1.addWidget(sc_btn, 0, Qt.AlignVCenter)
        qwc_layout.addLayout(q1)

        # Divider Q1
        qd1 = QFrame(qw_card)
        qd1.setFixedHeight(1)
        qd1.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        qwc_layout.addWidget(qd1)

        # Row Q3: Diagnostics Zip Export
        q3 = QHBoxLayout()
        q3.setContentsMargins(0, 4, 0, 4)
        q3.setSpacing(12)
        q3_icon = IconWidget(FluentIcon.SHARE, qw_card)
        q3_icon.setFixedSize(20, 20)
        q3.addWidget(q3_icon, 0, Qt.AlignVCenter)
        q3_col = QVBoxLayout()
        q3_col.setContentsMargins(0, 0, 0, 0)
        q3_col.setSpacing(1)
        q3_title = QLabel("Export Diagnostics Bundle")
        q3_title.setObjectName("PrefSectionTitle")
        q3_sub = QLabel("Export complete application logs and system session reports (.zip)")
        q3_sub.setObjectName("PrefCaption")
        q3_col.addWidget(q3_title)
        q3_col.addWidget(q3_sub)
        q3.addLayout(q3_col, 1)

        zip_btn = PushButton("Export (.zip)", qw_card)
        zip_btn.setFixedHeight(32)
        zip_btn.setIcon(FluentIcon.SHARE)
        zip_btn.clicked.connect(self._export_diagnostics_clicked)
        q3.addWidget(zip_btn, 0, Qt.AlignVCenter)
        qwc_layout.addLayout(q3)

        layout.addWidget(qw_card)

        layout.addStretch(1)
        return widget

    # --- 1. Personalization & Themes Tab ---
    def _build_appearance_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Appearance & Themes",
            "Select your preferred native color palette and dark or light surface mode."
        ))

        # Card 1: Dark Mode Switch Row (Windows 11 Settings standard horizontal row card)
        mode_box = PrefCardFrame()
        mb_layout = QHBoxLayout(mode_box)
        mb_layout.setContentsMargins(16, 12, 16, 12)
        mb_layout.setSpacing(12)

        mb_label_layout = QVBoxLayout()
        mb_label_layout.setContentsMargins(0, 0, 0, 0)
        mb_label_layout.setSpacing(2)
        mb_title = QLabel("Dark Mode")
        mb_title.setObjectName("PrefSectionTitle")
        mb_sub = QLabel("Use low-glare dark surfaces optimized for video editing environments")
        mb_sub.setObjectName("PrefCaption")
        mb_label_layout.addWidget(mb_title)
        mb_label_layout.addWidget(mb_sub)
        mb_layout.addLayout(mb_label_layout)
        mb_layout.addStretch(1)

        self.dark_switch = SwitchButton()
        self.dark_switch.setChecked(ThemeManager.is_dark)
        self.dark_switch.checkedChanged.connect(self._on_dark_mode_changed)
        mb_layout.addWidget(self.dark_switch, 0, Qt.AlignVCenter)

        layout.addWidget(mode_box)

        # Card 2: Theme Presets Group
        theme_box = PrefCardFrame()
        tb_layout = QVBoxLayout(theme_box)
        tb_layout.setContentsMargins(16, 14, 16, 14)
        tb_layout.setSpacing(10)

        tb_header = QVBoxLayout()
        tb_header.setContentsMargins(0, 0, 0, 2)
        tb_header.setSpacing(2)
        theme_lbl = QLabel("Color Palette Preset")
        theme_lbl.setObjectName("PrefSectionTitle")
        theme_sub = QLabel("Applies accent tokens and surface elevation strokes across all workspaces")
        theme_sub.setObjectName("PrefCaption")
        tb_header.addWidget(theme_lbl)
        tb_header.addWidget(theme_sub)
        tb_layout.addLayout(tb_header)

        self.theme_group = QButtonGroup(self)
        themes = [
            (ThemeVariant.PINK, "Femboy Pink", "#fb7185"),
            (ThemeVariant.BLUE, "Cloudy Blue", "#38bdf8"),
            (ThemeVariant.GREEN, "Matcha Green", "#7ec89b"),
            (ThemeVariant.GRAY, "Adobe Gray", "#4391E0"),
            (ThemeVariant.BLACK, "OLED Black", "#f4f4f5"),
        ]

        self.theme_radios = {}
        radios_grid = QGridLayout()
        radios_grid.setContentsMargins(0, 4, 0, 0)
        radios_grid.setHorizontalSpacing(16)
        radios_grid.setVerticalSpacing(8)

        for i, (var, name, col) in enumerate(themes):
            rb = QRadioButton(name)
            rb.setMinimumHeight(26)
            rb.setCursor(QCursor(Qt.PointingHandCursor))
            rb.setStyleSheet(f"""
                QRadioButton {{
                    font-size: 13px;
                    font-weight: 600;
                    spacing: 8px;
                    padding: 2px 4px;
                }}
                QRadioButton:hover {{
                    color: {col};
                }}
            """)
            self.theme_radios[var] = rb
            if var == ThemeManager.current_variant:
                rb.setChecked(True)
            self.theme_group.addButton(rb, i)
            rb.toggled.connect(lambda checked, v=var: self._on_theme_toggled(checked, v))
            radios_grid.addWidget(rb, i // 2, i % 2)

        tb_layout.addLayout(radios_grid)
        layout.addWidget(theme_box)

        # Card 3: Ambient Mica Orb Glow Intensity (On-track circular magnetic detent nodes)
        orb_box = PrefCardFrame()
        ob_layout = QVBoxLayout(orb_box)
        ob_layout.setContentsMargins(16, 14, 16, 14)
        ob_layout.setSpacing(8)

        ob_header_row = QHBoxLayout()
        ob_header_row.setContentsMargins(0, 0, 0, 0)
        ob_header_row.setSpacing(8)

        ob_title_col = QVBoxLayout()
        ob_title_col.setContentsMargins(0, 0, 0, 0)
        ob_title_col.setSpacing(2)
        ob_title = QLabel("Ambient Mica Orb Glow")
        ob_title.setObjectName("PrefSectionTitle")
        ob_sub = QLabel("Screen-space parallax orbs diffused beneath frosted Mica, dark tint, and 4% noise")
        ob_sub.setObjectName("PrefCaption")
        ob_sub.setWordWrap(True)
        ob_title_col.addWidget(ob_title)
        ob_title_col.addWidget(ob_sub)
        ob_header_row.addLayout(ob_title_col, 1)

        self.orb_value_badge = QLabel()
        self.orb_value_badge.setAlignment(Qt.AlignCenter)
        ob_header_row.addWidget(self.orb_value_badge, 0, Qt.AlignTop)
        ob_layout.addLayout(ob_header_row)

        self._orb_presets = [
            (0, "Off"),
            (25, "Subtle"),
            (55, "Middle"),
            (100, "Max"),
        ]
        self.orb_slider = MagneticDetentSlider(self._orb_presets, snap_radius=3, parent=orb_box)
        self.orb_slider.setValue(ThemeManager.orb_glow_intensity)
        self.orb_slider.valueChanged.connect(self._on_orb_slider_changed)
        self.orb_slider.sliderReleased.connect(lambda _: db.flush_settings())
        ob_layout.addWidget(self.orb_slider)
        layout.addWidget(orb_box)

        # Card 4: Window Glass Transparency (Controls base tint opacity over Windows 11 DWM Acrylic)
        glass_box = PrefCardFrame()
        gb_layout = QVBoxLayout(glass_box)
        gb_layout.setContentsMargins(16, 14, 16, 14)
        gb_layout.setSpacing(8)

        gb_header_row = QHBoxLayout()
        gb_header_row.setContentsMargins(0, 0, 0, 0)
        gb_header_row.setSpacing(8)

        gb_title_col = QVBoxLayout()
        gb_title_col.setContentsMargins(0, 0, 0, 0)
        gb_title_col.setSpacing(2)
        gb_title = QLabel("Window Glass Transparency")
        gb_title.setObjectName("PrefSectionTitle")
        gb_sub = QLabel("Adjusts how much live Windows 11 Desktop Acrylic blur shines through the surface tint")
        gb_sub.setObjectName("PrefCaption")
        gb_sub.setWordWrap(True)
        gb_title_col.addWidget(gb_title)
        gb_title_col.addWidget(gb_sub)
        gb_header_row.addLayout(gb_title_col, 1)

        self.glass_value_badge = QLabel()
        self.glass_value_badge.setAlignment(Qt.AlignCenter)
        gb_header_row.addWidget(self.glass_value_badge, 0, Qt.AlignTop)
        gb_layout.addLayout(gb_header_row)

        self._glass_presets = [
            (0, "Solid"),
            (35, "Subtle"),
            (65, "Balanced"),
            (100, "Max Glass"),
        ]
        self.glass_slider = MagneticDetentSlider(self._glass_presets, snap_radius=3, parent=glass_box)
        self.glass_slider.setValue(ThemeManager.glass_transparency)
        self.glass_slider.valueChanged.connect(self._on_glass_slider_changed)
        self.glass_slider.sliderReleased.connect(lambda _: db.flush_settings())
        gb_layout.addWidget(self.glass_slider)
        layout.addWidget(glass_box)

        self._update_orb_ui_state(ThemeManager.orb_glow_intensity)
        self._update_glass_ui_state(ThemeManager.glass_transparency)
        layout.addStretch(1)
        return widget

    def _set_orb_preset(self, value: int):
        self.orb_slider.blockSignals(True)
        self.orb_slider.setValue(value)
        self.orb_slider.blockSignals(False)
        self._apply_orb_intensity(value)

    def _on_orb_slider_changed(self, raw_value: int):
        snapped = raw_value
        for preset_val, _ in self._orb_presets:
            if abs(raw_value - preset_val) <= 3:
                snapped = preset_val
                break
        if self.orb_slider.value() != snapped:
            self.orb_slider.blockSignals(True)
            self.orb_slider.setValue(snapped)
            self.orb_slider.blockSignals(False)
        self._apply_orb_intensity(snapped)

    def _apply_orb_intensity(self, val: int):
        clamped = max(0, min(100, int(val)))
        dragging = bool(getattr(self, "orb_slider", None) and self.orb_slider.is_dragging)
        db.set_setting("orb_glow_intensity", str(clamped), flush=not dragging)
        if dragging:
            self._save_timer.start(150)
        ThemeManager.set_orb_glow_intensity(clamped)
        self._update_orb_ui_state(clamped)

    def _set_glass_preset(self, value: int):
        self.glass_slider.blockSignals(True)
        self.glass_slider.setValue(value)
        self.glass_slider.blockSignals(False)
        self._apply_glass_transparency(value)

    def _on_glass_slider_changed(self, raw_value: int):
        snapped = raw_value
        for preset_val, _ in self._glass_presets:
            if abs(raw_value - preset_val) <= 3:
                snapped = preset_val
                break
        if self.glass_slider.value() != snapped:
            self.glass_slider.blockSignals(True)
            self.glass_slider.setValue(snapped)
            self.glass_slider.blockSignals(False)
        self._apply_glass_transparency(snapped)

    def _apply_glass_transparency(self, val: int):
        clamped = max(0, min(100, int(val)))
        dragging = bool(getattr(self, "glass_slider", None) and self.glass_slider.is_dragging)
        db.set_setting("glass_transparency", str(clamped), flush=not dragging)
        if dragging:
            self._save_timer.start(150)
        ThemeManager.set_glass_transparency(clamped)
        self._update_glass_ui_state(clamped)

    def _update_orb_ui_state(self, val: int):
        supports_glass = ThemeManager.supports_glass_effects()
        if hasattr(self, "orb_slider") and self.orb_slider:
            if self.orb_slider.isEnabled() != supports_glass:
                self.orb_slider.setEnabled(supports_glass)
            self.orb_slider.update()
        preset_names = {0: "Off", 25: "Subtle", 55: "Middle", 100: "Max"}
        tag = preset_names.get(val, "Custom")
        is_dark = ThemeManager.is_dark
        badge_bg = "rgba(96, 205, 255, 0.15)" if is_dark else "rgba(0, 120, 212, 0.12)"
        badge_fg = "#60cdff" if is_dark else "#0078d4"
        badge_bd = "rgba(96, 205, 255, 0.35)" if is_dark else "rgba(0, 120, 212, 0.25)"
        muted = "#a1a1aa" if is_dark else "#71717a"
        if hasattr(self, "orb_value_badge") and self.orb_value_badge:
            if not supports_glass:
                self.orb_value_badge.setText("Disabled in Pro Solid Themes")
                _set_qss_if_changed(self.orb_value_badge, f"""
                    background-color: rgba(128, 128, 136, 0.12);
                    color: {muted};
                    font-size: 11px;
                    font-weight: 600;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid rgba(128, 128, 136, 0.24);
                """)
            else:
                self.orb_value_badge.setText(f"{val}%  •  {tag}")
                _set_qss_if_changed(self.orb_value_badge, f"""
                    background-color: {badge_bg};
                    color: {badge_fg};
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid {badge_bd};
                """)

    def _update_glass_ui_state(self, val: int):
        supports_glass = ThemeManager.supports_glass_effects()
        if hasattr(self, "glass_slider") and self.glass_slider:
            if self.glass_slider.isEnabled() != supports_glass:
                self.glass_slider.setEnabled(supports_glass)
            self.glass_slider.update()
        preset_names = {0: "Solid", 35: "Subtle", 65: "Balanced", 100: "Max Glass"}
        tag = preset_names.get(val, "Custom")
        is_dark = ThemeManager.is_dark
        badge_bg = "rgba(96, 205, 255, 0.15)" if is_dark else "rgba(0, 120, 212, 0.12)"
        badge_fg = "#60cdff" if is_dark else "#0078d4"
        badge_bd = "rgba(96, 205, 255, 0.35)" if is_dark else "rgba(0, 120, 212, 0.25)"
        muted = "#a1a1aa" if is_dark else "#71717a"
        if hasattr(self, "glass_value_badge") and self.glass_value_badge:
            if not supports_glass:
                self.glass_value_badge.setText("Disabled in Pro Solid Themes")
                _set_qss_if_changed(self.glass_value_badge, f"""
                    background-color: rgba(128, 128, 136, 0.12);
                    color: {muted};
                    font-size: 11px;
                    font-weight: 600;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid rgba(128, 128, 136, 0.24);
                """)
            else:
                self.glass_value_badge.setText(f"{val}%  •  {tag}")
                _set_qss_if_changed(self.glass_value_badge, f"""
                    background-color: {badge_bg};
                    color: {badge_fg};
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid {badge_bd};
                """)

    def paintEvent(self, e):
        painter = QPainter(self)
        is_dark = ThemeManager.is_dark
        # Windows 11 native neutral dark gray (#202020) or light gray (#f3f3f3)
        bg_col = QColor(32, 32, 32) if is_dark else QColor(243, 243, 243)
        painter.fillRect(self.rect(), bg_col)

    def moveEvent(self, e):
        super().moveEvent(e)

    def _save_window_geometry(self):
        if not self.isMaximized() and not self.isMinimized() and self.isVisible():
            if self.width() >= 1000 and self.height() >= 580:
                db.set_setting("pref_window_width", str(self.width()))
                db.set_setting("pref_window_height", str(self.height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "titleBar") and hasattr(self.titleBar, "_update_search_box_geometry"):
            self.titleBar._update_search_box_geometry()
        self._save_window_geometry()

    def _apply_caption_button_theme(self):
        tb = getattr(self, "titleBar", None)
        if tb is None or not all(hasattr(tb, a) for a in ("minBtn", "maxBtn", "closeBtn")):
            return
        is_dark = ThemeManager.is_dark
        base = QColor(255, 255, 255) if is_dark else QColor(24, 24, 27)
        hover_bg = QColor(255, 255, 255, 24) if is_dark else QColor(0, 0, 0, 18)
        press_bg = QColor(255, 255, 255, 42) if is_dark else QColor(0, 0, 0, 32)
        rgba = lambda c: f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"

        for btn in (tb.minBtn, tb.maxBtn):
            _set_qss_if_changed(
                btn,
                f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: {rgba(base)}; "
                f"qproperty-pressedColor: {rgba(base)}; "
                f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
                f"qproperty-hoverBackgroundColor: {rgba(hover_bg)}; "
                f"qproperty-pressedBackgroundColor: {rgba(press_bg)};"
            )
            btn.update()
        _set_qss_if_changed(
            tb.closeBtn,
            f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: rgba(255, 255, 255, 255); "
            f"qproperty-pressedColor: rgba(255, 255, 255, 255); "
            f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
            f"qproperty-hoverBackgroundColor: rgba(232, 17, 35, 255); "
            f"qproperty-pressedBackgroundColor: rgba(241, 112, 122, 255);"
        )
        tb.closeBtn.update()

    def apply_theme(self):
        f_theme = FluentTheme.DARK if ThemeManager.is_dark else FluentTheme.LIGHT
        if hasattr(self, '_categories') and hasattr(self, 'nav_list'):
            for idx, (_, icon) in enumerate(self._categories):
                item = self.nav_list.item(idx)
                if item:
                    item.setIcon(icon.icon(f_theme))

        # Standard Windows 11 Neutral Styling (decoupled from app theme color)
        is_dark = ThemeManager.is_dark
        accent = "#60cdff" if is_dark else "#0078d4"
        text_col = "#ffffff" if is_dark else "#18181b"
        unselected_text = "#f4f4f5" if is_dark else "#18181b"
        muted_col = "#a1a1aa" if is_dark else "#71717a"
        body_col = "#e4e4e7" if is_dark else "#27272a"
        hover_bg = "rgba(255, 255, 255, 0.06)" if is_dark else "rgba(0, 0, 0, 0.05)"
        selected_bg = "rgba(255, 255, 255, 0.09)" if is_dark else "rgba(0, 0, 0, 0.07)"

        nav_qss = f"""
            QListWidget#PrefNavList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#PrefNavList::item {{
                height: 42px;
                padding-left: 12px;
                border-radius: 5px;
                color: {unselected_text};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                font-size: 14px;
                font-weight: 600;
                margin: 2px 0px;
                border: none;
            }}
            QListWidget#PrefNavList::item:hover {{
                background-color: {hover_bg};
                color: {text_col};
                border: none;
            }}
            QListWidget#PrefNavList::item:selected {{
                background-color: {selected_bg};
                color: {text_col};
                font-weight: 600;
                border: none;
                border-left: none;
            }}
        """
        if hasattr(self, "nav_list") and self.nav_list:
            _set_qss_if_changed(self.nav_list, nav_qss)

        # Scoped content area stylesheet for high-contrast legible Windows 11 text
        content_qss = f"""
            QFrame#PrefContent {{
                background: transparent;
                border: none;
            }}
            QLabel#PrefTabTitle {{
                font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
                font-size: 28px;
                font-weight: 700;
                color: {text_col};
            }}
            QLabel#PrefTabSubtitle {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 13.5px;
                font-weight: 400;
                color: {muted_col};
            }}
            QLabel#PrefSectionTitle {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 14.5px;
                font-weight: 600;
                color: {text_col};
            }}
            QLabel#PrefCaption {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 12.5px;
                font-weight: 400;
                color: {muted_col};
            }}
            QLabel#PrefBodyText {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 13px;
                font-weight: 400;
                color: {body_col};
            }}
            PushButton {{
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 12.5px;
                font-weight: 600;
            }}
        """
        if hasattr(self, "content_frame") and self.content_frame:
            _set_qss_if_changed(self.content_frame, content_qss)

        # Update profile card
        if hasattr(self, "user_profile") and self.user_profile:
            self.user_profile.apply_theme()

        # Update search box (authentic Windows 11 neutral gray centered pill)
        if hasattr(self, "search_box") and self.search_box:
            bg_col = "rgba(255, 255, 255, 0.07)" if is_dark else "rgba(0, 0, 0, 0.04)"
            border_col = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(0, 0, 0, 0.08)"
            sb_qss = f"""
                SearchLineEdit {{
                    background-color: {bg_col};
                    color: {text_col};
                    border: 1px solid {border_col};
                    border-radius: 6px;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 13px;
                    padding-left: 10px;
                }}
                SearchLineEdit:focus {{
                    border: 1px solid {accent};
                    background-color: {"rgba(255, 255, 255, 0.10)" if is_dark else "rgba(0, 0, 0, 0.06)"};
                }}
            """
            _set_qss_if_changed(self.search_box, sb_qss)

        # Ensure title bar has NO bottom border / line
        if hasattr(self, "titleBar") and self.titleBar:
            _set_qss_if_changed(self.titleBar, "QWidget#PrefHeader { background-color: transparent; border: none; border-bottom: none; }")
            if hasattr(self.titleBar, "titleLabel") and self.titleBar.titleLabel:
                self.titleBar.titleLabel.setStyleSheet(
                    f"font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 12.5px; font-weight: 600; color: {text_col};"
                )

        # Update hero cards
        for attr in ("hero_device", "hero_vault", "hero_engine"):
            if hasattr(self, attr):
                getattr(self, attr).apply_theme()

        # Standardize switch toggle indicators to Windows 11 Blue
        win_blue = QColor("#0078d4")
        win_blue_dark = QColor("#0078d4")
        for sw_attr in ("home_dark_switch", "dark_switch"):
            sw = getattr(self, sw_attr, None)
            if sw:
                sw.setCheckedIndicatorColor(win_blue, win_blue_dark)
                sw.setTextColor(QColor(text_col), QColor(text_col))

        # Update home tab preset label
        if hasattr(self, "r1_sub") and self.r1_sub:
            self.r1_sub.setText(f"Current preset: {ThemeManager.current_variant.value.title()}")

        if getattr(self, "mcp_page", None) is not None:
            self.mcp_page.apply_theme()

        self._apply_caption_button_theme()
        self._update_orb_ui_state(ThemeManager.orb_glow_intensity)
        self._update_glass_ui_state(ThemeManager.glass_transparency)
        if hasattr(self, '_update_cookie_ui_state'):
            self._update_cookie_ui_state()
        if hasattr(self, "titleBar") and hasattr(self.titleBar, "_update_search_box_geometry"):
            self.titleBar._update_search_box_geometry()
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if hasattr(self, 'theme_radios') and ThemeManager.current_variant in self.theme_radios:
            self.theme_group.blockSignals(True)
            self.theme_radios[ThemeManager.current_variant].setChecked(True)
            self.theme_group.blockSignals(False)
        if hasattr(self, 'dark_switch') and self.dark_switch:
            self.dark_switch.blockSignals(True)
            self.dark_switch.setChecked(ThemeManager.is_dark)
            self.dark_switch.blockSignals(False)
        if hasattr(self, 'home_dark_switch') and self.home_dark_switch:
            self.home_dark_switch.blockSignals(True)
            self.home_dark_switch.setChecked(ThemeManager.is_dark)
            self.home_dark_switch.blockSignals(False)
        if hasattr(self, 'orb_slider') and self.orb_slider:
            self.orb_slider.blockSignals(True)
            self.orb_slider.setValue(ThemeManager.orb_glow_intensity)
            self.orb_slider.blockSignals(False)
        if hasattr(self, 'glass_slider') and self.glass_slider:
            self.glass_slider.blockSignals(True)
            self.glass_slider.setValue(ThemeManager.glass_transparency)
            self.glass_slider.blockSignals(False)
        if hasattr(self, "titleBar") and hasattr(self.titleBar, "_update_search_box_geometry"):
            self.titleBar._update_search_box_geometry()
        self.apply_theme()

    def _on_theme_toggled(self, checked: bool, variant: ThemeVariant):
        if checked:
            ThemeManager.current_variant = variant
            db.set_setting("theme_variant", variant.value)
            self.theme_changed.emit(variant, ThemeManager.is_dark)
            ThemeManager.enable_window_backdrop(self)
            self.apply_theme()

    def _on_dark_mode_changed(self, is_dark: bool):
        ThemeManager.is_dark = is_dark
        db.set_setting("theme_dark", "true" if is_dark else "false")
        if hasattr(self, "dark_switch") and self.dark_switch:
            self.dark_switch.blockSignals(True)
            self.dark_switch.setChecked(is_dark)
            self.dark_switch.blockSignals(False)
        if hasattr(self, "home_dark_switch") and self.home_dark_switch:
            self.home_dark_switch.blockSignals(True)
            self.home_dark_switch.setChecked(is_dark)
            self.home_dark_switch.blockSignals(False)
        self.theme_changed.emit(ThemeManager.current_variant, is_dark)
        ThemeManager.enable_window_backdrop(self)
        self.apply_theme()

    def _on_smpte_toggle_changed(self, checked: bool):
        db.set_setting("auto_conform_smpte", "true" if checked else "false")
        self.smpte_conforming_changed.emit(checked)

    def _on_fallback_fps_changed(self, text: str):
        fps_val = text.split(" ")[0].strip()
        db.set_setting("timeline_framerate", fps_val)

    # --- 2. Data Vault & Storage Tab ---
    def _build_vault_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Data Vault & Storage",
            "Monitor lifetime parsing telemetry and manage cached project scripts."
        ))

        # Lifetime Stats Summary Card with inline Reset button
        stats_box = PrefCardFrame()
        sb_layout = QHBoxLayout(stats_box)
        sb_layout.setContentsMargins(16, 12, 16, 12)
        sb_layout.setSpacing(24)
        stats = db.get_stats()

        def _format_time(sec: float) -> str:
            m = int(sec // 60)
            s = int(sec % 60)
            return f"{m}m {s}s"

        items = [
            ("Parse Time", _format_time(getattr(stats, "lifetime_seconds", 0))),
            ("Total Cuts", str(getattr(stats, "total_aroll_count", 0))),
            ("Duration Logged", _format_time(getattr(stats, "total_duration_logged", 0))),
        ]
        for label, val in items:
            v_box = QVBoxLayout()
            v_box.setContentsMargins(0, 0, 0, 0)
            v_box.setSpacing(2)
            l_lbl = QLabel(label)
            l_lbl.setObjectName("PrefCaption")
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(
                "color: #38bdf8; font-size: 15px; font-weight: 700; "
                "font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;"
            )
            v_box.addWidget(l_lbl)
            v_box.addWidget(v_lbl)
            sb_layout.addLayout(v_box)

        sb_layout.addStretch(1)

        reset_btn = PushButton("Reset Statistics", self)
        reset_btn.setFixedHeight(30)
        reset_btn.clicked.connect(self._reset_stats_clicked)
        sb_layout.addWidget(reset_btn, 0, Qt.AlignVCenter)

        layout.addWidget(stats_box)

        # Managed Projects Memory Save Card
        from ...core.project_manager import PROJECTS_DIR
        proj_box = PrefCardFrame()
        pb_layout = QHBoxLayout(proj_box)
        pb_layout.setContentsMargins(16, 12, 16, 12)
        pb_layout.setSpacing(16)

        pb_text_col = QVBoxLayout()
        pb_text_col.setContentsMargins(0, 0, 0, 0)
        pb_text_col.setSpacing(3)

        pb_title = QLabel("Memory File Save (.rainyproj)")
        pb_title.setObjectName("PrefSectionTitle")
        pb_text_col.addWidget(pb_title)

        pb_desc = QLabel(
            "Rainy automatically saves your checklist progress, custom notes, and linked footage paths "
            f"directly to isolated project files in:\n{PROJECTS_DIR}"
        )
        pb_desc.setObjectName("PrefCaption")
        pb_desc.setWordWrap(True)
        pb_text_col.addWidget(pb_desc)

        pb_layout.addLayout(pb_text_col, 1)

        open_folder_btn = PushButton("Open Projects Folder", self)
        open_folder_btn.setFixedHeight(32)
        open_folder_btn.clicked.connect(self._open_projects_folder_clicked)
        pb_layout.addWidget(open_folder_btn, 0, Qt.AlignVCenter)

        layout.addWidget(proj_box)

        # --- Activity & Diagnostics Logs Card ---
        log_box = PrefCardFrame()
        lb_layout = QVBoxLayout(log_box)
        lb_layout.setContentsMargins(16, 14, 16, 14)
        lb_layout.setSpacing(10)

        lb_header = QHBoxLayout()
        lb_title = QLabel("Activity & Diagnostics Logs")
        lb_title.setObjectName("PrefSectionTitle")
        lb_header.addWidget(lb_title, 1)

        lb_badge = QLabel("ACTIVE • ROTATING")
        lb_badge.setStyleSheet("""
            background-color: rgba(52, 211, 153, 0.15);
            color: #34d399;
            border: 1px solid rgba(52, 211, 153, 0.35);
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 8px;
        """)
        lb_header.addWidget(lb_badge, 0)
        lb_layout.addLayout(lb_header)

        self.log_desc_lbl = QLabel(
            "Silently records atomic project saves, SQLite transactions, media probes, and system events to a safe 5 MB ring buffer.\n"
            f"Location: {get_log_file_path()} ({get_log_size_display()})"
        )
        self.log_desc_lbl.setObjectName("PrefCaption")
        self.log_desc_lbl.setWordWrap(True)
        lb_layout.addWidget(self.log_desc_lbl)

        # Log Level Selection & Buttons Row
        lb_actions = QHBoxLayout()
        lb_actions.setContentsMargins(0, 4, 0, 0)
        lb_actions.setSpacing(8)

        lvl_lbl = QLabel("Level:")
        lvl_lbl.setObjectName("PrefCaption")
        lb_actions.addWidget(lvl_lbl, 0, Qt.AlignVCenter)

        self.log_level_combo = ComboBox(self)
        self.log_level_combo.addItems(["INFO (Standard)", "DEBUG (Verbose)", "WARNING", "ERROR"])
        current_lvl = db.get_setting("log_level", "INFO")
        for idx, item_txt in enumerate(["INFO", "DEBUG", "WARNING", "ERROR"]):
            if item_txt in current_lvl:
                self.log_level_combo.setCurrentIndex(idx)
                break
        self.log_level_combo.currentTextChanged.connect(self._on_log_level_changed)
        self.log_level_combo.setFixedWidth(145)
        lb_actions.addWidget(self.log_level_combo, 0, Qt.AlignVCenter)

        lb_actions.addStretch(1)

        open_logs_btn = PushButton(FluentIcon.FOLDER, "Open Folder", self)
        open_logs_btn.setFixedHeight(30)
        open_logs_btn.clicked.connect(self._open_logs_folder_clicked)
        lb_actions.addWidget(open_logs_btn)

        view_log_btn = PushButton(FluentIcon.DOCUMENT, "View Live Log...", self)
        view_log_btn.setFixedHeight(30)
        view_log_btn.clicked.connect(self._view_live_log_clicked)
        lb_actions.addWidget(view_log_btn)

        export_diag_btn = PushButton(FluentIcon.SHARE, "Export (.zip)", self)
        export_diag_btn.setFixedHeight(30)
        export_diag_btn.setToolTip("Export logs diagnostics bundle to Desktop")
        export_diag_btn.clicked.connect(self._export_diagnostics_clicked)
        lb_actions.addWidget(export_diag_btn)

        clear_log_btn = PushButton("Clear", self)
        clear_log_btn.setFixedHeight(30)
        clear_log_btn.clicked.connect(self._clear_logs_clicked)
        lb_actions.addWidget(clear_log_btn)

        lb_layout.addLayout(lb_actions)
        layout.addWidget(log_box)

        # --- Integrated Media Probe Engine Card ---
        probe_box = PrefCardFrame()
        prb_layout = QVBoxLayout(probe_box)
        prb_layout.setContentsMargins(16, 14, 16, 14)
        prb_layout.setSpacing(10)

        prb_header = QHBoxLayout()
        prb_title = QLabel("Integrated Media Probe Engine (FFmpeg / ffprobe)")
        prb_title.setObjectName("PrefSectionTitle")
        prb_header.addWidget(prb_title, 1)

        is_avail, ver_label, bin_path = get_ffprobe_status()
        prb_badge = QLabel("READY" if is_avail else "FALLBACK")
        badge_style = (
            "background-color: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.35);"
            if is_avail else
            "background-color: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35);"
        )
        prb_badge.setStyleSheet(f"""
            {badge_style}
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 8px;
        """)
        prb_header.addWidget(prb_badge, 0)
        prb_layout.addLayout(prb_header)

        cached_count = db.get_probed_media_count()
        path_desc = bin_path if bin_path else "Pure-Python Header Inspector"
        self.prb_desc_lbl = QLabel(
            f"Engine: {ver_label}\n"
            f"Binary: {path_desc}\n"
            f"Cache: {cached_count} media files cached in SQLite"
        )
        self.prb_desc_lbl.setObjectName("PrefCaption")
        self.prb_desc_lbl.setWordWrap(True)
        prb_layout.addWidget(self.prb_desc_lbl)

        prb_actions = QHBoxLayout()
        prb_actions.setContentsMargins(0, 4, 0, 0)
        prb_actions.addStretch(1)

        clear_prb_btn = PushButton("Clear Media Cache", self)
        clear_prb_btn.setFixedHeight(30)
        clear_prb_btn.clicked.connect(self._clear_probe_cache_clicked)
        prb_actions.addWidget(clear_prb_btn)

        prb_layout.addLayout(prb_actions)
        layout.addWidget(probe_box)

        # Active Scripts Section Header
        table_lbl = QLabel("Cached Scripts")
        table_lbl.setObjectName("PrefSectionTitle")
        layout.addWidget(table_lbl)

        self.scripts_table = QTableWidget(0, 3)
        self.scripts_table.setHorizontalHeaderLabels(["Script Name", "Last Opened", "Open"])
        self.scripts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.scripts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.scripts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.scripts_table.setColumnWidth(2, 68)
        self.scripts_table.verticalHeader().setVisible(False)
        self.scripts_table.verticalHeader().setDefaultSectionSize(36)
        self.scripts_table.setShowGrid(False)
        self.scripts_table.setFocusPolicy(Qt.NoFocus)
        self.scripts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.scripts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scripts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._populate_scripts_table()
        self.scripts_table.setFixedHeight(180)
        layout.addWidget(self.scripts_table, 0)

        layout.addStretch(1)
        return widget

    def _populate_scripts_table(self):
        recent_files = db.get_recent_files(20)
        self.scripts_table.setRowCount(len(recent_files))
        for row, rf in enumerate(recent_files):
            name_item = QTableWidgetItem(rf.get("name", "Untitled"))
            name_item.setToolTip(rf.get("path", ""))
            self.scripts_table.setItem(row, 0, name_item)

            date_str = rf.get("last_opened", "")[:10]
            date_item = QTableWidgetItem(date_str)
            date_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            self.scripts_table.setItem(row, 1, date_item)

            cell_wrap = QWidget()
            cell_layout = QHBoxLayout(cell_wrap)
            cell_layout.setContentsMargins(4, 2, 4, 2)
            cell_layout.setAlignment(Qt.AlignCenter)
            open_btn = ToolButton(FluentIcon.FOLDER, cell_wrap)
            open_btn.setFixedSize(26, 26)
            open_btn.setToolTip("Open script")
            open_btn.clicked.connect(lambda _, p=rf.get("path", ""): self._on_script_open_clicked(p))
            cell_layout.addWidget(open_btn)
            self.scripts_table.setCellWidget(row, 2, cell_wrap)

    def _on_script_open_clicked(self, path: str):
        if path and os.path.exists(path):
            self.close()
            self.document_opened.emit(path)

    def _open_projects_folder_clicked(self):
        from ...core.project_manager import PROJECTS_DIR
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        import subprocess
        if os.name == "nt":
            subprocess.Popen(["explorer", os.path.normpath(PROJECTS_DIR)])
        else:
            import webbrowser
            webbrowser.open(PROJECTS_DIR)

    def _reset_stats_clicked(self):
        w = Dialog("Reset Statistics", "Are you sure you want to reset lifetime parse statistics? This cannot be undone.", self)
        if w.exec():
            db.reset_stats()
            self.nav_list.setCurrentRow(1)

    def _on_log_level_changed(self, text: str):
        level_key = text.split()[0].strip()
        set_log_level(level_key)
        db.set_setting("log_level", level_key)

    def _open_logs_folder_clicked(self):
        log_dir = get_log_dir()
        if os.name == "nt":
            import subprocess
            subprocess.Popen(["explorer", os.path.normpath(log_dir)])
        else:
            import webbrowser
            webbrowser.open(log_dir)

    def _view_live_log_clicked(self):
        dlg = LogViewerDialog(self)
        dlg.exec()

    def _export_diagnostics_clicked(self):
        try:
            zip_path = export_diagnostics_bundle()
            InfoBar.success(
                title="Diagnostics Exported",
                content=f"Saved log bundle to:\n{zip_path}",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )
        except Exception as e:
            InfoBar.error(
                title="Export Failed",
                content=str(e),
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )

    def _clear_logs_clicked(self):
        box = Dialog("Clear Logs", "Are you sure you want to clear active log files? This cannot be undone.", self)
        if box.exec():
            clear_logs()
            if hasattr(self, "log_desc_lbl"):
                self.log_desc_lbl.setText(
                    "Silently records atomic project saves, SQLite transactions, media probes, and system events to a safe 5 MB ring buffer.\n"
                    f"Location: {get_log_file_path()} ({get_log_size_display()})"
                )
            InfoBar.success(
                title="Logs Cleared",
                content="Log files have been truncated.",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500
            )

    def _clear_probe_cache_clicked(self):
        count = db.clear_probed_media()
        is_avail, ver_label, bin_path = get_ffprobe_status()
        path_desc = bin_path if bin_path else "Pure-Python Header Inspector"
        if hasattr(self, "prb_desc_lbl"):
            self.prb_desc_lbl.setText(
                f"Engine: {ver_label}\n"
                f"Binary: {path_desc}\n"
                f"Cache: 0 media files cached in SQLite"
            )
        InfoBar.success(
            title="Cache Cleared",
            content=f"Removed {count} cached media probe records from SQLite.",
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=2500
        )

    # --- Media & Transcoding Tab ---
    def _build_transcode_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        is_avail, ver_label, bin_path = get_ffmpeg_status()
        badge_txt = "FFmpeg Ready" if is_avail else "FFmpeg Missing"
        layout.addLayout(self._create_tab_header(
            "Media & Transcoding",
            "High-throughput FFmpeg re-encoder for VFR sync correction and bodycam footage compression.",
            badge_text=badge_txt
        ))

        # Card 1: Engine Status & Hardware Probes
        engine_card = PrefCardFrame(widget)
        ec_layout = QVBoxLayout(engine_card)
        ec_layout.setContentsMargins(16, 12, 16, 12)
        ec_layout.setSpacing(10)

        r_eng = QHBoxLayout()
        r_eng.setSpacing(12)
        eng_icon = IconWidget(FluentIcon.MOVIE, engine_card)
        eng_icon.setFixedSize(20, 20)
        r_eng.addWidget(eng_icon, 0, Qt.AlignVCenter)

        ec_col = QVBoxLayout()
        ec_col.setSpacing(2)
        ec_title = QLabel("FFmpeg Transcode Engine")
        ec_title.setObjectName("PrefSectionTitle")
        ec_sub = QLabel(f"Version: {ver_label}\nBinary: {bin_path or 'Not installed on system PATH'}")
        ec_sub.setObjectName("PrefCaption")
        ec_col.addWidget(ec_title)
        ec_col.addWidget(ec_sub)
        r_eng.addLayout(ec_col, 1)

        # Hardware Probe Pill
        status_headline, best_enc = get_hardware_status_summary()
        hw_pill = QLabel(f"Hardware: {status_headline}")
        is_dark = ThemeManager.is_dark
        badge_bg = "rgba(96, 205, 255, 0.15)" if is_dark else "rgba(0, 120, 212, 0.12)"
        badge_fg = "#60cdff" if is_dark else "#0078d4"
        badge_bd = "rgba(96, 205, 255, 0.35)" if is_dark else "rgba(0, 120, 212, 0.25)"
        hw_pill.setStyleSheet(f"""
            background-color: {badge_bg};
            color: {badge_fg};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px solid {badge_bd};
        """)
        r_eng.addWidget(hw_pill, 0, Qt.AlignVCenter)
        ec_layout.addLayout(r_eng)
        layout.addWidget(engine_card)

        # Card 2: Global Transcode & Compression Defaults
        rules_card = PrefCardFrame(widget)
        rc_layout = QVBoxLayout(rules_card)
        rc_layout.setContentsMargins(16, 10, 16, 10)
        rc_layout.setSpacing(8)

        # Row 1: Hardware Video Encoder
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        ic1 = IconWidget(FluentIcon.DEVELOPER_TOOLS, rules_card)
        ic1.setFixedSize(20, 20)
        row1.addWidget(ic1, 0, Qt.AlignVCenter)
        col1 = QVBoxLayout()
        col1.setSpacing(1)
        t1 = QLabel("Default Video Encoder")
        t1.setObjectName("PrefSectionTitle")
        s1 = QLabel("Hardware-accelerated codec used for high-speed rendering and timeline optimization")
        s1.setObjectName("PrefCaption")
        col1.addWidget(t1)
        col1.addWidget(s1)
        row1.addLayout(col1, 1)

        self.enc_combo = ComboBox(rules_card)
        best_display = {
            "h264_nvenc": "NVIDIA NVENC H.264",
            "hevc_nvenc": "NVIDIA NVENC HEVC",
            "h264_amf": "AMD AMF H.264",
            "hevc_amf": "AMD AMF HEVC",
            "h264_qsv": "Intel QuickSync",
            "libx264": "CPU Software",
        }.get(best_enc, "CPU Software")

        self.enc_combo.addItem(f"Auto-Detect Hardware ({best_display})", userData="auto")
        for enc_id, name, is_avail, req in get_encoder_catalog():
            tag = "Ready" if is_avail else req
            self.enc_combo.addItem(f"{name} [{tag}]", userData=enc_id)

        cur_enc = db.get_setting("transcode_encoder", "auto")
        idx = self.enc_combo.findData(cur_enc)
        if idx >= 0:
            self.enc_combo.setCurrentIndex(idx)
        self.enc_combo.currentIndexChanged.connect(self._on_transcode_encoder_changed)
        self.enc_combo.setFixedWidth(300)
        self.enc_combo.setFixedHeight(32)
        row1.addWidget(self.enc_combo, 0, Qt.AlignVCenter)
        rc_layout.addLayout(row1)

        # Divider
        d1 = QFrame(rules_card)
        d1.setFixedHeight(1)
        d1.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d1)

        # Row 2: Default Quality & Compression Preset
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        ic2 = IconWidget(FluentIcon.TILES, rules_card)
        ic2.setFixedSize(20, 20)
        row2.addWidget(ic2, 0, Qt.AlignVCenter)
        col2 = QVBoxLayout()
        col2.setSpacing(1)
        t2 = QLabel("Default Quality & Compression")
        t2.setObjectName("PrefSectionTitle")
        s2 = QLabel("Balancing visual fidelity against disk storage for bodycams and interrogation clips")
        s2.setObjectName("PrefCaption")
        col2.addWidget(t2)
        col2.addWidget(s2)
        row2.addLayout(col2, 1)

        self.crf_combo = ComboBox(rules_card)
        self.crf_combo.addItem("Near-Lossless (CRF 18) - Mobile & OBS VFR Fix", userData="18")
        self.crf_combo.addItem("High Quality (CRF 20)", userData="20")
        self.crf_combo.addItem("Footage Compressor (CRF 23) - Bodycams & Interrogations", userData="23")
        self.crf_combo.addItem("Aggressive Compression (CRF 26)", userData="26")
        self.crf_combo.addItem("Archival Master (CRF 14)", userData="14")

        cur_crf = db.get_setting("transcode_crf", "18")
        idx_crf = self.crf_combo.findData(cur_crf)
        if idx_crf >= 0:
            self.crf_combo.setCurrentIndex(idx_crf)
        self.crf_combo.currentIndexChanged.connect(self._on_transcode_crf_changed)
        self.crf_combo.setFixedWidth(240)
        self.crf_combo.setFixedHeight(32)
        row2.addWidget(self.crf_combo, 0, Qt.AlignVCenter)
        rc_layout.addLayout(row2)

        # Divider
        d2 = QFrame(rules_card)
        d2.setFixedHeight(1)
        d2.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d2)

        # Row 3: Target Framerate Policy (Auto-Snap CFR)
        row3 = QHBoxLayout()
        row3.setSpacing(12)
        ic3 = IconWidget(FluentIcon.SPEED_HIGH, rules_card)
        ic3.setFixedSize(20, 20)
        row3.addWidget(ic3, 0, Qt.AlignVCenter)
        col3 = QVBoxLayout()
        col3.setSpacing(1)
        t3 = QLabel("Target Framerate Normalization")
        t3.setObjectName("PrefSectionTitle")
        s3 = QLabel("Standardized constant framerate (CFR) target to eliminate audio and video drift")
        s3.setObjectName("PrefCaption")
        col3.addWidget(t3)
        col3.addWidget(s3)
        row3.addLayout(col3, 1)

        self.fps_transcode_combo = ComboBox(rules_card)
        self.fps_transcode_combo.addItem("Auto-Snap to Standard CFR", userData="0.0")
        self.fps_transcode_combo.addItem("Force 23.976 fps", userData="23.976")
        self.fps_transcode_combo.addItem("Force 24.0 fps", userData="24.0")
        self.fps_transcode_combo.addItem("Force 25.0 fps", userData="25.0")
        self.fps_transcode_combo.addItem("Force 29.97 fps", userData="29.97")
        self.fps_transcode_combo.addItem("Force 30.0 fps", userData="30.0")
        self.fps_transcode_combo.addItem("Force 59.94 fps", userData="59.94")
        self.fps_transcode_combo.addItem("Force 60.0 fps", userData="60.0")

        cur_fps = db.get_setting("transcode_framerate", "0.0")
        idx_fps = self.fps_transcode_combo.findData(cur_fps)
        if idx_fps >= 0:
            self.fps_transcode_combo.setCurrentIndex(idx_fps)
        self.fps_transcode_combo.currentIndexChanged.connect(self._on_transcode_fps_changed)
        self.fps_transcode_combo.setFixedWidth(240)
        self.fps_transcode_combo.setFixedHeight(32)
        row3.addWidget(self.fps_transcode_combo, 0, Qt.AlignVCenter)
        rc_layout.addLayout(row3)

        # Divider
        d3 = QFrame(rules_card)
        d3.setFixedHeight(1)
        d3.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d3)

        # Row 4: Max Resolution Limit
        row4 = QHBoxLayout()
        row4.setSpacing(12)
        ic4 = IconWidget(FluentIcon.ZOOM_IN, rules_card)
        ic4.setFixedSize(20, 20)
        row4.addWidget(ic4, 0, Qt.AlignVCenter)
        col4 = QVBoxLayout()
        col4.setSpacing(1)
        t4 = QLabel("Resolution Filter")
        t4.setObjectName("PrefSectionTitle")
        s4 = QLabel("Optionally downscale 4K/UHD bodycam clips to 1080p to prevent timeline memory thrashing")
        s4.setObjectName("PrefCaption")
        col4.addWidget(t4)
        col4.addWidget(s4)
        row4.addLayout(col4, 1)

        self.res_combo = ComboBox(rules_card)
        self.res_combo.addItem("Keep Original Resolution (100% Native)", userData="none")
        self.res_combo.addItem("Downscale 4K/UHD to 1080p (FHD)", userData="1080")
        self.res_combo.addItem("Downscale to 720p (HD Proxy)", userData="720")

        cur_res = db.get_setting("transcode_max_height", "none")
        idx_res = self.res_combo.findData(cur_res)
        if idx_res >= 0:
            self.res_combo.setCurrentIndex(idx_res)
        self.res_combo.currentIndexChanged.connect(self._on_transcode_res_changed)
        self.res_combo.setFixedWidth(240)
        self.res_combo.setFixedHeight(32)
        row4.addWidget(self.res_combo, 0, Qt.AlignVCenter)
        rc_layout.addLayout(row4)

        # Divider
        d4 = QFrame(rules_card)
        d4.setFixedHeight(1)
        d4.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        rc_layout.addWidget(d4)

        # Row 5: Deinterlace / Decomb Filter Policy
        row5 = QHBoxLayout()
        row5.setSpacing(12)
        ic5 = IconWidget(FluentIcon.ALIGNMENT, rules_card)
        ic5.setFixedSize(20, 20)
        row5.addWidget(ic5, 0, Qt.AlignVCenter)
        col5 = QVBoxLayout()
        col5.setSpacing(1)
        t5 = QLabel("Deinterlace & Decomb Filter")
        t5.setObjectName("PrefSectionTitle")
        s5 = QLabel("Eliminates horizontal combing lines on interlaced broadcast, dashcam, and legacy CCTV footage")
        s5.setObjectName("PrefCaption")
        col5.addWidget(t5)
        col5.addWidget(s5)
        row5.addLayout(col5, 1)

        self.deint_combo = ComboBox(rules_card)
        self.deint_combo.addItem("Auto-Decomb Interlaced Clips (bwdif)", userData="auto")
        self.deint_combo.addItem("Force Deinterlace All (bwdif)", userData="bwdif")
        self.deint_combo.addItem("Standard Deinterlace (yadif)", userData="yadif")
        self.deint_combo.addItem("Disabled (Off)", userData="off")

        cur_deint = db.get_setting("transcode_deinterlace", "auto")
        idx_deint = self.deint_combo.findData(cur_deint)
        if idx_deint >= 0:
            self.deint_combo.setCurrentIndex(idx_deint)
        self.deint_combo.currentIndexChanged.connect(self._on_transcode_deint_changed)
        self.deint_combo.setFixedWidth(240)
        self.deint_combo.setFixedHeight(32)
        row5.addWidget(self.deint_combo, 0, Qt.AlignVCenter)
        rc_layout.addLayout(row5)

        layout.addWidget(rules_card)

        # Card 3: Storage Safety & Audio Track Policies (Locked Directives)
        safety_card = PrefCardFrame(widget)
        sc_layout = QVBoxLayout(safety_card)
        sc_layout.setContentsMargins(16, 10, 16, 10)
        sc_layout.setSpacing(8)

        # Row S1: Windows Recycle Bin
        s_row1 = QHBoxLayout()
        s_row1.setSpacing(12)
        s_ic1 = IconWidget(FluentIcon.DELETE, safety_card)
        s_ic1.setFixedSize(20, 20)
        s_row1.addWidget(s_ic1, 0, Qt.AlignVCenter)
        s_col1 = QVBoxLayout()
        s_col1.setSpacing(1)
        st1 = QLabel("File Replacement Safety")
        st1.setObjectName("PrefSectionTitle")
        ss1 = QLabel("Original files are moved to the Windows Recycle Bin before in-place swap for immediate recovery")
        ss1.setObjectName("PrefCaption")
        s_col1.addWidget(st1)
        s_col1.addWidget(ss1)
        s_row1.addLayout(s_col1, 1)

        rb_pill = QLabel("Recycle Bin (Active)")
        rb_pill.setStyleSheet(f"""
            background-color: {badge_bg};
            color: {badge_fg};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px solid {badge_bd};
        """)
        s_row1.addWidget(rb_pill, 0, Qt.AlignVCenter)
        sc_layout.addLayout(s_row1)

        # Divider
        sd1 = QFrame(safety_card)
        sd1.setFixedHeight(1)
        sd1.setStyleSheet("background-color: rgba(128, 128, 136, 0.12); border: none;")
        sc_layout.addWidget(sd1)

        # Row S2: Lossless Audio Pass-Through
        s_row2 = QHBoxLayout()
        s_row2.setSpacing(12)
        s_ic2 = IconWidget(FluentIcon.VOLUME, safety_card)
        s_ic2.setFixedSize(20, 20)
        s_row2.addWidget(s_ic2, 0, Qt.AlignVCenter)
        s_col2 = QVBoxLayout()
        s_col2.setSpacing(1)
        st2 = QLabel("Audio Pass-Through Policy")
        st2.setObjectName("PrefSectionTitle")
        ss2 = QLabel("Strictly bit-for-bit stream copy (-c:a copy). Re-encoding is bypassed to eliminate desync")
        ss2.setObjectName("PrefCaption")
        s_col2.addWidget(st2)
        s_col2.addWidget(ss2)
        s_row2.addLayout(s_col2, 1)

        audio_pill = QLabel("Always Copy (-c:a copy)")
        audio_pill.setStyleSheet(f"""
            background-color: {badge_bg};
            color: {badge_fg};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px solid {badge_bd};
        """)
        s_row2.addWidget(audio_pill, 0, Qt.AlignVCenter)
        sc_layout.addLayout(s_row2)

        layout.addWidget(safety_card)
        layout.addStretch(1)
        return widget

    def _on_transcode_encoder_changed(self, index: int):
        val = self.enc_combo.itemData(index) or "auto"
        db.set_setting("transcode_encoder", str(val))

    def _on_transcode_crf_changed(self, index: int):
        val = self.crf_combo.itemData(index) or "18"
        db.set_setting("transcode_crf", str(val))

    def _on_transcode_fps_changed(self, index: int):
        val = self.fps_transcode_combo.itemData(index) or "0.0"
        db.set_setting("transcode_framerate", str(val))

    def _on_transcode_res_changed(self, index: int):
        val = self.res_combo.itemData(index) or "none"
        db.set_setting("transcode_max_height", str(val))

    def _on_transcode_deint_changed(self, index: int):
        val = self.deint_combo.itemData(index) or "auto"
        db.set_setting("transcode_deinterlace", str(val))

    # --- 3. Core Engines & Binaries Tab ---
    def _build_core_engines_tab(self) -> QWidget:
        from ...core.engine_manager import (
            SUPPORTED_ENGINES, get_engine_status, get_writable_bin_dir,
            EngineDownloadWorker
        )

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Core Engines & Binaries",
            "Inspect, verify upstream open-source repositories, and download media processing and AI speech engines. Binaries are self-contained and require no external system configuration.",
            badge_text="STANDALONE ENGINES"
        ))

        # Top Control Action Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        refresh_btn = PushButton("Refresh Engine Status", widget)
        refresh_btn.setIcon(FluentIcon.SYNC)
        refresh_btn.setFixedHeight(30)
        refresh_btn.clicked.connect(self._refresh_core_engines_tab)
        top_bar.addWidget(refresh_btn)

        open_bin_btn = PushButton("Open Bin Folder", widget)
        open_bin_btn.setIcon(FluentIcon.FOLDER)
        open_bin_btn.setFixedHeight(30)
        open_bin_btn.clicked.connect(self._on_open_bin_folder)
        top_bar.addWidget(open_bin_btn)

        top_bar.addStretch(1)
        layout.addLayout(top_bar)

        self._engine_ui_map = {}
        self._active_engine_workers = {}

        # Render engine cards
        for engine_id in ("libmpv", "ffmpeg", "whisper"):
            spec = SUPPORTED_ENGINES[engine_id]
            card = PrefCardFrame(widget)
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(16, 14, 16, 14)
            c_layout.setSpacing(10)

            # Top Header Row: Icon + Title + Status Badge + [Visit Upstream]
            top_row = QHBoxLayout()
            top_row.setSpacing(8)

            icon_enum = FluentIcon.MOVIE if engine_id == "libmpv" else (
                FluentIcon.VIDEO if engine_id == "ffmpeg" else FluentIcon.ROBOT
            )
            icon_w = IconWidget(icon_enum, card)
            icon_w.setFixedSize(20, 20)
            top_row.addWidget(icon_w)

            title_lbl = QLabel(spec.name, card)
            title_lbl.setObjectName("PrefSectionTitle")
            top_row.addWidget(title_lbl)

            status_badge = QLabel("Checking...", card)
            top_row.addWidget(status_badge)
            top_row.addStretch(1)

            visit_btn = PushButton("Visit Upstream", card)
            visit_btn.setIcon(FluentIcon.LINK)
            visit_btn.setFixedHeight(28)
            visit_btn.clicked.connect(lambda _, url=spec.repo_url: webbrowser.open(url))
            top_row.addWidget(visit_btn)
            c_layout.addLayout(top_row)

            # Description
            desc_lbl = QLabel(spec.description, card)
            desc_lbl.setObjectName("PrefCaption")
            desc_lbl.setWordWrap(True)
            c_layout.addWidget(desc_lbl)

            # Upstream Source & Metadata Box
            meta_frame = QFrame(card)
            meta_frame.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 255, 255, 0.03);
                    border: 1px solid rgba(255, 255, 255, 0.07);
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
            meta_layout = QVBoxLayout(meta_frame)
            meta_layout.setContentsMargins(10, 8, 10, 8)
            meta_layout.setSpacing(4)

            repo_lbl = QLabel(f"<b>Repository:</b> {spec.repo_name}  |  <b>Maintainer:</b> {spec.maintainer}", meta_frame)
            repo_lbl.setObjectName("PrefCaption")
            meta_layout.addWidget(repo_lbl)

            lic_lbl = QLabel(f"<b>License:</b> {spec.license}", meta_frame)
            lic_lbl.setObjectName("PrefCaption")
            meta_layout.addWidget(lic_lbl)

            hw_lbl = QLabel("", meta_frame)
            hw_lbl.setObjectName("PrefCaption")
            meta_layout.addWidget(hw_lbl)

            path_lbl = QLabel("", meta_frame)
            path_lbl.setObjectName("PrefCaption")
            path_lbl.setWordWrap(True)
            meta_layout.addWidget(path_lbl)

            c_layout.addWidget(meta_frame)

            # Action / Download Button Row
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)

            dl_btn = PrimaryPushButton(f"Download {spec.name.split()[0]}", card)
            dl_btn.setIcon(FluentIcon.DOWNLOAD)
            dl_btn.setFixedHeight(30)
            dl_btn.clicked.connect(lambda _, eid=engine_id: self._on_download_engine(eid))
            btn_row.addWidget(dl_btn)

            if engine_id == "whisper":
                models_btn = PushButton("Manage Speech Models", card)
                models_btn.setIcon(FluentIcon.FOLDER)
                models_btn.setFixedHeight(30)
                models_btn.clicked.connect(lambda: self.switch_tab("ai"))
                btn_row.addWidget(models_btn)

            btn_row.addStretch(1)
            c_layout.addLayout(btn_row)

            # Progress Row (Hidden until download starts)
            p_row = QVBoxLayout()
            p_row.setSpacing(4)

            progress_bar = QProgressBar(card)
            progress_bar.setFixedHeight(6)
            progress_bar.setTextVisible(False)
            progress_bar.hide()
            p_row.addWidget(progress_bar)

            p_info_row = QHBoxLayout()
            p_status_lbl = QLabel("", card)
            p_status_lbl.setObjectName("PrefCaption")
            p_status_lbl.hide()
            p_info_row.addWidget(p_status_lbl)
            p_info_row.addStretch(1)

            cancel_btn = PushButton("Cancel", card)
            cancel_btn.setFixedHeight(24)
            cancel_btn.hide()
            cancel_btn.clicked.connect(lambda _, eid=engine_id: self._on_cancel_engine_download(eid))
            p_info_row.addWidget(cancel_btn)

            p_row.addLayout(p_info_row)
            c_layout.addLayout(p_row)

            layout.addWidget(card)

            self._engine_ui_map[engine_id] = {
                "badge": status_badge,
                "hw_lbl": hw_lbl,
                "path_lbl": path_lbl,
                "dl_btn": dl_btn,
                "progress_bar": progress_bar,
                "status_lbl": p_status_lbl,
                "cancel_btn": cancel_btn
            }

        layout.addStretch(1)
        self._refresh_core_engines_tab()
        return widget

    def _refresh_core_engines_tab(self):
        from ...core.engine_manager import get_engine_status
        for engine_id, ui in getattr(self, "_engine_ui_map", {}).items():
            st = get_engine_status(engine_id)
            if not st:
                continue
            installed = st.get("installed", False)
            lbl = st.get("status_label", "Unknown")
            hw = st.get("hw_info", "")
            path = st.get("path", "")

            # Style the badge
            if installed:
                ui["badge"].setText(f"  {lbl}  ")
                ui["badge"].setStyleSheet(
                    "background-color: rgba(16, 185, 129, 0.14); color: #34d399; "
                    "font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 5px; "
                    "border: 1px solid rgba(16, 185, 129, 0.35);"
                )
                ui["dl_btn"].setText("Reinstall / Update")
            else:
                ui["badge"].setText("  Not Installed  ")
                ui["badge"].setStyleSheet(
                    "background-color: rgba(245, 158, 11, 0.14); color: #fbbf24; "
                    "font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 5px; "
                    "border: 1px solid rgba(245, 158, 11, 0.35);"
                )
                name_short = st["spec"].name.split()[0]
                ui["dl_btn"].setText(f"Download {name_short}")

            ui["hw_lbl"].setText(f"<b>Hardware Capabilities:</b> {hw}")
            ui["path_lbl"].setText(f"<b>Location:</b> {path or 'Not Installed'}")

    def _on_download_engine(self, engine_id: str):
        from ...core.engine_manager import EngineDownloadWorker
        ui = self._engine_ui_map.get(engine_id)
        if not ui:
            return

        ui["dl_btn"].setEnabled(False)
        ui["progress_bar"].setValue(0)
        ui["progress_bar"].show()
        ui["status_lbl"].setText("Connecting to upstream repository...")
        ui["status_lbl"].show()
        ui["cancel_btn"].show()

        worker = EngineDownloadWorker(engine_id, self)
        worker.progress.connect(self._on_engine_download_progress)
        worker.finished.connect(self._on_engine_download_finished)
        self._active_engine_workers[engine_id] = worker
        worker.start()

    def _on_cancel_engine_download(self, engine_id: str):
        worker = self._active_engine_workers.get(engine_id)
        if worker:
            worker.cancel()

    def _on_engine_download_progress(self, engine_id: str, pct: int, status_str: str):
        ui = self._engine_ui_map.get(engine_id)
        if ui:
            ui["progress_bar"].setValue(pct)
            ui["status_lbl"].setText(status_str)

    def _on_engine_download_finished(self, engine_id: str, success: bool, msg: str):
        ui = self._engine_ui_map.get(engine_id)
        if ui:
            ui["progress_bar"].hide()
            ui["status_lbl"].hide()
            ui["cancel_btn"].hide()
            ui["dl_btn"].setEnabled(True)

        if engine_id in self._active_engine_workers:
            del self._active_engine_workers[engine_id]

        self._refresh_core_engines_tab()

        if success:
            InfoBar.success(
                "Engine Ready",
                msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )
        else:
            InfoBar.error(
                "Engine Error",
                msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=5000
            )

    def _on_open_bin_folder(self):
        from ...core.engine_manager import get_writable_bin_dir
        bin_dir = get_writable_bin_dir()
        if os.path.exists(bin_dir) and sys.platform == "win32":
            os.startfile(bin_dir)

    # --- 4. Keyboard Shortcuts Tab ---
    def _build_shortcuts_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Keyboard Navigation & Shortcuts",
            "Mouse and keyboard bindings for rapid NLE conforming."
        ))

        shortcuts = [
            ("Click Timecode", "Copy timecode to clipboard with TitleBar feedback toast"),
            ("Double Click Timecode", "Inline edit timecode override"),
            ("Enter", "Commit and save inline override to SQLite database"),
            ("Click Meta Pill", "Copy clip tag and smooth-scroll source script to matching section"),
            ("Drag Extracted Image", "Drag straight from script viewer into Premiere / DaVinci timeline"),
            ("Space", "Play / Pause video player (conforming engine)"),
        ]

        table = QTableWidget(len(shortcuts), 2)
        table.setHorizontalHeaderLabels(["Action / Shortcut", "Description"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setMinimumSectionSize(175)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(36)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.NoFocus)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)

        accent_col = QColor("#60cdff") if ThemeManager.is_dark else QColor("#0078d4")
        for row, (key, desc) in enumerate(shortcuts):
            k_item = QTableWidgetItem(key)
            k_item.setForeground(accent_col)
            k_font = QFont("Segoe UI Variable Text", 10)
            k_font.setBold(True)
            k_item.setFont(k_font)
            table.setItem(row, 0, k_item)
            d_item = QTableWidgetItem(desc)
            d_font = QFont("Segoe UI Variable Text", 10)
            d_item.setFont(d_font)
            table.setItem(row, 1, d_item)

        layout.addWidget(table, 1)
        return widget

    # --- 5. Cookies & Auth Tab ---
    @staticmethod
    def _configure_fast_combo(combo: ComboBox):
        pass

    def _build_cookies_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Cookie Auth Manager",
            "Authenticate private Google Drive folders and restricted media links locally without OAuth or external browser ports.",
            badge_text="LOCAL ONLY"
        ))

        # Card 1: Cookie Source Selector + Live Session Verification Row
        source_card = PrefCardFrame()
        sc_layout = QVBoxLayout(source_card)
        sc_layout.setContentsMargins(16, 14, 16, 14)
        sc_layout.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(12)

        mode_lbl_col = QVBoxLayout()
        mode_lbl_col.setContentsMargins(0, 0, 0, 0)
        mode_lbl_col.setSpacing(2)
        mode_title = QLabel("Cookie Source Mode")
        mode_title.setObjectName("PrefSectionTitle")
        mode_sub = QLabel(
            "Select how Rainy loads session cookies for private Google Drive folders & restricted YouTube links"
        )
        mode_sub.setObjectName("PrefCaption")
        mode_sub.setWordWrap(True)
        mode_lbl_col.addWidget(mode_title)
        mode_lbl_col.addWidget(mode_sub)
        mode_row.addLayout(mode_lbl_col, 1)

        self._cookie_mode_items = [
            ("disabled", "Disabled (Public Only)"),
            ("firefox", "Firefox Browser (Auto-Detect)"),
            ("file", "Netscape File (cookies.txt)"),
            ("paste", "Paste Raw Cookies"),
        ]
        self.cookie_mode_combo = ComboBox(source_card)
        self._configure_fast_combo(self.cookie_mode_combo)
        for _, label in self._cookie_mode_items:
            self.cookie_mode_combo.addItem(label)
        self.cookie_mode_combo.setFixedWidth(220)
        self.cookie_mode_combo.setFixedHeight(30)
        mode_row.addWidget(self.cookie_mode_combo, 0, Qt.AlignVCenter)
        sc_layout.addLayout(mode_row)

        divider = QFrame(source_card)
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(128, 128, 136, 0.16); border: none;")
        sc_layout.addWidget(divider)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 2, 0, 0)
        status_row.setSpacing(8)

        self.cookie_status_badge = QLabel("Checking cookie session...", source_card)
        self.cookie_status_badge.setObjectName("CookieStatusBadge")
        status_row.addWidget(self.cookie_status_badge, 1, Qt.AlignVCenter)

        self.cookie_verify_btn = PushButton("Verify & Reload", source_card)
        self.cookie_verify_btn.setFixedHeight(28)
        self.cookie_verify_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cookie_verify_btn.clicked.connect(lambda: self._update_cookie_ui_state(force_reload=True))
        status_row.addWidget(self.cookie_verify_btn, 0, Qt.AlignVCenter)

        sc_layout.addLayout(status_row)
        layout.addWidget(source_card)

        # Card 2: Dynamic Mode Configuration Stack
        self.cookie_config_stack = QStackedWidget(widget)

        # --- Page 0: Disabled / Public Only ---
        disabled_card = PrefCardFrame()
        dc_layout = QVBoxLayout(disabled_card)
        dc_layout.setContentsMargins(16, 14, 16, 14)
        dc_layout.setSpacing(6)
        dc_title = QLabel("Public Link Access Only")
        dc_title.setObjectName("PrefSectionTitle")
        dc_desc = QLabel(
            "Rainy will fetch link metadata without sending any browser cookies. "
            "Public YouTube videos and 'Anyone with the link' Drive files will work normally, "
            "while restricted client folders will show as locked until a cookie source is enabled above."
        )
        dc_desc.setWordWrap(True)
        dc_desc.setObjectName("PrefCaption")
        dc_layout.addWidget(dc_title)
        dc_layout.addWidget(dc_desc)
        dc_layout.addStretch(1)
        self.cookie_config_stack.addWidget(disabled_card)

        # --- Page 1: Firefox Browser Auto-Detect ---
        ff_card = PrefCardFrame()
        ff_layout = QVBoxLayout(ff_card)
        ff_layout.setContentsMargins(16, 14, 16, 14)
        ff_layout.setSpacing(8)

        ff_title = QLabel("Mozilla Firefox Profile")
        ff_title.setObjectName("PrefSectionTitle")
        ff_sub = QLabel(
            "Automatically detects local Firefox profiles and reads cookies.sqlite via an isolated "
            "temporary shadow copy so Firefox is never locked while running."
        )
        ff_sub.setWordWrap(True)
        ff_sub.setObjectName("PrefCaption")
        ff_layout.addWidget(ff_title)
        ff_layout.addWidget(ff_sub)

        ff_row = QHBoxLayout()
        ff_row.setContentsMargins(0, 4, 0, 0)
        ff_row.setSpacing(8)

        self.firefox_profile_combo = ComboBox(ff_card)
        self._configure_fast_combo(self.firefox_profile_combo)
        self.firefox_profile_combo.setMinimumWidth(280)
        self.firefox_profile_combo.setFixedHeight(30)
        self.firefox_profile_combo.currentIndexChanged.connect(self._on_firefox_profile_selected)
        ff_row.addWidget(self.firefox_profile_combo, 1)

        self.firefox_refresh_btn = PushButton("Rescan Profiles", ff_card)
        self.firefox_refresh_btn.setFixedHeight(30)
        self.firefox_refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.firefox_refresh_btn.clicked.connect(self._populate_firefox_profiles)
        ff_row.addWidget(self.firefox_refresh_btn)

        ff_layout.addLayout(ff_row)

        self.firefox_path_lbl = QLabel("", ff_card)
        self.firefox_path_lbl.setObjectName("PrefCaption")
        self.firefox_path_lbl.setWordWrap(True)
        ff_layout.addWidget(self.firefox_path_lbl)
        ff_layout.addStretch(1)
        self.cookie_config_stack.addWidget(ff_card)

        # --- Page 2: Netscape Cookies File (cookies.txt) ---
        file_card = PrefCardFrame()
        fc_layout = QVBoxLayout(file_card)
        fc_layout.setContentsMargins(16, 14, 16, 14)
        fc_layout.setSpacing(8)

        fc_title = QLabel("Netscape Cookies File (cookies.txt)")
        fc_title.setObjectName("PrefSectionTitle")
        fc_sub = QLabel(
            "Load an exported cookies.txt file (compatible with yt-dlp and 'Get cookies.txt LOCALLY') "
            "or a JSON cookie export."
        )
        fc_sub.setWordWrap(True)
        fc_sub.setObjectName("PrefCaption")
        fc_layout.addWidget(fc_title)
        fc_layout.addWidget(fc_sub)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 4, 0, 0)
        file_row.setSpacing(8)

        self.cookie_file_path_lbl = QLabel("No cookies.txt file selected", file_card)
        self.cookie_file_path_lbl.setObjectName("CookieFilePathLabel")
        self.cookie_file_path_lbl.setMinimumHeight(30)
        file_row.addWidget(self.cookie_file_path_lbl, 1)

        self.cookie_browse_btn = PushButton("Browse File...", file_card)
        self.cookie_browse_btn.setIcon(FluentIcon.FOLDER)
        self.cookie_browse_btn.setFixedHeight(30)
        self.cookie_browse_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cookie_browse_btn.clicked.connect(self._on_browse_cookie_file)
        file_row.addWidget(self.cookie_browse_btn)

        self.cookie_clear_file_btn = PushButton("Clear", file_card)
        self.cookie_clear_file_btn.setFixedHeight(30)
        self.cookie_clear_file_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cookie_clear_file_btn.clicked.connect(self._on_clear_cookie_file)
        file_row.addWidget(self.cookie_clear_file_btn)

        fc_layout.addLayout(file_row)
        fc_layout.addStretch(1)
        self.cookie_config_stack.addWidget(file_card)

        # --- Page 3: Paste Raw Cookies ---
        paste_card = PrefCardFrame()
        pc_layout = QVBoxLayout(paste_card)
        pc_layout.setContentsMargins(16, 14, 16, 14)
        pc_layout.setSpacing(8)

        pc_header_row = QHBoxLayout()
        pc_header_row.setContentsMargins(0, 0, 0, 0)
        pc_header_row.setSpacing(8)

        pc_lbl_col = QVBoxLayout()
        pc_lbl_col.setContentsMargins(0, 0, 0, 0)
        pc_lbl_col.setSpacing(2)
        pc_title = QLabel("Paste Raw Cookies")
        pc_title.setObjectName("PrefSectionTitle")
        pc_sub = QLabel("Paste Netscape cookies.txt lines, JSON cookie arrays, or a raw Cookie: header string")
        pc_sub.setObjectName("PrefCaption")
        pc_lbl_col.addWidget(pc_title)
        pc_lbl_col.addWidget(pc_sub)
        pc_header_row.addLayout(pc_lbl_col, 1)

        self.cookie_save_paste_btn = PushButton("Apply Pasted Cookies", paste_card)
        self.cookie_save_paste_btn.setFixedHeight(28)
        self.cookie_save_paste_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cookie_save_paste_btn.clicked.connect(self._on_save_pasted_cookies)
        pc_header_row.addWidget(self.cookie_save_paste_btn)

        self.cookie_clear_paste_btn = PushButton("Clear", paste_card)
        self.cookie_clear_paste_btn.setFixedHeight(28)
        self.cookie_clear_paste_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cookie_clear_paste_btn.clicked.connect(self._on_clear_pasted_cookies)
        pc_header_row.addWidget(self.cookie_clear_paste_btn)

        pc_layout.addLayout(pc_header_row)

        self.cookie_paste_input = QTextEdit(paste_card)
        self.cookie_paste_input.setObjectName("CookiePasteInput")
        self.cookie_paste_input.setPlaceholderText(
            "# Paste Netscape cookies.txt lines, JSON array, or raw Cookie header:\n"
            ".google.com\tTRUE\t/\tTRUE\t1893456000\tSID\t...\n"
            "# Or: SID=...; HSID=...; SAPISID=...; __Secure-1PSID=..."
        )
        self.cookie_paste_input.setMinimumHeight(110)
        self.cookie_paste_input.setPlainText(cookie_manager.get_raw_cookie_text())
        pc_layout.addWidget(self.cookie_paste_input, 1)
        self.cookie_config_stack.addWidget(paste_card)

        layout.addWidget(self.cookie_config_stack, 1)

        # Initialize UI from saved settings
        self._firefox_profiles_list = []
        self._populate_firefox_profiles(trigger_save=False)
        saved_file = cookie_manager.get_cookie_file_path()
        if saved_file:
            self.cookie_file_path_lbl.setText(saved_file)
            self.cookie_file_path_lbl.setToolTip(saved_file)

        cur_mode = cookie_manager.get_mode()
        mode_idx = next((i for i, (m, _) in enumerate(self._cookie_mode_items) if m == cur_mode), 0)
        self.cookie_mode_combo.setCurrentIndex(mode_idx)
        self.cookie_config_stack.setCurrentIndex(mode_idx)
        self.cookie_mode_combo.currentIndexChanged.connect(self._on_cookie_mode_changed)

        self._update_cookie_ui_state()
        return widget

    def _on_cookie_mode_changed(self, idx: int):
        if 0 <= idx < len(self._cookie_mode_items):
            mode_key = self._cookie_mode_items[idx][0]
            if mode_key == "firefox" and self._firefox_profiles_list:
                prof_idx = max(0, self.firefox_profile_combo.currentIndex())
                if 0 <= prof_idx < len(self._firefox_profiles_list):
                    cookie_manager.set_firefox_profile(self._firefox_profiles_list[prof_idx]["path"])
            cookie_manager.set_mode(mode_key)
            self.cookie_config_stack.setCurrentIndex(idx)
            self._update_cookie_ui_state(force_reload=True)

    def _populate_firefox_profiles(self, trigger_save: bool = True):
        self._firefox_profiles_list = cookie_manager.discover_firefox_profiles()
        self.firefox_profile_combo.blockSignals(True)
        self.firefox_profile_combo.clear()

        saved_path = cookie_manager.get_firefox_profile()
        selected_idx = 0
        matched_saved = False

        if not self._firefox_profiles_list:
            self.firefox_profile_combo.addItem("No Firefox profiles detected")
            self.firefox_path_lbl.setText(
                "No Firefox installation found in %APPDATA%\\Mozilla\\Firefox. "
                "You can use Netscape File (cookies.txt) or Paste Raw Cookies instead."
            )
        else:
            for i, prof in enumerate(self._firefox_profiles_list):
                tag = " (Default)" if prof.get("is_default") else ""
                db_tag = " • cookies.sqlite ready" if prof.get("has_cookies") else " • no cookies db"
                self.firefox_profile_combo.addItem(f"{prof['name']} [{prof['id']}]{tag}{db_tag}")
                if saved_path and os.path.normcase(prof["path"]) == os.path.normcase(saved_path):
                    selected_idx = i
                    matched_saved = True

            self.firefox_profile_combo.setCurrentIndex(selected_idx)
            chosen_path = self._firefox_profiles_list[selected_idx]["path"]
            self.firefox_path_lbl.setText(f"Profile Path: {chosen_path}")
            if trigger_save or not saved_path or not matched_saved:
                cookie_manager.set_firefox_profile(chosen_path)

        self.firefox_profile_combo.blockSignals(False)
        if trigger_save:
            self._update_cookie_ui_state(force_reload=True)

    def _on_firefox_profile_selected(self, idx: int):
        if 0 <= idx < len(self._firefox_profiles_list):
            chosen_path = self._firefox_profiles_list[idx]["path"]
            self.firefox_path_lbl.setText(f"Profile Path: {chosen_path}")
            cookie_manager.set_firefox_profile(chosen_path)
            self._update_cookie_ui_state(force_reload=True)

    def _on_browse_cookie_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Netscape cookies.txt or JSON Cookie File",
            "",
            "Cookie Files (*.txt *.json);;All Files (*.*)"
        )
        if file_path:
            self.set_cookie_file(file_path)

    def set_cookie_file(self, file_path: str):
        cookie_manager.set_cookie_file_path(file_path)
        self.cookie_file_path_lbl.setText(file_path if file_path else "No cookies.txt file selected")
        self.cookie_file_path_lbl.setToolTip(file_path or "")
        self._update_cookie_ui_state(force_reload=True)

    def _on_clear_cookie_file(self):
        self.set_cookie_file("")

    def _on_save_pasted_cookies(self):
        raw_text = self.cookie_paste_input.toPlainText()
        cookie_manager.set_raw_cookie_text(raw_text, flush=True)
        self._update_cookie_ui_state(force_reload=True)

    def _on_clear_pasted_cookies(self):
        self.cookie_paste_input.clear()
        cookie_manager.set_raw_cookie_text("", flush=True)
        self._update_cookie_ui_state(force_reload=True)

    def _update_cookie_ui_state(self, force_reload: bool = False):
        if not hasattr(self, "cookie_status_badge"):
            return
        if force_reload:
            unfurl_engine.clear_cache(restricted_only=True)
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        border = pal.get("border", "#27272a")
        accent = pal.get("accent", "#38bdf8")

        summary = cookie_manager.get_status_summary(force_reload=force_reload)
        state = summary.get("state", "disabled")
        msg = summary.get("message", "")

        if state == "ready":
            self.cookie_status_badge.setText(msg)
            _set_qss_if_changed(self.cookie_status_badge, """
                QLabel#CookieStatusBadge {
                    background-color: rgba(16, 185, 129, 0.14);
                    color: #34d399;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1px solid rgba(16, 185, 129, 0.35);
                }
            """)
        elif state == "warning":
            self.cookie_status_badge.setText(msg)
            _set_qss_if_changed(self.cookie_status_badge, """
                QLabel#CookieStatusBadge {
                    background-color: rgba(245, 158, 11, 0.14);
                    color: #fbbf24;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1px solid rgba(245, 158, 11, 0.35);
                }
            """)
        else:
            self.cookie_status_badge.setText(msg)
            _set_qss_if_changed(self.cookie_status_badge, f"""
                QLabel#CookieStatusBadge {{
                    background-color: rgba(128, 128, 136, 0.12);
                    color: {muted};
                    font-size: 11px;
                    font-weight: 600;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1px solid rgba(128, 128, 136, 0.24);
                }}
            """)

        if hasattr(self, "cookie_file_path_lbl"):
            _set_qss_if_changed(self.cookie_file_path_lbl, f"""
                QLabel#CookieFilePathLabel {{
                    background-color: {surface};
                    color: {text_col if cookie_manager.get_cookie_file_path() else muted};
                    border: 1px solid {border};
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 11px;
                    font-family: 'Cascadia Mono', 'Consolas', monospace;
                }}
            """)

        if hasattr(self, "cookie_paste_input"):
            _set_qss_if_changed(self.cookie_paste_input, f"""
                QTextEdit#CookiePasteInput {{
                    background-color: {surface};
                    color: {text_col};
                    border: 1px solid {border};
                    border-radius: 6px;
                    padding: 8px;
                    font-size: 11px;
                    font-family: 'Cascadia Mono', 'Consolas', monospace;
                }}
                QTextEdit#CookiePasteInput:focus {{
                    border: 1px solid {accent};
                }}
            """)

    # --- 6. AI Models Tab (Faster-Whisper Engine, Model Download Manager, Photo Censoring) ---
    def _build_ai_mcp_tab(self) -> QWidget:
        from ...core.vo_engine import (
            WhisperModelManager, ModelDownloadWorker, EngineUpdateWorker
        )
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "AI Models",
            "Configure the built-in Faster-Whisper voiceover transcription engine, manage local speech models, and the on-device photo censoring model. AI apps connect under MCP.",
            "VOICEOVER ENGINE"
        ))

        # --- Card 1: Faster-Whisper Engine & Compute Hardware ---
        engine_card = PrefCardFrame()
        ec_layout = QVBoxLayout(engine_card)
        ec_layout.setContentsMargins(16, 14, 16, 14)
        ec_layout.setSpacing(10)

        ec_top = QHBoxLayout()
        ec_title = QLabel("Faster-Whisper Transcription Engine")
        ec_title.setObjectName("PrefSectionTitle")
        ec_top.addWidget(ec_title)

        self.whisper_engine_badge = QLabel("Checking...", engine_card)
        self.whisper_engine_badge.setObjectName("WhisperEngineBadge")
        ec_top.addWidget(self.whisper_engine_badge)
        ec_top.addStretch(1)

        self.update_engine_btn = PushButton("Check / Update Engine", engine_card)
        self.update_engine_btn.setIcon(FluentIcon.SYNC)
        self.update_engine_btn.setFixedHeight(30)
        self.update_engine_btn.clicked.connect(self._on_update_whisper_engine)
        ec_top.addWidget(self.update_engine_btn)
        ec_layout.addLayout(ec_top)

        self.whisper_hw_lbl = QLabel("", engine_card)
        self.whisper_hw_lbl.setObjectName("PrefCaption")
        self.whisper_hw_lbl.setWordWrap(True)
        ec_layout.addWidget(self.whisper_hw_lbl)

        # Compute Backend + Active Model Dropdown Row
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(12)

        backend_lbl = QLabel("Compute Backend:", engine_card)
        backend_lbl.setObjectName("PrefBodyText")
        cfg_row.addWidget(backend_lbl)

        self.whisper_backend_combo = ComboBox(engine_card)
        self.whisper_backend_combo.addItems([
            "Auto (CUDA FP16 / CPU INT8)",
            "CUDA FP16 (NVIDIA GPU)",
            "CPU INT8 (Universal)",
        ])
        curr_backend = WhisperModelManager.get_compute_backend()
        backend_map = {"auto": 0, "cuda_fp16": 1, "cpu_int8": 2}
        self.whisper_backend_combo.setCurrentIndex(backend_map.get(curr_backend, 0))
        self.whisper_backend_combo.setFixedWidth(215)
        self.whisper_backend_combo.currentIndexChanged.connect(self._on_whisper_backend_changed)
        cfg_row.addWidget(self.whisper_backend_combo)

        cfg_row.addSpacing(12)

        active_lbl = QLabel("Active VO Model:", engine_card)
        active_lbl.setObjectName("PrefBodyText")
        cfg_row.addWidget(active_lbl)

        self.whisper_model_combo = ComboBox(engine_card)
        self._whisper_catalog = WhisperModelManager.get_catalog()
        active_id = WhisperModelManager.get_active_model_id()
        active_idx = 0
        for idx, spec in enumerate(self._whisper_catalog):
            self.whisper_model_combo.addItem(f"{spec.name} ({spec.size_display})")
            if spec.id == active_id:
                active_idx = idx
        self.whisper_model_combo.setCurrentIndex(active_idx)
        self.whisper_model_combo.setFixedWidth(240)
        self.whisper_model_combo.currentIndexChanged.connect(self._on_active_whisper_model_changed)
        cfg_row.addWidget(self.whisper_model_combo)
        cfg_row.addStretch(1)

        ec_layout.addLayout(cfg_row)
        layout.addWidget(engine_card)

        # --- Card 2: Whisper Model Download Manager ---
        models_card = PrefCardFrame()
        mc_layout = QVBoxLayout(models_card)
        mc_layout.setContentsMargins(16, 14, 16, 14)
        mc_layout.setSpacing(8)

        mc_header = QHBoxLayout()
        mc_title = QLabel("Whisper Model Download Manager")
        mc_title.setObjectName("PrefSectionTitle")
        mc_header.addWidget(mc_title)
        mc_header.addStretch(1)

        open_models_btn = PushButton("Open Models Folder", models_card)
        open_models_btn.setIcon(FluentIcon.FOLDER)
        open_models_btn.setFixedHeight(28)
        open_models_btn.clicked.connect(self._on_open_whisper_models_dir)
        mc_header.addWidget(open_models_btn)
        mc_layout.addLayout(mc_header)

        mc_sub = QLabel(
            "Pre-download speech models locally for instant offline voiceover segment transcription."
        )
        mc_sub.setObjectName("PrefCaption")
        mc_layout.addWidget(mc_sub)

        self._model_row_widgets = {}
        for spec in self._whisper_catalog:
            row_frame = QFrame(models_card)
            row_frame.setObjectName("WhisperModelRow")
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(10, 8, 10, 8)
            r_layout.setSpacing(10)

            info_col = QVBoxLayout()
            info_col.setContentsMargins(0, 0, 0, 0)
            info_col.setSpacing(2)

            name_lbl = QLabel(f"{spec.name}  •  {spec.size_display}  •  {spec.speed_badge}", row_frame)
            name_lbl.setStyleSheet("font-weight: 700; font-size: 12.5px;")
            info_col.addWidget(name_lbl)

            desc_lbl = QLabel(spec.description, row_frame)
            desc_lbl.setObjectName("PrefCaption")
            desc_lbl.setWordWrap(True)
            info_col.addWidget(desc_lbl)
            r_layout.addLayout(info_col, 1)

            status_lbl = QLabel("", row_frame)
            status_lbl.setFixedWidth(130)
            status_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r_layout.addWidget(status_lbl)

            dl_btn = PushButton("Download", row_frame)
            dl_btn.setIcon(FluentIcon.DOWNLOAD)
            dl_btn.setFixedWidth(106)
            dl_btn.setFixedHeight(28)
            dl_btn.clicked.connect(lambda _, mid=spec.id: self._on_download_whisper_model(mid))
            r_layout.addWidget(dl_btn)

            del_btn = ToolButton(FluentIcon.DELETE, row_frame)
            del_btn.setFixedSize(28, 28)
            del_btn.setToolTip(f"Delete cached {spec.name} model")
            del_btn.clicked.connect(lambda _, mid=spec.id: self._on_delete_whisper_model(mid))
            r_layout.addWidget(del_btn)

            self._model_row_widgets[spec.id] = {
                "frame": row_frame,
                "status_lbl": status_lbl,
                "dl_btn": dl_btn,
                "del_btn": del_btn,
            }
            mc_layout.addWidget(row_frame)

        # Shared Download Progress Bar
        self.whisper_dl_progress = QProgressBar(models_card)
        self.whisper_dl_progress.setRange(0, 100)
        self.whisper_dl_progress.setValue(0)
        self.whisper_dl_progress.setFixedHeight(6)
        self.whisper_dl_progress.setTextVisible(False)
        self.whisper_dl_progress.hide()
        mc_layout.addWidget(self.whisper_dl_progress)

        self.whisper_dl_status_lbl = QLabel("", models_card)
        self.whisper_dl_status_lbl.setObjectName("PrefCaption")
        self.whisper_dl_status_lbl.hide()
        mc_layout.addWidget(self.whisper_dl_status_lbl)

        layout.addWidget(models_card)
        layout.addWidget(self._build_photo_censor_card())

        layout.addStretch(1)

        self._refresh_whisper_tab_state()
        return widget

    # --- Automatic Photo Censoring (local sensitivity model) ---
    def _build_photo_censor_card(self) -> QWidget:
        from ...core import photo_censor as pc
        svc = pc.photo_censor

        card = PrefCardFrame()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Automatic Photo Censoring")
        title.setObjectName("PrefSectionTitle")
        top.addWidget(title)
        self.censor_badge = QLabel("", card)
        top.addWidget(self.censor_badge)
        top.addStretch(1)
        self.censor_enable_switch = SwitchButton(card)
        self.censor_enable_switch.setOnText("Enabled")
        self.censor_enable_switch.setOffText("Disabled")
        self.censor_enable_switch.setChecked(svc.auto_enabled())
        self.censor_enable_switch.checkedChanged.connect(self._on_censor_enabled_toggled)
        top.addWidget(self.censor_enable_switch)
        lay.addLayout(top)

        desc = QLabel(
            "Blurs graphic photos in the script (gore, bodies, crime scenes) behind a "
            "\"Sensitive Content\" cover until you click to view. The check runs entirely on this "
            "computer; photos are never uploaded. You can always censor or uncensor a photo yourself "
            "by right-clicking it, with or without the model."
        )
        desc.setObjectName("PrefBodyText")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        row = QFrame(card)
        row.setObjectName("WhisperModelRow")
        r_lay = QHBoxLayout(row)
        r_lay.setContentsMargins(10, 8, 10, 8)
        r_lay.setSpacing(10)
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        name_lbl = QLabel(f"{pc.MODEL_NAME}  \u2022  {pc.MODEL_SIZE_DISPLAY}  \u2022  Offline", row)
        name_lbl.setStyleSheet("font-weight: 700; font-size: 12.5px;")
        info.addWidget(name_lbl)
        sub_lbl = QLabel(
            "Downloads automatically the first time the app runs. Checks a photo in well under a second.", row
        )
        sub_lbl.setObjectName("PrefCaption")
        sub_lbl.setWordWrap(True)
        info.addWidget(sub_lbl)
        r_lay.addLayout(info, 1)

        self.censor_status_lbl = QLabel("", row)
        self.censor_status_lbl.setFixedWidth(130)
        self.censor_status_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r_lay.addWidget(self.censor_status_lbl)

        self.censor_dl_btn = PushButton("Download", row)
        self.censor_dl_btn.setFixedWidth(128)
        self.censor_dl_btn.setFixedHeight(28)
        self.censor_dl_btn.clicked.connect(self._on_censor_download_clicked)
        r_lay.addWidget(self.censor_dl_btn)

        self.censor_del_btn = ToolButton(FluentIcon.DELETE, row)
        self.censor_del_btn.setFixedSize(28, 28)
        self.censor_del_btn.setToolTip("Delete the auto-censor model from this computer")
        self.censor_del_btn.clicked.connect(self._on_censor_delete_clicked)
        r_lay.addWidget(self.censor_del_btn)
        lay.addWidget(row)

        self.censor_dl_progress = QProgressBar(card)
        self.censor_dl_progress.setRange(0, 100)
        self.censor_dl_progress.setFixedHeight(6)
        self.censor_dl_progress.setTextVisible(False)
        self.censor_dl_progress.hide()
        lay.addWidget(self.censor_dl_progress)

        self.censor_dl_status_lbl = QLabel("", card)
        self.censor_dl_status_lbl.setObjectName("PrefCaption")
        self.censor_dl_status_lbl.hide()
        lay.addWidget(self.censor_dl_status_lbl)

        sens_row = QHBoxLayout()
        sens_row.setSpacing(12)
        sens_lbl = QLabel("Sensitivity:", card)
        sens_lbl.setObjectName("PrefBodyText")
        sens_row.addWidget(sens_lbl)
        self.censor_sensitivity_combo = ComboBox(card)
        self._censor_levels = [key for key, _, _ in pc.SENSITIVITY_LEVELS]
        self.censor_sensitivity_combo.addItems([label for _, label, _ in pc.SENSITIVITY_LEVELS])
        self.censor_sensitivity_combo.setCurrentIndex(self._censor_levels.index(svc.sensitivity()))
        self.censor_sensitivity_combo.setFixedWidth(240)
        self.censor_sensitivity_combo.currentIndexChanged.connect(self._on_censor_sensitivity_changed)
        sens_row.addWidget(self.censor_sensitivity_combo)
        sens_row.addStretch(1)
        open_btn = PushButton("Open Model Folder", card)
        open_btn.setIcon(FluentIcon.FOLDER)
        open_btn.setFixedHeight(28)
        open_btn.clicked.connect(self._on_open_censor_model_dir)
        sens_row.addWidget(open_btn)
        lay.addLayout(sens_row)

        svc.model_state_changed.connect(self._refresh_photo_censor_state)
        svc.model_download_progress.connect(self._on_censor_dl_progress)
        svc.model_download_finished.connect(self._on_censor_dl_finished)
        svc.all_photos_changed.connect(self._refresh_photo_censor_state)
        self._refresh_photo_censor_state()
        return card

    def _refresh_photo_censor_state(self):
        from ...core import photo_censor as pc
        if not hasattr(self, "censor_badge"):
            return
        svc = pc.photo_censor
        ready, downloading, enabled = svc.model_ready(), svc.is_downloading(), svc.auto_enabled()
        green = ("rgba(16, 185, 129, 0.14)", "#34d399", "rgba(16, 185, 129, 0.35)")
        amber = ("rgba(245, 158, 11, 0.14)", "#fbbf24", "rgba(245, 158, 11, 0.35)")
        grey = ("rgba(113, 113, 122, 0.16)", "#a1a1aa", "rgba(113, 113, 122, 0.35)")
        if not enabled:
            text, tone = "Off \u2014 manual censoring only", grey
        elif downloading:
            text, tone = "Downloading model\u2026", amber
        elif ready:
            text, tone = "Active", green
        else:
            text, tone = "Model needed \u2014 manual censoring only", amber
        self.censor_badge.setText(text)
        self.censor_badge.setStyleSheet(
            f"background-color: {tone[0]}; color: {tone[1]}; font-size: 11px; font-weight: 700; "
            f"padding: 3px 9px; border-radius: 5px; border: 1px solid {tone[2]};"
        )

        if self.censor_enable_switch.isChecked() != enabled:
            self.censor_enable_switch.blockSignals(True)
            self.censor_enable_switch.setChecked(enabled)
            self.censor_enable_switch.blockSignals(False)

        self.censor_dl_btn.setEnabled(True)
        if downloading:
            pct, status = svc.download_progress()
            self.censor_status_lbl.setText(f"{pct}%")
            self.censor_status_lbl.setStyleSheet("color: #fbbf24; font-size: 11px; font-weight: 700;")
            self.censor_dl_btn.setText("Cancel")
            self.censor_dl_btn.setIcon(FluentIcon.CLOSE)
            self.censor_del_btn.setEnabled(False)
            self.censor_dl_progress.setValue(pct)
            self.censor_dl_progress.show()
            self.censor_dl_status_lbl.setText(f"Downloading\u2026 {status}")
            self.censor_dl_status_lbl.show()
        else:
            self.censor_dl_progress.hide()
            self.censor_dl_status_lbl.hide()
            self.censor_dl_btn.setIcon(FluentIcon.DOWNLOAD)
            if ready:
                self.censor_status_lbl.setText(f"{pc.model_disk_bytes() / 1_000_000:.0f} MB on disk")
                self.censor_status_lbl.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 700;")
                self.censor_dl_btn.setText("Downloaded")
                self.censor_dl_btn.setEnabled(False)
                self.censor_del_btn.setEnabled(True)
            else:
                partial = pc.model_disk_bytes() > 0
                self.censor_status_lbl.setText("Partly Downloaded" if partial else "Not Downloaded")
                self.censor_status_lbl.setStyleSheet("color: #71717a; font-size: 11px; font-weight: 600;")
                self.censor_dl_btn.setText("Resume" if partial else "Download")
                self.censor_del_btn.setEnabled(partial)

    def _on_censor_enabled_toggled(self, enabled: bool):
        from ...core.photo_censor import photo_censor
        photo_censor.set_auto_enabled(enabled)

    def _on_censor_sensitivity_changed(self, idx: int):
        from ...core.photo_censor import photo_censor
        if 0 <= idx < len(self._censor_levels):
            photo_censor.set_sensitivity(self._censor_levels[idx])

    def _on_censor_download_clicked(self):
        from ...core.photo_censor import photo_censor
        if photo_censor.is_downloading():
            photo_censor.cancel_download()
            self.censor_dl_btn.setEnabled(False)
            self.censor_dl_btn.setText("Cancelling\u2026")
        else:
            photo_censor.download_model()

    def _on_censor_dl_progress(self, pct: int, status: str):
        if hasattr(self, "censor_dl_progress"):
            self.censor_dl_progress.setValue(pct)
            self.censor_dl_status_lbl.setText(f"Downloading\u2026 {status}")
            self.censor_status_lbl.setText(f"{pct}%")

    def _on_censor_dl_finished(self, outcome: str, message: str):
        from ...core import photo_censor as pc
        self._refresh_photo_censor_state()
        if not self.isVisible():
            return   # the main window shows its own notice
        if outcome == pc.DOWNLOAD_OK:
            InfoBar.success("Auto-Censor Ready", message, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3500)
        elif outcome == pc.DOWNLOAD_FAILED:
            InfoBar.error("Download Failed", message, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=6000)

    def _on_censor_delete_clicked(self):
        from ...core.photo_censor import photo_censor
        if photo_censor.delete_model():
            InfoBar.info(
                "Model Removed",
                "Auto-censor model deleted. Photos you censored by hand stay censored.",
                parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3500,
            )
        else:
            InfoBar.error(
                "Could Not Delete Model", "The model files are in use. Close other Rainy windows and try again.",
                parent=self, position=InfoBarPosition.TOP_RIGHT, duration=5000,
            )

    def _on_open_censor_model_dir(self):
        import sys
        from ...core.photo_censor import MODEL_DIR
        os.makedirs(MODEL_DIR, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(MODEL_DIR)

    def _refresh_whisper_tab_state(self):
        from ...core.vo_engine import WhisperModelManager
        status = WhisperModelManager.get_engine_status()
        if hasattr(self, "whisper_engine_badge"):
            if status["installed"]:
                ver = status["faster_whisper_version"]
                hw = "CUDA GPU" if status["cuda_available"] else "CPU INT8"
                self.whisper_engine_badge.setText(f"Faster-Whisper v{ver}  •  {hw}")
                self.whisper_engine_badge.setStyleSheet(
                    "background-color: rgba(16, 185, 129, 0.14); color: #34d399; "
                    "font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 5px; "
                    "border: 1px solid rgba(16, 185, 129, 0.35);"
                )
            else:
                self.whisper_engine_badge.setText("Engine Not Installed — Click Check / Update")
                self.whisper_engine_badge.setStyleSheet(
                    "background-color: rgba(245, 158, 11, 0.14); color: #fbbf24; "
                    "font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 5px; "
                    "border: 1px solid rgba(245, 158, 11, 0.35);"
                )

        if hasattr(self, "whisper_hw_lbl"):
            ct2_v = status["ctranslate2_version"]
            cuda_txt = f"CUDA Available ({status['cuda_device_count']} GPU)" if status["cuda_available"] else "CPU Mode (INT8 Quantized)"
            self.whisper_hw_lbl.setText(
                f"CTranslate2 Runtime: {ct2_v}  |  Hardware Acceleration: {cuda_txt}  |  "
                f"Models Directory: {WhisperModelManager.ensure_models_dir()}"
            )

        for model_id, widgets in getattr(self, "_model_row_widgets", {}).items():
            downloaded = WhisperModelManager.is_model_downloaded(model_id)
            size_txt = WhisperModelManager.get_downloaded_size_display(model_id)
            if downloaded:
                widgets["status_lbl"].setText(size_txt)
                widgets["status_lbl"].setStyleSheet("color: #34d399; font-size: 11px; font-weight: 700;")
                widgets["dl_btn"].setText("Downloaded")
                widgets["dl_btn"].setEnabled(False)
                widgets["del_btn"].setEnabled(True)
            else:
                widgets["status_lbl"].setText("Not Downloaded")
                widgets["status_lbl"].setStyleSheet("color: #71717a; font-size: 11px; font-weight: 600;")
                widgets["dl_btn"].setText("Download")
                widgets["dl_btn"].setEnabled(True)
                widgets["del_btn"].setEnabled(False)

    def _on_whisper_backend_changed(self, idx: int):
        from ...core.vo_engine import WhisperModelManager
        codes = ["auto", "cuda_fp16", "cpu_int8"]
        if 0 <= idx < len(codes):
            WhisperModelManager.set_compute_backend(codes[idx])

    def _on_active_whisper_model_changed(self, idx: int):
        from ...core.vo_engine import WhisperModelManager
        if hasattr(self, "_whisper_catalog") and 0 <= idx < len(self._whisper_catalog):
            WhisperModelManager.set_active_model_id(self._whisper_catalog[idx].id)

    def _on_open_whisper_models_dir(self):
        import sys
        from ...core.vo_engine import WhisperModelManager
        folder = WhisperModelManager.ensure_models_dir()
        if sys.platform == "win32":
            os.startfile(folder)

    def _on_download_whisper_model(self, model_id: str):
        from ...core.vo_engine import ModelDownloadWorker
        widgets = self._model_row_widgets.get(model_id)
        if widgets:
            widgets["dl_btn"].setEnabled(False)
            widgets["dl_btn"].setText("Downloading...")
        self.whisper_dl_progress.setValue(0)
        self.whisper_dl_progress.show()
        self.whisper_dl_status_lbl.setText(f"Starting download for {model_id}...")
        self.whisper_dl_status_lbl.show()

        worker = ModelDownloadWorker(model_id)
        worker.setAutoDelete(False)
        self._active_model_dl_worker = worker
        worker.signals.progress.connect(self._on_model_dl_progress, Qt.QueuedConnection)
        worker.signals.finished.connect(self._on_model_dl_finished, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _on_model_dl_progress(self, model_id: str, pct: int, status_text: str):
        self.whisper_dl_progress.setValue(pct)
        self.whisper_dl_status_lbl.setText(status_text)

    def _on_model_dl_finished(self, model_id: str, success: bool, msg: str):
        self._active_model_dl_worker = None
        self.whisper_dl_progress.hide()
        self.whisper_dl_status_lbl.hide()
        self._refresh_whisper_tab_state()
        if success:
            InfoBar.success("Model Downloaded", msg, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3500)
        else:
            InfoBar.error("Download Failed", msg, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _on_delete_whisper_model(self, model_id: str):
        from ...core.vo_engine import WhisperModelManager
        if WhisperModelManager.delete_model(model_id):
            self._refresh_whisper_tab_state()
            InfoBar.info("Model Removed", f"Deleted cached files for {model_id}.", parent=self, position=InfoBarPosition.TOP_RIGHT, duration=2500)

    def _on_update_whisper_engine(self):
        from ...core.vo_engine import EngineUpdateWorker
        self.update_engine_btn.setEnabled(False)
        self.update_engine_btn.setText("Updating Engine...")
        worker = EngineUpdateWorker()
        worker.setAutoDelete(False)
        self._active_engine_update_worker = worker
        worker.signals.finished.connect(self._on_engine_update_finished, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _on_engine_update_finished(self, ok: bool, msg: str):
        self._active_engine_update_worker = None
        self.update_engine_btn.setEnabled(True)
        self.update_engine_btn.setText("Check / Update Engine")
        self._refresh_whisper_tab_state()
        if ok:
            InfoBar.success("Engine Ready", msg, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3500)
        else:
            InfoBar.error("Update Error", msg, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=5000)

    # --- 7. MCP Tab (live bridge, auto-install into AI apps, live activity log) ---
    def _build_mcp_tab(self) -> QWidget:
        from .mcp_settings import McpSettingsPage
        self.mcp_page = McpSettingsPage(
            PrefCardFrame,
            self._create_tab_header(
                "MCP",
                "Connect AI apps (Claude, Codex, Antigravity, Cursor...) to this Rainy and watch what they do, live.",
                "LIVE BRIDGE",
            ),
        )
        self.mcp_page.bridge_toggled.connect(self.mcp_bridge_toggled)
        return self.mcp_page

    # --- 7. About & License Tab ---
    def _build_about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "About Rainy",
            "Maintainer information, community channels, and software licensing."
        ))

        # Card 0: version and updates (the same card the About window and the update-required screen use)
        from .update_card import UpdateCard
        self.update_card = UpdateCard(parent=widget)
        layout.addWidget(self.update_card)

        # Card 0b: report a problem + the opt-in usage-sharing choice
        from .feedback_card import FeedbackCard
        self.feedback_card = FeedbackCard(parent=widget)
        layout.addWidget(self.feedback_card)

        # Card 1: Maintained by Canefly
        m_card = PrefCardFrame()
        mc_layout = QVBoxLayout(m_card)
        mc_layout.setContentsMargins(16, 14, 16, 14)
        mc_layout.setSpacing(8)

        m_title = QLabel("Maintained by Canefly")
        m_title.setObjectName("PrefSectionTitle")
        mc_layout.addWidget(m_title)

        m_text = QLabel("This native desktop application is built and maintained by Canefly. For release updates, support, and tools, join the Discord community:")
        m_text.setWordWrap(True)
        m_text.setObjectName("PrefBodyText")
        mc_layout.addWidget(m_text)

        discord_btn = PushButton("discord.gg/Nv58zRGJma", self)
        discord_btn.setIcon(FluentIcon.MESSAGE)
        discord_btn.setFixedWidth(210)
        discord_btn.clicked.connect(lambda: webbrowser.open("https://discord.gg/Nv58zRGJma"))
        mc_layout.addWidget(discord_btn)
        layout.addWidget(m_card)

        # Card 2: Connect with Cane (2x2 Fluent Grid inside Card)
        socials_card = PrefCardFrame()
        sc_layout = QVBoxLayout(socials_card)
        sc_layout.setContentsMargins(16, 14, 16, 14)
        sc_layout.setSpacing(10)

        s_lbl = QLabel("Connect with Cane")
        s_lbl.setObjectName("PrefSectionTitle")
        sc_layout.addWidget(s_lbl)

        socials = [
            ("Instagram", "https://www.instagram.com/yow_ph/", "@yow_ph"),
            ("TikTok", "https://www.tiktok.com/@yow_ph", "@yow_ph"),
            ("YouTube", "https://www.youtube.com/@yowph", "@yowph"),
            ("Discord", "https://discord.com/users/758307200422248478", "@yowph"),
        ]
        s_grid = QGridLayout()
        s_grid.setContentsMargins(0, 0, 0, 0)
        s_grid.setSpacing(8)
        for idx, (label, url, handle) in enumerate(socials):
            btn = PushButton(f"{label}  •  {handle}", self)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _, u=url: webbrowser.open(u))
            s_grid.addWidget(btn, idx // 2, idx % 2)
        sc_layout.addLayout(s_grid)
        layout.addWidget(socials_card)

        # Card 3: Legal Notice (Restrained 1px elevation stroke, 16x12 margins)
        legal_card = PrefCardFrame()
        lc_layout = QVBoxLayout(legal_card)
        lc_layout.setContentsMargins(16, 12, 16, 12)
        lc_layout.setSpacing(4)
        lc_title = QLabel("Legal Notice")
        lc_title.setObjectName("PrefSectionTitle")
        lc_layout.addWidget(lc_title)

        lc_text = QLabel(
            "This software is the exclusive property of Cane. Unauthorized reproduction, distribution, "
            "modification, or reverse engineering of this codebase is strictly prohibited by law."
        )
        lc_text.setWordWrap(True)
        lc_text.setObjectName("PrefCaption")
        lc_layout.addWidget(lc_text)
        layout.addWidget(legal_card)

        layout.addStretch(1)
        return widget

    def exec(self):
        """Modeless compatibility invocation for callers."""
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._save_window_geometry()
        db.flush_settings()
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        self._save_window_geometry()
        db.flush_settings()
        super().hideEvent(event)

PreferencesDialog = PreferencesWindow
