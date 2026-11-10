# Rainy Tool Native — Workspace Rules & UI Layout Guidelines

This document governs agent behavior strictly within the `knot-tool-native` workspace (PySide6 + QFluentWidgets).

---

## 🚫 Non-Negotiable UI & Layout Rules (Preventing Clipping & Margin Bugs)

1. **Measure Text, Never Guess Pixel Widths:**
   - **NEVER** hardcode guessed `setFixedWidth(...)` values on text `QLabel`s (e.g., `setFixedWidth(16)` on `"Out"`, which clips at 10px bold Segoe UI).
   - **ALWAYS** apply the stylesheet first, call `label.ensurePolished()`, and measure with `label.fontMetrics().horizontalAdvance(text)` (or use `measure_text_width()` in `src/ui/components/timeline_card.py`).
   - Keep layout sizing constants in one block at the top of the component file rather than scattering magic numbers across methods.

2. **QFluentWidgets `PushButton` Icon + Custom QSS Gotcha:**
   - **NEVER** set custom QSS `padding` on a `qfluentwidgets.PushButton` that also has an icon (`PushButton(FluentIcon.X, "Text")`). QFluentWidgets' custom `paintEvent` relies on internal left-padding to offset the text from the icon; overriding `padding` in QSS causes the icon to draw directly on top of the text.
   - Use either a text-only `PushButton("Text")` when applying custom QSS padding, or an icon-only `ToolButton` / `TransparentToolButton`.

3. **Mandatory Visual Pixel Verification (`widget.grab()`):**
   - Before declaring any UI, card, or trimmer layout change complete, run:
     ```powershell
     .\.venv\Scripts\python.exe scripts/verify_card_layout.py
     ```
   - Inspect the generated PNG snapshots (`card_285px.png`, `timeline_list_compact_290px.png`, `clip_player_loop_mode.png`, `clip_player_edit_mode.png`) with `read_file` to verify zero text clipping, zero icon overlap, and clean column alignment.
