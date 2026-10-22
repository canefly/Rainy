# Materials & Elevation in Windows 11 Fluent Design

This guide outlines the exact rules for implementing **Mica**, **Acrylic**, and **Elevation Layering** in desktop applications using PySide6 and QFluentWidgets.

---

## 1. The Materials Philosophy

Windows 11 Fluent Design replaces flat, opaque planes with luminous, context-aware materials. These materials communicate visual hierarchy, window state, and spatial relationship to the desktop.

### Summary Comparison

| Attribute | Mica (Canvas Material) | Acrylic (Transient Material) |
| :--- | :--- | :--- |
| **Visual Nature** | Opaque, dynamic tint derived from user's desktop wallpaper. | Semi-transparent frosted glass with live gaussian blur & noise. |
| **Performance Impact** | Extremely low (uses pre-sampled static wallpaper texture). | Moderate to high (requires continuous GPU compositing behind window). |
| **State Awareness** | Automatically falls back to a neutral solid color when window loses focus. | Blurs content continuously or dims; requires active dismissal. |
| **Proper Usage** | **Primary application window backdrop / base layer.** | **Transient overlays only**: Menus, flyouts, popovers, command palettes. |
| **Strict Anti-Pattern** | Do not place on small floating cards or popup menus. | **Do not use as the full background of primary productivity windows.** |

---

## 2. Deep Dive: Mica & Mica Alt

### How Mica Works
Mica does not sample live pixels from applications behind it. Instead, it reads the Windows desktop wallpaper and system color scheme once, applies a subtle neutral luminosity mask, and paints the window frame.

- **Active State:** The app window reflects the desktop wallpaper's subtle warmth or coolness.
- **Inactive State:** As soon as the user focuses another window, the Mica material switches to a neutral, solid gray/dark shade (`#202020` in dark mode, `#f3f3f3` in light mode). This visually clarifies which window currently accepts keyboard and mouse events.

### Mica Alt
Mica Alt provides a higher-contrast tint of the wallpaper color. In native Windows applications (like modern File Explorer and Edge), Mica Alt is applied to the **tab strip or title bar**, while standard Mica or a subtle card layer hosts the body content.

### Implementing Mica in QFluentWidgets

Inherit from `FluentWindow` (or `MSFluentWindow` / `SplitFluentWindow`). Mica is supported on Windows 11 natively:

```python
from qfluentwidgets import FluentWindow, setTheme, Theme

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        
        # 1. Enable native Mica backdrop (Windows 11)
        self.setMicaEffectEnabled(True)
        
        # 2. Window geometry
        self.resize(1200, 800)
        self.setWindowTitle("Rainy Tool")
```

> [!IMPORTANT]
> Do NOT set a solid QSS `background-color` on `MainWindow` (e.g. `QWidget#MainWindow { background-color: #1a161b; }`). Doing so overrides the native DWM Mica composition and reverts the window to an opaque flat canvas.

---

## 3. Deep Dive: Acrylic

### In-App vs Background Acrylic
1. **Background Acrylic:** Samples the desktop and windows behind the app frame. Used for context menus (`RoundMenu`) and system flyouts.
2. **In-App Acrylic:** Samples pixels *inside* the current application window behind the overlay. Used for transient side sheets, command palettes, or search overlays that slide over content.

### Rules of Acrylic Usage

1. **Rule of Transience:** Acrylic surfaces must be dismissible with a click outside (light-dismiss) or a quick action. They are never permanent work surfaces.
2. **Never Stack Acrylic:** Placing an acrylic flyout on top of an acrylic window results in visual noise, muddy colors, and severe rendering performance drops. Always place Acrylic surfaces over an opaque Mica or Card layer.
3. **No Adjacent Acrylic Panes:** Do not place two acrylic frames side-by-side without clear demarcation. The blurred textures will clash and create distracting seam artifacts.
4. **Theme Tinting:** Tint the Acrylic layer subtly with your application's primary accent color so the frosted glass matches the visual identity:
   ```python
   from qfluentwidgets import setThemeColor
   setThemeColor("#fb7185")  # Acrylic popups inherit the subtle accent tint
   ```

---

## 4. The Windows 11 Elevation & Layering System

Windows 11 does NOT rely on heavy, blurry drop shadows (common in web neumorphism and Material Design 2). Instead, depth is constructed through **Layering** and **Subtle Structural Strokes**.

### The Elevation Hierarchy Scale

```
Z-Index / Elevation Level
▲
│  [128] Top-Level Modeless Windows / Critical Dialogs (MessageBox, Tool Windows)
│  [32]  Transient Overlays (RoundMenu, Flyout, TeachingTip)
│  [16]  Tooltips & Transient Badges
│  [8]   Cards & Content Containers (CardWidget, ElevatedCardWidget)
│  [2]   Interactive In-Page Controls (PushButton, LineEdit, ComboBox)
│  [0]   Mica Base Canvas (Window Background)
▼
```

### Elevation Stroke Rules (The 1px Border Rule)
Every elevated surface in Windows 11 uses a **1px subtle border stroke** to clearly define its silhouette against lower layers.

- **Dark Theme Stroke:** `1px solid rgba(255, 255, 255, 0.08)` to `rgba(255, 255, 255, 0.12)`.
- **Light Theme Stroke:** `1px solid rgba(0, 0, 0, 0.06)` to `rgba(0, 0, 0, 0.08)`.
- **Active / Hover Stroke:** Slightly higher alpha (`rgba(255, 255, 255, 0.20)` or subtle accent tint `rgba(accent_rgb, 0.40)`).
- **Prohibited:** Never use thick (2px+), bright, saturated neon borders (e.g. pure `#fbbf24` or `#fb7185`) on non-focused cards.

### Implementing Elevation Cards in PySide6

Use QFluentWidgets' dedicated card containers instead of hand-crafted `QFrame` widgets:

```python
from qfluentwidgets import ElevatedCardWidget, SimpleCardWidget
from PySide6.QtWidgets import QVBoxLayout, QLabel

class TimelineCueCard(ElevatedCardWidget):
    """Card with built-in Fluent elevation, hover animation, and 1px stroke."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 90)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        
        self.timestamp = QLabel("00:01:24", self)
        self.cue_text = QLabel("Host begins product demo", self)
        
        layout.addWidget(self.timestamp)
        layout.addWidget(self.cue_text)
```

`ElevatedCardWidget` automatically provides the correct Fluent shadow elevation and smoothly increases elevation on hover without any custom CSS!
