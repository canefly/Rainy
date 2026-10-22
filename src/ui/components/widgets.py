import os
import webbrowser
from typing import Optional
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QObject, QElapsedTimer, QEvent, QPointF, QPoint
from PySide6.QtGui import QPainter, QColor, QCursor, QFont, QPen, QBrush, QLinearGradient, QRadialGradient
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QAbstractScrollArea, QApplication, QSplitter, QSplitterHandle
from qfluentwidgets import FluentIcon, IconWidget

class StatusDot(QWidget):
    """Paints a clean vector online status dot with an animated breathing halo (no emojis)."""
    def __init__(self, color="#34d399", halo_color="rgba(52, 211, 153, 0.35)", parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._dot_color = QColor(color)
        self._halo_color = QColor(halo_color)
        self._halo_radius = 5.0
        self._expanding = True

        # Animation timer for pulse
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate_pulse)
        self.timer.start(80)

    def _animate_pulse(self):
        if self._expanding:
            self._halo_radius += 0.25
            if self._halo_radius >= 6.5:
                self._expanding = False
        else:
            self._halo_radius -= 0.25
            if self._halo_radius <= 4.5:
                self._expanding = True
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        center_x = self.width() / 2.0
        center_y = self.height() / 2.0

        # Draw outer glowing halo
        painter.setPen(Qt.NoPen)
        halo_c = QColor(self._halo_color)
        painter.setBrush(QBrush(halo_c))
        painter.drawEllipse(QRectF(center_x - self._halo_radius, center_y - self._halo_radius, self._halo_radius * 2, self._halo_radius * 2))

        # Draw solid center dot
        painter.setBrush(QBrush(self._dot_color))
        painter.drawEllipse(QRectF(center_x - 3.0, center_y - 3.0, 6.0, 6.0))


class ClickableLabel(QLabel):
    """A label that behaves like an anchor tag with hover color transition and URL opening."""
    clicked = Signal()

    def __init__(self, text: str, url: str = "", parent=None):
        super().__init__(text, parent)
        self.url = url
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._default_style = "color: #94a3b8; font-weight: 700; font-size: 10px;"
        self._hover_style = "color: #38bdf8; font-weight: 700; font-size: 10px;"
        self.setStyleSheet(self._default_style)

    def setStyles(self, default_style: str, hover_style: str):
        self._default_style = default_style
        self._hover_style = hover_style
        self.setStyleSheet(default_style)

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_style)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._default_style)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            if self.url:
                webbrowser.open(self.url)
        super().mousePressEvent(event)


class CopiedToastIndicator(QWidget):
    """Shows a smooth 'Copied!' indicator matching the React TitleBar feedback."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 8, 2)
        layout.setSpacing(4)

        self.icon = IconWidget(FluentIcon.ACCEPT, self)
        self.icon.setFixedSize(12, 12)
        layout.addWidget(self.icon)

        self.label = QLabel("Copied!", self)
        self.label.setStyleSheet("color: #38bdf8; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px;")
        layout.addWidget(self.label)

        self.setStyleSheet("""
            QWidget {
                background-color: rgba(56, 189, 248, 0.15);
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 6px;
            }
        """)

        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def trigger(self, text: str = "Copied!", duration_ms: int = 1500):
        self.label.setText(text)
        self.show()
        self.raise_()
        self.timer.start(duration_ms)


def get_display_frame_interval_ms(widget: QWidget = None) -> int:
    """Returns the frame interval in ms matched to the active monitor's native refresh rate (e.g. 3ms @ 300Hz, 4ms @ 240Hz)."""
    hz = 60.0
    try:
        screen = None
        if widget is not None:
            win = widget.window()
            if win and win.windowHandle() and win.windowHandle().screen():
                screen = win.windowHandle().screen()
            elif widget.screen():
                screen = widget.screen()
        if screen is None and QApplication.instance():
            screen = QApplication.primaryScreen()
        if screen is not None:
            r = float(screen.refreshRate())
            if r > 30.0:
                hz = min(360.0, max(60.0, r))
    except Exception:
        hz = 60.0
    return max(2, int(round(1000.0 / hz)))


class GpuSmoothScroller(QObject):
    """High-refresh-rate (240Hz/300Hz VSync-paced) velocity-eased smooth scroll engine.

    Uses a C2-continuous asymmetric ease-in-out velocity profile (20 * t * (1 - t)^3)
    with Hermite initial-velocity preservation on mid-flight retargeting:
    - Smoothly accelerates from rest over the first 25% of duration (no initial snap/jerk).
    - Glides into the target over the remaining 75% with a silky quartic deceleration tail.
    - Preserves real instantaneous velocity (px/ms) when retargeted mid-scroll.
    """
    scroll_finished = Signal()

    def __init__(self, scroll_area: QAbstractScrollArea, parent: QObject = None):
        super().__init__(parent or scroll_area)
        self.scroll_area = scroll_area
        self._start_val: float = 0.0
        self._current_val: float = 0.0
        self._target_val: float = 0.0
        self._initial_velocity: float = 0.0  # px / ms at start of current trajectory
        self._velocity: float = 0.0          # live instantaneous velocity (px / ms)
        self._duration_ms: float = 360.0
        self._active: bool = False

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._on_frame)

        if self.scroll_area.viewport():
            self.scroll_area.viewport().installEventFilter(self)
        sb = self.scroll_area.verticalScrollBar()
        if sb:
            sb.sliderPressed.connect(self.stop)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def target_value(self) -> int:
        return int(round(self._target_val))

    def eventFilter(self, obj, event: QEvent):
        if event.type() == QEvent.Type.Wheel and self._active:
            # Immediately yield to manual user mouse wheel scrolling
            self.stop()
        return super().eventFilter(obj, event)

    def stop(self):
        if self._active:
            self._active = False
            self._velocity = 0.0
            self._initial_velocity = 0.0
            self._timer.stop()
            self._sync_fluent_delegate(self.scroll_area.verticalScrollBar().value())

    def scroll_to(self, target_value: int, duration_ms: int = None):
        sb = self.scroll_area.verticalScrollBar()
        if sb is None:
            return

        # Stop any competing QFluentWidgets SmoothScrollBar animation first
        self._stop_fluent_delegate_ani()

        clamped_target = float(max(sb.minimum(), min(sb.maximum(), int(target_value))))
        current = self._current_val if self._active else float(sb.value())
        distance = abs(clamped_target - current)

        if distance < 1.5:
            self._apply_scroll_value(int(round(clamped_target)))
            self.stop()
            self.scroll_finished.emit()
            return

        self._start_val = current
        self._current_val = current
        self._target_val = clamped_target
        # Preserve live momentum if retargeting mid-flight, otherwise accelerate smoothly from 0
        self._initial_velocity = self._velocity if self._active else 0.0

        if duration_ms is not None:
            self._duration_ms = float(max(80, duration_ms))
        else:
            # Adaptive duration allowing smooth ease-in velocity buildup + long cushioned landing
            self._duration_ms = float(min(540, max(250, int(200 + (distance ** 0.5) * 7.8))))

        interval = get_display_frame_interval_ms(self.scroll_area)
        self._clock.restart()
        self._active = True
        if self._timer.interval() != interval or not self._timer.isActive():
            self._timer.start(interval)

    def _on_frame(self):
        if not self._active:
            self._timer.stop()
            return

        sb = self.scroll_area.verticalScrollBar()
        if sb is None:
            self.stop()
            return

        # Re-clamp target in case document/container max range changed dynamically
        clamped_target = float(max(sb.minimum(), min(sb.maximum(), int(round(self._target_val)))))
        dur = max(1.0, self._duration_ms)
        elapsed_ms = self._clock.nsecsElapsed() / 1_000_000.0
        t = min(1.0, max(0.0, elapsed_ms / dur))

        # Asymmetric C2 ease-in-out velocity curve:
        # s(t) = 1 - (1 - t)^4 * (1 + 4*t)
        # ds/dt = 20 * t * (1 - t)^3  --> accelerates smoothly from 0, peaks at t=0.25, glides out to t=1.0
        inv = 1.0 - t
        inv3 = inv * inv * inv
        inv4 = inv3 * inv
        eased = 1.0 - inv4 * (1.0 + 4.0 * t)
        deased_dt = 20.0 * t * inv3

        delta = clamped_target - self._start_val
        v0_term = (self._initial_velocity * dur) * t * inv4
        dv0_dt = (self._initial_velocity * dur) * inv3 * (1.0 - 5.0 * t)

        self._current_val = self._start_val + delta * eased + v0_term
        self._velocity = (delta * deased_dt + dv0_dt) / dur
        int_val = int(round(self._current_val))

        if sb.value() != int_val:
            self._apply_scroll_value(int_val)

        if t >= 1.0 or (t > 0.35 and abs(clamped_target - self._current_val) < 0.5 and abs(self._velocity) < 0.02):
            final_int = int(round(clamped_target))
            if sb.value() != final_int:
                self._apply_scroll_value(final_int)
            self._active = False
            self._velocity = 0.0
            self._initial_velocity = 0.0
            self._timer.stop()
            self.scroll_finished.emit()

    def _apply_scroll_value(self, val: int):
        sb = self.scroll_area.verticalScrollBar()
        if sb is not None:
            sb.setValue(val)
        self._sync_fluent_delegate(val)

    def _stop_fluent_delegate_ani(self):
        delegate = getattr(self.scroll_area, "delegate", None)
        if delegate is not None:
            vbar = getattr(delegate, "vScrollBar", None)
            if vbar is not None and hasattr(vbar, "ani"):
                vbar.ani.stop()

    def _sync_fluent_delegate(self, val: int):
        delegate = getattr(self.scroll_area, "delegate", None)
        if delegate is not None:
            vbar = getattr(delegate, "vScrollBar", None)
            if vbar is not None:
                if hasattr(vbar, "resetValue"):
                    vbar.resetValue(val)
                if vbar.value() != val:
                    vbar.val = val


class AutoScrollIndicator(QWidget):
    """Sleek floating translucent Fluent anchor indicator for middle-click autoscroll."""
    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(36, 36)
        self.direction = 0  # -1: up, 0: idle, 1: down

    def set_direction(self, direction: int):
        if self.direction != direction:
            self.direction = direction
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        accent = QColor(pal.get("accent", "#38bdf8"))

        # Base circle
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1.2))
        painter.setBrush(QBrush(QColor(24, 24, 27, 230)))
        painter.drawEllipse(1, 1, 34, 34)

        # Center dot
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 180)))
        painter.drawEllipse(QPointF(18, 18), 2.2, 2.2)

        # Up chevron
        up_pen = QPen(accent if self.direction == -1 else QColor(255, 255, 255, 120), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(up_pen)
        painter.drawLine(12, 12, 18, 7)
        painter.drawLine(18, 7, 24, 12)

        # Down chevron
        down_pen = QPen(accent if self.direction == 1 else QColor(255, 255, 255, 120), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(down_pen)
        painter.drawLine(12, 24, 18, 29)
        painter.drawLine(18, 29, 24, 24)


class _GlobalAutoScrollFilter(QObject):
    """Single shared event filter for all MiddleClickAutoScroller instances.

    When no autoscroll is active, immediately ignores all events except MouseButtonPress
    in O(1) without per-instance overhead across the application.
    """
    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        import weakref
        self.scrollers = weakref.WeakSet()

    def eventFilter(self, obj, event: QEvent) -> bool:
        active = MiddleClickAutoScroller._ACTIVE_SCROLLER
        etype = event.type()

        if active is None:
            if etype != QEvent.MouseButtonPress:
                return False
            if event.button() != Qt.MiddleButton:
                return False
            for scroller in list(self.scrollers):
                if scroller._is_target_event(obj):
                    gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                    scroller.start(gpos)
                    return True
            return False

        return active.eventFilter(obj, event)


class MiddleClickAutoScroller(QObject):
    """High-refresh-rate middle-click autoscroll engine for native scroll areas.
    
    Provides frictionless browsing through long scripts and timestamps:
    - Hold-and-drag: hold middle mouse button, move away from center to scroll smoothly, release to stop.
    - Click-and-glide: click middle mouse button once to enter sticky autoscroll, move mouse to cruise,
      click any mouse button or press Escape/scroll wheel to stop.
    """
    _ACTIVE_SCROLLER: Optional['MiddleClickAutoScroller'] = None
    _GLOBAL_FILTER: Optional[_GlobalAutoScrollFilter] = None

    def __init__(self, scroll_area: QAbstractScrollArea, parent: QObject = None):
        super().__init__(parent or scroll_area)
        self.scroll_area = scroll_area
        self._indicator: Optional[AutoScrollIndicator] = None

        self._active = False
        self._anchor_pos = QPoint()
        self._current_pos = QPoint()
        self._press_pos = QPoint()
        self._has_dragged = False
        self._subpixel = 0.0

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._on_frame)

        app = QApplication.instance()
        if app:
            if MiddleClickAutoScroller._GLOBAL_FILTER is None:
                MiddleClickAutoScroller._GLOBAL_FILTER = _GlobalAutoScrollFilter(app)
                app.installEventFilter(MiddleClickAutoScroller._GLOBAL_FILTER)
            MiddleClickAutoScroller._GLOBAL_FILTER.scrollers.add(self)

    @property
    def is_active(self) -> bool:
        return self._active

    def _is_target_event(self, obj) -> bool:
        if obj is None:
            return False
        try:
            if obj is self.scroll_area:
                return True
            vp = self.scroll_area.viewport()
            if vp is not None:
                if obj is vp:
                    return True
                if hasattr(self.scroll_area, "isAncestorOf") and self.scroll_area.isAncestorOf(obj):
                    return True
        except RuntimeError:
            return False
        return False

    def eventFilter(self, obj, event: QEvent) -> bool:
        etype = event.type()

        if not self._active:
            if etype == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                if self._is_target_event(obj):
                    gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                    self.start(gpos)
                    return True
            return False

        if etype == QEvent.MouseMove:
            gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            self._current_pos = gpos
            if not self._has_dragged:
                if (self._current_pos - self._press_pos).manhattanLength() > 6:
                    self._has_dragged = True
            return False

        if etype == QEvent.MouseButtonPress:
            self.stop()
            return True

        if etype == QEvent.MouseButtonRelease:
            if event.button() == Qt.MiddleButton and self._has_dragged:
                self.stop()
                return True
            return False

        if etype in (QEvent.Wheel, QEvent.KeyPress):
            self.stop()
            if etype == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                return True
            return False

        return False

    def start(self, global_pos: QPoint):
        if MiddleClickAutoScroller._ACTIVE_SCROLLER is not None and MiddleClickAutoScroller._ACTIVE_SCROLLER is not self:
            MiddleClickAutoScroller._ACTIVE_SCROLLER.stop()

        MiddleClickAutoScroller._ACTIVE_SCROLLER = self
        self._active = True
        self._anchor_pos = QPoint(global_pos)
        self._current_pos = QPoint(global_pos)
        self._press_pos = QPoint(global_pos)
        self._has_dragged = False
        self._subpixel = 0.0

        if self._indicator is None:
            self._indicator = AutoScrollIndicator()
        self._indicator.set_direction(0)
        self._indicator.move(global_pos.x() - 18, global_pos.y() - 18)
        self._indicator.show()
        self._indicator.raise_()

        QApplication.setOverrideCursor(Qt.SizeVerCursor)

        interval = get_display_frame_interval_ms(self.scroll_area)
        self._clock.restart()
        self._timer.start(interval)

    def stop(self):
        if not self._active:
            return
        self._active = False
        if MiddleClickAutoScroller._ACTIVE_SCROLLER is self:
            MiddleClickAutoScroller._ACTIVE_SCROLLER = None

        self._timer.stop()
        if self._indicator is not None:
            self._indicator.hide()

        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass

        sb = self.scroll_area.verticalScrollBar()
        if sb is not None:
            self._sync_fluent_delegate(sb.value())

    def _on_frame(self):
        if not self._active:
            self._timer.stop()
            return

        sb = self.scroll_area.verticalScrollBar()
        if sb is None:
            self.stop()
            return

        dy = self._current_pos.y() - self._anchor_pos.y()
        dead_zone = 8

        if abs(dy) <= dead_zone:
            if self._indicator is not None:
                self._indicator.set_direction(0)
            return

        sign = 1 if dy > 0 else -1
        if self._indicator is not None:
            self._indicator.set_direction(sign)

        delta = abs(dy) - dead_zone
        # Smooth progressive speed curve
        speed = sign * min(150.0, (delta * 0.35 + (delta ** 1.55) * 0.045))

        interval = max(2, self._timer.interval())
        self._subpixel += speed * (interval / 16.0)
        step = int(self._subpixel)

        if step != 0:
            self._subpixel -= step
            old_val = sb.value()
            new_val = max(sb.minimum(), min(sb.maximum(), old_val + step))
            if new_val != old_val:
                sb.setValue(new_val)
                self._sync_fluent_delegate(new_val)

    def _sync_fluent_delegate(self, val: int):
        delegate = getattr(self.scroll_area, "delegate", None)
        if delegate is not None:
            vbar = getattr(delegate, "vScrollBar", None)
            if vbar is not None:
                if hasattr(vbar, "ani") and vbar.ani is not None:
                    vbar.ani.stop()
                if hasattr(vbar, "resetValue"):
                    vbar.resetValue(val)
                if vbar.value() != val:
                    vbar.val = val


class GpuGlowSplitterHandle(QSplitterHandle):
    """High-refresh-rate GPU-composited glowing divider handle between Script and Timeline panes.

    Provides an unmistakable visual cue that the middle split is adjustable:
    - Idle: subtle vertical guide line + centered capsule grip pill.
    - Hover / Drag: smooth 240Hz/300Hz animated neon/accent glow beam, radial bloom,
      and expanding tactile grip capsule.
    """
    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.SplitHCursor if orientation == Qt.Horizontal else Qt.SplitVCursor)

        self._hovered = False
        self._dragging = False
        self._hover_t = 0.0  # 0.0 (idle) -> 1.0 (hovered)
        self._drag_t = 0.0   # 0.0 (not dragging) -> 1.0 (actively dragging)

        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._step_animation)

    def enterEvent(self, event):
        self._hovered = True
        self._start_anim()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._start_anim()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._start_anim()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._start_anim()
        super().mouseReleaseEvent(event)

    def _start_anim(self):
        self._clock.restart()
        interval = get_display_frame_interval_ms(self)
        if self._timer.interval() != interval or not self._timer.isActive():
            self._timer.start(interval)

    def _step_animation(self):
        dt_ms = min(32.0, max(1.0, self._clock.nsecsElapsed() / 1_000_000.0))
        self._clock.restart()

        target_hover = 1.0 if (self._hovered or self._dragging) else 0.0
        target_drag = 1.0 if self._dragging else 0.0

        # Smooth exponential velocity damping (~140ms response)
        rate = min(1.0, dt_ms / 110.0)
        self._hover_t += (target_hover - self._hover_t) * rate
        self._drag_t += (target_drag - self._drag_t) * rate

        if abs(target_hover - self._hover_t) < 0.008 and abs(target_drag - self._drag_t) < 0.008:
            self._hover_t = target_hover
            self._drag_t = target_drag
            self._timer.stop()

        self.update()

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        rgb_parts = [int(x.strip()) for x in pal.get("accent_rgb", "251, 113, 133").split(",")]
        ar, ag, ab = rgb_parts[0], rgb_parts[1], rgb_parts[2]

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        cx = w / 2.0
        cy = h / 2.0
        glow = max(self._hover_t, self._drag_t)

        # 1. Subtle idle guide line (fades out at top and bottom edges)
        idle_grad = QLinearGradient(cx, 0, cx, h)
        base_alpha = 26 if is_dark else 32
        idle_col = QColor(255, 255, 255, base_alpha) if is_dark else QColor(0, 0, 0, base_alpha)
        clear_col = QColor(idle_col.red(), idle_col.green(), idle_col.blue(), 0)
        idle_grad.setColorAt(0.0, clear_col)
        idle_grad.setColorAt(0.12, idle_col)
        idle_grad.setColorAt(0.88, idle_col)
        idle_grad.setColorAt(1.0, clear_col)
        painter.setBrush(QBrush(idle_grad))
        painter.drawRect(QRectF(cx - 0.5, 4.0, 1.0, max(0.0, h - 8.0)))

        # 2. GPU-composited hover/drag multi-stage glow line & radial bloom
        if glow > 0.005:
            # Stage A: Wide soft vertical bloom across the handle gutter
            bloom_w = min(float(w), 6.0 + 4.0 * glow + 2.0 * self._drag_t)
            bloom_grad = QLinearGradient(cx, 0, cx, h)
            bloom_alpha = int(round((52 + 38 * self._drag_t) * glow))
            bloom_col = QColor(ar, ag, ab, bloom_alpha)
            bloom_clear = QColor(ar, ag, ab, 0)
            bloom_grad.setColorAt(0.0, bloom_clear)
            bloom_grad.setColorAt(0.10, bloom_col)
            bloom_grad.setColorAt(0.50, QColor(ar, ag, ab, min(255, int(round(bloom_alpha * 1.35)))))
            bloom_grad.setColorAt(0.90, bloom_col)
            bloom_grad.setColorAt(1.0, bloom_clear)
            painter.setBrush(QBrush(bloom_grad))
            painter.drawRoundedRect(QRectF(cx - bloom_w / 2.0, 2.0, bloom_w, max(0.0, h - 4.0)), bloom_w / 2.0, bloom_w / 2.0)

            # Stage B: Center radial hotspot around the grip pill
            hotspot_r = 48.0 + 24.0 * glow + 16.0 * self._drag_t
            radial = QRadialGradient(QPointF(cx, cy), hotspot_r)
            radial.setColorAt(0.0, QColor(ar, ag, ab, int(round((115 + 45 * self._drag_t) * glow))))
            radial.setColorAt(0.5, QColor(ar, ag, ab, int(round(45 * glow))))
            radial.setColorAt(1.0, QColor(ar, ag, ab, 0))
            painter.setBrush(QBrush(radial))
            painter.drawRect(QRectF(0.0, max(0.0, cy - hotspot_r), float(w), min(float(h), hotspot_r * 2.0)))

            # Stage C: Crisp luminous 2px core laser line
            core_w = 1.6 + 0.6 * self._drag_t
            core_grad = QLinearGradient(cx, 0, cx, h)
            core_alpha = int(round((215 + 40 * self._drag_t) * glow))
            core_col = QColor(ar, ag, ab, min(255, core_alpha))
            core_grad.setColorAt(0.0, bloom_clear)
            core_grad.setColorAt(0.08, core_col)
            core_grad.setColorAt(0.50, QColor(min(255, ar + 35), min(255, ag + 35), min(255, ab + 35), min(255, core_alpha)))
            core_grad.setColorAt(0.92, core_col)
            core_grad.setColorAt(1.0, bloom_clear)
            painter.setBrush(QBrush(core_grad))
            painter.drawRoundedRect(QRectF(cx - core_w / 2.0, 4.0, core_w, max(0.0, h - 8.0)), core_w / 2.0, core_w / 2.0)

        # 3. Always-visible tactile center grip capsule (expands and brightens on hover/drag)
        pill_h = 34.0 + (22.0 * glow) + (18.0 * self._drag_t)
        pill_w = 3.0 + (1.2 * glow) + (0.6 * self._drag_t)
        idle_pill_alpha = 95 if is_dark else 110
        pill_alpha = int(round(idle_pill_alpha + (255 - idle_pill_alpha) * glow))
        pill_col = QColor(
            int(round(ar + (255 - ar) * (0.28 * glow + 0.22 * self._drag_t))),
            int(round(ag + (255 - ag) * (0.28 * glow + 0.22 * self._drag_t))),
            int(round(ab + (255 - ab) * (0.28 * glow + 0.22 * self._drag_t))),
            min(255, pill_alpha)
        )
        painter.setBrush(QBrush(pill_col))
        painter.drawRoundedRect(
            QRectF(cx - pill_w / 2.0, cy - pill_h / 2.0, pill_w, pill_h),
            pill_w / 2.0,
            pill_w / 2.0
        )


class GpuGlowSplitter(QSplitter):
    """Custom QSplitter with GPU-accelerated glowing hover/drag divider handle."""
    def __init__(self, orientation: Qt.Orientation = Qt.Horizontal, parent: QWidget = None):
        super().__init__(orientation, parent)
        self.setHandleWidth(12)

    def createHandle(self) -> QSplitterHandle:
        return GpuGlowSplitterHandle(self.orientation(), self)


class MagneticDetentSlider(QWidget):
    """Custom Fluent slider with on-track circular detent nodes, subtle caption labels beneath each node,
    and magnetic snapping (±3%) while retaining 0..100% free sliding.
    """
    valueChanged = Signal(int)
    sliderReleased = Signal(int)

    def __init__(self, detents=None, snap_radius: int = 3, parent: QWidget = None):
        super().__init__(parent)
        self._min = 0
        self._max = 100
        self._value = 25
        # List of (int_value, label_str)
        self._detents = detents or [(0, "Off"), (25, "Subtle"), (55, "Middle"), (100, "Max")]
        self._snap_radius = int(snap_radius)
        self._dragging = False
        self._hovered = False
        self._hovered_detent: int = -1

        self.setFixedHeight(42)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    def value(self) -> int:
        return self._value

    def setRange(self, min_val: int, max_val: int):
        self._min = int(min_val)
        self._max = max(self._min + 1, int(max_val))
        self.update()

    def setValue(self, val: int, apply_snap: bool = False):
        clamped = max(self._min, min(self._max, int(val)))
        if apply_snap:
            for d_val, _ in self._detents:
                if abs(clamped - d_val) <= self._snap_radius:
                    clamped = d_val
                    break
        if clamped != self._value:
            self._value = clamped
            self.update()
            self.valueChanged.emit(self._value)
        else:
            self.update()

    def _track_geometry(self):
        margin_x = 28.0
        track_y = 13.0
        track_w = max(10.0, float(self.width()) - margin_x * 2.0)
        return margin_x, track_y, track_w

    def _val_to_x(self, val: float) -> float:
        margin_x, _, track_w = self._track_geometry()
        ratio = (float(val) - self._min) / max(1.0, float(self._max - self._min))
        return margin_x + max(0.0, min(1.0, ratio)) * track_w

    def _x_to_val(self, x: float, apply_snap: bool = True) -> int:
        margin_x, _, track_w = self._track_geometry()
        ratio = (float(x) - margin_x) / max(1.0, track_w)
        raw = int(round(self._min + max(0.0, min(1.0, ratio)) * (self._max - self._min)))
        if apply_snap:
            for d_val, _ in self._detents:
                if abs(raw - d_val) <= self._snap_radius:
                    return d_val
        return raw

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._hovered_detent = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if not self.isEnabled():
            return
        if event.button() == Qt.LeftButton:
            self._dragging = True
            pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
            # Check if user clicked directly on/near a detent node or its label
            target_val = None
            for d_val, _ in self._detents:
                dx = abs(pos.x() - self._val_to_x(d_val))
                if dx <= 12.0:
                    target_val = d_val
                    break
            if target_val is None:
                target_val = self._x_to_val(pos.x(), apply_snap=True)
            self.setValue(target_val)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.isEnabled():
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._dragging:
            if event.buttons() & Qt.LeftButton:
                self.setValue(self._x_to_val(pos.x(), apply_snap=True))
                event.accept()
                return
            else:
                self._dragging = False
                self.update()
                self.sliderReleased.emit(self._value)
        # Update hovered detent highlight
        best = -1
        for d_val, _ in self._detents:
            if abs(pos.x() - self._val_to_x(d_val)) <= 12.0:
                best = d_val
                break
        if best != self._hovered_detent:
            self._hovered_detent = best
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.update()
            self.sliderReleased.emit(self._value)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        enabled = self.isEnabled()
        rgb_parts = [int(x.strip()) for x in pal.get("accent_rgb", "251, 113, 133").split(",")]
        accent_col = QColor(rgb_parts[0], rgb_parts[1], rgb_parts[2])
        if not enabled:
            accent_col = QColor(115, 115, 125, 110)

        margin_x, track_y, track_w = self._track_geometry()
        thumb_x = self._val_to_x(self._value)
        track_h = 4.0

        # 1. Unfilled background track
        unfilled_col = QColor(255, 255, 255, 42 if enabled else 22) if is_dark else QColor(0, 0, 0, 42 if enabled else 24)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(unfilled_col))
        painter.drawRoundedRect(QRectF(margin_x, track_y - track_h / 2.0, track_w, track_h), 2.0, 2.0)

        # 2. Active filled track (from start to thumb)
        fill_w = max(0.0, thumb_x - margin_x)
        if fill_w > 0.5:
            painter.setBrush(QBrush(accent_col))
            painter.drawRoundedRect(QRectF(margin_x, track_y - track_h / 2.0, fill_w, track_h), 2.0, 2.0)

        # 3. Circular detent snap nodes directly on the track + centered caption labels underneath
        font = painter.font()
        font.setPixelSize(10)
        for d_val, d_label in self._detents:
            dx = self._val_to_x(d_val)
            is_passed = self._value >= d_val
            is_exact = self._value == d_val
            is_node_hover = (self._hovered_detent == d_val) and enabled

            node_r = 5.5 if (is_exact or is_node_hover) else 4.5
            if is_passed and enabled:
                node_fill = QColor(accent_col)
                node_border = QColor(255, 255, 255, 190) if is_dark else QColor(255, 255, 255, 230)
            else:
                node_fill = QColor(52, 56, 66, 245 if enabled else 140) if is_dark else QColor(215, 220, 228, 250 if enabled else 160)
                node_border = QColor(255, 255, 255, 85 if enabled else 40) if is_dark else QColor(0, 0, 0, 70 if enabled else 35)

            painter.setPen(QPen(node_border, 1.4))
            painter.setBrush(QBrush(node_fill))
            painter.drawEllipse(QPointF(dx, track_y), node_r, node_r)

            # Subtle caption centered directly beneath each detent dot
            if d_label:
                font.setBold(is_exact and enabled)
                painter.setFont(font)
                if is_exact and enabled:
                    lbl_col = accent_col
                else:
                    lbl_col = QColor(pal.get("muted", "#94a3b8"))
                    lbl_col.setAlpha(215 if enabled else 115)
                painter.setPen(lbl_col)
                lbl_w = 68.0
                lbl_x = max(0.0, min(float(self.width()) - lbl_w, dx - lbl_w / 2.0))
                painter.drawText(QRectF(lbl_x, track_y + 10.0, lbl_w, 16.0), Qt.AlignHCenter | Qt.AlignTop, d_label)

        # 4. Slider Thumb Handle (Fluent outer ring + inner accent core)
        outer_r = 9.0 if (self._dragging or self._hovered) and enabled else 8.0
        inner_r = 4.6 if self._dragging and enabled else (4.2 if self._hovered and enabled else 3.8)
        ring_bg = QColor(42, 44, 50, 245) if is_dark else QColor(255, 255, 255, 250)
        ring_border = QColor(255, 255, 255, 65 if enabled else 25) if is_dark else QColor(0, 0, 0, 45 if enabled else 20)

        painter.setPen(QPen(ring_border, 1.2))
        painter.setBrush(QBrush(ring_bg))
        painter.drawEllipse(QPointF(thumb_x, track_y), outer_r, outer_r)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(accent_col))
        painter.drawEllipse(QPointF(thumb_x, track_y), inner_r, inner_r)
