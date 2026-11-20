"""LiveChannel against a real (local, in-process) WebSocket server built with Qt."""
import itertools
import json
import os
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import live_channel as lcm
from src.core.live_channel import LiveChannel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def wait_until(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


def settle(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


class FakeServer(QObject):
    """Records what clients send; lets a test push frames or drop the connection."""

    def __init__(self):
        super().__init__()
        self.server = QWebSocketServer("fake-rainy", QWebSocketServer.SslMode.NonSecureMode, self)
        assert self.server.listen(QHostAddress.LocalHost, 0)
        self.server.newConnection.connect(self._on_new)
        self.conns = []

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.server.serverPort()}/v1/ws"

    def _on_new(self):
        sock = self.server.nextPendingConnection()
        rec = {"sock": sock, "received": [], "closed": False}
        sock.textMessageReceived.connect(lambda text, r=rec: r["received"].append(json.loads(text)))
        sock.disconnected.connect(lambda r=rec: r.__setitem__("closed", True))
        self.conns.append(rec)

    def send(self, frame, idx=-1):
        self.conns[idx]["sock"].sendTextMessage(json.dumps(frame))

    def drop(self, idx=-1):
        self.conns[idx]["sock"].close()

    def shutdown(self):
        for rec in self.conns:
            try:
                rec["sock"].abort()
            except RuntimeError:
                pass
        self.server.close()


@pytest.fixture
def server(qapp):
    s = FakeServer()
    yield s
    s.shutdown()


@pytest.fixture
def make(server):
    made = []
    counter = itertools.count(1)

    def factory(*, hello=True, url=None, **kw):
        provider = (lambda: {"t": "hello", "n": next(counter)}) if hello else (lambda: None)
        kw.setdefault("rng", lambda: 0.0)   # jitter factor 0.75: predictable delays
        ch = LiveChannel(lambda: url or server.url, provider, **kw)
        made.append(ch)
        return ch

    yield factory
    for ch in made:
        ch.stop()


def record(ch):
    log = {"frames": [], "health": [], "rejected": []}
    ch.frame_received.connect(log["frames"].append)
    ch.connected_changed.connect(log["health"].append)
    ch.rejected.connect(log["rejected"].append)
    return log


# -- connecting -------------------------------------------------------------------------------
def test_sends_hello_then_becomes_healthy_when_the_server_answers(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns and server.conns[0]["received"])
    assert server.conns[0]["received"][0]["t"] == "hello"
    assert not ch.healthy                               # connected is not enough: the server must accept us
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    assert log["health"] == [True]
    assert log["frames"] == [{"t": "status", "status": "pending"}]


def test_never_connects_without_a_hello(server, make):
    ch = make(hello=False)
    ch.start()
    settle(0.4)
    assert server.conns == [] and not ch.healthy


def test_start_is_idempotent(server, make):
    ch = make()
    for _ in range(4):
        ch.start()
    assert wait_until(lambda: len(server.conns) >= 1)
    settle(0.3)
    assert len(server.conns) == 1


def test_bad_url_disables_quietly(server, make):
    def bad():
        raise ValueError("not an http(s) URL")
    ch = LiveChannel(bad, lambda: {"t": "hello"}, rng=lambda: 0.0)
    ch.start()
    settle(0.2)
    assert not ch.healthy and ch._ws is None
    ch.stop()


# -- frames -----------------------------------------------------------------------------------
def test_pongs_and_junk_are_swallowed_but_real_frames_pass(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "pong"})
    server.conns[0]["sock"].sendTextMessage("not json at all")
    server.conns[0]["sock"].sendTextMessage("[1, 2, 3]")
    server.send({"t": "status", "status": "approved"})
    server.send({"t": "token", "token": "abc", "expires_at": 5})
    assert wait_until(lambda: len(log["frames"]) == 2)
    assert [f["t"] for f in log["frames"]] == ["status", "token"]
    assert ch.healthy


def test_error_frames_are_reported_and_never_count_as_healthy(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "error", "code": "auth_failed"})
    assert wait_until(lambda: log["rejected"] == ["auth_failed"])
    assert not ch.healthy and log["frames"] == []


# -- keepalive --------------------------------------------------------------------------------
def test_pings_are_sent_while_connected(server, make):
    ch = make(ping_ms=80)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: sum(1 for f in server.conns[0]["received"] if f == {"t": "ping"}) >= 3)


def test_watchdog_drops_and_reconnects_a_silent_link(server, make):
    ch = make(watchdog_ms=300)
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    # server now says nothing at all: after the watchdog window the client gives up and reconnects
    assert wait_until(lambda: log["health"] == [True, False], timeout=3)
    assert wait_until(lambda: len(server.conns) == 2, timeout=5)
    assert wait_until(lambda: server.conns[1]["received"])
    assert server.conns[1]["received"][0]["n"] > server.conns[0]["received"][0]["n"]  # fresh hello, new nonce


def test_any_frame_resets_the_watchdog(server, make):
    ch = make(watchdog_ms=400, ping_ms=10_000)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    for _ in range(6):                                  # 6 x 150 ms > 400 ms, but traffic keeps it alive
        settle(0.15)
        server.send({"t": "pong"})
    assert ch.healthy and len(server.conns) == 1


# -- reconnecting -----------------------------------------------------------------------------
def test_reconnects_after_the_server_drops_us_and_reports_the_outage(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    server.drop()
    assert wait_until(lambda: log["health"] == [True, False])
    assert wait_until(lambda: len(server.conns) == 2, timeout=5)
    assert server.conns[1]["received"] or wait_until(lambda: server.conns[1]["received"])


def test_stop_closes_the_link_and_never_reconnects(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    ch.stop()
    assert wait_until(lambda: server.conns[0]["closed"])
    assert log["health"] == [True, False]
    settle(1.2)
    assert len(server.conns) == 1 and not ch.healthy


def test_restart_after_stop_works(server, make):
    ch = make()
    ch.start()
    assert wait_until(lambda: server.conns)
    ch.stop()
    ch.start()
    assert wait_until(lambda: len(server.conns) == 2)


def test_unreachable_server_keeps_trying_but_never_looks_healthy(qapp, make):
    ch = make(url="ws://127.0.0.1:9/v1/ws")             # nothing listens on port 9
    log = record(ch)
    delays = []
    ch._retry.start = lambda ms: delays.append(ms)
    ch.start()
    assert wait_until(lambda: len(delays) >= 1, timeout=5)
    assert not ch.healthy and log["health"] == []


def test_backoff_grows_is_capped_and_resets_after_a_good_connection(server, make, monkeypatch):
    ch = make()
    delays = []
    monkeypatch.setattr(ch._retry, "start", lambda ms: delays.append(ms), raising=False)
    for _ in range(9):
        ch._schedule_reconnect()
    assert delays[:4] == [750, 1500, 3000, 6000]                 # 2**n seconds x 0.75 jitter
    assert delays[-1] == int(lcm.BACKOFF_CAP_S * 1000 * 0.75)    # capped at a minute
    assert max(delays) == delays[-1]

    delays.clear()
    ch._attempt = 5
    ch._enabled = True
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    assert ch._attempt == 0                                      # a good connection resets the backoff


def test_jitter_is_applied(make, monkeypatch):
    lo = make(rng=lambda: 0.0)
    hi = make(rng=lambda: 1.0)
    d_lo, d_hi = [], []
    monkeypatch.setattr(lo._retry, "start", lambda ms: d_lo.append(ms), raising=False)
    monkeypatch.setattr(hi._retry, "start", lambda ms: d_hi.append(ms), raising=False)
    lo._schedule_reconnect()
    hi._schedule_reconnect()
    assert (d_lo[0], d_hi[0]) == (750, 1250)


@pytest.mark.parametrize("code,floor", [("replaced", 60), ("rate_limited", 60), ("server_busy", 30),
                                        ("auth_failed", 30), ("clock_skew", 30), ("replayed", 30),
                                        ("unknown_device", 30)])
def test_server_refusals_back_off_much_longer(make, monkeypatch, code, floor):
    ch = make()
    delays = []
    monkeypatch.setattr(ch._retry, "start", lambda ms: delays.append(ms), raising=False)
    ch._error_code = code
    ch._schedule_reconnect()
    assert delays[0] >= int(floor * 1000 * 0.75)


def test_an_ordinary_error_does_not_slow_the_retry(make, monkeypatch):
    ch = make()
    delays = []
    monkeypatch.setattr(ch._retry, "start", lambda ms: delays.append(ms), raising=False)
    ch._error_code = "idle_timeout"
    ch._schedule_reconnect()
    assert delays[0] == 750


def test_replaced_connection_does_not_reconnect_quickly(server, make, monkeypatch):
    """Two copies of the app with the same device must not keep knocking each other off."""
    ch = make()
    delays = []
    monkeypatch.setattr(ch._retry, "start", lambda ms: delays.append(ms), raising=False)
    ch.start()
    assert wait_until(lambda: server.conns)
    server.send({"t": "status", "status": "pending"})
    assert wait_until(lambda: ch.healthy)
    server.send({"t": "error", "code": "replaced"})
    server.drop()
    assert wait_until(lambda: delays)
    assert delays[0] >= 45_000                          # a minute-ish, not a second


def test_stale_socket_events_after_stop_are_ignored(server, make):
    ch = make()
    log = record(ch)
    ch.start()
    assert wait_until(lambda: server.conns)
    ch.stop()
    server.send({"t": "status", "status": "approved"})  # arrives from a socket we already discarded
    settle(0.3)
    assert log["frames"] == [] and not ch.healthy
