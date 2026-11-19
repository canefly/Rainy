import os
import json
import time
from typing import Optional, Dict, Any, Callable
from PySide6.QtCore import QObject, Signal, QByteArray, QEventLoop, QTimer, QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .database import db
from .logger import get_logger

logger = get_logger("GuiIpcServer")

PIPE_NAME = "knot-native-mcp"


class GuiIpcServer(QObject):
    """
    Live GUI IPC Server hosted inside the running Rainy PySide6 application.
    Routes incoming MCP tool calls directly onto the Qt Main Thread so the GUI
    simulates real checkbox clicks, renders VO script diff highlights, and saves
    state through Rainy' own native engine without external SQLite touches.
    """
    command_received = Signal(str, dict)  # (action, params)

    def __init__(self, handler_callback: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self._handler_callback = handler_callback
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._clients = set()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running and self._server.isListening()

    @property
    def pipe_name(self) -> str:
        return PIPE_NAME

    def set_handler(self, callback: Callable[[str, Dict[str, Any]], Dict[str, Any]]):
        self._handler_callback = callback

    def start(self, pipe_name: str = PIPE_NAME) -> bool:
        if self._server.isListening():
            return True
        QLocalServer.removeServer(pipe_name)
        ok = self._server.listen(pipe_name)
        self._is_running = ok
        from .mcp_activity import mcp_activity
        mcp_activity.set_bridge_running(ok)
        if ok:
            logger.info("Rainy Live GUI MCP Pipe listening on '%s'", self._server.fullServerName())
        else:
            logger.warning("Failed to start GUI IPC Server on '%s': %s", pipe_name, self._server.errorString())
        return ok

    def stop(self):
        for sock in list(self._clients):
            try:
                sock.disconnectFromServer()
                sock.deleteLater()
            except Exception:
                pass
        self._clients.clear()
        if self._server.isListening():
            self._server.close()
        self._is_running = False
        from .mcp_activity import mcp_activity
        mcp_activity.set_bridge_running(False)

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if not sock:
                continue
            self._clients.add(sock)
            sock._recv_buf = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_disconnected(self, sock: QLocalSocket):
        self._clients.discard(sock)
        sock.deleteLater()

    def _on_ready_read(self, sock: QLocalSocket):
        try:
            raw = bytes(sock.readAll())
            sock._recv_buf = getattr(sock, "_recv_buf", b"") + raw
            while b"\n" in sock._recv_buf:
                line, sock._recv_buf = sock._recv_buf.split(b"\n", 1)
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                response = self._process_message(line_str)
                out_bytes = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
                sock.write(QByteArray(out_bytes))
                sock.flush()
        except Exception as e:
            logger.error("Error handling IPC message: %s", e, exc_info=True)

    def _process_message(self, line_str: str) -> Dict[str, Any]:
        try:
            req = json.loads(line_str)
        except Exception as e:
            return {"ok": False, "error": f"Invalid JSON payload: {e}"}

        action = req.get("action", "")
        params = req.get("params") or {}
        req_id = req.get("id")
        client = req.get("client") if isinstance(req.get("client"), dict) else {}

        from .mcp_activity import HELLO_ACTION, mcp_activity
        if action == HELLO_ACTION:
            label = mcp_activity.note_client(client, connected=True)
            return {"id": req_id, "ok": True, "result": {"pong": True, "client": label}}

        if not self._handler_callback:
            return {"id": req_id, "ok": False, "error": "GUI handler not attached."}

        t0 = time.perf_counter()
        try:
            result = self._handler_callback(action, params)
            response = {"id": req_id, "ok": True, "result": result}
        except Exception as e:
            logger.error("GUI handler error for action '%s': %s", action, e, exc_info=True)
            response = {"id": req_id, "ok": False, "error": str(e)}
        try:
            mcp_activity.record_call(
                action, params, client,
                ok=response["ok"], result=response.get("result"), error=response.get("error", ""),
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except Exception as e:  # the log must never break a call
            logger.warning("MCP activity log failed for '%s': %s", action, e)
        return response


def send_gui_ipc_request(
    action: str,
    params: Optional[Dict[str, Any]] = None,
    pipe_name: str = PIPE_NAME,
    timeout_ms: int = 8000,
    client: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Client helper used by mcp_server.py and unit tests to send a command to the
    running Rainy GUI over QLocalSocket and await the JSON response.
    """
    app = QCoreApplication.instance()
    sock = QLocalSocket()
    sock.connectToServer(pipe_name)

    conn_elapsed = 0
    while sock.state() != QLocalSocket.ConnectedState and conn_elapsed < min(2000, timeout_ms):
        if app:
            app.processEvents()
        if sock.waitForConnected(25):
            break
        conn_elapsed += 25

    if sock.state() != QLocalSocket.ConnectedState:
        return {
            "ok": False,
            "error": (
                "Rainy Native GUI is not currently running or its Live MCP Bridge is disabled. "
                "Please open Rainy and ensure your script project is loaded."
            ),
        }

    if app:
        app.processEvents()

    payload = json.dumps({"action": action, "params": params or {}, "client": client or {}}, ensure_ascii=False) + "\n"
    sock.write(QByteArray(payload.encode("utf-8")))
    sock.flush()
    sock.waitForBytesWritten(200)
    if app:
        app.processEvents()

    buf = b""
    elapsed = 0
    step = 25
    while b"\n" not in buf and elapsed < timeout_ms:
        if app:
            app.processEvents()
        if sock.bytesAvailable() > 0 or sock.waitForReadyRead(step):
            buf += bytes(sock.readAll())
            if b"\n" in buf:
                break
        else:
            elapsed += step
            if sock.state() != QLocalSocket.ConnectedState and sock.bytesAvailable() == 0:
                break

    sock.disconnectFromServer()
    if app:
        app.processEvents()
    if not buf:
        return {"ok": False, "error": f"Timed out waiting for Rainy GUI response ({action})."}

    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
    try:
        return json.loads(line)
    except Exception as e:
        return {"ok": False, "error": f"Malformed response from GUI: {e}"}
