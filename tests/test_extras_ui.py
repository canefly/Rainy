"""The window side of announcements, the usage-sharing question, the bug report dialog and the Preferences card."""
import os
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import extras_service as es
from src.core import license_service as ls
from src.core.extras_core import Announcement
from src.core.extras_service import AnalyticsService, AnnouncementService, BugReportService
from src.core.license_core import LicenseSnapshot, LicenseState, MemorySettings
from src.ui.components.bug_report_dialog import BugReportDialog
from src.ui.components.feedback_card import SHARING_EXPLANATION, FeedbackCard
from src.ui.extras_controller import ExtrasController
from test_extras_service import Api, item, listing
from test_license_gate import FakeController, FakeLive
from test_update_core import NOW

S = LicenseState
ID1, ID2, ID3 = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


def pump(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def ann(id_=ID1, title="Maintenance tonight", body="Back at 9pm", severity="warning", created=NOW - 100):
    return Announcement(id_, title, body, severity, created, None)


# ------------------------------------------------------------------------------------------ fakes
class FakeBugService(QObject):
    finished = Signal(bool, str, str)

    def __init__(self):
        super().__init__()
        self.sending = False
        self.sent = []

    def send(self, title, description, include_log):
        self.sending = True
        self.sent.append((title, description, include_log))


class FakeAnnouncements(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.items = []
        self.refreshes = 0
        self.periodic = 0
        self.read = []

    def refresh(self):
        self.refreshes += 1

    def start_periodic(self):
        self.periodic += 1

    def mark_read(self, id_):
        self.read.append(id_)
        self.items = [a for a in self.items if a.id != id_]


class FakeAnalytics(QObject):
    enabled_changed = Signal(bool)

    def __init__(self, asked=False, enabled=False):
        super().__init__()
        self.asked, self.enabled = asked, enabled
        self.tracked, self.calls = [], []

    def set_enabled(self, on):
        self.calls.append(("set_enabled", on))
        self.asked, self.enabled = True, on
        self.enabled_changed.emit(on)

    def track(self, name, **props):
        self.tracked.append((name, props))

    def stop(self):
        self.calls.append("stop")

    def flush_on_exit(self):
        self.calls.append("flush_on_exit")


@pytest.fixture
def host(qapp):
    w = QWidget()
    w.resize(900, 600)
    w.show()
    yield w
    w.close()


def make_ctl(host, asked=False, enabled=False):
    items, analytics = FakeAnnouncements(), FakeAnalytics(asked, enabled)
    ctl = ExtrasController(host, items, analytics, "1.4.0")
    return ctl, items, analytics


def unlock(ctl, **kw):
    ctl.on_license_state(LicenseSnapshot(S.APPROVED, editor_name="Cane", **kw))
    ctl.set_gated(False)


# =============================================================================================
# the bug report dialog
# =============================================================================================
def make_dialog(host, preview="line1\nline2\nboom"):
    svc = FakeBugService()
    dlg = BugReportDialog(host, service=svc, log_provider=lambda: preview)
    dlg.show()
    pump(0.05)
    return dlg, svc


def test_sending_needs_a_title(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("   ")
    assert dlg.validate() is False
    assert svc.sent == [] and not dlg.status.isHidden() and "summary" in dlg.status.text()


def test_sending_hands_the_fields_to_the_service_and_locks_the_form(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("Export crashes")
    dlg.desc_edit.setPlainText("It closes when I press export")
    assert dlg.validate() is False                       # never closes by itself: the answer comes later
    assert svc.sent == [("Export crashes", "It closes when I press export", True)]
    assert not dlg.yesButton.isEnabled() and dlg.yesButton.text() == "Sending..."
    assert not dlg.title_edit.isEnabled() and not dlg.desc_edit.isEnabled() and not dlg.log_check.isEnabled()
    assert dlg.validate() is False and len(svc.sent) == 1    # a second click does nothing


def test_unticking_the_log_sends_without_it(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("t")
    dlg.log_check.setChecked(False)
    dlg.validate()
    assert svc.sent[0][2] is False


def test_success_closes_the_dialog(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("t")
    dlg.validate()
    svc.sending = False
    svc.finished.emit(True, "Thanks! Your report was sent.", "01234567")
    pump(0.05)
    assert not dlg.isVisible()


def test_failure_keeps_the_text_and_offers_another_try(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("Keep this")
    dlg.desc_edit.setPlainText("and this")
    dlg.validate()
    svc.sending = False
    svc.finished.emit(False, "Couldn't reach the server. Check your connection and try again.", "")
    assert dlg.isVisible() and dlg.yesButton.isEnabled() and dlg.yesButton.text() == "Try again"
    assert dlg.title_edit.isEnabled() and dlg.title_edit.text() == "Keep this" and dlg.desc_edit.toPlainText() == "and this"
    assert "reach the server" in dlg.status.text() and not dlg.status.isHidden()
    dlg.title_edit.setText("Keep this too")
    assert dlg.status.isHidden()                           # typing clears the old complaint
    dlg.validate()
    assert len(svc.sent) == 2


def test_the_preview_shows_exactly_what_the_provider_returns_and_toggles(host):
    dlg, svc = make_dialog(host, preview="alpha\nbeta\n~\\Videos\\clip.mp4")
    assert dlg.preview.isHidden()
    dlg.preview_btn.click()
    assert not dlg.preview.isHidden() and dlg.preview.toPlainText() == "alpha\nbeta\n~\\Videos\\clip.mp4"
    assert dlg.preview_btn.text() == "Hide"
    dlg.preview_btn.click()
    assert dlg.preview.isHidden() and "Show" in dlg.preview_btn.text()


def test_unticking_the_log_hides_the_preview_and_disables_its_button(host):
    dlg, svc = make_dialog(host)
    dlg.preview_btn.click()
    dlg.log_check.setChecked(False)
    assert dlg.preview.isHidden() and not dlg.preview_btn.isEnabled()
    dlg.log_check.setChecked(True)
    assert dlg.preview_btn.isEnabled()


def test_a_broken_log_reader_does_not_break_the_dialog(host):
    svc = FakeBugService()

    def boom():
        raise OSError("disk gone")

    dlg = BugReportDialog(host, service=svc, log_provider=boom)
    dlg.show()
    dlg.preview_btn.click()
    assert "couldn't read" in dlg.preview.toPlainText()


def test_an_empty_log_says_so(host):
    dlg, svc = make_dialog(host, preview="")
    dlg.preview_btn.click()
    assert "empty" in dlg.preview.toPlainText()


def test_the_dialog_says_what_else_is_sent_and_how_it_is_cleaned(host):
    dlg, svc = make_dialog(host)
    text = dlg.privacy.text()
    assert "editor name" in text and "user name" in text and "e-mail" in text and "removed" in text


def test_the_description_is_capped(host):
    dlg, svc = make_dialog(host)
    dlg.desc_edit.setPlainText("x" * 5000)
    assert len(dlg.desc_edit.toPlainText()) == 4000


def test_the_title_is_capped(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("t" * 300)
    assert len(dlg.title_edit.text()) == 120


def test_an_answer_that_arrives_after_closing_is_ignored(host):
    dlg, svc = make_dialog(host)
    dlg.title_edit.setText("t")
    dlg.validate()
    dlg.reject()
    pump(0.05)
    svc.finished.emit(True, "ok", "x")            # must not raise or pop anything up


# =============================================================================================
# the Preferences card
# =============================================================================================
def test_the_card_reflects_the_current_choice_and_explains_it(host):
    analytics = FakeAnalytics(asked=True, enabled=True)
    card = FeedbackCard(analytics, host)
    assert card.share_switch.isChecked()
    assert card.explain_lbl.text() == SHARING_EXPLANATION
    lower = SHARING_EXPLANATION.lower()
    for must in ("never sends", "scripts", "footage", "file", "ip address", "turn it off any time", "deletes what was sent"):
        assert must in lower, must


def test_toggling_the_switch_changes_the_setting(host):
    analytics = FakeAnalytics()
    card = FeedbackCard(analytics, host)
    assert not card.share_switch.isChecked()
    card.share_switch.setChecked(True)
    card.share_switch.setChecked(False)
    assert analytics.calls == [("set_enabled", True), ("set_enabled", False)]


def test_a_change_made_elsewhere_moves_the_switch_without_echoing(host):
    analytics = FakeAnalytics()
    card = FeedbackCard(analytics, host)
    analytics.enabled = True
    analytics.enabled_changed.emit(True)
    assert card.share_switch.isChecked() and analytics.calls == []


def test_the_report_button_calls_back(host):
    got = []
    card = FeedbackCard(FakeAnalytics(), host, on_report=lambda: got.append(1))
    card.report_btn.click()
    assert got == [1]


def test_the_real_card_uses_the_real_service(qapp, monkeypatch):
    svc = AnalyticsService(controller=Api(), store=MemorySettings(), can_send=lambda: True)
    monkeypatch.setattr(AnalyticsService, "_instance", svc)
    card = FeedbackCard()
    card.share_switch.setChecked(True)
    assert svc.enabled and svc.asked
    card.share_switch.setChecked(False)
    assert not svc.enabled


# =============================================================================================
# announcements in the window
# =============================================================================================
def titles(ctl):
    return sorted(bar.titleLabel.text() for bar in ctl._bars.values())


def test_nothing_shows_while_locked_and_notices_appear_once_unlocked(host):
    ctl, items, analytics = make_ctl(host, asked=True)
    items.items = [ann(ID1, "First")]
    items.changed.emit(items.items)
    assert ctl._bars == {}                                  # locked: nothing
    ctl.on_license_state(LicenseSnapshot(S.APPROVED))
    assert ctl._bars == {}                                  # still gated by the window
    ctl.set_gated(False)
    assert titles(ctl) == ["First"] and items.refreshes == 1 and items.periodic == 1


def test_a_notice_is_plain_text_even_when_it_looks_like_html(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "<b>Bold?</b>", "<img src=x onerror=alert(1)> & more")]
    items.changed.emit(items.items)
    (bar,) = ctl._bars.values()
    assert bar.titleLabel.textFormat() == Qt.PlainText and bar.contentLabel.textFormat() == Qt.PlainText
    assert bar.titleLabel.text() == "<b>Bold?</b>" and bar.contentLabel.text() == "<img src=x onerror=alert(1)> & more"


def test_a_notice_without_a_body_hides_the_empty_line(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "Just a title", "")]
    items.changed.emit(items.items)
    (bar,) = ctl._bars.values()
    assert bar.contentLabel.isHidden()


def test_closing_a_notice_marks_it_read_once(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "A"), ann(ID2, "B", created=NOW - 5)]
    items.changed.emit(items.items)
    bar = ctl._bars[ID1]
    bar.close()
    pump(0.05)
    assert items.read == [ID1] and ID1 not in ctl._bars and ID2 in ctl._bars


def test_updates_to_the_list_add_and_remove_notices_without_marking_anything_read(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "A"), ann(ID2, "B")]
    items.changed.emit(items.items)
    assert titles(ctl) == ["A", "B"]
    items.changed.emit([ann(ID2, "B"), ann(ID3, "C")])        # A ended on the server, C is new
    assert titles(ctl) == ["B", "C"]
    items.changed.emit([])
    assert ctl._bars == {} and items.read == []              # ending a notice is not the same as reading it


def test_the_same_notice_is_not_shown_twice(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "A")]
    for _ in range(3):
        items.changed.emit(items.items)
    assert len(ctl._bars) == 1


def test_locking_hides_notices_without_reading_them_and_unlocking_brings_them_back(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "A")]
    items.changed.emit(items.items)
    assert titles(ctl) == ["A"]
    ctl.set_gated(True)
    pump(0.05)
    assert ctl._bars == {} and items.read == []
    ctl.set_gated(False)
    assert titles(ctl) == ["A"]


def test_a_revoked_editor_sees_no_notices(host):
    ctl, items, _ = make_ctl(host, asked=True)
    unlock(ctl)
    items.items = [ann(ID1, "A")]
    items.changed.emit(items.items)
    ctl.on_license_state(LicenseSnapshot(S.REVOKED))
    assert ctl._bars == {}


def test_each_unlock_refreshes_but_the_session_start_is_counted_once(host):
    ctl, items, analytics = make_ctl(host, asked=True, enabled=True)
    unlock(ctl)
    ctl.on_license_state(LicenseSnapshot(S.REVOKED))
    ctl.on_license_state(LicenseSnapshot(S.APPROVED))
    assert items.refreshes == 2
    assert analytics.tracked == [("app_start", {"version": "1.4.0"})]


# =============================================================================================
# the one-time question
# =============================================================================================
def test_the_question_is_asked_once_unlocked_and_answering_yes_opts_in(host):
    ctl, items, analytics = make_ctl(host)
    unlock(ctl)
    assert ctl._ask_timer.isActive() and ctl._ask_bar is None
    ctl._ask_timer.stop()
    ctl._ask_about_sharing()
    assert ctl._ask_bar is not None and "Help improve" in ctl._ask_bar.titleLabel.text()
    ctl._ask_about_sharing()                                  # not stacked
    assert ctl._ask_bar is not None
    buttons = {b.text(): b for b in ctl._ask_bar.findChildren(QPushButton)}
    assert {"Share anonymously", "No thanks"} <= set(buttons)
    buttons["Share anonymously"].click()
    assert analytics.calls == [("set_enabled", True)] and ctl._ask_bar is None
    assert ("app_start", {"version": "1.4.0"}) in analytics.tracked


def test_answering_no_opts_out_and_closing_the_notice_counts_as_no(host):
    ctl, items, analytics = make_ctl(host)
    unlock(ctl)
    ctl._ask_timer.stop()
    ctl._ask_about_sharing()
    ctl._answer(False)
    assert analytics.calls == [("set_enabled", False)] and ctl._ask_bar is None

    ctl2, _, analytics2 = make_ctl(host)
    unlock(ctl2)
    ctl2._ask_timer.stop()
    ctl2._ask_about_sharing()
    ctl2._ask_bar.close()                                    # the X
    pump(0.05)
    assert analytics2.calls == [("set_enabled", False)] and analytics2.asked


def test_the_question_is_never_asked_twice(host):
    ctl, items, analytics = make_ctl(host, asked=True)
    unlock(ctl)
    assert not ctl._ask_timer.isActive()
    ctl._ask_about_sharing()
    assert ctl._ask_bar is None


def test_locking_while_the_question_is_up_withdraws_it_without_answering(host):
    ctl, items, analytics = make_ctl(host)
    unlock(ctl)
    ctl._ask_timer.stop()
    ctl._ask_about_sharing()
    ctl.set_gated(True)
    pump(0.05)
    assert ctl._ask_bar is None and analytics.calls == [] and not analytics.asked
    ctl.set_gated(False)
    assert ctl._ask_timer.isActive()                          # it will ask again later


def test_the_question_waits_while_gated(host):
    ctl, items, analytics = make_ctl(host)
    ctl.on_license_state(LicenseSnapshot(S.APPROVED))
    ctl._ask_about_sharing()
    assert ctl._ask_bar is None


def test_shutdown_stops_the_crash_hook_and_sends_what_is_waiting(host):
    ctl, items, analytics = make_ctl(host, asked=True, enabled=True)
    ctl.shutdown()
    assert analytics.calls == ["stop", "flush_on_exit"]


def test_a_failing_shutdown_never_blocks_closing(host):
    ctl, items, analytics = make_ctl(host)
    analytics.stop = lambda: (_ for _ in ()).throw(RuntimeError("nope"))
    ctl.shutdown()


# =============================================================================================
# the real window
# =============================================================================================
@pytest.fixture
def real_window(qapp, tmp_path, monkeypatch):
    """A real MainWindow with the license gate ON and fake services behind it."""
    from src.core.updater import UpdateService
    from src.ui import window as window_mod
    from test_update_service import make as make_update_service

    upd = make_update_service(tmp_path)
    monkeypatch.setattr(UpdateService, "_instance", upd)
    api = Api()
    api.replies[("GET", "/v1/announcements")] = listing(item(ID1, title="Server maintenance tonight", body="9pm"))
    store = MemorySettings()
    lic = ls.LicenseService(controller=FakeController(LicenseSnapshot(S.NEEDS_REGISTRATION)), live=FakeLive())
    announcements = AnnouncementService(controller=api, clock=lambda: NOW)
    analytics = AnalyticsService(controller=api, store=store, can_send=lambda: lic.is_unlocked)
    monkeypatch.setattr(ls.LicenseService, "_instance", lic)
    monkeypatch.setattr(AnnouncementService, "_instance", announcements)
    monkeypatch.setattr(AnalyticsService, "_instance", analytics)
    monkeypatch.setattr(window_mod, "license_gate_disabled", lambda: False)
    win = window_mod.MainWindow()
    yield win, lic, announcements, analytics, api, store
    for t in (lic._timer, upd._timer, announcements._timer, analytics._timer):
        t.stop()
    win.close()


def test_the_window_shows_a_notice_after_approval_and_marks_it_read_when_closed(real_window):
    win, lic, announcements, analytics, api, store = real_window
    assert win._extras is not None and win._extras._bars == {}
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    assert wait_until(lambda: len(win._extras._bars) == 1)
    (bar,) = win._extras._bars.values()
    assert bar.titleLabel.text() == "Server maintenance tonight"
    bar.close()
    assert wait_until(lambda: ("POST", f"/v1/announcements/{ID1}/read") in api.paths())


def test_the_live_nudge_makes_the_window_fetch_the_list(real_window):
    win, lic, announcements, analytics, api, store = real_window
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    assert wait_until(lambda: len(win._extras._bars) == 1)
    before = len([c for c in api.calls if c[0] == "GET"])
    lic.announcements_nudged.emit()
    assert wait_until(lambda: len([c for c in api.calls if c[0] == "GET"]) == before + 1)


def test_a_locked_window_shows_no_notices_and_does_not_fetch(real_window):
    win, lic, announcements, analytics, api, store = real_window
    pump(0.1)
    assert win._extras._bars == {} and [c for c in api.calls if c[0] == "GET"] == []


def test_help_menu_report_a_problem_opens_the_dialog(real_window, monkeypatch):
    win, *_ = real_window
    opened = []
    monkeypatch.setattr(BugReportDialog, "exec", lambda self: opened.append(self) or 0)
    win.title_menu.report_problem_requested.emit()
    assert len(opened) == 1 and isinstance(opened[0], BugReportDialog)


def test_the_window_asks_about_sharing_once_after_approval(real_window):
    win, lic, announcements, analytics, api, store = real_window
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    assert win._extras._ask_timer.isActive()
    win._extras._ask_timer.stop()
    win._extras._ask_about_sharing()
    win._extras._answer(False)
    assert analytics.asked and not analytics.enabled
    lic._emit(LicenseSnapshot(S.REVOKED))
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    assert not win._extras._ask_timer.isActive()


def test_nothing_is_sent_until_the_editor_opts_in(real_window):
    win, lic, announcements, analytics, api, store = real_window
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    pump(0.2)
    analytics.flush()
    pump(0.1)
    assert [c for c in api.calls if c[1] == "/v1/analytics"] == [] and len(analytics.queue) == 0


def test_the_window_without_a_license_still_has_the_report_menu(qapp, monkeypatch):
    """Dev builds skip the license gate; Report a Problem must still be wired (it will say the install isn't registered)."""
    from src.ui import window as window_mod
    win = window_mod.MainWindow()
    try:
        opened = []
        monkeypatch.setattr(BugReportDialog, "exec", lambda self: opened.append(self) or 0)
        win.title_menu.report_problem_requested.emit()
        assert len(opened) == 1
    finally:
        win.close()


def test_closing_the_window_stops_the_crash_hook_and_the_timers(real_window):
    win, lic, announcements, analytics, api, store = real_window
    assert sys.excepthook.__name__ == "hook" and analytics._timer.isActive()         # the window started them
    analytics.set_enabled(True)
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    analytics.track("app_close")
    win.close()
    assert not analytics._timer.isActive() and sys.excepthook.__name__ != "hook"
    assert wait_until(lambda: any(c[1] == "/v1/analytics" for c in api.calls))        # waiting events were sent on the way out


def test_a_revoked_window_hides_notices_that_were_showing(real_window):
    win, lic, announcements, analytics, api, store = real_window
    lic._emit(LicenseSnapshot(S.APPROVED, editor_name="Cane"))
    assert wait_until(lambda: len(win._extras._bars) == 1)
    lic._emit(LicenseSnapshot(S.REVOKED))
    pump(0.05)
    assert win._extras._bars == {}
    assert ("POST", f"/v1/announcements/{ID1}/read") not in api.paths()           # hiding is not reading
