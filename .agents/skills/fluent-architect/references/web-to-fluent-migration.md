# Web-to-Fluent Migration Guide (Rainy Native Case Study)

This guide provides concrete before-and-after refactoring patterns to dismantle web-emulation anti-patterns and implement authentic Windows 11 Fluent desktop architecture.

---

## Case 1: Main Window Material (Acrylic vs Mica)

### 🚫 The Web / Anti-Pattern Code
Using `AcrylicWindow` as the base frame, and then overriding it with a heavy dark opaque QSS background:

```python
# BEFORE: window.py
from qframelesswindow import AcrylicWindow

class MainWindow(AcrylicWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        # Problem 1: Full-window acrylic causes window-dragging lag and blur churn
        # Problem 2: QSS sets solid opaque background, cancelling the material anyway!
```
```css
/* theme.py */
QWidget#MainWindow {
    background-color: #1a161b; /* Opaque solid fill overrides material */
}
```

### ✅ The Fluent Desktop Architecture
Inherit from `FluentWindow` (or `MSFluentWindow`) and enable native Windows 11 **Mica**. Allow the Mica canvas to serve as the base layer without painting solid dark QSS over it:

```python
# AFTER: window.py
from qfluentwidgets import FluentWindow, setThemeColor, setTheme, Theme

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("KNOT TOOLS")
        
        # 1. Enable native Mica backdrop (samples desktop wallpaper)
        self.setMicaEffectEnabled(True)
        
        # 2. Window proportions
        self.resize(1340, 840)
        self.setMinimumSize(1024, 640)
        
        # 3. Transparent main body so Mica breathes through
        # (Content will be elevated using CardWidget and ElevatedCardWidget)
```

---

## Case 2: Dialogs vs Modeless Multi-Monitor Windows

### 🚫 The Web / Anti-Pattern Code
Using blocking modal dialogs (`QDialog.exec()`) for work panels like Preferences or Data Vault:

```python
# BEFORE
def open_settings(self):
    dialog = PreferencesDialog(self)
    dialog.exec()  # 🚫 Freezes the timeline, cannot be moved to 2nd monitor independently
```

### ✅ The Fluent Desktop Architecture
Implement a modeless desktop tool window (`FramelessWindow` or `QWidget`) that can be detached, moved to Monitor 2, and kept open side-by-side:

```python
# AFTER
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.preferences_window = None

    def open_settings(self, initial_tab=0):
        if self.preferences_window is None:
            self.preferences_window = PreferencesWindow()
        
        self.preferences_window.set_tab(initial_tab)
        self.preferences_window.show()
        self.preferences_window.raise_()
        self.preferences_window.activateWindow()
```

---

## Case 3: CSS Neon Borders vs Elevation Layering

### 🚫 The Web / Anti-Pattern Code
Writing hundreds of lines of QSS with saturated neon border colors and arbitrary paddings:

```css
/* BEFORE: theme.py */
QFrame#TimelineCard {
    background-color: #251f27;
    border: 1px solid #3d3240;
    border-radius: 6px;
}
QFrame#TimelineCard:hover {
    background-color: rgba(251, 113, 133, 0.08);
    border-color: #fb7185; /* Saturated neon border */
}
QSplitter::handle:horizontal:hover {
    background-color: #fbbf24; /* Web yellow neon glow */
}
```

### ✅ The Fluent Desktop Architecture
Use `ElevatedCardWidget` with subtle Windows 11 hairline stroke (`rgba(255, 255, 255, 0.08)`) and built-in kinetic elevation shadow:

```python
# AFTER: components/timeline_card.py
from qfluentwidgets import ElevatedCardWidget
from PySide6.QtWidgets import QVBoxLayout, QLabel

class TimelineCueCard(ElevatedCardWidget):
    """Native Fluent card with automatic hover elevation and 1px subtle stroke."""
    def __init__(self, cue_data, parent=None):
        super().__init__(parent)
        self.cue_data = cue_data
        self.setFixedSize(280, 84)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        
        # Sub-widgets...
        # Hover elevation and subtle border are handled natively by QFluentWidgets!
```

---

## Case 4: Theme Tokens vs 400-Line QSS Overrides

### 🚫 The Web / Anti-Pattern Code
Manually defining dozens of color strings and interpolating them into a massive monolithic stylesheet that strips QFluentWidgets of its built-in interactive states.

### ✅ The Fluent Desktop Architecture
Configure the global QFluentWidgets theme engine. Pass the accent color to `setThemeColor()`, and let QFluentWidgets automatically generate hover, press, focus, and border shades:

```python
# AFTER: theme.py
from qfluentwidgets import setThemeColor, setTheme, Theme

def apply_fluent_theme(accent_hex: str, dark: bool = True):
    # 1. Configure the core engine
    setTheme(Theme.DARK if dark else Theme.LIGHT)
    setThemeColor(accent_hex)
    
    # 2. Minimal, surgical QSS strictly for custom layout geometry (margins/dividers)
    # Never override QPushButton, QLineEdit, or QScrollArea styling directly!
```

---

## Case 5: Programmatic Timeline Auto-Scrolling

### 🚫 The Web / Anti-Pattern Code
Using web-like URL hashes, string matching, or layout jumps to scroll content.

### ✅ The Fluent Desktop Architecture
Use PySide6 native scroll APIs on `SmoothScrollArea`:

```python
# AFTER: In WorkspaceView
def scroll_to_cue(self, target_card: QWidget):
    """Smoothly scroll the native viewport to center the target cue card."""
    scroll_bar = self.timeline_scroll_area.verticalScrollBar()
    card_pos_y = target_card.mapTo(self.timeline_container, target_card.rect().topLeft()).y()
    viewport_height = self.timeline_scroll_area.viewport().height()
    
    target_value = max(0, card_pos_y - (viewport_height // 2) + (target_card.height() // 2))
    scroll_bar.setValue(target_value)
```
