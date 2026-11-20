"""Live channel to rainy-api over a WebSocket (wss in release builds).

Sends the signed `hello`, keeps the link alive with pings, reconnects with backoff, and hands every
server frame to the license service. It makes no licensing decisions itself.

Backoff matters: when the server tells us something that retrying won't fix (another copy of this
device connected, we're rate limited, our signature was refused) we wait much longer instead of
hammering it. Protocol details: docs/rainy-api-plan.md ("Live channel").
"""
import json
import random
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from .logger import get_logger

logger = get_logger("LiveChannel")

PING_MS = 25_000
WATCHDOG_MS = 70_000          # nothing at all from the server for this long: assume the link is dead
BACKOFF_CAP_S = 60.0

# error code from the server -> minimum seconds before we try again
SLOW_RETRY_S = {
    "replaced": 60.0,         # another copy of this device took over: don't fight it
    "rate_limited": 60.0,
    "server_busy": 30.0,
    "auth_failed": 30.0,
    "clock_skew": 30.0,
    "replayed": 30.0,
    "unknown_device": 30.0,
}
# codes that mean "the HTTP API should look at this" (the service runs a refresh when it sees one)
AUTH_REJECTIONS = {"auth_failed", "clock_skew", "replayed", "unknown_device"}


class LiveChannel(QObject):
    frame_received = Signal(dict)     # status / token / revoked frames for the license service
    connected_changed = Signal(bool)  # True once the server has answered our hello
    rejected = Signal(str)            # the server sent an {"t": "error", "code": ...} frame

    def __init__(self, url_provider: Callable[[], str], hello_provider: Callable[[], Optional[dict]], *,
                 ping_ms: int = PING_MS, watchdog_ms: int = WATCHDOG_MS, rng: Callable[[], float] = random.random,
                 parent=None):
        super().__init__(parent)
        self._url_provider = url_provider
        self._hello_provider = hello_provider
        self._rng = rng
        self._enabled = False
        self._healthy = False
        self._attempt = 0
        self._ws: Optional[QWebSocket] = None
        self._down_handled = True
        self._error_code: Optional[str] = None

        self._ping = QTimer(self)
        self._ping.setInterval(ping_ms)
        self._ping.timeout.connect(self._send_ping)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(watchdog_ms)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._connect)

    # -- public ----------------------------------------------------------------------
    @property
    def healthy(self) -> bool:
        return self._healthy

    def start(self) -> None:
        """Idempotent: connect if we aren't connected, connecting, or waiting to retry."""
        self._enabled = True
        if self._ws is None and not self._retry.isActive():
            self._connect()

    def stop(self) -> None:
        self._enabled = False
        self._retry.stop()
        self._ping.stop()
        self._watchdog.stop()
        sock, self._ws = self._ws, None
        self._down_handled = True
        if sock is not None:
            self._discard(sock)
        self._set_healthy(False)

    # -- connection lifecycle --------------------------------------------------------
    def _connect(self) -> None:
        if not self._enabled or self._ws is not None:
            return
        if self._hello_provider() is None:  # not registered / no key: nothing to say, don't connect
            return
        try:
            url = self._url_provider()
        except ValueError as e:
            logger.error("Live channel disabled: %s", e)
            return
        sock = QWebSocket()
        self._ws = sock
        self._down_handled = False
        self._error_code = None
        sock.connected.connect(lambda s=sock: self._on_connected(s))
        sock.disconnected.connect(lambda s=sock: self._on_down(s))
        sock.errorOccurred.connect(lambda _e, s=sock: self._on_down(s))
        sock.textMessageReceived.connect(lambda text, s=sock: self._on_text(s, text))
        sock.open(QUrl(url))

    def _discard(self, sock: QWebSocket) -> None:
        try:
            sock.connected.disconnect()
            sock.disconnected.disconnect()
            sock.errorOccurred.disconnect()
            sock.textMessageReceived.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            sock.abort()
        except RuntimeError:
            pass
        sock.deleteLater()

    def _on_connected(self, sock: QWebSocket) -> None:
        if sock is not self._ws:
            return
        hello = self._hello_provider()
        if hello is None:
            self._on_down(sock)
            return
        sock.sendTextMessage(json.dumps(hello))
        self._ping.start()
        self._watchdog.start()

    def _on_text(self, sock: QWebSocket, text: str) -> None:
        if sock is not self._ws:
            return
        self._watchdog.start()  # any frame proves the link is alive
        try:
            frame = json.loads(text)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        kind = frame.get("t")
        if kind == "pong":
            return
        if kind == "error":
            code = str(frame.get("code", ""))
            self._error_code = code
            logger.info("Live channel refused by server: %s", code)
            self.rejected.emit(code)
            return
        if not self._healthy:
            self._attempt = 0  # the server accepted us: a good connection resets the backoff
            self._set_healthy(True)
        self.frame_received.emit(frame)

    def _on_down(self, sock: QWebSocket) -> None:
        if sock is not self._ws or self._down_handled:
            return
        self._down_handled = True
        self._ping.stop()
        self._watchdog.stop()
        self._ws = None
        self._discard(sock)
        self._set_healthy(False)
        if self._enabled:
            self._schedule_reconnect()

    def _on_watchdog(self) -> None:
        logger.info("Live channel silent for too long, reconnecting")
        if self._ws is not None:
            self._on_down(self._ws)

    def _send_ping(self) -> None:
        if self._ws is not None:
            self._ws.sendTextMessage('{"t":"ping"}')

    def _schedule_reconnect(self) -> None:
        floor = SLOW_RETRY_S.get(self._error_code or "", 0.0)
        base = min(BACKOFF_CAP_S, 2.0 ** self._attempt)
        self._attempt += 1
        delay = max(floor, base) * (0.75 + 0.5 * self._rng())  # jitter so clients don't stampede
        self._retry.start(int(delay * 1000))

    def _set_healthy(self, ok: bool) -> None:
        if ok != self._healthy:
            self._healthy = ok
            self.connected_changed.emit(ok)
