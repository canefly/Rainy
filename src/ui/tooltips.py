"""Hover tips drawn by Rainy itself.

Qt's own tips take their look from the Windows style and from whatever style the hovered widget carries, which put
dark text on a dark box in light mode. These are painted directly from the current theme, so box and text always
contrast, in every theme and mode.
"""
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .theme import ThemeManager

SHOW_FOR_MS = 7000
_HIDE_EVENTS = (QEvent.Leave, QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.KeyPress,
                QEvent.Wheel, QEvent.Hide, QEvent.WindowDeactivate, QEvent.FocusOut)


def _looks_rich(text: str) -> bool:
    return "<" in text and ">" in text


class TipBubble(QWidget):
    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._text = ""

    def set_text(self, text: str) -> None:
        self._text = text
        fm = QFontMetrics(self.font())
        r = fm.boundingRect(QRect(0, 0, 420, 2000), Qt.TextWordWrap, text)
        self.resize(r.width() + 20, r.height() + 14)

    def colors(self):
        pal = ThemeManager.get_palette()
        return QColor(pal["surface"]), QColor(pal["text"]), QColor(pal["border"])

    def paintEvent(self, _e) -> None:
        bg, fg, border = self.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(border)
        p.setBrush(bg)
        p.drawRoundedRect(QRect(0, 0, self.width() - 1, self.height() - 1), 6, 6)
        p.setPen(fg)
        p.drawText(QRect(10, 7, self.width() - 20, self.height() - 14), Qt.TextWordWrap, self._text)
        p.end()


class TipFilter(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.bubble = TipBubble()
        self.owner = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_tip)

    def hide_tip(self) -> None:
        self.owner = None
        self.timer.stop()
        self.bubble.hide()

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et == QEvent.ToolTip and isinstance(obj, QWidget):
            text = obj.toolTip()
            if not text or _looks_rich(text):
                return False                       # item views and rich text keep Qt's own tip
            self.owner = obj
            self.bubble.set_text(text)
            pos = event.globalPos() + QPoint(14, 18)
            screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            x = min(pos.x(), geo.right() - self.bubble.width())
            y = pos.y() if pos.y() + self.bubble.height() <= geo.bottom() else pos.y() - self.bubble.height() - 30
            self.bubble.move(x, y)
            self.bubble.show()
            self.bubble.update()
            self.timer.start(SHOW_FOR_MS)
            return True
        if self.owner is not None and obj is self.owner and et in _HIDE_EVENTS:
            self.hide_tip()
        return False


_filter = None
_patched = False


def install_tooltips() -> None:
    """Give every widget that gets a hover tip our drawn tip. The filter is attached to those widgets only (not to the
    whole application), so it never sees the thousands of other events a running window produces."""
    global _filter, _patched
    app = QApplication.instance()
    if app is None:
        return
    if _filter is None:
        _filter = TipFilter(app)
    if _patched:
        return
    _patched = True
    original = QWidget.setToolTip

    def set_tool_tip(self, text):
        original(self, text)
        if text:
            try:
                self.installEventFilter(_filter)      # installing twice on one widget is a no-op
            except Exception:  # noqa: BLE001  (cosmetic)
                pass

    QWidget.setToolTip = set_tool_tip
