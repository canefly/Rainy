"""The Rainy logo, drawn sharply at any screen density.

assets/logo.png is a 512 px source. A label that shows it at 64 logical pixels needs 64 pixels on a normal
screen but 128 on a 2x display, so the image is rescaled for the screen the label is actually on, and again
if the window moves to another screen. (Stretching a small image over more pixels is what made it blurry.)
"""
import os
from typing import Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QWidget

LOGO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"))


def logo_pixmap(logical_size: int, dpr: float = 1.0, path: str = LOGO_PATH) -> QPixmap:
    """The logo scaled to `logical_size` logical pixels for a screen with this device pixel ratio.
    Returns an empty pixmap if the file is missing, so a missing asset never stops a screen from opening."""
    src = QPixmap(path)
    if src.isNull():
        return QPixmap()
    dpr = max(1.0, float(dpr))
    side = max(1, int(round(logical_size * dpr)))
    px = src.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    px.setDevicePixelRatio(dpr)
    return px


class LogoLabel(QLabel):
    def __init__(self, logical_size: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = logical_size
        self._dpr = 0.0
        self.setAlignment(Qt.AlignCenter)
        self._refresh()

    def _refresh(self) -> None:
        dpr = self.devicePixelRatioF()
        if dpr != self._dpr:
            self._dpr = dpr
            self.setPixmap(logo_pixmap(self._size, dpr))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()          # the real screen is only known once the window is shown

    def event(self, e) -> bool:
        ratio_change = getattr(QEvent.Type, "DevicePixelRatioChange", None)
        if ratio_change is not None and e.type() == ratio_change:
            self._refresh()      # moved to a screen with a different scale
        return super().event(e)
