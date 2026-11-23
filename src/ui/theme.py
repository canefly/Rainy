import sys
from enum import Enum
from typing import Dict, Any, Tuple
from PySide6.QtCore import Qt, QPoint, QPointF, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap, QImage, QBrush, QRadialGradient
from PySide6.QtWidgets import QWidget, QApplication
from qfluentwidgets import setThemeColor, Theme as FluentTheme, setTheme

from . import ui_config

ui_config.install()   # before anything can change a theme: keeps the settings file out of the install folder

def disable_menu_animations():
    """
    Globally disables sliding, masking, and fading animations for all QFluentWidgets menus:
    - DropDown and PullUp menus (ComboBox, RoundMenu, CheckableMenu, etc.)
    - Context menus (Right-click context menus across cards, dialogs, drawers)
    - Titlebar navigation menus (File, Edit, Export, Help)

    Preserves directional screen clamping (Drop Down vs Pull Up) while popping open
    instantaneously with zero latency.
    """
    try:
        from qfluentwidgets.components.widgets.menu import (
            DropDownMenuAnimationManager,
            PullUpMenuAnimationManager,
            FadeInDropDownMenuAnimationManager,
            FadeInPullUpMenuAnimationManager,
        )

        def _instant_drop_down_exec(self, pos):
            self.menu.clearMask()
            self.menu.move(self._endPosition(pos))

        def _instant_pull_up_exec(self, pos):
            self.menu.clearMask()
            self.menu.move(self._endPosition(pos))

        def _instant_fade_drop_exec(self, pos):
            self.menu.setWindowOpacity(1.0)
            self.menu.clearMask()
            self.menu.move(self._endPosition(pos))

        def _instant_fade_pull_exec(self, pos):
            self.menu.setWindowOpacity(1.0)
            self.menu.clearMask()
            self.menu.move(self._endPosition(pos))

        DropDownMenuAnimationManager.exec = _instant_drop_down_exec
        PullUpMenuAnimationManager.exec = _instant_pull_up_exec
        FadeInDropDownMenuAnimationManager.exec = _instant_fade_drop_exec
        FadeInPullUpMenuAnimationManager.exec = _instant_fade_pull_exec
    except Exception:
        pass

# Apply globally on module load
disable_menu_animations()


class ThemeVariant(Enum):
    PINK = "pink"
    BLUE = "blue"
    GREEN = "green"
    GRAY = "gray"
    BLACK = "black"

PALETTES: Dict[ThemeVariant, Dict[str, Any]] = {
    ThemeVariant.PINK: {
        "name": "Femboy Pink",
        "supports_glass": True,
        "accent": "#fb7185",
        "accent_light": "#e11d48",
        "accent_rgb": "251, 113, 133",
        "accent_rgb_light": "225, 29, 72",
        "bg_dark": "#121212",
        "header_dark": "#0e0c0f",
        "surface_dark": "#121212",
        "surface_hover_dark": "#1c181e",
        "border_dark": "#2a222c",
        "sidebar_dark": "#0e0c0f",
        "bg_light": "#faf5f6",
        "header_light": "#d8c8ce",
        "surface_light": "#ffffff",
        "surface_hover_light": "#fcebee",
        "border_light": "#e2cfd5",
        "sidebar_light": "#f3e8eb",
        "text_dark": "#ffffff",
        "text_light": "#1e1318",
        "muted_dark": "#fda4af",
        "muted_light": "#881337",
        "orbs": [
            (0.20, 0.24, 0.56, "#fb7185", 1.00),  # Primary Rose Pink (top-left)
            (0.80, 0.72, 0.62, "#c084fc", 0.85),  # Complementary Lavender/Violet (bottom-right)
            (0.52, 0.86, 0.46, "#f472b6", 0.70),  # Warm Blush Accent (bottom-center)
        ],
    },
    ThemeVariant.BLUE: {
        "name": "Cloudy Blue",
        "supports_glass": True,
        "accent": "#38bdf8",
        "accent_light": "#0284c7",
        "accent_rgb": "56, 189, 248",
        "accent_rgb_light": "2, 132, 199",
        "bg_dark": "#121212",
        "header_dark": "#070a12",
        "surface_dark": "#121212",
        "surface_hover_dark": "#161b28",
        "border_dark": "#1e2436",
        "sidebar_dark": "#05070c",
        "bg_light": "#f1f7ff",
        "header_light": "#c8daf2",
        "surface_light": "#ffffff",
        "surface_hover_light": "#e8f2fe",
        "border_light": "#cde0f7",
        "sidebar_light": "#e4effd",
        "text_dark": "#ffffff",
        "text_light": "#0b1526",
        "muted_dark": "#94a3b8",
        "muted_light": "#334155",
        "orbs": [
            (0.22, 0.25, 0.58, "#38bdf8", 1.00),  # Sky Cyan (top-left)
            (0.78, 0.70, 0.62, "#6366f1", 0.88),  # Deep Indigo/Cobalt (bottom-right)
            (0.50, 0.18, 0.48, "#60a5fa", 0.75),  # Ice Blue (top-center)
        ],
    },
    ThemeVariant.GREEN: {
        "name": "Matcha Green",
        "supports_glass": True,
        "accent": "#7ec89b",
        "accent_light": "#2d7a4f",
        "accent_rgb": "126, 200, 155",
        "accent_rgb_light": "45, 122, 79",
        "bg_dark": "#111613",
        "header_dark": "#0c100d",
        "surface_dark": "#151c18",
        "surface_hover_dark": "#1d2721",
        "border_dark": "#25332b",
        "sidebar_dark": "#0c100d",
        "bg_light": "#f4f8f4",
        "header_light": "#cfe0d2",
        "surface_light": "#ffffff",
        "surface_hover_light": "#e7f1e8",
        "border_light": "#cce0d0",
        "sidebar_light": "#e5efe7",
        "text_dark": "#f2f7f4",
        "text_light": "#16241b",
        "muted_dark": "#a3cbb1",
        "muted_light": "#365942",
        "orbs": [
            (0.22, 0.25, 0.58, "#7ec89b", 1.00),  # Creamy Matcha Leaf (top-left)
            (0.78, 0.70, 0.62, "#a3d9a5", 0.85),  # Warm Pistachio Sage (bottom-right)
            (0.50, 0.20, 0.48, "#5fb3a1", 0.75),  # Misty Botanical Eucalyptus (top-center)
        ],
    },
    ThemeVariant.GRAY: {
        "name": "Adobe Gray",
        "supports_glass": False,
        "accent": "#4391E0",
        "accent_light": "#147AF3",
        "accent_rgb": "67, 145, 224",
        "accent_rgb_light": "20, 122, 243",
        "bg_dark": "#1d1d1f",
        "header_dark": "#151517",
        "surface_dark": "#252528",
        "surface_hover_dark": "#2e2e32",
        "border_dark": "#38383d",
        "sidebar_dark": "#18181a",
        "bg_light": "#f5f5f7",
        "header_light": "#cfcfd6",
        "surface_light": "#ffffff",
        "surface_hover_light": "#ebebef",
        "border_light": "#d0d0d6",
        "sidebar_light": "#eaebee",
        "text_dark": "#f0f0f2",
        "text_light": "#18181b",
        "muted_dark": "#a1a1aa",
        "muted_light": "#52525b",
        "orbs": [],
    },
    ThemeVariant.BLACK: {
        "name": "OLED Black",
        "supports_glass": False,
        "accent": "#f4f4f5",
        "accent_light": "#18181b",
        "accent_rgb": "244, 244, 245",
        "accent_rgb_light": "24, 24, 27",
        "bg_dark": "#000000",
        "header_dark": "#000000",
        "surface_dark": "#0a0a0c",
        "surface_hover_dark": "#141417",
        "border_dark": "#222226",
        "sidebar_dark": "#000000",
        "bg_light": "#ffffff",
        "header_light": "#d6d9df",
        "surface_light": "#f8fafc",
        "surface_hover_light": "#f1f5f9",
        "border_light": "#e2e8f0",
        "sidebar_light": "#f1f5f9",
        "text_dark": "#ffffff",
        "text_light": "#090d16",
        "muted_dark": "#8e8e93",
        "muted_light": "#475569",
        "orbs": [],
    },
}

class ThemeManager:
    current_variant = ThemeVariant.PINK
    is_dark = True
    orb_glow_intensity: int = 25              # 0..100% (Default: 25% Subtle preset)
    glass_transparency: int = 65              # 0..100% (Default: 65% Balanced DWM glass preset)
    _orb_field_cache: Dict[Tuple[Any, ...], QPixmap] = {}
    _noise_brush = None
    _applied_fluent_state = None

    @classmethod
    def supports_glass_effects(cls, variant: ThemeVariant = None) -> bool:
        v = variant if variant is not None else cls.current_variant
        return bool(PALETTES.get(v, {}).get("supports_glass", True))

    @classmethod
    def get_pref_surface_qcolors(cls) -> Tuple[QColor, QColor, QColor]:
        """Returns (sidebar_bg, card_bg, border_color) dynamically scaled by glass_transparency."""
        pal = cls.get_palette()
        dark = cls.is_dark
        border_col = QColor(pal["border"])
        if pal["supports_glass"]:
            gt = max(0, min(100, int(cls.glass_transparency))) / 100.0
            card_a_dark = int(round((0.88 - gt * 0.36) * 255))
            card_a_light = int(round((0.92 - gt * 0.28) * 255))
            side_a_dark = int(round((0.42 - gt * 0.24) * 255))
            side_a_light = int(round((0.62 - gt * 0.25) * 255))
            side_col = QColor(0, 0, 0, max(0, min(255, side_a_dark))) if dark else QColor(255, 255, 255, max(0, min(255, side_a_light)))
            card_col = QColor(18, 18, 22, max(0, min(255, card_a_dark))) if dark else QColor(255, 255, 255, max(0, min(255, card_a_light)))
        else:
            side_col = QColor(pal["sidebar"])
            card_col = QColor(pal["surface"])
        return side_col, card_col, border_col

    @classmethod
    def get_noise_brush(cls):
        if cls._noise_brush is None:
            try:
                noise_img = QImage(':/qfluentwidgets/images/acrylic/noise.png')
                if not noise_img.isNull():
                    noise_tile = QImage(noise_img.size(), QImage.Format_ARGB32_Premultiplied)
                    noise_tile.fill(Qt.transparent)
                    np = QPainter(noise_tile)
                    np.setOpacity(0.04)  # 4% subtle grain to smooth color banding
                    np.drawImage(0, 0, noise_img)
                    np.end()
                    cls._noise_brush = QBrush(noise_tile)
            except Exception:
                cls._noise_brush = None
        return cls._noise_brush

    @classmethod
    def refresh_theme_styles(cls):
        app = QApplication.instance()
        if app:
            qss = cls.apply_theme(cls.current_variant, cls.is_dark)
            app.setStyleSheet(qss)

    @classmethod
    def set_orb_glow_intensity(cls, intensity: int):
        new_val = max(0, min(100, int(intensity)))
        if cls.orb_glow_intensity == new_val:
            return
        cls.orb_glow_intensity = new_val
        cls._refresh_all_windows()

    @classmethod
    def set_glass_transparency(cls, transparency: int):
        new_val = max(0, min(100, int(transparency)))
        if cls.glass_transparency == new_val:
            return
        cls.glass_transparency = new_val
        cls._refresh_all_windows()

    @classmethod
    def _refresh_all_windows(cls):
        app = QApplication.instance()
        if app:
            for w in app.topLevelWidgets():
                if w.isVisible():
                    w.update()

    @classmethod
    def _get_orb_field_pixmap(cls, screen_w: int, screen_h: int) -> QPixmap:
        sw = max(640, int(screen_w))
        sh = max(480, int(screen_h))
        key = (cls.current_variant, cls.is_dark, sw, sh)
        cached = cls._orb_field_cache.get(key)
        if cached is not None:
            return cached

        if len(cls._orb_field_cache) > 8:
            cls._orb_field_cache.clear()

        pix = QPixmap(sw, sh)
        pix.fill(Qt.transparent)
        p = PALETTES[cls.current_variant]
        orbs = p.get("orbs", [])
        diag = (sw * sw + sh * sh) ** 0.5

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        # Render multi-tone diffused radial orbs onto the virtual monitor canvas
        for nx, ny, rad_factor, hex_col, weight in orbs:
            cx = nx * sw
            cy = ny * sh
            radius = max(240.0, diag * rad_factor)
            base_c = QColor(hex_col)

            peak_alpha = int(round((140 if cls.is_dark else 110) * weight))
            mid_alpha = int(round(peak_alpha * 0.45))
            outer_alpha = int(round(peak_alpha * 0.14))

            grad = QRadialGradient(QPointF(cx, cy), radius)
            grad.setColorAt(0.00, QColor(base_c.red(), base_c.green(), base_c.blue(), min(255, peak_alpha)))
            grad.setColorAt(0.38, QColor(base_c.red(), base_c.green(), base_c.blue(), min(255, mid_alpha)))
            grad.setColorAt(0.72, QColor(base_c.red(), base_c.green(), base_c.blue(), min(255, outer_alpha)))
            grad.setColorAt(1.00, QColor(base_c.red(), base_c.green(), base_c.blue(), 0))

            painter.setBrush(QBrush(grad))
            painter.drawRect(0, 0, sw, sh)

        painter.end()
        cls._orb_field_cache[key] = pix
        return pix

    @classmethod
    def enable_window_backdrop(cls, window: QWidget):
        """Enables Windows 11 DWM glass (Desktop Acrylic) on consumer glass themes,
        or disables DWM glass on Pro Solid themes.
        """
        if sys.platform != "win32" or sys.getwindowsversion().build < 22000:
            return
        try:
            from ctypes import c_int, byref, pointer
            hwnd = int(window.winId())
            win_effect = getattr(window, "windowEffect", None)
            use_glass = cls.supports_glass_effects()
            if win_effect is not None:
                is_dark_flag = 1 if cls.is_dark else 0
                win_effect.DwmSetWindowAttribute(hwnd, 20, byref(c_int(is_dark_flag)), 4)
                # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2 (Enforce native Windows 11 rounded window corners)
                win_effect.DwmSetWindowAttribute(hwnd, 33, byref(c_int(2)), 4)
                if not use_glass:
                    # 1 = DWMSBT_NONE (Completely disable DWM glass/mica for Adobe Gray / OLED Black)
                    win_effect.disableBlurBehindWindow(hwnd)
                    win_effect.winCompAttrData.Attribute = 19  # WCA_ACCENT_POLICY
                    win_effect.accentPolicy.AccentState = 0    # ACCENT_DISABLED
                    win_effect.accentPolicy.AccentFlags = 0
                    win_effect.accentPolicy.GradientColor = 0
                    win_effect.SetWindowCompositionAttribute(hwnd, pointer(win_effect.winCompAttrData))
                    win_effect.DwmSetWindowAttribute(hwnd, 38, byref(c_int(1)), 4)
                else:
                    # Default Standard Windows 11 Desktop Acrylic (DWMSBT_TRANSIENTWINDOW = 3)
                    # Keep DWM blur-behind alpha compositing enabled (matching AcrylicWindow)
                    # so our paint_window_backdrop alpha channel blends over Desktop Acrylic.
                    win_effect.enableBlurBehindWindow(hwnd)
                    win_effect.accentPolicy.AccentFlags = 0
                    win_effect.accentPolicy.GradientColor = 0
                    win_effect.setMicaEffect(hwnd, cls.is_dark)
                    win_effect.DwmSetWindowAttribute(hwnd, 38, byref(c_int(3)), 4)
        except Exception:
            pass
        window.update()

    @classmethod
    def paint_window_backdrop(cls, window: QWidget, painter: QPainter):
        """Paints the backdrop according to theme archetype:
        - Pro Solid Themes (Adobe Gray, OLED Black): 100% opaque matte studio surface, zero glass, zero orbs.
        - Consumer Glass Themes (Femboy Pink, Cloudy Blue, Matcha Green):
          1. Desktop Raw (DWM blur) + Glass-Transparency-scaled base tint
          2. Screen-Space Glowing Orb Composite (monitor-anchored parallax)
          3. Frosted Mica Diffusion Veil
          4. 4% Acrylic Noise Texture
        """
        rect = window.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        pal = cls.get_palette()
        bg_hex = pal.get("bg", "#121212")

        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.setPen(Qt.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)

        # Pro Solid Themes (Adobe Gray / OLED Black): 100% Opaque wall, zero glass, zero orbs
        if not pal.get("supports_glass", True):
            solid_col = QColor(bg_hex)
            solid_col.setAlpha(255)
            painter.fillRect(rect, solid_col)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            return

        # Consumer Glass Themes (Femboy Pink / Cloudy Blue / Matcha Green):
        glass_t = max(0, min(100, int(cls.glass_transparency))) / 100.0
        # Windows 11 Desktop Acrylic (DWMSBT_TRANSIENTWINDOW = 3):
        # Scales smoothly from 255 (0% Solid) -> ~76 (65% Balanced, our usual ~25-30% tint) -> 22 (100% Max Glass)
        if cls.is_dark:
            base_alpha = int(round(255 - glass_t * 233))  # 255 @ 0%, 104 @ 65%, 22 @ 100%
        else:
            base_alpha = int(round(255 - glass_t * 215))  # 255 @ 0%, 115 @ 65%, 40 @ 100%
        min_alpha = 20

        base_tint = QColor(bg_hex)
        base_tint.setAlpha(max(min_alpha, min(255, base_alpha)))
        painter.fillRect(rect, base_tint)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Layer 2: Screen-Space Glowing Orb Composite (Monitor-Anchored Parallax)
        intensity = max(0, min(100, int(cls.orb_glow_intensity)))
        if intensity > 0:
            screen = None
            if window.windowHandle() and window.windowHandle().screen():
                screen = window.windowHandle().screen()
            elif window.screen():
                screen = window.screen()
            elif QApplication.instance():
                screen = QApplication.primaryScreen()

            if screen is not None:
                s_geom = screen.geometry()
                sw, sh = max(640, s_geom.width()), max(480, s_geom.height())
                orb_pix = cls._get_orb_field_pixmap(sw, sh)

                global_tl = window.mapToGlobal(QPoint(0, 0))
                sx = (global_tl.x() - s_geom.x()) % sw
                sy = (global_tl.y() - s_geom.y()) % sh

                painter.save()
                norm = intensity / 100.0
                painter.setOpacity(min(1.0, norm * 0.90))
                src_w = min(rect.width(), sw - sx)
                src_h = min(rect.height(), sh - sy)
                if src_w > 0 and src_h > 0:
                    painter.drawPixmap(
                        QRect(0, 0, src_w, src_h),
                        orb_pix,
                        QRect(sx, sy, src_w, src_h)
                    )
                if src_w < rect.width():
                    rem_w = rect.width() - src_w
                    painter.drawPixmap(
                        QRect(src_w, 0, rem_w, src_h),
                        orb_pix,
                        QRect(0, sy, min(rem_w, sw), src_h)
                    )
                if src_h < rect.height():
                    rem_h = rect.height() - src_h
                    painter.drawPixmap(
                        QRect(0, src_h, src_w, rem_h),
                        orb_pix,
                        QRect(sx, 0, src_w, min(rem_h, sh))
                    )
                painter.restore()

            # Layer 3: Frosted Mica Diffusion Veil
            veil_factor = 1.0 - glass_t * 0.55
            veil_alpha = int(round((32 if cls.is_dark else 42) * max(0.0, veil_factor)))
            if veil_alpha > 2:
                veil_col = QColor(bg_hex)
                veil_col.setAlpha(veil_alpha)
                painter.fillRect(rect, veil_col)

        # Layer 4: 4% Fluent Acrylic noise grain to eliminate 8-bit color banding
        noise = cls.get_noise_brush()
        if noise is not None:
            painter.fillRect(rect, noise)

    @classmethod
    def get_palette(cls) -> Dict[str, Any]:
        p = PALETTES[cls.current_variant]
        d = cls.is_dark
        return {
            "variant": cls.current_variant,
            "name": p["name"],
            "supports_glass": p.get("supports_glass", True),
            "is_dark": d,
            "accent": p["accent"] if d else p.get("accent_light", p["accent"]),
            "accent_rgb": p["accent_rgb"] if d else p.get("accent_rgb_light", p["accent_rgb"]),
            "header": p["header_dark"] if d else p["header_light"],
            "bg": p["bg_dark"] if d else p["bg_light"],
            "surface": p["surface_dark"] if d else p["surface_light"],
            "surface_hover": p["surface_hover_dark"] if d else p["surface_hover_light"],
            "border": p["border_dark"] if d else p["border_light"],
            "sidebar": p["sidebar_dark"] if d else p["sidebar_light"],
            "text": p["text_dark"] if d else p["text_light"],
            "muted": p["muted_dark"] if d else p["muted_light"],
            "orbs": p.get("orbs", []),
        }

    @classmethod
    def apply_theme(cls, variant: ThemeVariant, dark: bool = True) -> str:
        cls.current_variant = variant
        cls.is_dark = dark
        pal = cls.get_palette()

        # 1. Configure native QFluentWidgets theme engine (only when dark/accent actually changes)
        fluent_state = (bool(dark), pal["accent"])
        if cls._applied_fluent_state != fluent_state:
            setTheme(FluentTheme.DARK if dark else FluentTheme.LIGHT)
            setThemeColor(pal["accent"])
            cls._applied_fluent_state = fluent_state

        accent = pal["accent"]
        rgb = pal["accent_rgb"]
        header = pal["header"]
        bg = pal["bg"]
        surface = pal["surface"]
        surface_hover = pal["surface_hover"]
        border = pal["border"]
        sidebar = pal["sidebar"]
        text = pal["text"]
        muted = pal["muted"]

        sb_handle = "rgba(255, 255, 255, 0.18)" if dark else "rgba(0, 0, 0, 0.22)"
        note_card_bg = "rgba(255, 255, 255, 0.025)" if dark else "#ffffff"
        if pal["supports_glass"]:
            gt = max(0, min(100, int(cls.glass_transparency))) / 100.0
            card_a_dark = round(0.88 - gt * 0.36, 2)    # 0.88 @ 0%, 0.65 @ 65%, 0.52 @ 100%
            card_a_light = round(0.92 - gt * 0.28, 2)
            side_a_dark = round(0.42 - gt * 0.24, 2)
            side_a_light = round(0.62 - gt * 0.25, 2)
            pref_sidebar_bg = f"rgba(0, 0, 0, {side_a_dark})" if dark else f"rgba(255, 255, 255, {side_a_light})"
            pref_card_bg = f"rgba(18, 18, 22, {card_a_dark})" if dark else f"rgba(255, 255, 255, {card_a_light})"
        else:
            pref_sidebar_bg = sidebar
            pref_card_bg = surface

        # Standard Windows 11 Settings colors (decoupled from workspace accent themes)
        pref_win_text = "#ffffff" if dark else "#18181b"
        pref_win_unselected = "#f4f4f5" if dark else "#18181b"
        pref_win_muted = "#a1a1aa" if dark else "#71717a"
        pref_win_body = "#e4e4e7" if dark else "#27272a"
        pref_win_hover_bg = "rgba(255, 255, 255, 0.06)" if dark else "rgba(0, 0, 0, 0.05)"
        pref_win_sel_bg = "rgba(255, 255, 255, 0.09)" if dark else "rgba(0, 0, 0, 0.07)"

        # 2. Surgical, targeted layout QSS strictly for containers, splitters, tables, and scrollbars
        return f"""
        /* Segoe UI Variable Global Typography */
        QWidget {{
            font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Nirmala UI', 'Microsoft YaHei', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        QLabel {{
            color: {text};
            background: transparent;
        }}

        /* Subtle Fluent Window Chrome */
        QWidget#MainWindow {{
            color: {text};
            background: transparent;
            background-color: transparent;
        }}
        QWidget#MainBody, QStackedWidget, QWidget#HomeView {{
            background: transparent;
            background-color: transparent;
            border: none;
        }}
        QWidget#TitleBar, TitleBar#TitleBar, QFrame#TitleBar {{
            background-color: transparent;
            border-bottom: 1px solid {border};
        }}
        QLabel#TitleBrandingText {{
            color: {text};
            font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}

        /* Native Sidebar Dock - Pure acrylic backdrop across window */
        QFrame#SidebarDock {{
            background-color: transparent;
            background: transparent;
            border-right: none;
            border: none;
        }}

        /* Workspace Split Panes & Splitter */
        QFrame#ScriptViewerPane, QFrame#TimelineListPane {{
            background-color: transparent;
            background: transparent;
            border: none;
        }}

        /* Timeline Cards & Scroll Area - Transparent so they blend directly into the pane */
        QFrame#TimelineCard, CardWidget#TimelineCard {{
            background-color: transparent;
            background: transparent;
            border: none;
        }}
        QScrollArea, SmoothScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
            background: transparent;
            background-color: transparent;
            border: none;
        }}
        QLabel#PaneTitle {{
            color: {accent};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QSplitter::handle:horizontal {{
            background-color: transparent;
            border: none;
            width: 12px;
        }}
        QSplitter::handle:horizontal:hover {{
            background-color: transparent;
            border: none;
        }}

        /* Modern Desktop ScrollBars (Zero checkered/dithered artifacts) */
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {sb_handle};
            border-radius: 4px;
            min-height: 24px;
            border: none;
        }}
        QScrollBar::handle:vertical:hover {{
            background: rgba({rgb}, 0.6);
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
        QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{
            background: none;
            border: none;
            height: 0px;
            width: 0px;
        }}

        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {sb_handle};
            border-radius: 4px;
            min-width: 24px;
            border: none;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: rgba({rgb}, 0.6);
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
        QScrollBar::up-arrow:horizontal, QScrollBar::down-arrow:horizontal {{
            background: none;
            border: none;
            height: 0px;
            width: 0px;
        }}

        /* Native Preferences Window, Sidebar & Table Controls */
        QWidget#PreferencesDialog, QWidget#DataVaultWindow, QWidget#AboutWindow, QWidget#DesktopToolWindow {{
            background: transparent;
            background-color: transparent;
            color: {text};
        }}
        QWidget#VaultHeader, QWidget#AboutHeader, QWidget#ToolWindowTitleBar {{
            background-color: transparent;
            border-bottom: 1px solid {border};
        }}
        QWidget#PrefHeader {{
            background-color: transparent;
            border: none;
            border-bottom: none;
        }}
        QFrame#PrefSidebar {{
            background: transparent;
            background-color: transparent;
            border: none;
        }}
        QFrame#PrefContent {{
            background: transparent;
            background-color: transparent;
            border: none;
        }}
        QFrame#PrefSidebar QListWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QFrame#PrefSidebar QListWidget::item {{
            color: {pref_win_unselected};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
            font-size: 14px;
            font-weight: 600;
            padding-left: 12px;
            border-radius: 5px;
            margin: 2px 0px;
            border: none;
        }}
        QFrame#PrefSidebar QListWidget::item:hover {{
            background-color: {pref_win_hover_bg};
            color: {pref_win_text};
            border: none;
        }}
        QFrame#PrefSidebar QListWidget::item:selected {{
            background-color: {pref_win_sel_bg};
            color: {pref_win_text};
            font-weight: 600;
            border: none;
            border-left: none;
        }}
        QLabel#PrefTabTitle {{
            color: {pref_win_text};
            font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
            font-size: 28px;
            font-weight: 700;
        }}
        QLabel#PrefTabSubtitle {{
            color: {pref_win_muted};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            font-size: 13.5px;
            font-weight: 400;
        }}
        QLabel#PrefSectionTitle {{
            color: {pref_win_text};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            font-size: 14.5px;
            font-weight: 600;
        }}
        QLabel#PrefCaption {{
            color: {pref_win_muted};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            font-size: 12.5px;
            font-weight: 400;
        }}
        QLabel#PrefBodyText {{
            color: {pref_win_body};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            font-size: 13px;
            font-weight: 400;
        }}
        QFrame#PrefCard {{
            background: transparent;
            background-color: transparent;
            border: none;
        }}
        QTableWidget {{
            background-color: {pref_card_bg};
            border: 1px solid {border};
            border-radius: 8px;
            color: {text};
            gridline-color: {border};
            selection-background-color: rgba({rgb}, 0.12);
            selection-color: {text};
        }}
        QTableWidget::item {{
            color: {text};
            padding: 6px 10px;
            border-bottom: 1px solid {border};
        }}
        QHeaderView::section {{
            background-color: {surface_hover};
            color: {muted};
            font-size: 11px;
            font-weight: 600;
            border: none;
            border-bottom: 1px solid {border};
            padding: 8px 10px;
        }}
        QRadioButton {{
            color: {text};
            font-size: 13px;
            spacing: 8px;
        }}

        /* Compact Checkboxes in Cards and Drawers */
        CheckBox#CompactCheckBox,
        QFrame#TimelineCard CheckBox,
        QFrame#LinkCard CheckBox,
        QFrame#NoteCard CheckBox {{
            min-width: 22px;
            max-width: 22px;
            min-height: 22px;
            max-height: 22px;
            width: 22px;
            height: 22px;
            spacing: 0px;
            margin: 0px;
            padding: 0px;
        }}

        /* Tool Window Drawer Cards */
        QFrame#NoteCard, QFrame#LinkCard {{
            background-color: {note_card_bg};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QFrame#NoteCard:hover, QFrame#LinkCard:hover {{
            background-color: {surface_hover};
            border-color: rgba({rgb}, 0.35);
        }}
        QLabel#DrawerProgressLabel {{
            color: {muted};
        }}
        QTextEdit#DrawerNoteInput {{
            background-color: {surface};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 6px;
            font-size: 11px;
        }}
        """

