---
name: fluent-architect
description: >-
  Guide the creation and refactoring of native Windows desktop applications using PySide6,
  QFluentWidgets, and the Windows 11 Fluent Design System. Use when building or styling native
  Windows UI, replacing web-based UI paradigms, applying Mica/Acrylic materials, configuring
  modeless desktop windows, or setting up native typography, elevation, and component hierarchies.
---

# Fluent Architect — Native Windows 11 Desktop UI Skill

This skill enforces strict native Windows desktop UI paradigms for applications built with **PySide6** and **QFluentWidgets** (`PySide6-Fluent-Widgets`). It ensures applications look, feel, and behave like first-party Windows 11 software (such as Windows Settings, Task Manager, modern File Explorer, and Dev Home), completely replacing web-emulation anti-patterns (such as Tailwind-style neon glows, thick arbitrary borders, bloated margins, and single-page blocking modals).

---

## 🧭 Navigation & Detailed References

For comprehensive technical blueprints and code patterns, explore the modular reference guides:

- **[Materials & Elevation](./references/materials-and-elevation.md)**: Deep dive into Mica, Mica Alt, Acrylic (In-app vs Background), Smoke, depth levels (0 to 128), and subtle 1px elevation strokes.
- **[Desktop Windowing & DWM Chrome](./references/windowing-and-chrome.md)**: Modeless top-level windows vs blocking modals, multi-monitor workflows, Snap Layouts, and custom `MSFluentTitleBar` integration.
- **[Styling, Theming & Tokens](./references/styling-and-tokens.md)**: Native QFluentWidgets theming (`setThemeColor`, `setTheme`, `qconfig`), eliminating 400-line web QSS files, and crafting restrained 60-30-10 palettes.
- **[Components & Layout System](./references/components-and-layout.md)**: `CardWidget`, `ElevatedCardWidget`, `RoundMenu`, `Flyout`, 4px spacing grids, and Segoe UI Variable typography.
- **[Web-to-Fluent Migration Guide](./references/web-to-fluent-migration.md)**: Concrete before-and-after refactoring patterns for Rainy native tool components (`MainWindow`, `theme.py`, timeline cards, tool drawers).

---

## 🏛️ Core Principles & Design Language

### 1. Modeless Over Modals (Desktop Multi-Windowing)
- **The Desktop Advantage:** Video editors, audio engineers, and power users operate on multi-monitor setups. Applications should never trap non-critical tools inside the main window viewport like a browser tab.
- **Modeless Secondary Windows:** Panels such as **Settings**, **Document Vaults**, **Source Links**, and **Inspector Drawers** must spawn as independent, top-level modeless windows (`QWidget.show()` or `FramelessWindow`) that users can drag across screens and keep open side-by-side with their timeline.
- **Eliminate `QDialog.exec()` for Workspaces:** Reserve modal execution strictly for destructive, blocking confirmations (e.g., "Discard unsaved changes?").

### 2. Windows 11 Materials: Mica vs Acrylic
- **Mica Canvas (Base Layer):**
  - Use Mica as the opaque backdrop for primary, long-lived application windows.
  - Mica samples the user's desktop wallpaper to subtly tint the window frame. It dynamically renders a solid neutral fallback when the window loses focus, providing instant feedback on active state.
  - Enable natively in QFluentWidgets via `window.setMicaEffectEnabled(True)`.
- **Acrylic Surfaces (Transient Layer):**
  - Acrylic replicates frosted glass using active blur, noise, and exclusion.
  - **Rule of Transience:** Use Acrylic strictly for *transient, light-dismiss surfaces* (popovers, context menus, flyouts, floating command palettes, search dropdowns).
  - **Never use full Acrylic as the base main window background:** It causes visual clutter, window dragging lag, and creates muddy artifacts when placed behind cards.
  - **Never layer Acrylic on Acrylic:** Stacking two acrylic surfaces breaks visual hierarchy and crushes GPU performance.
  - **Tinting:** When acrylic is used, tint it harmoniously using the primary theme color via QFluentWidgets' `setThemeColor()`.

### 3. Exorcising Web Aesthetics
- **No Neon Glows or Loud Borders:** Eliminate bright saturated borders (e.g. pure Tailwind `#fb7185` or `#fbbf24` frames) and heavy box-shadows. Windows 11 Fluent establishes surface boundaries using subtle 1px elevation strokes (`rgba(255, 255, 255, 0.08)` in dark mode, `rgba(0, 0, 0, 0.06)` in light mode).
- **Restrained Accent Color Usage:** The accent color is applied strictly to primary interactive states: primary action buttons (`PrimaryPushButton`), active navigation indicator pills, checked switches, and slider thumbs. Backgrounds and cards must remain clean neutrals.
- **Tight, Information-Dense Grids:** Replace airy, loose web paddings (24px–32px) with compact desktop spacing (4px, 8px, 12px, 16px).
- **Native Typography:** Use **Segoe UI Variable** (`Segoe UI Variable Text`, `Segoe UI Variable Display`) via `setFont(widget, 14, QFont.Normal)` instead of arbitrary web CSS font rules and uppercase tracking hacks.

### 4. Systematic Fluent Geometry
- **8px Corner Radius:** Top-level application windows, floating dialogs, content cards (`CardWidget`, `ElevatedCardWidget`), and flyouts.
- **4px Corner Radius:** Interactive controls (buttons, line edits, checkboxes, list item rows).
- **0px Corner Radius:** Snapped or maximized windows, splitters, divider lines, and flush table headers.

### 5. Programmatic Desktop Interactions
- **Native Scrolling:** Use `SmoothScrollArea`, `scrollToItem()`, or direct scrollbar manipulation via `verticalScrollBar().setValue()`. Never emulate browser hash-anchors or inject HTML smooth-scroll hacks.
- **Keyboard Accelerators:** Equip native menus and buttons with proper shortcuts (`QKeySequence`, Alt mnemonics `&File`, `&Edit`).

---

## ⚡ Quick Operational Checklist

| Web Anti-Pattern (What to Avoid) | Fluent Native Pattern (What to Implement) | QFluentWidgets Implementation |
| :--- | :--- | :--- |
| `AcrylicWindow` as main background | `FluentWindow` with native Mica backdrop | `class MainWindow(FluentWindow): ... self.setMicaEffectEnabled(True)` |
| 400-line raw QSS overriding everything | QFluentWidgets theming tokens + native widgets | `setTheme(Theme.DARK); setThemeColor('#fb7185')` |
| Embedding Settings/Vault inside main frame | Independent modeless top-level window | Subclass `FramelessWindow`, call `.show()`, `.raise_()` |
| Handcrafted `QFrame` cards with thick CSS | Native elevation cards with built-in hover depth | `ElevatedCardWidget` or `SimpleCardWidget` |
| Modal `QDialog.exec()` for tool panels | Non-blocking window or contextual flyout | `Flyout.make()` or modeless `FramelessWindow` |
| Arbitrary CSS font declarations | Native Segoe UI Variable font ramp | `setFont(widget, fontSize=13, weight=QFont.Normal)` |
| Browser-like `alert()` or `confirm()` | Native Fluent message boxes | `MessageBox("Title", "Content", self).exec()` |
| Hand-coded context menus | Native rounded Fluent menus with icons | `RoundMenu` + `Action(FluentIcon.COPY, "Copy")` |

---

## 🚫 Common Pitfalls & Red Flags

1. **The "Everything is Acrylic" Trap:** Putting acrylic on the main window, the sidebar, the cards, and the menus. Result: sluggish frame rate, GPU churn, and unreadable muddy text. Mica is for canvas; Acrylic is for transient overlays.
2. **The "Web CSS Override" Trap:** Overriding `QWidget`, `QPushButton`, and `QLabel` in a single global stylesheet string. This strips QFluentWidgets of its native state animations, theme-switching logic, and Windows 11 micro-interactions. Use built-in components and tokenized styling.
3. **Blocking Modals on Multi-Monitor Workspaces:** Video editors scrub footage on Monitor 1 while referencing scripts on Monitor 2. A modal dialog locks the entire application event loop and prevents multi-window productivity.
