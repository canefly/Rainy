import os
import sys
from typing import Optional
from ctypes.wintypes import MSG
import win32con
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QIcon, QColor, QPainter, QImage, QBrush, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication, QFileDialog, QInputDialog,
    QSystemTrayIcon
)
from qfluentwidgets import (
    FluentWindow, FluentTitleBar, InfoBar, InfoBarPosition, isDarkTheme
)

from .theme import ThemeManager, ThemeVariant
from .components.preferences_dialog import PreferencesDialog
from .components.about_dialog import AboutWindow
from .components.data_vault import DataVaultWindow
from .components.titlebar import TitleBarBrandingWidget, TitleBarStatsWidget, TitleBarMenuWidget
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
        self.setWindowTitle("CLICKABLES TOOLS")
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

        # Inline Action Menubar: File | Edit | Export | Help
        self.title_menu = TitleBarMenuWidget(self.titleBar)
        self.title_menu.setFixedHeight(32)
        self.titleBar.hBoxLayout.insertWidget(1, self.title_menu, 0, Qt.AlignTop)

        # Wire title menu actions
        self.title_menu.open_doc_requested.connect(self._on_menu_open_doc)
        self.title_menu.open_project_requested.connect(self._on_menu_open_project)
        self.title_menu.reimport_script_requested.connect(self._on_menu_reimport_script)
        self.title_menu.save_project_as_requested.connect(self._on_menu_save_project_as)
        self.title_menu.set_footage_requested.connect(self._on_menu_set_footage)
        self.title_menu.open_projects_folder_requested.connect(self._on_menu_open_projects_folder)
        self.title_menu.close_workspace_requested.connect(self.go_home)
        self.title_menu.exit_requested.connect(self.close)

        self.title_menu.find_requested.connect(self._on_menu_find)
        self.title_menu.mark_all_done_requested.connect(lambda: self.workspace_view.mark_all_done())
        self.title_menu.reset_checklists_requested.connect(lambda: self.workspace_view.reset_all_checklists())

        self.title_menu.export_xml_requested.connect(self._on_menu_export_xml)
        self.title_menu.export_timestamps_requested.connect(self._on_menu_export_timestamps)

        self.title_menu.shortcuts_requested.connect(self._on_menu_shortcuts)
        self.title_menu.settings_requested.connect(self.open_settings)
        self.title_menu.about_requested.connect(self.open_about)

        # Global Keyboard Accelerators
        QShortcut(QKeySequence("Ctrl+O"), self, self._on_menu_open_doc)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._on_menu_save_project_as)
        QShortcut(QKeySequence("Ctrl+E"), self, self._on_menu_export_xml)
        QShortcut(QKeySequence("Ctrl+F"), self, self._on_menu_find)
        QShortcut(QKeySequence("F1"), self, self._on_menu_shortcuts)

        # Right stats widget (inserted right before window control buttons)
        self.title_stats = TitleBarStatsWidget(self.titleBar)
        self.title_stats.setFixedHeight(32)
        self.title_stats.pin_toggled.connect(self._on_pin_toggled)
        self.title_stats.render_queue_clicked.connect(self._on_menu_set_footage)
        insert_idx = max(1, self.titleBar.hBoxLayout.count() - 1)
        self.titleBar.hBoxLayout.insertWidget(insert_idx, self.title_stats, 0, Qt.AlignTop)
        self._apply_caption_button_theme()

        # Global Render Queue wiring (persists & runs even when Footage modal is closed)
        from ..core.render_queue import render_queue
        render_queue.queue_changed.connect(self._sync_render_queue_indicator)
        render_queue.job_progress.connect(lambda *_: self._sync_render_queue_indicator())
        render_queue.job_finished.connect(self._on_global_render_job_finished)
        render_queue.batch_finished.connect(self._on_global_render_batch_finished)
        self._sync_render_queue_indicator()

        # System Tray for Windows Toast Notifications
        self._init_tray_notifications()

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
        self.sidebar.footage_clicked.connect(self._on_footage_clicked)
        self.sidebar.vault_clicked.connect(self.open_vault)
        self.sidebar.settings_clicked.connect(self.open_settings)
        self.sidebar.hide()
        body_layout.addWidget(self.sidebar)

        # Central Stack of Views: Strictly HomeView & WorkspaceView
        self.stack = QStackedWidget(body_container)
        if self.stack.layout():
            self.stack.layout().setContentsMargins(0, 0, 0, 0)

        self.home_view = HomeView(self.stack)
        self.home_view.file_selected.connect(self.open_file)
        self.home_view.settings_requested.connect(self.open_settings)
        self.home_view.updates_requested.connect(self.open_about)
        self.home_view.copyright_requested.connect(lambda: self.open_settings(initial_tab=4))
        self.stack.addWidget(self.home_view)

        self.workspace_view = WorkspaceView(self.stack)
        self.workspace_view.stats_updated.connect(self.title_stats.update_stats)
        self.workspace_view.copy_feedback_requested.connect(self.title_stats.trigger_copied)
        self.workspace_view.notes_count_changed.connect(self.sidebar.set_notes_count)
        self.workspace_view.links_count_changed.connect(self.sidebar.set_links_count)
        self.workspace_view.footage_count_changed.connect(self.sidebar.set_footage_count)
        self.workspace_view.set_footage_folder_requested.connect(self._on_menu_set_footage)
        self.workspace_view.relink_requested.connect(self._on_menu_set_footage)
        self.workspace_view.links_drawer.relink_requested.connect(self._on_menu_set_footage)
        self.workspace_view.open_settings_requested.connect(self.open_settings)
        self.stack.addWidget(self.workspace_view)

        body_layout.addWidget(self.stack, 1)
        self.widgetLayout.addWidget(body_container, 1)

        # Initial View: Home Screen (Full Width, Centered, No Sidebar, No Contextual Stats)
        self.go_home()

        # Initial Theme & Ambient Orb Glow Apply
        self.apply_palette(self._saved_variant, dark=self._saved_dark)

        # 3. Asynchronous Licensing & Auth Verification
        self._init_auth()

        # 4. Idle pre-warm settings window and hardware encoder probe (instant 0ms opening)
        QTimer.singleShot(1000, self._idle_prewarm_services)

    def _idle_prewarm_services(self):
        """Pre-warms independent background services and windows during main thread idle."""
        import threading
        from ..core.ffmpeg_encoder import detect_available_encoders
        threading.Thread(target=detect_available_encoders, daemon=True).start()

        try:
            if not hasattr(self, 'pref_window') or self.pref_window is None:
                self.pref_window = PreferencesDialog(None, initial_tab=0)
                self.pref_window.theme_changed.connect(self.apply_palette)
                self.pref_window.document_opened.connect(self.load_docx_file)
                self.pref_window.smpte_conforming_changed.connect(self._on_smpte_conforming_changed)
        except Exception as e:
            logger.warning(f"Could not pre-warm preferences dialog: {e}")

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
        # While actively moving/dragging the window across the screen, NEVER trigger synchronous repaints!
        # Windows DWM handles window position translation in GPU hardware with zero CPU lag.
        # Once the move finishes (WM_EXITSIZEMOVE), _on_resize_finished refreshes the screen-space orb parallax.

    def _on_resize_finished(self):
        self._is_in_sizemove = False
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
            self._is_in_sizemove = True
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
        if hasattr(self, 'title_menu') and self.title_menu:
            self.title_menu.apply_theme()
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
        """Switches to Home Screen: Hides Sidebar, closes floating tool drawers, & resets pending jobs."""
        self.sidebar.hide()
        self.title_stats.set_has_document(False)
        if hasattr(self, 'title_menu') and self.title_menu:
            self.title_menu.set_has_document(False)
        if hasattr(self, 'workspace_view') and self.workspace_view:
            self.workspace_view.close_drawers()
        from ..core.unfurl_engine import unfurl_engine
        unfurl_engine.cancel_pending()
        self.stack.setCurrentWidget(self.home_view)
        self.home_view.refresh_recents()

    def open_workspace(self):
        """Switches to Workspace View: Shows Sidebar & Shows Contextual Stats."""
        self.sidebar.show()
        self.title_stats.set_has_document(True)
        if hasattr(self, 'title_menu') and self.title_menu:
            self.title_menu.set_has_document(True)
        self.stack.setCurrentWidget(self.workspace_view)

    def open_settings(self, initial_tab=0):
        """Opens or focuses the independent Preferences desktop window."""
        if not hasattr(self, 'pref_window') or self.pref_window is None:
            self.pref_window = PreferencesDialog(None, initial_tab=initial_tab)
            self.pref_window.theme_changed.connect(self.apply_palette)
            self.pref_window.document_opened.connect(self.load_docx_file)
            self.pref_window.smpte_conforming_changed.connect(self._on_smpte_conforming_changed)
        else:
            self.pref_window.switch_tab(initial_tab)

        self.pref_window.show()
        self.pref_window.raise_()
        self.pref_window.activateWindow()

    def _on_smpte_conforming_changed(self, checked: bool = True):
        if hasattr(self, 'workspace_view') and self.workspace_view:
            self.workspace_view.refresh_timecodes()

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
            self.pref_window.switch_tab(1)
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

    def _on_footage_clicked(self):
        if self.stack.currentWidget() != self.workspace_view:
            self.open_workspace()
        self._on_menu_set_footage()

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

        worker = DocxParseWorker(file_path)
        worker.signals.finished.connect(self._on_parse_finished)
        worker.signals.error.connect(self._on_parse_error)
        self.thread_pool.start(worker)

    def _on_parse_finished(self, doc_state: DocumentState):
        self.current_doc = doc_state
        self.workspace_view.load_document(doc_state)

        # Update sidebar badge counts
        all_notes_count = len(doc_state.notes) + len(doc_state.custom_notes)
        self.sidebar.set_notes_count(all_notes_count)
        self.sidebar.set_links_count(len(doc_state.links))
        self.sidebar.set_footage_count(len(getattr(self.workspace_view, "linked_footage", {})))

        # Switch to Workspace View
        self.open_workspace()

    def _on_parse_error(self, err_msg: str):
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

    def open_file(self, file_path: str, force_reimport: bool = False):
        """Unified intelligent file opener. Loads standalone .clickproj in <10ms if available,
        falling back to parsing .docx only when no project exists or user requests an explicit re-import."""
        if not file_path or not os.path.exists(file_path):
            return

        # 1. Direct .clickproj file selection
        if file_path.lower().endswith(".clickproj"):
            self._load_clickproj_file(file_path)
            return

        # 2. DOCX file selection
        if not force_reimport:
            from ..core.project_manager import project_manager
            auto_proj = project_manager.get_auto_project_path(os.path.basename(file_path))
            if os.path.exists(auto_proj):
                # Instant load from the self-contained project!
                self._load_clickproj_file(auto_proj)
                return

        # 3. First-time import or explicit re-import of DOCX
        self.load_docx_file(file_path)

    def _on_menu_reimport_script(self):
        """Re-imports script text from DOCX while preserving existing checklist, overrides, and footage links."""
        if not self.current_doc:
            return
        target_docx = getattr(self.current_doc, "file_path", "") or ""
        if not target_docx or not os.path.exists(target_docx):
            target_docx, _ = QFileDialog.getOpenFileName(
                self,
                f"Locate Updated Word Document for {self.current_doc.name}",
                "",
                "Word Documents (*.docx);;All Files (*.*)"
            )
        if target_docx and os.path.exists(target_docx):
            self.load_docx_file(target_docx)

    def _on_menu_open_doc(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Script or Project",
            "",
            "Supported Files (*.docx *.clickproj);;Word Documents (*.docx);;Clickables Projects (*.clickproj);;All Files (*.*)"
        )
        if not file_path:
            return
        self.open_file(file_path)

    def _on_menu_open_project(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Clickables Project",
            "",
            "Clickables Projects (*.clickproj);;All Files (*.*)"
        )
        if not file_path:
            return
        self.open_file(file_path)

    def _load_clickproj_file(self, proj_path: str):
        from ..core.project_manager import project_manager
        proj = project_manager.load_project(proj_path)
        if not proj:
            InfoBar.error(
                title="Invalid Project",
                content=f"Could not load project: {os.path.basename(proj_path)}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )
            return

        # 1. Instant load from self-contained project data (no DOCX required!)
        if proj.has_embedded_document():
            doc_state = proj.to_document_state()
            self.current_doc = doc_state
            from ..core.database import db
            if proj.overrides and doc_state.file_signature:
                for itm_id, ov in proj.overrides.items():
                    db.save_override(
                        doc_state.file_signature,
                        itm_id,
                        ov.get("start_tc") or ov.get("in_tc") or "",
                        ov.get("end_tc") or ov.get("out_tc") or "",
                        ov.get("text")
                    )
            self.workspace_view.load_document(doc_state, project_path=proj_path)

            all_notes_count = len(doc_state.notes) + len(doc_state.custom_notes)
            self.sidebar.set_notes_count(all_notes_count)
            self.sidebar.set_links_count(len(doc_state.links))
            self.sidebar.set_footage_count(len(getattr(self.workspace_view, "linked_footage", {})))
            self.open_workspace()

            db.add_recent_file(proj_path, proj.project_name or os.path.basename(proj_path))

            InfoBar.success(
                title="Project Loaded",
                content=f"Opened standalone project: {proj.project_name or os.path.basename(proj_path)}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500
            )
            return

        # 2. Fallback for legacy v1 projects that only stored check state
        target_docx = proj.docx_path or proj.script_file_name
        if not target_docx or not os.path.exists(target_docx):
            target_docx, _ = QFileDialog.getOpenFileName(
                self,
                f"Locate Associated Script (.docx) for {proj.doc_name or os.path.basename(proj_path)}",
                "",
                "Word Documents (*.docx);;All Files (*.*)"
            )
        if target_docx and os.path.exists(target_docx):
            self.load_docx_file(target_docx)
        else:
            InfoBar.warning(
                title="Associated Script Not Found",
                content=f"Legacy project '{proj.doc_name or os.path.basename(proj_path)}' requires its original .docx script to open.",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )

    def _on_menu_save_project_as(self):
        if not self.current_doc:
            return
        doc_base = self.current_doc.name or "Project"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project Copy As",
            f"{doc_base}_copy.clickproj",
            "Clickables Project (*.clickproj);;All Files (*.*)"
        )
        if save_path:
            self.workspace_view._save_auto_project(custom_path=save_path)
            InfoBar.success(
                title="Project Copy Saved",
                content=f"Saved copy to {os.path.basename(save_path)}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )

    def _on_menu_set_footage(self):
        if not self.current_doc:
            return
        from .components.footage_relink_dialog import FootageRelinkDialog
        curr_folder = getattr(self.workspace_view, "footage_folder", "")
        curr_linked = getattr(self.workspace_view, "linked_footage", {})
        curr_vo = getattr(self.current_doc, "vo_file_path", "")
        curr_vo_segs = getattr(self.current_doc, "vo_segments", [])
        curr_vo_files = getattr(self.current_doc, "vo_files", []) or ([curr_vo] if curr_vo else [])
        curr_vo_trans = getattr(self.current_doc, "vo_transcript_path", "")
        proj_name = getattr(self.current_doc, "name", "") or "Voiceover"

        dlg = FootageRelinkDialog(
            links=self.current_doc.links,
            current_footage_dir=curr_folder,
            existing_linked=curr_linked,
            current_vo_path=curr_vo,
            existing_vo_segments=curr_vo_segs,
            doc_signature=getattr(self.current_doc, "file_signature", "") or getattr(self.current_doc, "id", ""),
            parent=self,
            project_name=proj_name,
            vo_transcript_path=curr_vo_trans,
            vo_files=curr_vo_files,
        )

        def _on_linked(folder: str, mapping: dict):
            self.workspace_view.set_footage_folder(folder, mapping)
            total = len(self.current_doc.links)
            matched = len(mapping)
            dir_name = os.path.basename(folder) or folder
            InfoBar.success(
                title="Footage Audit Complete",
                content=f"Linked {matched} of {total} media assets in {dir_name}.",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )

        def _on_vo_linked(vo_path: str, vo_segments: list):
            self.workspace_view.set_vo_data(vo_path, vo_segments)

        def _on_vo_transcript_ready(json_path: str, vo_files: list, vo_segments: list):
            primary = vo_files[0] if vo_files else curr_vo
            self.workspace_view.set_vo_data(primary, vo_segments, vo_transcript_path=json_path, vo_files=vo_files)

        dlg.footage_linked.connect(_on_linked)
        dlg.vo_linked.connect(_on_vo_linked)
        dlg.vo_transcript_ready.connect(_on_vo_transcript_ready)
        dlg.exec()

    def _sync_render_queue_indicator(self):
        from ..core.render_queue import render_queue
        counts = render_queue.get_summary_counts()
        running_job = next((j for j in render_queue.jobs if j.status == "RUNNING"), None)
        if running_job:
            active_total = counts["done"] + 1 + counts["queued"]
            idx_num = counts["done"] + 1
            txt = f"Rendering {idx_num}/{active_total} · {running_job.progress:.0f}%"
            self.title_stats.update_render_queue_indicator(True, txt)
        elif counts["queued"] > 0:
            self.title_stats.update_render_queue_indicator(True, f"Queue ({counts['queued']} waiting)")
        else:
            self.title_stats.update_render_queue_indicator(False, "")

    def _init_tray_notifications(self):
        """Initializes native Windows system tray notification bridge for background rendering."""
        self.tray_icon: Optional[QSystemTrayIcon] = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"))
            icon = QIcon(logo_path) if os.path.exists(logo_path) else self.windowIcon()
            self.tray_icon = QSystemTrayIcon(icon, self)
            self.tray_icon.setToolTip("Clickables Native Tools")
            self.tray_icon.messageClicked.connect(self._on_tray_message_clicked)
            self.tray_icon.show()

    def _on_tray_message_clicked(self):
        """Brings Clickables to the foreground when a Windows toast notification is clicked."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_system_notification(self, title: str, message: str, is_error: bool = False):
        """Dispatches a native Windows 11 Action Center notification."""
        if self.tray_icon and self.tray_icon.isVisible():
            icon_type = QSystemTrayIcon.Warning if is_error else QSystemTrayIcon.Information
            self.tray_icon.showMessage(title, message, icon_type, 6000)

    def _on_global_render_job_finished(self, job, success: bool, final_path_or_err: str):
        if success and final_path_or_err and os.path.isfile(final_path_or_err):
            # If the file path changed (e.g. .avi -> .mp4), update linked footage in workspace
            if hasattr(self, "workspace_view") and self.workspace_view:
                self.workspace_view.replace_linked_footage_path(job.source_path, final_path_or_err)

        if not success and final_path_or_err != "Cancelled by user":
            title = f"Render Failed: {os.path.basename(job.source_path)}"
            err_msg = str(final_path_or_err)[:120]
            if not self.isActiveWindow():
                self.show_system_notification(title, err_msg, is_error=True)
            InfoBar.error(
                title=title,
                content=err_msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4500,
            )

    def _on_global_render_batch_finished(self, done_count: int, fail_count: int):
        if done_count > 0 and fail_count == 0:
            title = "Render Queue Complete"
            msg = f"Finished encoding {done_count} clip{'s' if done_count != 1 else ''}. Originals moved safely to Recycle Bin."
            self.show_system_notification(title, msg, is_error=False)
            if self.isActiveWindow():
                InfoBar.success(
                    title=title,
                    content=msg,
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=4000,
                )
        elif done_count > 0 and fail_count > 0:
            title = "Render Queue Finished with Warnings"
            msg = f"{done_count} succeeded, {fail_count} failed. Open Footage Library to retry failed jobs."
            self.show_system_notification(title, msg, is_error=True)
            if self.isActiveWindow():
                InfoBar.warning(
                    title=title,
                    content=msg,
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=4500,
                )

    def set_mcp_bridge_enabled(self, enabled: bool):
        if hasattr(self, "workspace_view") and hasattr(self.workspace_view, "mcp_ipc_server"):
            if enabled:
                self.workspace_view.mcp_ipc_server.start()
            else:
                self.workspace_view.mcp_ipc_server.stop()

    def _on_menu_open_projects_folder(self):
        from ..core.project_manager import project_manager
        folder = project_manager.get_projects_dir()
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_menu_find(self):
        if self.stack.currentWidget() != self.workspace_view or not self.current_doc:
            return
        query, ok = QInputDialog.getText(self, "Find in Script", "Search text:")
        if ok and query:
            browser = self.workspace_view.script_viewer.browser
            found = browser.find(query)
            if not found:
                browser.moveCursor(QTextCursor.Start)
                found = browser.find(query)
            if not found:
                InfoBar.warning(
                    title="Not Found",
                    content=f"No occurrences of '{query}' found in script.",
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=2500
                )

    def _on_menu_export_xml(self):
        if not self.current_doc:
            return

        from ..core.parser import normalize_link
        from qfluentwidgets import MessageBox

        footage = getattr(self.workspace_view, "footage_folder", "")
        linked_map = getattr(self.workspace_view, "linked_footage", {})
        total_links = len(self.current_doc.links)
        matched_count = len([lk for lk in self.current_doc.links if linked_map.get(normalize_link(lk)) or linked_map.get(lk)])
        missing_count = total_links - matched_count

        if total_links > 0 and missing_count > 0:
            box = MessageBox(
                "Unlinked Media Detected",
                f"{matched_count} of {total_links} media assets are linked to local files.\n\n"
                f"{missing_count} asset(s) are currently unlinked and will be exported as offline media placeholders in Premiere Pro.\n\n"
                "Would you like to locate the missing files first, or export anyway?",
                self
            )
            box.yesButton.setText("Locate Missing Files...")
            box.cancelButton.setText("Export Anyway")
            if box.exec():
                self._on_menu_set_footage()
                return

        doc_base = self.current_doc.name or "Sequence"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Premiere Pro XML Sequence",
            f"{doc_base}_sequence.xml",
            "Premiere Pro XML (*.xml);;All Files (*.*)"
        )
        if not save_path:
            return
        from ..core.xml_exporter import export_fcp_xml_file, export_assembled_fcp_xml_file
        try:
            has_vo_locks = any(getattr(it, "vo_cut_after_sec", None) is not None for it in self.current_doc.items)
            has_assembly = bool(getattr(self.current_doc, "timeline_assembly", None))
            if has_vo_locks or has_assembly:
                export_assembled_fcp_xml_file(
                    save_path,
                    self.current_doc,
                    vo_file_path=getattr(self.current_doc, "vo_file_path", ""),
                    linked_footage=linked_map,
                )
            else:
                export_fcp_xml_file(save_path, self.current_doc, footage_folder=footage, linked_footage=linked_map)
            InfoBar.success(
                title="XML Exported",
                content=f"Saved Premiere Pro XML sequence to {os.path.basename(save_path)}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )
        except Exception as e:
            InfoBar.error(
                title="Export Failed",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )

    def _on_menu_export_timestamps(self):
        if not self.current_doc:
            return
        doc_base = self.current_doc.name or "Script"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Timestamps",
            f"{doc_base}_timestamps.txt",
            "Text Files (*.txt);;CSV Files (*.csv);;All Files (*.*)"
        )
        if not save_path:
            return
        is_csv = save_path.lower().endswith(".csv")
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                if is_csv:
                    f.write("Status,Start Timecode,End Timecode,Duration,Text,Speaker\n")
                    for item in self.current_doc.items:
                        status = "Checked" if item.is_checked else "Pending"
                        speaker = (getattr(item, "speaker", "") or "").replace('"', '""')
                        clean_text = (item.text or "").replace('"', '""')
                        f.write(f'"{status}","{item.start_timecode}","{item.end_timecode}","{item.duration_seconds}s","{clean_text}","{speaker}"\n')
                else:
                    f.write(f"Clickables Timestamps Export - {doc_base}\n")
                    f.write("=" * 60 + "\n\n")
                    for item in self.current_doc.items:
                        box = "[x]" if item.is_checked else "[ ]"
                        f.write(f"{box} {item.start_timecode} - {item.end_timecode} ({item.duration_seconds}s): {item.text}\n")
            InfoBar.success(
                title="Timestamps Exported",
                content=f"Saved timestamps to {os.path.basename(save_path)}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )
        except Exception as e:
            InfoBar.error(
                title="Export Failed",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )

    def _on_menu_shortcuts(self):
        """Opens Preferences window directly to the Keyboard Navigation & Shortcuts reference tab."""
        self.open_settings(initial_tab=5)

    def closeEvent(self, event):
        if hasattr(self, 'tray_icon') and self.tray_icon:
            try:
                self.tray_icon.hide()
            except Exception:
                pass
        if hasattr(self, 'pref_window') and self.pref_window:
            self.pref_window.close()
        if hasattr(self, 'about_window') and self.about_window:
            self.about_window.close()
        if hasattr(self, 'vault_window') and self.vault_window:
            self.vault_window.close()
        if hasattr(self, 'workspace_view') and self.workspace_view:
            if hasattr(self.workspace_view, 'trimmer_window') and self.workspace_view.trimmer_window:
                try:
                    self.workspace_view.trimmer_window.destroy_window()
                except Exception:
                    pass
            if hasattr(self.workspace_view, 'notes_drawer') and self.workspace_view.notes_drawer:
                self.workspace_view.notes_drawer.close()
            if hasattr(self.workspace_view, 'links_drawer') and self.workspace_view.links_drawer:
                self.workspace_view.links_drawer.close()
            if hasattr(self.workspace_view, 'mcp_ipc_server') and self.workspace_view.mcp_ipc_server:
                try:
                    self.workspace_view.mcp_ipc_server.stop()
                except Exception:
                    pass
        try:
            from ..core.waveform_engine import waveform_precache_manager
            waveform_precache_manager.cancel_all()
        except Exception:
            pass
        super().closeEvent(event)
