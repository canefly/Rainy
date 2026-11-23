"""The one calm window shown the first time Rainy gets ready: what it is fetching, why, and how far along it is.

It never blocks the app (it is not modal) and closing it with the X just hides it. The window only draws state;
ui/first_run_controller.py decides what is in it.
"""
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import ProgressBar, PrimaryPushButton, PushButton

from ..theme import ThemeManager

# row states
WAITING, RUNNING, DONE, SKIPPED, FAILED = "waiting", "running", "done", "skipped", "failed"

INTRO = ("Rainy needs a few helper programs to play and prepare your video. They download once, straight from "
         "their official sources, and every file is checked before it is used.")
NOTE = "You can keep using Rainy while this happens."


class FirstRunDialog(QDialog):
    hide_clicked = Signal()
    cancel_clicked = Signal()
    retry_clicked = Signal()
    skip_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent: Optional[QWidget], rows: List[Tuple[str, str]]):
        super().__init__(parent)
        self.setObjectName("FirstRunDialog")
        self.setWindowTitle("Getting Rainy ready")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setFixedWidth(480)
        self._labels: Dict[str, QLabel] = {}
        self._status: Dict[str, QLabel] = {}
        self._states: Dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        self.title = QLabel("Getting Rainy ready", self)
        self.title.setObjectName("FirstRunTitle")
        root.addWidget(self.title)

        self.intro = self._text(INTRO)
        root.addWidget(self.intro)
        self.note = self._text(NOTE)
        self.note.setObjectName("FirstRunNote")
        root.addWidget(self.note)

        self.list_frame = QFrame(self)
        self.list_frame.setObjectName("FirstRunList")
        lf = QVBoxLayout(self.list_frame)
        lf.setContentsMargins(14, 10, 14, 10)
        lf.setSpacing(8)
        for key, label in rows:
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            name = QLabel(label, self.list_frame)
            name.setTextFormat(Qt.PlainText)
            status = QLabel("Waiting", self.list_frame)
            status.setTextFormat(Qt.PlainText)
            status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line.addWidget(name, 1)
            line.addWidget(status, 0)
            lf.addLayout(line)
            self._labels[key], self._status[key], self._states[key] = name, status, WAITING
        root.addWidget(self.list_frame)

        self.bar = ProgressBar(self)
        self.bar.setRange(0, 100)
        root.addWidget(self.bar)

        self.message = self._text("")
        self.message.setObjectName("FirstRunMessage")
        self.message.setVisible(False)
        root.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.skip_btn = PushButton("Skip photo censoring", self)
        self.cancel_btn = PushButton("Cancel", self)
        self.hide_btn = PushButton("Hide", self)
        self.retry_btn = PrimaryPushButton("Try again", self)
        self.close_btn = PrimaryPushButton("Close", self)
        for b in (self.skip_btn, self.cancel_btn, self.hide_btn, self.retry_btn, self.close_btn):
            b.setFixedHeight(32)
            buttons.addWidget(b)
        root.addLayout(buttons)

        self.skip_btn.clicked.connect(self.skip_clicked)
        self.cancel_btn.clicked.connect(self.cancel_clicked)
        self.hide_btn.clicked.connect(self._hide)
        self.retry_btn.clicked.connect(self.retry_clicked)
        self.close_btn.clicked.connect(self.close_clicked)

        self.apply_theme()
        self.show_running()

    # -- building blocks ---------------------------------------------------------------
    def _text(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.PlainText)          # error text can come from a download: shown as plain text, always
        return lbl

    def apply_theme(self) -> None:
        pal = ThemeManager.get_palette()
        text, muted, border = pal.get("text", "#ffffff"), pal.get("muted", "#94a3b8"), pal.get("border", "#33384a")
        surface, bg = pal.get("surface", "#1c2030"), pal.get("bg", "#12141c")
        self.setStyleSheet(f"""
            QDialog#FirstRunDialog {{ background: {bg}; }}
            QLabel {{ color: {text}; background: transparent; font-size: 13px; }}
            QLabel#FirstRunTitle {{ font-size: 18px; font-weight: 700; }}
            QLabel#FirstRunNote {{ color: {muted}; }}
            QLabel#FirstRunMessage {{ color: {pal.get('accent', '#38bdf8')}; }}
            QFrame#FirstRunList {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; }}
        """)
        for key in self._states:
            self._paint_status(key)

    def _paint_status(self, key: str) -> None:
        pal = ThemeManager.get_palette()
        color = {WAITING: pal.get("muted", "#94a3b8"), RUNNING: pal.get("accent", "#38bdf8"), DONE: "#4ade80",
                 SKIPPED: pal.get("muted", "#94a3b8"), FAILED: "#f87171"}[self._states[key]]
        self._status[key].setStyleSheet(f"color: {color}; background: transparent; font-size: 12px;")

    # -- state ---------------------------------------------------------------------------
    def row_keys(self) -> List[str]:
        return list(self._labels)

    def row_state(self, key: str) -> str:
        return self._states[key]

    def row_text(self, key: str) -> str:
        return self._status[key].text()

    def set_row(self, key: str, state: str, text: str) -> None:
        if key not in self._status:
            return
        self._states[key] = state
        self._status[key].setText(text)
        self._paint_status(key)

    def set_progress(self, pct: int) -> None:
        self.bar.setValue(max(0, min(100, int(pct))))

    def set_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self._fit()

    def _fit(self) -> None:
        """Wrapped text only knows its height once it has a width, so re-measure after every change instead of
        letting a long message get cut off at the bottom."""
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        self.setFixedHeight(max(self.sizeHint().height(), lay.totalHeightForWidth(self.width()) if lay else 0))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def _buttons(self, *, skip=False, cancel=False, hide=False, retry=False, close=False) -> None:
        for btn, show in ((self.skip_btn, skip), (self.cancel_btn, cancel), (self.hide_btn, hide),
                          (self.retry_btn, retry), (self.close_btn, close)):
            btn.setVisible(show)
        self._fit()

    def show_running(self, *, can_skip: bool = False) -> None:
        self.title.setText("Getting Rainy ready")
        self.set_message("")
        self._buttons(skip=can_skip, cancel=True, hide=True)

    def show_failed(self, text: str, *, retry_label: str = "Try again") -> None:
        self.title.setText("Couldn't finish getting Rainy ready")
        self.set_message(text)
        self.retry_btn.setText(retry_label)
        self._buttons(retry=True, hide=True)

    def show_done(self, note: str = "") -> None:
        self.title.setText("Rainy is ready")
        self.set_progress(100)
        self.set_message("Everything is set up. Have fun!" + ("\n" + note if note else ""))
        self._buttons(close=True)

    # -- closing = hiding ----------------------------------------------------------------
    def _hide(self) -> None:
        self.hide()
        self.hide_clicked.emit()

    def closeEvent(self, event) -> None:
        event.ignore()
        self._hide()
