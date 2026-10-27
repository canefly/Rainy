import os
import sys
from typing import Optional
from ctypes.wintypes import MSG
import win32con
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QIcon, QColor, QPainter, QImage, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication
)
from qfluentwidgets import (
    FluentWindow, FluentTitleBar, InfoBar, InfoBarPosition, isDarkTheme
)

from .theme import ThemeManager, ThemeVariant
from .components.preferences_dialog import PreferencesDialog
from .components.about_dialog import AboutWindow
from .components.data_vault import DataVaultWindow
from .components.titlebar import TitleBarBrandingWidget, TitleBarStatsWidget
from .components.sidebar import SidebarDock
from .views.home_view import HomeView
from .views.workspace_view import WorkspaceView
from ..core.models import DocumentState
from ..core.parser import DocxParseWorker
from ..core.auth import AuthService
from ..core.database import db

class MainWindow(FluentWindow):
    """The root desktop window with native Windows 11 Mica backdrop and MSFluentTitleBar snap layouts."""
    _is_resizing = False
    _is_left_resizing = False

    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("KNOT TOOLS")
        self.current_doc: Optional[DocumentState] = None
        self.thread_pool = QThreadPool.globalInstance()

        # Win32 Sizing State Machine to eliminate left-side jitter & ghosting
        self._is_resizing = False
        self._is_left_resizing = False
        self._resize_reset_timer = QTimer(self)
        self._resize_reset_timer.setSingleShot(True)
        self._resize_reset_timer.setInterval(120)
        self._resize_reset_timer.timeout.connect(self._on_resize_finished)

        # Fluent Acrylic noise texture to eliminate color banding
        self._noise_brush = None
        self._init_noise_brush()

        # 1. Enable Windows 11 Desktop Acrylic backdrop (true frosted glass translucency)
        self.setMicaEffectEnabled(True)
        try:
            from ctypes import c_int, byref
            # 3 = DWMSBT_TRANSIENTWINDOW (Windows 11 Desktop Acrylic blur)
            self.windowEffect.DwmSetWindowAttribute(int(self.winId()), 38, byref(c_int(3)), 4)
        except Exception:
            pass

        # Load Fox Logo
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # Desktop proportions matching production
        self.resize(1340, 840)
        self.setMinimumSize(1024, 640)

        # Early Theme Loading from SQLite so child widgets construct with correct palette
        saved_var_str = db.get_setting("theme_variant", "pink")
        saved_dark_str = db.get_setting("theme_dark", "true")
        saved_orb_str = db.get_setting("orb_glow_intensity", "25")
        saved_glass_str = db.get_setting("glass_transparency", "65")
        saved_exp_dwm_str = db.get_setting("experimental_pure_dwm_glass", "false")
        try:
            self._saved_variant = ThemeVariant(saved_var_str)
        except ValueError:
            self._saved_variant = ThemeVariant.PINK
        self._saved_dark = (saved_dark_str.lower() != "false")
        try:
            ThemeManager.orb_glow_intensity = max(0, min(100, int(saved_orb_str)))
        except ValueError:
            ThemeManager.orb_glow_intensity = 25
        try:
            ThemeManager.glass_transparency = max(0, min(100, int(saved_glass_str)))
        except ValueError:
            ThemeManager.glass_transparency = 65
        ThemeManager.experimental_pure_dwm_glass = (saved_exp_dwm_str.lower() == "true")
        ThemeManager.current_variant = self._saved_variant
        ThemeManager.is_dark = self._saved_dark

        # 2. Native FluentTitleBar Configuration with Windows 11 Snap Layouts
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setFixedHeight(40)
        self.titleBar.setObjectName("TitleBar")
        self.titleBar.move(0, 0)
        self.titleBar.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        self.titleBar.hBoxLayout.setSpacing(0)
        if hasattr(self.titleBar, 'iconLabel') and self.titleBar.iconLabel:
            self.titleBar.iconLabel.hide()
        if hasattr(self.titleBar, 'titleLabel') and self.titleBar.titleLabel:
            self.titleBar.titleLabel.hide()

        # Left branding (flush to top-left corner)
        # Native caption buttons are 32px tall and top-anchored, so branding/stats/pin share that
        # same 32px row to line up on one center line with min/max/close.
        self.title_branding = TitleBarBrandingWidget(self.titleBar)
        self.title_branding.setFixedHeight(32)
        self.titleBar.hBoxLayout.insertWidget(0, self.title_branding, 0, Qt.AlignTop)

        # Right stats widget (inserted right before window control buttons)
        self.title_stats = TitleBarStatsWidget(self.titleBar)
        self.title_stats.setFixedHeight(32)
        self.title_stats.pin_toggled.connect(self._on_pin_toggled)
        insert_idx = max(1, self.titleBar.hBoxLayout.count() - 1)
        self.titleBar.hBoxLayout.insertWidget(insert_idx, self.title_stats, 0, Qt.AlignTop)
        self._apply_caption_button_theme()

        # Remove default navigation interface and stackedWidget so TitleBar and MainBody start flush at x=0
        if hasattr(self, 'navigationInterface') and self.navigationInterface:
            self.hBoxLayout.removeWidget(self.navigationInterface)
            self.navigationInterface.deleteLater()
            self.navigationInterface = None

        if hasattr(self, 'stackedWidget') and self.stackedWidget:
            self.widgetLayout.removeWidget(self.stackedWidget)
            self.stackedWidget.deleteLater()

        self.widgetLayout.setContentsMargins(0, 40, 0, 0)
        self.widgetLayout.setSpacing(0)

        # Central Body: Horizontal split between SidebarDock and Central Stack
        body_container = QWidget(self)
        body_container.setObjectName("MainBody")
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Sidebar Dock - Hidden by default on Home Screen!
        self.sidebar = SidebarDock(body_container)
        self.sidebar.home_clicked.connect(self.go_home)
        self.sidebar.outline_clicked.connect(self._on_outline_clicked)
        self.sidebar.notes_clicked.connect(self._on_notes_clicked)
        self.sidebar.links_clicked.connect(self._on_links_clicked)
        self.sidebar.focus_mode_clicked.connect(self._on_focus_mode_clicked)
        self.sidebar.vault_clicked.connect(self.open_vault)
        self.sidebar.settings_clicked.connect(self.open_settings)
        self.sidebar.hide()
        body_layout.addWidget(self.sidebar)

        # Central Stack of Views: Strictly HomeView & WorkspaceView
        self.stack = QStackedWidget(body_container)
        if self.stack.layout():
            self.stack.layout().setContentsMargins(0, 0, 0, 0)

        self.home_view = HomeView(self.stack)
        self.home_view.file_selected.connect(self.load_docx_file)
        self.home_view.settings_requested.connect(self.open_settings)
        self.home_view.updates_requested.connect(self.open_about)
        self.home_view.copyright_requested.connect(lambda: self.open_settings(initial_tab=3))
        self.stack.addWidget(self.home_view)

        self.workspace_view = WorkspaceView(self.stack)
        self.workspace_view.stats_updated.connect(self.title_stats.update_stats)
        self.workspace_view.copy_feedback_requested.connect(self.title_stats.trigger_copied)
        self.workspace_view.notes_count_changed.connect(self.sidebar.set_notes_count)
        self.workspace_view.links_count_changed.connect(self.sidebar.set_links_count)
        self.stack.addWidget(self.workspace_view)

        body_layout.addWidget(self.stack, 1)
        self.widgetLayout.addWidget(body_container, 1)

        # Initial View: Home Screen (Full Width, Centered, No Sidebar, No Contextual Stats)
        self.go_home()

        # Initial Theme & Ambient Orb Glow Apply
        self.apply_palette(self._saved_variant, dark=self._saved_dark)

        # 3. Asynchronous Licensing & Auth Verification
        self._init_auth()

    def _init_noise_brush(self):
        try:
            noise_img = QImage(':/qfluentwidgets/images/acrylic/noise.png')
            if not noise_img.isNull():
                noise_tile = QImage(noise_img.size(), QImage.Format_ARGB32_Premultiplied)
                noise_tile.fill(Qt.transparent)
                np = QPainter(noise_tile)
                np.setOpacity(0.04) # 4% subtle grain to smooth color banding
                np.drawImage(0, 0, noise_img)
                np.end()
                self._noise_brush = QBrush(noise_tile)
        except Exception:
            self._noise_brush = None

    def _normalBackgroundColor(self):
        # 20% black tint (alpha 51 = 20% of 255) over Windows 11 Desktop Acrylic for soft glass
        return QColor(0, 0, 0, 51) if isDarkTheme() else QColor(255, 255, 255, 60)

    def _apply_backdrop_effect(self):
        """Enforces Windows 11 Desktop Acrylic for consumer glass themes or solid opaque wall for Pro themes."""
        ThemeManager.enable_window_backdrop(self)
        self.setBackgroundColor(self._normalBackgroundColor())
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e):
        super().moveEvent(e)
        # Repaint screen-space orb parallax as the window moves across the monitor
        # (Skipped during active window resize to prevent duplicate paints on left/top resize)
        if not getattr(self, '_is_resizing', False) and ThemeManager.orb_glow_intensity > 0:
            self.update()

    def _on_resize_finished(self):
        self._is_resizing = False
        self._is_left_resizing = False
        if hasattr(self, 'workspace_view') and self.stack.currentWidget() == self.workspace_view:
            self.workspace_view.set_hold_reflow(False)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def nativeEvent(self, eventType, message):
        msg = MSG.from_address(message.__int__())
        if not msg.hWnd:
            return super().nativeEvent(eventType, message)

        if msg.message == win32con.WM_ENTERSIZEMOVE:
            self._is_resizing = False
            self._is_left_resizing = False

        elif msg.message == win32con.WM_SIZING:
            edge = msg.wParam
            # Win32 Sizing edges: WMSZ_LEFT = 1, WMSZ_TOPLEFT = 4, WMSZ_BOTTOMLEFT = 7
            is_left = edge in (win32con.WMSZ_LEFT, win32con.WMSZ_TOPLEFT, win32con.WMSZ_BOTTOMLEFT)
            self._is_resizing = True
            self._is_left_resizing = is_left
            if is_left and hasattr(self, 'workspace_view') and self.stack.currentWidget() == self.workspace_view:
                self.workspace_view.set_hold_reflow(True)
            self._resize_reset_timer.start(120)

        elif msg.message == win32con.WM_EXITSIZEMOVE:
            self._resize_reset_timer.stop()
            self._on_resize_finished()

        return super().nativeEvent(eventType, message)

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_backdrop_effect()

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()
        self._apply_backdrop_effect()

    def resizeEvent(self, e):
        super(FluentWindow, self).resizeEvent(e)
        if self.titleBar.x() != 0 or self.titleBar.y() != 0:
            self.titleBar.move(0, 0)
        if self.titleBar.width() != self.width():
            self.titleBar.resize(self.width(), self.titleBar.height())
        if self.titleBar.layout():
            self.titleBar.layout().activate()
        if self.layout():
            self.layout().activate()

    def _init_auth(self):
        auth = AuthService.instance()
        # Set cached name immediately
        self.title_stats.set_editor_identity(auth.current_editor_name)
        # Verify in background
        auth.verify_async(
            on_verified=self._on_auth_verified,
            on_failed=self._on_auth_failed
        )

    def _on_auth_verified(self, name: str, is_offline: bool, time_left: str):
        self.title_stats.set_editor_identity(name, is_offline, time_left)

    def _on_auth_failed(self, reason: str):
        self.title_stats.set_editor_identity("UNVERIFIED", True, reason)

    def _apply_caption_button_theme(self):
        """Bind min/max/close caption button colors to the active theme palette."""
        from PySide6.QtGui import QColor
        tb = getattr(self, 'titleBar', None)
        if tb is None:
            return
        pal = ThemeManager.get_palette()
        base = QColor(pal.get("text", "#ffffff"))
        hover_bg = QColor(base.red(), base.green(), base.blue(), 28)
        press_bg = QColor(base.red(), base.green(), base.blue(), 46)

        def rgba(c: QColor) -> str:
            return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"

        # qfluentwidgets re-applies its own dark/light QSS (qproperty-*) on style polish, which
        # would overwrite plain setters; a widget-level stylesheet takes precedence over it.
        for btn in (tb.minBtn, tb.maxBtn):
            btn.setStyleSheet(
                f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: {rgba(base)}; "
                f"qproperty-pressedColor: {rgba(base)}; "
                f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
                f"qproperty-hoverBackgroundColor: {rgba(hover_bg)}; "
                f"qproperty-pressedBackgroundColor: {rgba(press_bg)};"
            )
            btn.update()
        tb.closeBtn.setStyleSheet(
            f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: rgba(255, 255, 255, 255); "
            f"qproperty-pressedColor: rgba(255, 255, 255, 255); "
            f"qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
            f"qproperty-hoverBackgroundColor: rgba(232, 17, 35, 255); "
            f"qproperty-pressedBackgroundColor: rgba(241, 112, 122, 255);"
        )
        tb.closeBtn.update()
        if hasattr(self, 'title_stats') and self.title_stats:
            self.title_stats.pin_btn.update()

    def apply_palette(self, variant: ThemeVariant, dark: bool = True):
        # Persist to SQLite single source of truth
        db.set_setting("theme_variant", variant.value)
        db.set_setting("theme_dark", "true" if dark else "false")

        qss = ThemeManager.apply_theme(variant, dark=dark)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss)
        else:
            self.setStyleSheet(qss)
        if hasattr(self, 'title_branding') and self.title_branding:
            self.title_branding.apply_theme()
        if hasattr(self, 'title_stats') and self.title_stats:
            self.title_stats.apply_theme()
        self._apply_caption_button_theme()
        if hasattr(self, 'sidebar') and self.sidebar:
            self.sidebar.apply_theme()
        if hasattr(self, 'workspace_view') and self.workspace_view:
            self.workspace_view.apply_theme(variant, dark=dark)
        if hasattr(self, 'home_view') and self.home_view:
            self.home_view.apply_theme(variant, dark=dark)
        self._apply_backdrop_effect()
        for attr in ('pref_window', 'vault_window', 'about_window'):
            win = getattr(self, attr, None)
            if win is not None:
                if hasattr(win, 'apply_theme'):
                    win.apply_theme()
                ThemeManager.enable_window_backdrop(win)
        if hasattr(self, 'workspace_view') and self.workspace_view:
            for d_attr in ('notes_drawer', 'links_drawer'):
                drawer = getattr(self.workspace_view, d_attr, None)
                if drawer is not None:
                    if hasattr(drawer, 'apply_theme'):
                        drawer.apply_theme()
                    ThemeManager.enable_window_backdrop(drawer)

    def go_home(self):
        """Switches to Home Screen: Hides Sidebar & Hides Contextual Stats."""
        self.sidebar.hide()
        self.title_stats.set_has_document(False)
        self.stack.setCurrentWidget(self.home_view)
        self.home_view.refresh_recents()

    def open_workspace(self):
        """Switches to Workspace View: Shows Sidebar & Shows Contextual Stats."""
        self.sidebar.show()
        self.title_stats.set_has_document(True)
        self.stack.setCurrentWidget(self.workspace_view)

    def open_settings(self, initial_tab: int = 0):
        """Opens or focuses the independent Preferences desktop window."""
        if not hasattr(self, 'pref_window') or self.pref_window is None:
            self.pref_window = PreferencesDialog(None, initial_tab=initial_tab)
            self.pref_window.theme_changed.connect(self.apply_palette)
            self.pref_window.document_opened.connect(self.load_docx_file)
        else:
            self.pref_window.nav_list.setCurrentRow(initial_tab)

        self.pref_window.show()
        self.pref_window.raise_()
        self.pref_window.activateWindow()

    def open_about(self):
        """Opens or focuses the independent About & Updates desktop window."""
        if not hasattr(self, 'about_window') or self.about_window is None:
            self.about_window = AboutWindow()
        self.about_window.show()
        self.about_window.raise_()
        self.about_window.activateWindow()

    def open_vault(self):
        """Opens or focuses the independent Data Vault desktop window."""
        if hasattr(self, 'pref_window') and self.pref_window is not None:
            self.pref_window.nav_list.setCurrentRow(1)
        if not hasattr(self, 'vault_window') or self.vault_window is None:
            self.vault_window = DataVaultWindow()
            self.vault_window.document_opened.connect(self.load_docx_file)
        self.vault_window.refresh()
        self.vault_window.show()
        self.vault_window.raise_()
        self.vault_window.activateWindow()

    def _on_outline_clicked(self):
        if self.stack.currentWidget() != self.workspace_view:
            self.open_workspace()
        self.workspace_view.toggle_outline()

    def _on_notes_clicked(self):
        if self.stack.currentWidget() != self.workspace_view:
            self.open_workspace()
        self.workspace_view.toggle_notes_drawer()

    def _on_links_clicked(self):
        if self.stack.currentWidget() != self.workspace_view:
            self.open_workspace()
        self.workspace_view.toggle_links_drawer()

    def _on_focus_mode_clicked(self):
        if self.stack.currentWidget() != self.workspace_view:
            self.open_workspace()
        self.workspace_view.toggle_split_view()

    def load_docx_file(self, file_path: str):
        """Asynchronous, non-blocking DOCX parser using worker threads."""
        if not os.path.exists(file_path):
            InfoBar.error(
                title="File Not Found",
                content=f"Could not open file: {file_path}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000
            )
            return

        self.loading_bar = InfoBar.info(
            title="Parsing Script...",
            content=f"Reading {os.path.basename(file_path)} with typo-tolerant normalizer...",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=-1
        )

        worker = DocxParseWorker(file_path)
        worker.signals.finished.connect(self._on_parse_finished)
        worker.signals.error.connect(self._on_parse_error)
        self.thread_pool.start(worker)

    def _on_parse_finished(self, doc_state: DocumentState):
        if hasattr(self, 'loading_bar') and self.loading_bar:
            try:
                self.loading_bar.close()
            except RuntimeError:
                pass
            self.loading_bar = None

        self.current_doc = doc_state
        self.workspace_view.load_document(doc_state)

        # Update sidebar badge counts
        all_notes_count = len(doc_state.notes) + len(doc_state.custom_notes)
        self.sidebar.set_notes_count(all_notes_count)
        self.sidebar.set_links_count(len(doc_state.links))

        # Switch to Workspace View
        self.open_workspace()

        InfoBar.success(
            title="Script Loaded!",
            content=f"Parsed {len(doc_state.items)} checklist items & {len(doc_state.links)} links.",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2500
        )

    def _on_parse_error(self, err_msg: str):
        if hasattr(self, 'loading_bar') and self.loading_bar:
            try:
                self.loading_bar.close()
            except RuntimeError:
                pass
            self.loading_bar = None

        InfoBar.error(
            title="Parser Error",
            content=f"Failed to parse document: {err_msg}",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000
        )

    def _on_pin_toggled(self, is_pinned: bool):
        flags = self.windowFlags()
        if is_pinned:
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        if hasattr(self, "updateFrameless"):
            self.updateFrameless()
        self.show()
        self._apply_backdrop_effect()

    def closeEvent(self, event):
        if hasattr(self, 'pref_window') and self.pref_window:
            self.pref_window.close()
        if hasattr(self, 'about_window') and self.about_window:
            self.about_window.close()
        if hasattr(self, 'vault_window') and self.vault_window:
            self.vault_window.close()
        if hasattr(self, 'workspace_view') and self.workspace_view:
            if hasattr(self.workspace_view, 'notes_drawer') and self.workspace_view.notes_drawer:
                self.workspace_view.notes_drawer.close()
            if hasattr(self.workspace_view, 'links_drawer') and self.workspace_view.links_drawer:
                self.workspace_view.links_drawer.close()
        super().closeEvent(event)
