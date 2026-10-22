# Styling, Theming & Tokens in Fluent Design

This guide details how to style PySide6 applications with QFluentWidgets, replace fragile web-like QSS overrides with native design tokens, and enforce the **60-30-10 Fluent Color Rule**.

---

## 1. The 60-30-10 Fluent Color Rule

Web applications often suffer from "color flooding"—covering 50% or more of the screen in intense saturated accent colors, bright neon borders, and glowing cards.

Native Windows 11 Fluent Design follows a disciplined balance:

```
┌────────────────────────────────────────────────────────┐
│  60% Dominant Neutral Canvas (Mica / Window Base)      │
│  - Dark Theme: #202020 / Desktop wallpaper tint        │
│  - Light Theme: #f3f3f3 / Desktop wallpaper tint       │
├────────────────────────────────────────────────────────┤
│  30% Structural Surfaces & Cards (Layer 1 & 2)         │
│  - Dark Theme: #2b2b2b, subtle 1px stroke (alpha 0.08) │
│  - Light Theme: #ffffff, subtle 1px stroke (alpha 0.06)│
├────────────────────────────────────────────────────────┤
│  10% Restrained Accent Highlights (Interactive States) │
│  - Primary Action Buttons (PrimaryPushButton)          │
│  - Active Navigation Pill Indicator                    │
│  - Checked Switches & Sliders                          │
│  - Focus Rings                                         │
└────────────────────────────────────────────────────────┘
```

> [!WARNING]
> Never use your primary accent color (e.g. pink, blue, amber) as the background fill of general content cards, table rows, or full headers. Doing so creates visual fatigue and destroys native desktop aesthetics.

---

## 2. Using QFluentWidgets Built-In Theming Engine

QFluentWidgets includes an internal design system engine that automatically calculates hover states, pressed states, border alphas, and ripple animations when you supply a theme color.

### Core API

```python
from qfluentwidgets import setTheme, setThemeColor, Theme, qconfig

# 1. Switch Theme Mode
setTheme(Theme.DARK)   # or Theme.LIGHT, Theme.AUTO

# 2. Set Application Accent Color
# QFluentWidgets automatically generates light/dark variations and hover colors!
setThemeColor("#fb7185")  # Femboy Pink
```

### Listening to Theme Changes
```python
# React to system or user theme toggles cleanly
qconfig.themeChanged.connect(lambda theme: print(f"Theme switched to: {theme}"))
```

---

## 3. Defining Clean Color Palettes

Instead of maintaining 400 lines of manual QSS for every color variant, define palettes as **design token dictionaries** that configure `setThemeColor()` and subtle container surfaces:

```python
from enum import Enum
from dataclasses import dataclass
from typing import Dict

class ThemeVariant(Enum):
    PINK = "pink"
    GRAY = "gray"
    BLACK = "black"
    BLUE = "blue"

@dataclass(frozen=True)
class FluentPalette:
    name: str
    accent: str           # The 10% interactive highlight
    canvas_dark: str      # 60% window background fallback
    surface_dark: str     # 30% card surface
    border_dark: str      # 1px subtle stroke

THEME_PALETTES: Dict[ThemeVariant, FluentPalette] = {
    ThemeVariant.PINK: FluentPalette(
        name="Femboy Pink",
        accent="#fb7185",
        canvas_dark="#201a1c",
        surface_dark="#2b2326",
        border_dark="rgba(255, 255, 255, 0.08)"
    ),
    ThemeVariant.GRAY: FluentPalette(
        name="Adobe Gray",
        accent="#38bdf8",
        canvas_dark="#1e1e1f",
        surface_dark="#282829",
        border_dark="rgba(255, 255, 255, 0.08)"
    ),
    ThemeVariant.BLACK: FluentPalette(
        name="OLED Black",
        accent="#38bdf8",
        canvas_dark="#000000",
        surface_dark="#121212",
        border_dark="rgba(255, 255, 255, 0.12)"
    ),
    ThemeVariant.BLUE: FluentPalette(
        name="Cloudy Blue",
        accent="#38bdf8",
        canvas_dark="#161b26",
        surface_dark="#202636",
        border_dark="rgba(255, 255, 255, 0.08)"
    ),
}
```

---

## 4. Why Giant Web QSS Files Break Fluent UI

In web development, CSS is used to build everything from scratch. In Qt desktop development with QFluentWidgets, injecting a massive custom QSS string:
1. **Breaks Component Invariants:** Overriding `QPushButton` wipes out QFluentWidgets' internal focus indicators, ripple animations, and theme-adaptive icon colors.
2. **Breaks Light/Dark Toggling:** Hardcoded CSS colors do not react to system dark/light switching.
3. **Creates Web Badges:** Web badges with `border-radius: 9999px` look alien on Windows 11.

### When You DO Need Custom QSS: Use `StyleSheetBase`
If you need custom widget styling that changes with the theme, use QFluentWidgets' `StyleSheetBase` with token interpolation:

```python
from qfluentwidgets import StyleSheetBase, Theme, isDarkTheme

class CustomWidgetStyleSheet(StyleSheetBase):
    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return f":/qss/{theme.value.lower()}/custom_widget.qss"
```
Or apply minimal targeted QSS referencing object names:

```css
/* Clean, subtle, targeted desktop QSS */
QFrame#WorkspaceDivider {
    background-color: rgba(255, 255, 255, 0.08);
    max-width: 1px;
}
```
