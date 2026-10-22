# Components & Layout System in Fluent Design

This guide catalogs the core QFluentWidgets component suite, the 4px desktop spacing grid, and the native typography ramp.

---

## 1. The 4px Spacing Grid

Windows 11 desktop interfaces use a **4px modular grid** to balance visual breathing room with high information density:

| Spacing Token | Pixels | Usage |
| :--- | :--- | :--- |
| **`space-1`** | `4px` | Spacing between tightly grouped elements (e.g. icon and text inside a badge or button). |
| **`space-2`** | `8px` | Gap between list items, internal padding of compact controls, gap between cards in a dense list. |
| **`space-3`** | `12px` | Content margins inside cards (`CardWidget`), padding inside toolbars. |
| **`space-4`** | `16px` | Standard page edge padding, gap between major structural sections. |
| **`space-6`** | `24px` | Large hero section margins, top-level view headers. |

> [!TIP]
> Avoid web-scale paddings (32px to 64px) in productivity desktop workspaces. Desktop users expect efficient use of screen real estate.

---

## 2. Typography Ramp (Segoe UI Variable)

Windows 11 introduces **Segoe UI Variable**, which dynamically adjusts letter spacing and character geometry across optical sizes.

### Type Scale

| Role | Font Size | Weight | QFluentWidgets Implementation |
| :--- | :--- | :--- | :--- |
| **Caption** | `11px` / `12px` | Normal (`QFont.Normal`) | `setFont(label, 11, QFont.Normal)` |
| **Body** | `13px` / `14px` | Normal (`QFont.Normal`) | `setFont(label, 13, QFont.Normal)` (Default) |
| **Body Strong** | `13px` / `14px` | SemiBold (`QFont.DemiBold`) | `setFont(label, 13, QFont.DemiBold)` |
| **Subtitle** | `18px` / `20px` | SemiBold (`QFont.DemiBold`) | `setFont(label, 18, QFont.DemiBold)` |
| **Title** | `24px` / `28px` | SemiBold (`QFont.DemiBold`) | `setFont(label, 24, QFont.DemiBold)` |

### Setting Typography Globally or Per-Widget

```python
from qfluentwidgets import setFont, setFontFamilies
from PySide6.QtGui import QFont

# Set global font family priority (Windows 11 standard)
setFontFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei"])

# Configure a specific widget
setFont(my_label, fontSize=18, weight=QFont.DemiBold)
```

---

## 3. Cards & Container Hierarchy

Instead of creating raw `QFrame` instances and writing custom CSS border and hover animations, use QFluentWidgets' native card family:

### 1. `ElevatedCardWidget` (Hover Interactive)
Provides soft shadow elevation that elevates smoothly when hovered. Ideal for clickable timeline cues, recent files, and actionable items:

```python
from qfluentwidgets import ElevatedCardWidget
from PySide6.QtWidgets import QVBoxLayout, QLabel

class ActionableItemCard(ElevatedCardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(280, 110)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        # Content widgets...
```

### 2. `SimpleCardWidget` (Static Grouping)
A clean, flat card with a 1px subtle Fluent border stroke and 8px corner radius. Ideal for non-clickable content grouping, inspector panels, or property sheets.

### 3. `HeaderCardWidget` (Section Container)
Contains a designated top header bar and a content body area. Ideal for complex settings groups or multi-item inspectors.

---

## 4. Context Menus & Flyouts

### `RoundMenu` (Native Fluent Context Menu)
Replaces standard square `QMenu` with rounded corners, icons, and smooth pop-in animations:

```python
from qfluentwidgets import RoundMenu, Action, FluentIcon
from PySide6.QtGui import QCursor

def show_context_menu(self, global_pos):
    menu = RoundMenu(parent=self)
    menu.addAction(Action(FluentIcon.COPY, "Copy Timestamp", triggered=self.copy_ts))
    menu.addAction(Action(FluentIcon.SHARE, "Send to Premiere Pro", triggered=self.send_to_nle))
    menu.addSeparator()
    menu.addAction(Action(FluentIcon.DELETE, "Remove Cue", triggered=self.remove_cue))
    menu.exec(global_pos)
```

### `Flyout` & `FlyoutView` (Anchored Transient Popovers)
Replaces blocking popups with anchored floating cards pointing to a target widget:

```python
from qfluentwidgets import Flyout, FlyoutAnimationType, InfoBarIcon

# Instant transient notification/popover
Flyout.create(
    icon=InfoBarIcon.SUCCESS,
    title="Cue Copied",
    content="Timestamp 00:01:24 copied in Premiere format.",
    target=copy_button,
    parent=self,
    isClosable=True,
    aniType=FlyoutAnimationType.PULL_UP
)
```

---

## 5. Smooth Kinetic Scrolling (`SmoothScrollArea`)

Standard Qt `QScrollArea` exhibits stepped, jerky wheel movement. QFluentWidgets provides `SmoothScrollArea`, which applies smooth kinetic deceleration matching native Windows 11 precision trackpads and mouse wheels:

```python
from qfluentwidgets import SmoothScrollArea
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt

class TimelineScrollView(SmoothScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Transparent viewport so Mica backdrop shines through
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setStyleSheet("background: transparent;")
        
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(8)
        self.setWidget(self.container)
```
