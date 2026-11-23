"""Full-window screen shown while this install is not approved (or its license has run out)."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import IndeterminateProgressRing, LineEdit, PrimaryPushButton

from ...core.license_core import LicenseSnapshot, LicenseState
from ..logo import LogoLabel
from ..theme import ThemeManager

COLLECTION_NOTICE = (
    "To verify you, Rainy sends your editor name, a hardware summary (CPU, GPU, RAM, "
    "Windows version) and your public IP, which gives a rough city/region. No GPS, no files."
)

_ERROR_STATES = {LicenseState.REVOKED, LicenseState.ERROR, LicenseState.EXPIRED}


def _skew_text(seconds):
    if seconds is None:
        return "Your system time doesn't match the server's."
    mins = abs(seconds) // 60
    span = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{max(1, mins)} min"
    side = "behind" if seconds > 0 else "ahead of"
    return f"Your system time is about {span} {side} the server."


class GateView(QWidget):
    register_requested = Signal(str)
    retry_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GateView")
        self._state = LicenseState.CHECKING

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setAlignment(Qt.AlignCenter)

        self.card = QFrame(self)
        self.card.setObjectName("GateCard")
        self.card.setFixedWidth(480)
        self.card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        outer.addWidget(self.card, 0, Qt.AlignCenter)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(36, 32, 36, 28)
        lay.setSpacing(12)

        self.logo = LogoLabel(64, self.card)
        lay.addWidget(self.logo)

        self.title = QLabel(self.card)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        self.body = QLabel(self.card)
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setWordWrap(True)
        lay.addWidget(self.body)

        self.spinner = IndeterminateProgressRing(self.card)
        self.spinner.setFixedSize(36, 36)
        self.spinner.setStrokeWidth(3)
        lay.addWidget(self.spinner, 0, Qt.AlignCenter)

        self.input = LineEdit(self.card)
        self.input.setPlaceholderText("Editor name")
        self.input.setMaxLength(100)
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self._on_primary)
        lay.addWidget(self.input)

        self.message = QLabel(self.card)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        lay.addWidget(self.message)

        self.primary = PrimaryPushButton(self.card)
        self.primary.setFixedHeight(36)
        self.primary.clicked.connect(self._on_primary)
        lay.addWidget(self.primary)

        self.notice = QLabel(COLLECTION_NOTICE, self.card)
        self.notice.setAlignment(Qt.AlignCenter)
        self.notice.setWordWrap(True)
        lay.addWidget(self.notice)

        self.footer = QLabel(self.card)
        self.footer.setAlignment(Qt.AlignCenter)
        self.footer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.footer)

        self.apply_theme()
        self.show_snapshot(LicenseSnapshot(LicenseState.CHECKING))

    # -- theme -----------------------------------------------------------------------
    def apply_theme(self):
        pal = ThemeManager.get_palette()
        self._pal = pal
        self.card.setStyleSheet(
            f"#GateCard {{ background-color: {pal['surface']}; border: 1px solid {pal['border']}; border-radius: 16px; }}"
        )
        self.title.setStyleSheet(f"color: {pal['text']}; font-size: 22px; font-weight: 800; background: transparent;")
        self.body.setStyleSheet(f"color: {pal['muted']}; font-size: 13px; background: transparent;")
        self.notice.setStyleSheet(f"color: {pal['muted']}; font-size: 11px; background: transparent;")
        self.footer.setStyleSheet(f"color: {pal['muted']}; font-size: 11px; background: transparent;")
        self._style_message(self._state)

    def _style_message(self, state: LicenseState):
        color = "#f87171" if state in _ERROR_STATES or state is LicenseState.NEEDS_REGISTRATION else self._pal["accent"]
        if state is LicenseState.UNREACHABLE or state is LicenseState.CLOCK_SKEW:
            color = "#f59e0b"
        self.message.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;")

    # -- content ---------------------------------------------------------------------
    def show_snapshot(self, snap: LicenseSnapshot):
        self._state = snap.state
        s = snap.state
        name = snap.editor_name or "there"
        title, body, button = "", "", None
        show_input = show_spinner = show_notice = False
        message = snap.message

        if s is LicenseState.NEEDS_REGISTRATION:
            title = "Welcome to Rainy"
            body = "Pick the name you edit under. Cane approves every editor before the app unlocks."
            button, show_input, show_notice = "Request access", True, True
            if snap.editor_name and not self.input.text():
                self.input.setText(snap.editor_name)
        elif s is LicenseState.CHECKING:
            title = "One moment"
            body = "Contacting the license server..."
            show_spinner = True
            message = message if message not in ("", "Checking your license...") else ""
        elif s is LicenseState.PENDING:
            title = "Waiting for approval"
            body = f"Hey {name}, your request is in. This screen unlocks by itself once Cane approves it."
            button, show_spinner = "Check now", True
        elif s is LicenseState.REVOKED:
            title = "Access revoked"
            body = "This device isn't allowed to use Rainy. If that's a mistake, message Cane with your request ID."
            button = "Check again"
        elif s is LicenseState.EXPIRED:
            title = "License expired"
            body = "Rainy couldn't reach the server in time to renew your license. Connect to the internet and retry."
            button = "Retry"
        elif s is LicenseState.UNREACHABLE:
            title = "Can't reach the server"
            body = "Check your connection. Rainy keeps retrying in the background."
            button, show_spinner = "Retry now", True
        elif s is LicenseState.CLOCK_SKEW:
            title = "Your clock is off"
            body = f"{_skew_text(snap.skew_seconds)} Fix the date and time in Windows, then retry."
            button = "Retry"
            message = ""
        else:  # ERROR, or APPROVED (never shown, the window hides the gate)
            title = "Something's wrong"
            body = "Rainy couldn't verify this install."
            button = "Retry"

        self.title.setText(title)
        self.body.setText(body)
        self.input.setVisible(show_input)
        self.spinner.setVisible(show_spinner)
        self.notice.setVisible(show_notice)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.primary.setVisible(button is not None)
        if button:
            self.primary.setText(button)
        self.primary.setEnabled(True)
        self.input.setEnabled(True)
        if snap.device_id:
            self.footer.setText(f"Request ID  {snap.device_id[:8]}")
        else:
            self.footer.setText("")
        self.footer.setVisible(bool(snap.device_id))
        self._style_message(s)

        if s is LicenseState.CHECKING:
            self.primary.setEnabled(False)
            self.input.setEnabled(False)

    # -- actions ---------------------------------------------------------------------
    def _on_primary(self):
        if self._state is LicenseState.NEEDS_REGISTRATION:
            self.register_requested.emit(self.input.text())
        elif self._state is not LicenseState.CHECKING:
            self.retry_requested.emit()
