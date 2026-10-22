import os
import webbrowser
from typing import Optional
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QCursor, QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QFrame, QStackedWidget, QListWidget, QListWidgetItem,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QRadioButton, QButtonGroup, QAbstractItemView
)
from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, SwitchButton,
    FluentIcon, IconWidget, MessageBox, Dialog, Slider, Theme as FluentTheme
)

from ..theme import ThemeManager, ThemeVariant
from .widgets import MagneticDetentSlider
from ...core.database import db


def _set_qss_if_changed(widget: QWidget, qss: str):
    """Avoids expensive Qt unpolish/repolish cycles when stylesheet text is unchanged."""
    if getattr(widget, "_cached_qss", None) != qss:
        widget._cached_qss = qss
        widget.setStyleSheet(qss)


class PrefSidebarFrame(QFrame):
    """GPU-painted sidebar frame that dynamically reflects live glass_transparency without QSS repolishing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PrefSidebar")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        side_col, _, border_col = ThemeManager.get_pref_surface_qcolors()
        painter.fillRect(self.rect(), side_col)
        painter.setPen(border_col)
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class PrefCardFrame(QFrame):
    """GPU-painted card container that dynamically reflects live glass_transparency without QSS repolishing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PrefCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        _, card_col, border_col = ThemeManager.get_pref_surface_qcolors()
        painter.setPen(border_col)
        painter.setBrush(card_col)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)


class PreferencesWindow(FramelessWindow):
    """Unified native modeless preferences window following the Windows 11 Fluent Settings architecture."""
    theme_changed = Signal(object, bool) # (ThemeVariant, is_dark)
    document_opened = Signal(str)

    def __init__(self, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        self.setObjectName("PreferencesDialog")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("Preferences — Rainy Tools")
        self.resize(840, 620)
        self.setMinimumSize(740, 540)

        # Set Window Icon
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # TitleBar setup
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setFixedHeight(40)
        self.titleBar.setObjectName("PrefHeader")
        if hasattr(self.titleBar, "titleLabel") and self.titleBar.titleLabel:
            self.titleBar.titleLabel.setStyleSheet(
                "font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 12px; font-weight: 600;"
            )

        # Main Layout (offset by 40px for titlebar)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 40, 0, 0)
        root_layout.setSpacing(0)

        # 2. Body: Left Category Sidebar + Right Content Stack
        body = QWidget(self)
        b_layout = QHBoxLayout(body)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(0)

        # Left Navigation Pane (200px fixed width, 4px modular grid padding)
        self.sidebar_frame = PrefSidebarFrame(body)
        self.sidebar_frame.setFixedWidth(200)
        s_layout = QVBoxLayout(self.sidebar_frame)
        s_layout.setContentsMargins(10, 12, 10, 12)
        s_layout.setSpacing(4)

        self.nav_list = QListWidget(self.sidebar_frame)
        self.nav_list.setObjectName("PrefNavList")
        self.nav_list.setIconSize(QSize(16, 16))
        self.nav_list.setSpacing(2)
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._categories = [
            ("Appearance", FluentIcon.PALETTE),
            ("Data Vault", FluentIcon.FOLDER),
            ("Shortcuts", FluentIcon.COMMAND_PROMPT),
            ("Copyright Helper", FluentIcon.VIDEO),
            ("About & License", FluentIcon.INFO),
        ]

        f_theme = FluentTheme.DARK if ThemeManager.is_dark else FluentTheme.LIGHT
        for text, icon in self._categories:
            item = QListWidgetItem(icon.icon(f_theme), f"  {text}")
            item.setSizeHint(QSize(180, 36))
            self.nav_list.addItem(item)

        s_layout.addWidget(self.nav_list)
        b_layout.addWidget(self.sidebar_frame)

        # Right Stacked Content Area (24px horizontal, 20px vertical Fluent page margins)
        self.content_frame = QFrame(body)
        self.content_frame.setObjectName("PrefContent")
        c_layout = QVBoxLayout(self.content_frame)
        c_layout.setContentsMargins(24, 20, 24, 20)
        c_layout.setSpacing(0)

        self.stack = QStackedWidget(self.content_frame)
        self.stack.addWidget(self._build_appearance_tab())
        self.stack.addWidget(self._build_vault_tab())
        self.stack.addWidget(self._build_shortcuts_tab())
        self.stack.addWidget(self._build_copyright_tab())
        self.stack.addWidget(self._build_about_tab())

        c_layout.addWidget(self.stack)
        b_layout.addWidget(self.content_frame, 1)
        root_layout.addWidget(body, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(db.flush_settings)

        self.titleBar.raise_()
        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav_list.setCurrentRow(initial_tab)

    def _create_tab_header(self, title_text: str, subtitle_text: str, badge_text: str = None) -> QVBoxLayout:
        """Builds a consistent Windows 11 Fluent Settings page header block (4px internal gap)."""
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 4)
        header_layout.setSpacing(4)

        if badge_text:
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(8)

            title = QLabel(title_text)
            title.setObjectName("PrefTabTitle")
            title_row.addWidget(title)

            pal = ThemeManager.get_palette()
            rgb = pal.get("accent_rgb", "251, 113, 133")
            accent = pal.get("accent", "#fb7185")
            badge = QLabel(badge_text)
            badge.setStyleSheet(f"""
                background-color: rgba({rgb}, 0.14);
                color: {accent};
                font-size: 10px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid rgba({rgb}, 0.28);
            """)
            title_row.addWidget(badge, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            header_layout.addLayout(title_row)
        else:
            title = QLabel(title_text)
            title.setObjectName("PrefTabTitle")
            header_layout.addWidget(title)

        sub = QLabel(subtitle_text)
        sub.setObjectName("PrefTabSubtitle")
        sub.setWordWrap(True)
        header_layout.addWidget(sub)
        return header_layout

    # --- 1. Appearance & Themes Tab ---
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

        # Subtle divider + Optional Experimental Pure DWM See-Through Glass row (Default: Off)
        exp_divider = QFrame(glass_box)
        exp_divider.setFixedHeight(1)
        exp_divider.setStyleSheet("background-color: rgba(128, 128, 136, 0.16); border: none;")
        gb_layout.addWidget(exp_divider)

        exp_row = QHBoxLayout()
        exp_row.setContentsMargins(0, 4, 0, 0)
        exp_row.setSpacing(12)

        exp_label_col = QVBoxLayout()
        exp_label_col.setContentsMargins(0, 0, 0, 0)
        exp_label_col.setSpacing(2)

        exp_title_row = QHBoxLayout()
        exp_title_row.setContentsMargins(0, 0, 0, 0)
        exp_title_row.setSpacing(8)
        exp_title = QLabel("Pure DWM See-Through Glass")
        exp_title.setObjectName("PrefSectionTitle")
        self.exp_dwm_badge = QLabel("EXPERIMENTAL")
        exp_title_row.addWidget(exp_title)
        exp_title_row.addWidget(self.exp_dwm_badge, 0, Qt.AlignVCenter)
        exp_title_row.addStretch(1)
        exp_label_col.addLayout(exp_title_row)

        exp_sub = QLabel(
            "Bypasses Windows 11's milky Acrylic luminosity wash via low-level DWM blur-behind "
            "so underlying active windows shine directly through the glass"
        )
        exp_sub.setWordWrap(True)
        exp_sub.setObjectName("PrefCaption")
        exp_label_col.addWidget(exp_sub)
        exp_row.addLayout(exp_label_col, 1)

        self.exp_dwm_switch = SwitchButton(glass_box)
        self.exp_dwm_switch.setChecked(ThemeManager.experimental_pure_dwm_glass)
        self.exp_dwm_switch.checkedChanged.connect(self._on_exp_dwm_glass_changed)
        exp_row.addWidget(self.exp_dwm_switch, 0, Qt.AlignVCenter)

        gb_layout.addLayout(exp_row)
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

    def _on_exp_dwm_glass_changed(self, enabled: bool):
        db.set_setting("experimental_pure_dwm_glass", "true" if enabled else "false")
        ThemeManager.set_experimental_pure_dwm_glass(enabled)
        self._update_glass_ui_state(ThemeManager.glass_transparency)
        self.update()

    def _update_orb_ui_state(self, val: int):
        supports_glass = ThemeManager.supports_glass_effects()
        if hasattr(self, "orb_slider") and self.orb_slider:
            if self.orb_slider.isEnabled() != supports_glass:
                self.orb_slider.setEnabled(supports_glass)
            self.orb_slider.update()
        preset_names = {0: "Off", 25: "Subtle", 55: "Middle", 100: "Max"}
        tag = preset_names.get(val, "Custom")
        pal = ThemeManager.get_palette()
        rgb = pal.get("accent_rgb", "251, 113, 133")
        accent = pal.get("accent", "#fb7185")
        muted = pal.get("muted", "#8e8e98")
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
                    background-color: rgba({rgb}, 0.14);
                    color: {accent};
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid rgba({rgb}, 0.28);
                """)

    def _update_glass_ui_state(self, val: int):
        supports_glass = ThemeManager.supports_glass_effects()
        if hasattr(self, "glass_slider") and self.glass_slider:
            if self.glass_slider.isEnabled() != supports_glass:
                self.glass_slider.setEnabled(supports_glass)
            self.glass_slider.update()
        if hasattr(self, "exp_dwm_switch") and self.exp_dwm_switch:
            if self.exp_dwm_switch.isEnabled() != supports_glass:
                self.exp_dwm_switch.setEnabled(supports_glass)
        preset_names = {0: "Solid", 35: "Subtle", 65: "Balanced", 100: "Max Glass"}
        tag = preset_names.get(val, "Custom")
        pal = ThemeManager.get_palette()
        rgb = pal.get("accent_rgb", "251, 113, 133")
        accent = pal.get("accent", "#fb7185")
        muted = pal.get("muted", "#8e8e98")
        if hasattr(self, "exp_dwm_badge") and self.exp_dwm_badge:
            _set_qss_if_changed(self.exp_dwm_badge, f"""
                background-color: rgba({rgb}, 0.14);
                color: {accent if supports_glass else muted};
                font-size: 9px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid rgba({rgb}, 0.28);
            """)
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
                mode_suffix = "  •  Pure DWM" if ThemeManager.experimental_pure_dwm_glass else ""
                self.glass_value_badge.setText(f"{val}%  •  {tag}{mode_suffix}")
                _set_qss_if_changed(self.glass_value_badge, f"""
                    background-color: rgba({rgb}, 0.14);
                    color: {accent};
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 10px;
                    border-radius: 4px;
                    border: 1px solid rgba({rgb}, 0.28);
                """)

    def paintEvent(self, e):
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e):
        super().moveEvent(e)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def _apply_caption_button_theme(self):
        tb = getattr(self, "titleBar", None)
        if tb is None or not all(hasattr(tb, a) for a in ("minBtn", "maxBtn", "closeBtn")):
            return
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        base = QColor(pal.get("text", "#ffffff" if is_dark else "#18181b"))
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
        self._apply_caption_button_theme()
        self._update_orb_ui_state(ThemeManager.orb_glow_intensity)
        self._update_glass_ui_state(ThemeManager.glass_transparency)
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        ThemeManager.enable_window_backdrop(self)
        if hasattr(self, 'theme_radios') and ThemeManager.current_variant in self.theme_radios:
            self.theme_group.blockSignals(True)
            self.theme_radios[ThemeManager.current_variant].setChecked(True)
            self.theme_group.blockSignals(False)
        if hasattr(self, 'dark_switch'):
            self.dark_switch.blockSignals(True)
            self.dark_switch.setChecked(ThemeManager.is_dark)
            self.dark_switch.blockSignals(False)
        if hasattr(self, 'orb_slider'):
            self.orb_slider.blockSignals(True)
            self.orb_slider.setValue(ThemeManager.orb_glow_intensity)
            self.orb_slider.blockSignals(False)
        if hasattr(self, 'exp_dwm_switch'):
            self.exp_dwm_switch.blockSignals(True)
            self.exp_dwm_switch.setChecked(ThemeManager.experimental_pure_dwm_glass)
            self.exp_dwm_switch.blockSignals(False)
        if hasattr(self, 'glass_slider'):
            self.glass_slider.blockSignals(True)
            self.glass_slider.setValue(ThemeManager.glass_transparency)
            self.glass_slider.blockSignals(False)
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
        self.theme_changed.emit(ThemeManager.current_variant, is_dark)
        ThemeManager.enable_window_backdrop(self)
        self.apply_theme()

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
        layout.addWidget(self.scripts_table, 1)

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

    def _reset_stats_clicked(self):
        w = Dialog("Reset Statistics", "Are you sure you want to reset lifetime parse statistics? This cannot be undone.", self)
        if w.exec():
            db.reset_stats()
            self.nav_list.setCurrentRow(1)

    # --- 3. Keyboard Shortcuts Tab ---
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

        for row, (key, desc) in enumerate(shortcuts):
            k_item = QTableWidgetItem(key)
            k_item.setForeground(QColor("#fb7185"))
            table.setItem(row, 0, k_item)
            d_item = QTableWidgetItem(desc)
            table.setItem(row, 1, d_item)

        layout.addWidget(table, 1)
        return widget

    # --- 4. Copyright Helper Tab ---
    def _build_copyright_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "Copyright Helper",
            "Audio-Visual & Segment Heatmaps Conforming Engine.",
            badge_text="BETA"
        ))

        card = PrefCardFrame()
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 14, 16, 14)
        c_layout.setSpacing(8)

        card_title = QLabel("Broadcast & Platform Delivery Protection")
        card_title.setObjectName("PrefSectionTitle")
        c_layout.addWidget(card_title)

        info_lbl = QLabel(
            "The Copyright Helper tool scans video segments and audio cues to identify high-risk "
            "licensing content, background audio overlaps, and potential copyright strikes before export.\n\n"
            "This conforming engine connects directly with PTS-to-SMPTE timecode verification to ensure "
            "your YouTube, TikTok, and broadcast deliveries stay safe from demonetization."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setObjectName("PrefBodyText")
        info_lbl.setStyleSheet("font-size: 12px; line-height: 1.55;")
        c_layout.addWidget(info_lbl)
        layout.addWidget(card)

        layout.addStretch(1)
        return widget

    # --- 5. About & License Tab ---
    def _build_about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addLayout(self._create_tab_header(
            "About Rainy Tools",
            "Maintainer information, community channels, and software licensing."
        ))

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
        db.flush_settings()
        self.hide()
        event.ignore()

PreferencesDialog = PreferencesWindow
