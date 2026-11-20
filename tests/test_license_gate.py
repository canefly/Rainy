"""Qt side of licensing: the gate screen, the background service, and the window locking itself."""
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.license_core import LicenseSnapshot, LicenseState
from src.core import license_service as ls
from src.ui.views.gate_view import GateView

S = LicenseState


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


def snap(state, **kw):
    return LicenseSnapshot(state, **kw)


# -- GateView ---------------------------------------------------------------------------------
def test_gate_registration_form(qapp):
    g = GateView()
    g.show_snapshot(snap(S.NEEDS_REGISTRATION, editor_name="Cane"))
    assert not g.input.isHidden() and g.input.text() == "Cane"
    assert not g.notice.isHidden() and "No GPS" in g.notice.text()
    assert g.spinner.isHidden() and g.primary.text() == "Request access"
    assert g.footer.isHidden()


def test_gate_registration_submit_emits_name(qapp):
    g = GateView()
    g.show_snapshot(snap(S.NEEDS_REGISTRATION))
    got = []
    g.register_requested.connect(got.append)
    g.input.setText("  Zac ")
    g.primary.click()
    assert got == ["  Zac "]  # trimming/validation is the controller's job, one source of truth


def test_gate_registration_error_message_is_shown_and_input_kept(qapp):
    g = GateView()
    g.show_snapshot(snap(S.NEEDS_REGISTRATION, editor_name="Cane"))
    g.input.setText("typed")
    g.show_snapshot(snap(S.NEEDS_REGISTRATION, editor_name="Cane", message="Can't reach the server."))
    assert not g.message.isHidden() and "Can't reach" in g.message.text()
    assert g.input.text() == "typed"  # never wipe what the user typed


def test_gate_pending_shows_request_id_and_retry(qapp):
    g = GateView()
    g.show_snapshot(snap(S.PENDING, editor_name="Cane", device_id="abcdef0123456789" * 2))
    assert g.input.isHidden() and not g.spinner.isHidden()
    assert "Cane" in g.body.text() and "approves" in g.body.text()
    assert "abcdef01" in g.footer.text() and not g.footer.isHidden()
    got = []
    g.retry_requested.connect(lambda: got.append(1))
    g.primary.click()
    assert got == [1]


def test_gate_checking_disables_actions(qapp):
    g = GateView()
    g.show_snapshot(snap(S.CHECKING, message="Sending your request..."))
    assert not g.primary.isEnabled() and not g.input.isEnabled()
    got = []
    g.register_requested.connect(got.append)
    g.retry_requested.connect(lambda: got.append("retry"))
    g._on_primary()
    assert got == []  # can't double-submit while a request is in flight


@pytest.mark.parametrize("state,title,button", [
    (S.REVOKED, "Access revoked", "Check again"),
    (S.EXPIRED, "License expired", "Retry"),
    (S.UNREACHABLE, "Can't reach the server", "Retry now"),
    (S.CLOCK_SKEW, "Your clock is off", "Retry"),
    (S.ERROR, "Something's wrong", "Retry"),
])
def test_gate_other_states(qapp, state, title, button):
    g = GateView()
    g.show_snapshot(snap(state, message="why", skew_seconds=3 * 3600))
    assert g.title.text() == title and g.primary.text() == button and g.input.isHidden()


def test_gate_clock_skew_wording(qapp):
    g = GateView()
    g.show_snapshot(snap(S.CLOCK_SKEW, skew_seconds=2 * 3600 + 120))
    assert "2h 2m behind" in g.body.text()
    g.show_snapshot(snap(S.CLOCK_SKEW, skew_seconds=-600))
    assert "10 min ahead of" in g.body.text()
    g.show_snapshot(snap(S.CLOCK_SKEW, skew_seconds=None))
    assert "doesn't match" in g.body.text()


def test_gate_theme_reapply_does_not_crash(qapp):
    g = GateView()
    g.show_snapshot(snap(S.REVOKED))
    g.apply_theme()


# -- LicenseService ---------------------------------------------------------------------------
class FakeController:
    def __init__(self, local, refreshes=(), register_result=None):
        self.local = local
        self.refreshes = list(refreshes)
        self.register_result = register_result
        self.calls = []
        self.boom = False

    def load_local(self):
        self.calls.append("local")
        return self.local

    def refresh(self):
        self.calls.append("refresh")
        if self.boom:
            raise RuntimeError("kaboom")
        return self.refreshes.pop(0) if self.refreshes else snap(S.UNREACHABLE)

    def register(self, name):
        self.calls.append(("register", name))
        return self.register_result or snap(S.PENDING, editor_name=name.strip(), device_id="d" * 32)


def make_service(ctl):
    return ls.LicenseService(controller=ctl)


def collect(service):
    seen = []
    service.state_changed.connect(seen.append)
    return seen


def test_service_start_cached_unlock_then_server_confirms(qapp):
    ctl = FakeController(snap(S.APPROVED, offline=True, seconds_left=1000), [snap(S.APPROVED, seconds_left=2000)])
    svc = make_service(ctl)
    seen = collect(svc)
    svc.start()
    assert seen[0].unlocked and seen[0].offline        # instantly unlocked from cache, no waiting
    assert wait_until(lambda: len(seen) == 2)
    assert seen[1].unlocked and not seen[1].offline
    assert svc.is_unlocked and svc.lock_reason() is None


def test_service_new_install_waits_for_the_user(qapp):
    ctl = FakeController(snap(S.NEEDS_REGISTRATION))
    svc = make_service(ctl)
    seen = collect(svc)
    svc.start()
    QCoreApplication.processEvents()
    assert [s.state for s in seen] == [S.NEEDS_REGISTRATION]
    assert ctl.calls == ["local"]                       # no network until they ask for access
    assert "locked" in svc.lock_reason().lower()


def test_service_register_flow_and_polling_schedule(qapp):
    ctl = FakeController(snap(S.NEEDS_REGISTRATION))
    svc = make_service(ctl)
    seen = collect(svc)
    svc.start()
    svc.register("  Cane ")
    assert seen[-1].state is S.CHECKING and seen[-1].editor_name == "Cane"   # immediate feedback
    assert wait_until(lambda: seen[-1].state is S.PENDING)
    assert ctl.calls[-1] == ("register", "  Cane ")
    assert svc._timer.isActive() and svc._timer.interval() == ls.PENDING_POLL_MS
    svc._timer.stop()


@pytest.mark.parametrize("state,interval", [
    (S.APPROVED, ls.APPROVED_POLL_MS), (S.REVOKED, ls.REVOKED_POLL_MS), (S.UNREACHABLE, ls.RETRY_POLL_MS),
    (S.EXPIRED, ls.RETRY_POLL_MS), (S.CLOCK_SKEW, ls.RETRY_POLL_MS),
])
def test_service_poll_intervals_by_state(qapp, state, interval):
    svc = make_service(FakeController(snap(S.CHECKING), [snap(state)]))
    svc.start()
    assert wait_until(lambda: svc.snapshot.state is state)
    assert svc._timer.isActive() and svc._timer.interval() == interval
    svc._timer.stop()


@pytest.mark.parametrize("state", [S.NEEDS_REGISTRATION, S.ERROR])
def test_service_does_not_poll_when_user_input_is_needed(qapp, state):
    svc = make_service(FakeController(snap(S.CHECKING), [snap(state)]))
    svc.start()
    assert wait_until(lambda: svc.snapshot.state is state)
    assert not svc._timer.isActive()


def test_service_worker_exception_becomes_error_screen(qapp):
    ctl = FakeController(snap(S.CHECKING))
    ctl.boom = True
    svc = make_service(ctl)
    svc.start()
    assert wait_until(lambda: svc.snapshot.state is S.ERROR)
    assert "kaboom" in svc.snapshot.message and not svc.is_unlocked


def test_service_check_now_does_not_flicker_when_unlocked(qapp):
    ctl = FakeController(snap(S.APPROVED, offline=True), [snap(S.APPROVED), snap(S.APPROVED)])
    svc = make_service(ctl)
    seen = collect(svc)
    svc.start()
    assert wait_until(lambda: ctl.calls.count("refresh") == 1 and not svc._busy)
    seen.clear()
    svc.check_now()
    assert wait_until(lambda: ctl.calls.count("refresh") == 2 and not svc._busy)
    assert all(s.state is S.APPROVED for s in seen)    # never dipped into a CHECKING screen
    svc._timer.stop()


def test_service_requests_during_a_job_are_queued_not_dropped(qapp):
    ctl = FakeController(snap(S.NEEDS_REGISTRATION), register_result=snap(S.PENDING))
    svc = make_service(ctl)
    svc.start()
    svc.register("one")
    svc.register("two")     # arrives while the first is running
    assert wait_until(lambda: ("register", "two") in ctl.calls and not svc._busy)
    assert ctl.calls.count(("register", "one")) + ctl.calls.count(("register", "two")) >= 1
    svc._timer.stop()


# -- IPC gate ---------------------------------------------------------------------------------
def test_ipc_server_refuses_calls_while_locked(qapp):
    from src.core.gui_ipc_server import GuiIpcServer
    calls = []
    srv = GuiIpcServer(handler_callback=lambda a, p: calls.append(a) or {"done": True})
    msg = json.dumps({"id": 1, "action": "get_workspace_status", "params": {}})

    assert srv._process_message(msg)["ok"] is True      # no gate set: unchanged behaviour
    srv.set_gate(lambda: "Rainy is locked")
    r = srv._process_message(msg)
    assert r == {"id": 1, "ok": False, "error": "Rainy is locked"}
    assert calls == ["get_workspace_status"]            # handler was NOT invoked the second time
    srv.set_gate(lambda: None)
    assert srv._process_message(msg)["ok"] is True


# -- MainWindow integration -------------------------------------------------------------------
@pytest.fixture
def locked_window(qapp, monkeypatch):
    """A real MainWindow with the gate enabled, wired to a fake controller (no network, no keyring)."""
    from src.ui import window as window_mod
    ctl = FakeController(snap(S.NEEDS_REGISTRATION), register_result=snap(S.PENDING, editor_name="Cane", device_id="d" * 32))
    svc = ls.LicenseService(controller=ctl)
    monkeypatch.setattr(ls.LicenseService, "_instance", svc)
    monkeypatch.setattr(window_mod, "license_gate_disabled", lambda: False)
    win = window_mod.MainWindow()
    yield win, svc, ctl
    svc._timer.stop()
    win.close()


def test_window_starts_locked_behind_the_gate(locked_window):
    win, svc, ctl = locked_window
    assert win.stack.currentWidget() is win.gate_view
    assert win.sidebar.isHidden() and not win.title_menu.isEnabled()
    assert all(not sc.isEnabled() for sc in win._shortcuts)
    assert ctl.calls[0] == "local"


def test_window_unlocks_when_approved_and_relocks_on_revoke(locked_window):
    win, svc, ctl = locked_window
    svc._emit(snap(S.APPROVED, editor_name="Cane", seconds_left=3600))
    assert win.stack.currentWidget() is win.home_view
    assert win.title_menu.isEnabled() and all(sc.isEnabled() for sc in win._shortcuts)

    svc._emit(snap(S.REVOKED, editor_name="Cane", device_id="d" * 32))
    assert win.stack.currentWidget() is win.gate_view
    assert not win.title_menu.isEnabled() and win.sidebar.isHidden()
    assert "revoked" in win.gate_view.title.text().lower()


def test_window_restores_workspace_after_a_temporary_lock(locked_window):
    win, svc, ctl = locked_window
    svc._emit(snap(S.APPROVED, editor_name="Cane"))
    win.open_workspace()
    assert win.stack.currentWidget() is win.workspace_view and not win.sidebar.isHidden()

    svc._emit(snap(S.UNREACHABLE))                       # lock mid-session
    assert win.stack.currentWidget() is win.gate_view and win.sidebar.isHidden()
    svc._emit(snap(S.APPROVED, editor_name="Cane"))      # server back and happy
    assert win.stack.currentWidget() is win.workspace_view and not win.sidebar.isHidden()


def test_window_gate_buttons_drive_the_service(locked_window):
    win, svc, ctl = locked_window
    win.gate_view.input.setText("Cane")
    win.gate_view.primary.click()
    assert wait_until(lambda: ("register", "Cane") in ctl.calls)
    assert wait_until(lambda: win.gate_view.title.text() == "Waiting for approval")
    win.gate_view.primary.click()                         # "Check now"
    assert wait_until(lambda: "refresh" in ctl.calls)


def test_window_blocks_mcp_while_locked(locked_window):
    win, svc, ctl = locked_window
    ipc = win.workspace_view.mcp_ipc_server
    msg = json.dumps({"id": 7, "action": "get_workspace_status", "params": {}})
    r = ipc._process_message(msg)
    assert r["ok"] is False and "locked" in r["error"].lower()
    svc._emit(snap(S.APPROVED, editor_name="Cane"))
    r = ipc._process_message(msg)
    # Unlocked: the call now reaches the real handler, which (with no script open) answers its own error.
    assert "No script document" in r["error"] and "locked" not in r["error"].lower()


def test_window_titlebar_shows_offline_time_left(locked_window):
    win, svc, ctl = locked_window
    svc._emit(snap(S.APPROVED, editor_name="Cane", offline=True, seconds_left=2 * 86400 + 3600))
    assert "2D 1H" in win.title_stats.user_name_lbl.text().upper()
    svc._emit(snap(S.APPROVED, editor_name="Cane", offline=False))
    assert "•" not in win.title_stats.user_name_lbl.text()


def test_locked_window_ignores_every_way_around_the_gate(locked_window, tmp_path):
    win, svc, ctl = locked_window
    f = tmp_path / "script.docx"
    f.write_bytes(b"not really a docx")
    for call in (win.open_workspace, win.go_home, win.open_settings, win.open_about, win.open_vault,
                 win._on_footage_clicked, win._on_menu_set_footage,
                 lambda: win.open_file(str(f)), lambda: win.load_docx_file(str(f))):
        call()
        QCoreApplication.processEvents()
        assert win.stack.currentWidget() is win.gate_view, call
    for attr in ("pref_window", "about_window", "vault_window"):
        w = getattr(win, attr, None)
        assert w is None or not w.isVisible(), attr
    assert win.current_doc is None


def test_background_work_finishing_while_locked_cannot_unlock_the_view(locked_window):
    """e.g. a script parse that completes after the license lapsed calls open_workspace()."""
    win, svc, ctl = locked_window
    win.open_workspace()
    assert win.stack.currentWidget() is win.gate_view and win.sidebar.isHidden()
    svc._emit(snap(S.APPROVED, editor_name="Cane"))     # unlocking lands where that work wanted to go
    assert win.stack.currentWidget() is win.workspace_view and not win.sidebar.isHidden()


def test_window_hides_open_tool_windows_when_locking(locked_window):
    win, svc, ctl = locked_window
    svc._emit(snap(S.APPROVED, editor_name="Cane"))
    fake = SimpleNamespace(hidden=False, hide=lambda: setattr(fake, "hidden", True), close=lambda: None)
    win.pref_window = fake
    svc._emit(snap(S.REVOKED))
    assert fake.hidden is True


def test_dev_switch_skips_the_gate_for_ui_tests(qapp):
    from src.ui.window import MainWindow
    win = MainWindow()                                    # conftest sets KNOT_SKIP_LICENSE=1
    assert win.stack.currentWidget() is win.home_view and win._license is None
    win.close()
