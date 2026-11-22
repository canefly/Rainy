"""Full-window screen shown when this version is below the minimum the server still supports."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...core.updater import UpdateService
from ..components.update_card import UpdateCard
from ..theme import ThemeManager


class UpdateRequiredView(QWidget):
    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateRequiredView")
        self.service = service or UpdateService.instance()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setAlignment(Qt.AlignCenter)

        self.card = QFrame(self)
        self.card.setObjectName("UpdateGateCard")
        self.card.setFixedWidth(540)
        self.card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        outer.addWidget(self.card, 0, Qt.AlignCenter)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)

        self.logo = QLabel(self.card)
        self.logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.logo.setPixmap(QPixmap(logo_path).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(self.logo)

        self.title = QLabel("Update required", self.card)
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)

        self.body = QLabel(
            "This version of Rainy is too old to keep working. "
            "Install the update to continue. Your projects and settings are kept.", self.card)
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setWordWrap(True)
        lay.addWidget(self.body)

        self.update_card = UpdateCard(self.service, self.card, show_title=False)
        lay.addWidget(self.update_card)

        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        self.card.setStyleSheet(
            f"#UpdateGateCard {{ background-color: {pal['surface']}; border: 1px solid {pal['border']}; border-radius: 16px; }}"
        )
        self.title.setStyleSheet(f"color: {pal['text']}; font-size: 22px; font-weight: 800; background: transparent;")
        self.body.setStyleSheet(f"color: {pal['muted']}; font-size: 13px; background: transparent;")
        self.update_card.apply_theme()

    def start_if_needed(self) -> None:
        """Begin downloading right away: the user has no other choice, so save them a click."""
        s = self.service
        if s.required and s.manifest is not None and not s.is_downloading and s.downloaded_path is None:
            s.start_download()

    def showEvent(self, e):
        super().showEvent(e)
        self.update_card.refresh_view()
        self.start_if_needed()
