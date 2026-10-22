# Desktop Windowing & DWM Chrome in Fluent Design

Desktop applications have a massive operational advantage over web applications: **true OS multi-windowing, multi-monitor productivity, and deep Desktop Window Manager (DWM) integration**.

This guide details how to build native desktop windowing architectures using PySide6 and QFluentWidgets.

---

## 1. Modeless Architecture Over Modals

### The Video Editor / Desktop Paradigm
Web applications are trapped inside a single browser viewport, forcing developers to use blocking overlays and full-screen modals. 

In desktop workflows (e.g. video editors scrubbing timelines, developers using IDEs, audio engineers mixing tracks):
- Users operate on **two or more monitors**.
- Secondary panels—such as **Settings**, **Document Vaults**, **Source Link Inspectors**, and **Notes**—must be accessible simultaneously without freezing the timeline or locking the main window.

### Anti-Pattern: Blocking `QDialog.exec()`
```python
# 🚫 ANTI-PATTERN: Freezes the entire desktop application event loop!
# User cannot scrub timeline, click other tools, or move this to Monitor 2 independently.
dialog = PreferencesDialog(self)
dialog.exec()
```

### Native Pattern: Independent Modeless Tool Windows
```python
# ✅ FLUENT DESKTOP PATTERN: Modeless, top-level, multi-monitor capable window
class ToolWindowManager:
    _instance_vault = None

    @classmethod
    def open_vault(cls, parent_window=None):
        if cls._instance_vault is None:
            cls._instance_vault = DataVaultWindow()
        
        # Bring to front if already open
        cls._instance_vault.show()
        cls._instance_vault.raise_()
        cls._instance_vault.activateWindow()
```

---

## 2. Window Types & Chrome

QFluentWidgets and `qframelesswindow` provide specialized window classes:

| Class | Base Layer Material | Primary Use Case |
| :--- | :--- | :--- |
| **`FluentWindow`** | Mica (Win 11) | Standard main app window with collapsible left navigation bar. |
| **`MSFluentWindow`** | Mica (Win 11) | Microsoft Store style window with compact header/sidebar navigation. |
| **`SplitFluentWindow`** | Mica (Win 11) | Main window with split navigation interface. |
| **`FramelessWindow`** | Clean native surface | Secondary modeless tool windows (Settings, Vault, Inspectors). |
| **`AcrylicWindow`** | Frosted glass Acrylic | Floating HUD tools, transient command palettes, or overlay widgets. |

---

## 3. Custom TitleBar & Snap Layouts

Windows 11 introduced **Snap Layouts** (hovering over the maximize button reveals grid snapping options). A poorly implemented frameless window breaks this native OS feature.

### Utilizing Native TitleBars
QFluentWidgets provides built-in title bars (`MSFluentTitleBar`, `SplitTitleBar`) that integrate with DWM window messaging (`WM_NCHITTEST` and `WS_MAXIMIZEBOX`).

```python
from qfluentwidgets import MSFluentWindow, MSFluentTitleBar, SearchLineEdit
from PySide6.QtWidgets import QHBoxLayout, QLabel
from PySide6.QtGui import QIcon

class CustomTitleBar(MSFluentTitleBar):
    """Native Fluent title bar with search and editor status."""
    def __init__(self, parent):
        super().__init__(parent)
        
        # The titleBar already contains minBtn, maxBtn, closeBtn
        # Inject custom widgets into the titleBar layout:
        self.search_bar = SearchLineEdit(self)
        self.search_bar.setPlaceholderText("Search cues or timestamps...")
        self.search_bar.setFixedWidth(280)
        
        # Insert before window control buttons
        insert_idx = max(1, self.layout().count() - 3)
        self.layout().insertWidget(insert_idx, self.search_bar)
```

### Height and Drag Region Rules
- **Standard Height:** Fixed `40px` (or `h-10`).
- **Drag Region:** The titlebar must respond to mouse drag events to move the window across monitors. Do NOT put full-bleed clickable buttons that cover the draggable area.
- **Z-Index & Overlays:** In native PySide6, never position child dialogs or drawer frames covering the `(0, 0, width, 40)` coordinate space. The title bar must remain interactable and draggable at all times.

---

## 4. Modeless Tool Window Template

Here is the production blueprint for creating secondary tool windows (e.g., Preferences, Data Vault, Notes):

```python
from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import setTheme, setThemeColor, Theme
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

class SecondaryToolWindow(FramelessWindow):
    """A modeless desktop tool window that can be moved to secondary displays."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SecondaryToolWindow")
        self.setWindowTitle(title)
        self.resize(720, 520)
        self.setMinimumSize(500, 380)
        
        # Configure title bar
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setFixedHeight(36)
        
        # Central layout
        self.container = QWidget(self)
        self.main_layout = QVBoxLayout(self.container)
        self.main_layout.setContentsMargins(16, 40, 16, 16)
        self.main_layout.setSpacing(12)
        
        # Make window stay on top optionally, or standard desktop window
        # self.setWindowFlags(self.windowFlags() | Qt.Tool)
        
    def closeEvent(self, event):
        # Allow hide instead of destroy for fast re-opening
        self.hide()
        event.ignore()
```
