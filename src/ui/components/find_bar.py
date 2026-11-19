"""VS Code style floating find widget for the script viewer.

The bar owns the search state (query, toggles, match list, current index) and
reports results back to its host through a small duck-typed interface:

    host.set_find_matches(all_cursors, current_cursor_or_None)
    host.reveal_find_match(cursor)
    host.document()              -> QTextDocument
    host.focus_content()         -> give focus back to the text view
"""
import re
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QPoint, QEasingCurve
from PySide6.QtGui import QTextCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QLabel, QToolButton

from ..theme import ThemeManager

MAX_MATCHES = 5000


class FindBar(QFrame):
    closed = Signal()

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._matches: List[tuple] = []  # (start, end) document positions
        self._index = -1
        self._regex_error = False
        self._slide: Optional[QPropertyAnimation] = None

        self.setObjectName("FindBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(2)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Find")
        self.input.setClearButtonEnabled(False)
        self.input.setFixedWidth(190)
        self.input.setFixedHeight(26)
        self.input.setObjectName("FindInput")
        self.input.textChanged.connect(lambda _t: self._search(keep_position=False))
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        self.case_btn = self._toggle("Aa", "Match Case (Alt+C)")
        self.word_btn = self._toggle("ab", "Match Whole Word (Alt+W)")
        self.regex_btn = self._toggle(".*", "Use Regular Expression (Alt+R)")
        for b in (self.case_btn, self.word_btn, self.regex_btn):
            b.toggled.connect(lambda _c: self._search(keep_position=True))
            lay.addWidget(b)

        self.count_lbl = QLabel("No results", self)
        self.count_lbl.setObjectName("FindCount")
        self.count_lbl.setMinimumWidth(66)
        self.count_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.count_lbl)

        self.prev_btn = self._action("↑", "Previous Match (Shift+Enter)", self.prev_match)
        self.next_btn = self._action("↓", "Next Match (Enter)", self.next_match)
        self.close_btn = self._action("✕", "Close (Escape)", self.close_bar)
        lay.addWidget(self.prev_btn)
        lay.addWidget(self.next_btn)
        lay.addWidget(self.close_btn)

        for seq, btn in (("Alt+C", self.case_btn), ("Alt+W", self.word_btn), ("Alt+R", self.regex_btn)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(btn.toggle)

        # Re-run the search (debounced) when the document changes while open (edit mode)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(180)
        self._refresh_timer.timeout.connect(lambda: self._search(keep_position=True))

        self.apply_theme()
        self.hide()

    # ---- construction helpers -------------------------------------------------
    def _toggle(self, text: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(26, 24)
        b.setFocusPolicy(Qt.NoFocus)
        b.setProperty("findToggle", True)
        return b

    def _action(self, text: str, tip: str, slot) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(24, 24)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda: slot())
        return b

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        dark = pal["is_dark"]
        surface = pal["surface"]
        text = pal["text"]
        muted = pal["muted"]
        accent = pal["accent"]
        border = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.16)"
        field_bg = "rgba(255,255,255,0.07)" if dark else "rgba(0,0,0,0.05)"
        hover = "rgba(255,255,255,0.12)" if dark else "rgba(0,0,0,0.09)"
        self.setStyleSheet(f"""
            QFrame#FindBar {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLineEdit#FindInput {{
                background-color: {field_bg};
                color: {text};
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 0px 6px;
                font-size: 12px;
                selection-background-color: {accent};
                selection-color: #0b0f1a;
            }}
            QLineEdit#FindInput:focus {{ border: 1px solid {accent}; }}
            QLineEdit#FindInput[error="true"] {{ border: 1px solid #ef4444; }}
            QLabel#FindCount {{ color: {muted}; font-size: 11px; background: transparent; border: none; }}
            QLabel#FindCount[empty="true"] {{ color: #f87171; }}
            QToolButton {{
                background: transparent; color: {text}; border: 1px solid transparent;
                border-radius: 5px; font-size: 12px; font-weight: 600;
            }}
            QToolButton:hover {{ background-color: {hover}; }}
            QToolButton:disabled {{ color: {muted}; }}
            QToolButton[findToggle="true"]:checked {{
                background-color: rgba({pal['accent_rgb']}, 0.28);
                border: 1px solid {accent};
            }}
        """)

    # ---- public API -----------------------------------------------------------
    def open_bar(self, seed_text: str = ""):
        was_hidden = not self.isVisible()
        if seed_text:
            self.input.setText(seed_text)
        if was_hidden:
            self.show()
            self.raise_()
            self._host.position_find_bar()
            self._animate_in()
            self._host.document().contentsChanged.connect(self._on_doc_changed)
        self.input.setFocus(Qt.ShortcutFocusReason)
        self.input.selectAll()
        self._search(keep_position=False)

    def close_bar(self):
        if not self.isVisible():
            return
        try:
            self._host.document().contentsChanged.disconnect(self._on_doc_changed)
        except (RuntimeError, TypeError):
            pass
        self.hide()
        self._matches = []
        self._index = -1
        self._host.set_find_matches([], None)
        self._host.focus_content()
        self.closed.emit()

    def next_match(self):
        self._step(1)

    def prev_match(self):
        self._step(-1)

    # ---- internals ------------------------------------------------------------
    def _animate_in(self):
        target = self.pos()
        start = QPoint(target.x(), target.y() - 14)
        self.move(start)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(150)
        self._slide.setStartValue(start)
        self._slide.setEndValue(target)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.start()

    def _on_doc_changed(self):
        if self.isVisible():
            self._refresh_timer.start()

    def _build_pattern(self):
        q = self.input.text()
        if not q:
            return None
        flags = 0 if self.case_btn.isChecked() else (re.IGNORECASE | re.UNICODE)
        pat = q if self.regex_btn.isChecked() else re.escape(q)
        if self.word_btn.isChecked():
            pat = rf"(?<!\w)(?:{pat})(?!\w)"
        return re.compile(pat, flags | re.MULTILINE)

    def _search(self, keep_position: bool):
        prev_start = self._matches[self._index][0] if (keep_position and 0 <= self._index < len(self._matches)) else None
        self._matches = []
        self._index = -1
        self._set_error(False)
        try:
            pattern = self._build_pattern()
        except re.error:
            pattern = None
            self._set_error(True)

        if pattern is not None:
            text = self._host.document().toPlainText()
            for m in pattern.finditer(text):
                if m.end() == m.start():
                    continue
                self._matches.append((m.start(), m.end()))
                if len(self._matches) >= MAX_MATCHES:
                    break

        if self._matches:
            if prev_start is not None:
                self._index = next((i for i, (s, _e) in enumerate(self._matches) if s >= prev_start), 0)
            else:
                self._index = self._nearest_from_view()
        self._publish(reveal=not keep_position)

    def _nearest_from_view(self) -> int:
        """First match at/after the top of the visible area (VS Code behaviour)."""
        anchor = self._host.view_anchor_position()
        for i, (s, _e) in enumerate(self._matches):
            if s >= anchor:
                return i
        return 0

    def _step(self, delta: int):
        if not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self._publish(reveal=True)

    def _cursor_for(self, span) -> QTextCursor:
        c = QTextCursor(self._host.document())
        c.setPosition(span[0])
        c.setPosition(span[1], QTextCursor.KeepAnchor)
        return c

    def _publish(self, reveal: bool):
        total = len(self._matches)
        has_q = bool(self.input.text())
        if total:
            suffix = "+" if total >= MAX_MATCHES else ""
            self.count_lbl.setText(f"{self._index + 1} of {total}{suffix}")
        else:
            self.count_lbl.setText("No results" if has_q and not self._regex_error else ("Invalid" if self._regex_error else "No results"))
        self.count_lbl.setProperty("empty", bool(has_q and not total))
        self.count_lbl.style().unpolish(self.count_lbl)
        self.count_lbl.style().polish(self.count_lbl)
        self.prev_btn.setEnabled(total > 0)
        self.next_btn.setEnabled(total > 0)

        cursors = [self._cursor_for(s) for s in self._matches]
        current = cursors[self._index] if 0 <= self._index < total else None
        self._host.set_find_matches(cursors, current)
        if reveal and current is not None:
            self._host.reveal_find_match(current)

    def _set_error(self, err: bool):
        self._regex_error = err
        self.input.setProperty("error", err)
        self.input.style().unpolish(self.input)
        self.input.style().polish(self.input)

    # ---- keyboard -------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.prev_match() if event.modifiers() & Qt.ShiftModifier else self.next_match()
                return True
            if key == Qt.Key_Escape:
                self.close_bar()
                return True
            if key == Qt.Key_F3:
                self.prev_match() if event.modifiers() & Qt.ShiftModifier else self.next_match()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up):
                self.next_match() if key == Qt.Key_Down else self.prev_match()
                return True
        return super().eventFilter(obj, event)
