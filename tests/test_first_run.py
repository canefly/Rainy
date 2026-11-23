"""The first-run window: one calm place for "getting Rainy ready" instead of a pile of corner notices."""
import sys
import time

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from qfluentwidgets import InfoBar

from src.core import engine_setup as es
from src.core.license_core import LicenseSnapshot, LicenseState
from src.ui.components import first_run_dialog as frd
from src.ui.components.first_run_dialog import FirstRunDialog
from src.ui.extras_controller import ExtrasController
from src.ui.first_run_controller import FirstRunController
from test_engine_setup import FakeWorker, make
from test_extras_ui import ann, make_ctl, pump, unlock


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


class FakeCensor(QObject):
    """Stands in for the photo-censor service: the model download is optional and reports through two signals."""
    model_download_progress = Signal(int, str)
    model_download_finished = Signal(str, str)

    def __init__(self, due=True):
        super().__init__()
        self.due = due
        self.started = 0
        self.cancelled = 0

    def auto_download_pending(self):
        return self.due

    def maybe_auto_download(self):
        if not self.due:
            return False
        self.started += 1
        return True

    def cancel_download(self):
        self.cancelled += 1
        self.model_download_finished.emit("cancelled", "Download cancelled.")


class Snap:
    def __init__(self, unlocked):
        self.unlocked = unlocked


@pytest.fixture
def setup(qapp):
    FakeWorker.made = []
    host = QWidget()
    host.resize(900, 600)
    host.show()
    made = []

    def build(missing=("libmpv", "ffmpeg"), censor_due=True):
        svc, need, _ = make(list(missing))
        censor = FakeCensor(censor_due)
        ctl = FirstRunController(host, svc, censor)
        quiet = []
        ctl.quiet_changed.connect(quiet.append)
        made.append(ctl)
        return ctl, svc, censor, quiet, need

    yield build
    for c in made:
        c.shutdown()
    host.close()


def unlock_it(ctl):
    ctl.on_license_state(Snap(True))
    pump(0.02)


def rows(ctl):
    d = ctl._dialog
    return {k: (d.row_state(k), d.row_text(k)) for k in d.row_keys()}


# -- before approval / nothing to do ---------------------------------------------------------------------
def test_nothing_happens_until_the_install_is_approved_and_then_only_once(setup):
    ctl, svc, censor, quiet, _ = setup()
    ctl.on_license_state(Snap(False))
    assert ctl._dialog is None and FakeWorker.made == [] and censor.started == 0 and quiet == []
    unlock_it(ctl)
    unlock_it(ctl)
    assert ctl._dialog is not None and len(FakeWorker.made) == 1


def test_nothing_to_fetch_means_no_window_and_no_held_notices(setup):
    ctl, svc, censor, quiet, _ = setup(missing=[], censor_due=False)
    unlock_it(ctl)
    assert ctl._dialog is None and quiet == [] and FakeWorker.made == []


# -- the happy path -------------------------------------------------------------------------------------------
def test_the_window_lists_every_step_and_walks_through_them(setup):
    ctl, svc, censor, quiet, _ = setup()
    unlock_it(ctl)
    d = ctl._dialog
    assert d.isVisible() and quiet == [True]
    assert [d._labels[k].text() for k in d.row_keys()] == ["Video player", "Video tools", "Automatic photo censoring (optional)"]
    assert rows(ctl)["libmpv"] == (frd.RUNNING, "Downloading 0%") and rows(ctl)["ffmpeg"][0] == frd.WAITING
    FakeWorker.made[0].report(60)
    assert rows(ctl)["libmpv"] == (frd.RUNNING, "Downloading 60%") and d.bar.value() == 60
    FakeWorker.made[0].done(True)
    assert rows(ctl)["libmpv"][0] == frd.DONE and rows(ctl)["ffmpeg"][0] == frd.RUNNING and d.bar.value() == 0
    FakeWorker.made[1].report(100)
    FakeWorker.made[1].done(True)
    # the optional model comes last, in the same window
    assert censor.started == 1 and ctl.owns_censor_download is True
    assert rows(ctl)["censor"][0] == frd.RUNNING and d.skip_btn.isVisible() is True
    censor.model_download_progress.emit(35, "54 MB of 156 MB")
    assert rows(ctl)["censor"] == (frd.RUNNING, "Downloading 35%") and d.bar.value() == 35
    censor.model_download_finished.emit("ok", "ready")
    assert ctl.owns_censor_download is True                       # still "owned" while the other listeners are told
    assert rows(ctl)["censor"] == (frd.DONE, "Done")
    assert d.title.text() == "Rainy is ready" and d.close_btn.isVisible() and quiet == [True, False]
    pump(0.05)
    assert ctl.owns_censor_download is False                    # released only after every listener saw the signal


def test_the_window_says_why_and_that_the_app_stays_usable(setup):
    ctl, *_ = setup()
    unlock_it(ctl)
    texts = " ".join(lbl.text() for lbl in ctl._dialog.findChildren(QLabel))
    assert "helper programs" in texts and "official sources" in texts and "checked" in texts
    assert "keep using Rainy" in texts
    assert ctl._dialog.isModal() is False                         # it never blocks the app


def test_it_closes_itself_a_few_seconds_after_everything_is_ready(setup):
    ctl, svc, censor, quiet, _ = setup(censor_due=False)
    unlock_it(ctl)
    FakeWorker.made[0].done(True)
    FakeWorker.made[1].done(True)
    d = ctl._dialog
    assert d.isVisible() and d.title.text() == "Rainy is ready"
    ctl._auto_close()                                             # what the timer does after AUTO_CLOSE_MS
    assert not d.isVisible()


def test_the_optional_model_is_not_started_if_it_is_no_longer_wanted_by_the_time_the_engines_finish(setup):
    ctl, svc, censor, quiet, _ = setup()
    unlock_it(ctl)
    censor.due = False                                              # e.g. the person turned automatic censoring off meanwhile
    FakeWorker.made[0].done(True)
    FakeWorker.made[1].done(True)
    assert censor.started == 0 and rows(ctl)["censor"][0] == frd.SKIPPED
    assert ctl._dialog.title.text() == "Rainy is ready"


def test_with_the_engines_already_there_only_the_optional_model_is_shown(setup):
    ctl, svc, censor, quiet, _ = setup(missing=[], censor_due=True)
    unlock_it(ctl)
    assert ctl._dialog.row_keys() == ["censor"] and FakeWorker.made == [] and censor.started == 1


# -- the optional step is allowed to fail or be skipped ----------------------------------------------------
def test_skipping_the_photo_model_is_remembered_and_still_ends_in_ready(setup):
    ctl, svc, censor, quiet, _ = setup(missing=[], censor_due=True)
    unlock_it(ctl)
    ctl._dialog.skip_btn.click()
    assert censor.cancelled == 1
    assert rows(ctl)["censor"] == (frd.SKIPPED, "Skipped")
    assert ctl._dialog.title.text() == "Rainy is ready" and "Settings > AI Models" in ctl._dialog.message.text()


def test_a_failed_photo_model_download_is_calm_not_a_failure_of_the_whole_setup(setup):
    ctl, svc, censor, quiet, _ = setup(missing=[], censor_due=True)
    unlock_it(ctl)
    censor.model_download_finished.emit("failed", "couldn't reach huggingface")
    assert rows(ctl)["censor"] == (frd.SKIPPED, "Not downloaded")
    d = ctl._dialog
    assert d.title.text() == "Rainy is ready" and not d.retry_btn.isVisible()
    assert "was not installed" in d.message.text() and "Settings > AI Models" in d.message.text()


# -- failures of the required step ----------------------------------------------------------------------------
def test_a_failed_engine_brings_the_window_back_with_the_reason_and_try_again(setup):
    ctl, svc, censor, quiet, need = setup()
    unlock_it(ctl)
    ctl._dialog.hide_btn.click()                                  # the person hid it...
    assert not ctl._dialog.isVisible()
    FakeWorker.made[0].done(False, "Couldn't verify the download site's security certificate. <b>x</b>")
    d = ctl._dialog
    assert d.isVisible()                                           # ...but a failure must be seen
    assert rows(ctl)["libmpv"] == (frd.FAILED, "Failed")
    assert "security certificate" in d.message.text() and "<b>x</b>" in d.message.text()
    assert d.message.textFormat() == Qt.PlainText                  # never rich text
    assert d.retry_btn.isVisible() and quiet == [True, False]       # other notices come back so the person is not left blind
    assert censor.started == 0                                      # the optional step waits for the required ones
    need[:] = ["libmpv", "ffmpeg"]
    d.retry_btn.click()
    assert quiet == [True, False, True] and svc.running and d.title.text() == "Getting Rainy ready"


def test_cancelling_offers_resume(setup):
    ctl, svc, censor, quiet, _ = setup()
    unlock_it(ctl)
    ctl._dialog.cancel_btn.click()
    FakeWorker.made[0].done(False, "Download cancelled by user.")
    d = ctl._dialog
    assert d.retry_btn.text() == "Resume" and "won't work" in d.message.text()


# -- hiding and the lock screen ----------------------------------------------------------------------------------
def test_hiding_does_not_stop_the_work_and_finishing_says_so_once(setup, qapp):
    ctl, svc, censor, quiet, _ = setup(censor_due=False)
    unlock_it(ctl)
    ctl._dialog.hide_btn.click()
    assert not ctl._dialog.isVisible() and svc.running
    FakeWorker.made[0].done(True)
    FakeWorker.made[1].done(True)
    pump(0.05)
    assert not ctl._dialog.isVisible()                              # stays out of the way
    ready = [b for b in ctl.window.findChildren(InfoBar) if b.title == "Rainy is ready"]
    assert len(ready) == 1                                          # one quiet note instead of a window


def test_closing_with_the_x_only_hides_it(setup):
    ctl, *_ = setup()
    unlock_it(ctl)
    ctl._dialog.close()
    assert not ctl._dialog.isVisible() and ctl._hidden_by_user and ctl.service.running


def test_the_lock_screen_hides_the_window_and_it_returns_after(setup):
    ctl, svc, censor, quiet, _ = setup()
    unlock_it(ctl)
    ctl.set_gated(True)
    assert not ctl._dialog.isVisible()
    ctl.set_gated(False)
    assert ctl._dialog.isVisible()
    ctl._dialog.hide_btn.click()
    ctl.set_gated(True)
    ctl.set_gated(False)
    assert not ctl._dialog.isVisible()                              # what the person hid stays hidden


# -- holding the other notices ---------------------------------------------------------------------------------------
def test_other_notices_are_held_while_the_window_is_busy_and_come_back_after(qapp):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ctl, items, analytics = make_ctl(host)
    unlock(ctl)
    items.items = [ann()]
    items.changed.emit(items.items)
    pump(0.05)
    assert len(ctl._bars) == 1
    ctl.set_quiet(True)
    pump(0.05)
    assert ctl._bars == {} and ctl._ask_bar is None and not ctl._ask_timer.isActive()
    assert items.read == []                                          # held is not "read"
    ctl.set_quiet(False)
    pump(0.05)
    assert len(ctl._bars) == 1 and ctl._ask_timer.isActive()        # the announcement and the question return
    host.close()


def test_the_sharing_question_will_not_open_while_held_even_if_asked_directly(qapp):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ctl, items, analytics = make_ctl(host)
    unlock(ctl)
    ctl.set_quiet(True)
    ctl._ask_about_sharing()                                        # what its timer would do
    assert ctl._ask_bar is None
    ctl.set_quiet(False)
    ctl._ask_about_sharing()
    assert ctl._ask_bar is not None
    ctl._close_ask_bar()
    host.close()


def test_a_notice_that_arrives_while_held_waits_for_the_release(qapp):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ctl, items, analytics = make_ctl(host)
    ctl.set_quiet(True)
    unlock(ctl)
    items.items = [ann()]
    items.changed.emit(items.items)
    pump(0.05)
    assert ctl._bars == {} and not ctl._ask_timer.isActive()
    ctl.set_quiet(False)
    pump(0.05)
    assert len(ctl._bars) == 1
    host.close()


# -- the main window's own censor notices ------------------------------------------------------------------------
def test_the_main_window_stays_quiet_about_a_download_the_first_run_window_shows(qapp, monkeypatch):
    from src.core import photo_censor as pc
    from src.ui.window import MainWindow
    win = MainWindow()
    try:
        class Owner:
            owns_censor_download = True

            def shutdown(self): pass

            def set_gated(self, gated): pass

            def on_license_state(self, snap): pass
        win._engine_setup = Owner()
        monkeypatch.setattr(pc.photo_censor, "is_downloading", lambda: True)        # a download is under way
        before = len(win.findChildren(InfoBar))
        win._sync_censor_download_notice()
        win._on_censor_download_finished(pc.DOWNLOAD_FAILED, "no network")
        assert win._censor_dl_bar is None and len(win.findChildren(InfoBar)) == before    # the window already says it
        Owner.owns_censor_download = False
        win._sync_censor_download_notice()
        assert win._censor_dl_bar is not None                       # an ordinary download (from Settings) still gets its notice
        win._on_censor_download_finished(pc.DOWNLOAD_FAILED, "no network")
        assert win._censor_dl_bar is None and len(win.findChildren(InfoBar)) > before     # ...and reports how it ended
    finally:
        win.close()


def test_an_installed_build_does_not_start_the_photo_model_download_before_approval():
    """The 4-second timer in the main window only exists for builds without the license gate."""
    import inspect
    from src.ui import window
    src = inspect.getsource(window.MainWindow)
    guarded = ("        if license_gate_disabled():     # installed builds start it after approval, inside the first-run window\n"
               "            QTimer.singleShot(4000, photo_censor.maybe_auto_download)")
    assert guarded in src                                           # the timer is only ever inside that condition
    assert src.count("photo_censor.maybe_auto_download") == 1       # and there is no other automatic start


# -- when the photo model is "due" ---------------------------------------------------------------------------------
def test_the_photo_model_is_due_only_when_nothing_says_otherwise(monkeypatch):
    from src.core import photo_censor as pc
    from src.core.database import db
    svc = pc.photo_censor
    monkeypatch.delenv("KNOT_NO_AUTO_DOWNLOAD", raising=False)
    monkeypatch.setattr(pc, "model_ready", lambda: False)
    monkeypatch.setattr(svc, "auto_enabled", lambda: True)
    monkeypatch.setattr(svc, "_downloading", False)
    db.set_setting("photo_censor_autodownload", pc.AUTODL_PENDING)
    assert svc.auto_download_pending() is True
    monkeypatch.setattr(svc, "_downloading", True)                  # one is already running
    assert svc.auto_download_pending() is False
    monkeypatch.setattr(svc, "_downloading", False)
    monkeypatch.setattr(pc, "model_ready", lambda: True)            # already installed
    assert svc.auto_download_pending() is False
    monkeypatch.setattr(pc, "model_ready", lambda: False)
    monkeypatch.setattr(svc, "auto_enabled", lambda: False)         # turned off in Settings
    assert svc.auto_download_pending() is False
    monkeypatch.setattr(svc, "auto_enabled", lambda: True)
    db.set_setting("photo_censor_autodownload", pc.AUTODL_DECLINED)  # skipped or removed before
    assert svc.auto_download_pending() is False
    db.set_setting("photo_censor_autodownload", pc.AUTODL_PENDING)
    monkeypatch.setenv("KNOT_NO_AUTO_DOWNLOAD", "1")                # the developer/test switch
    assert svc.auto_download_pending() is False


# -- the window itself ----------------------------------------------------------------------------------------------
def test_the_window_shows_the_right_buttons_for_each_phase(qapp):
    d = FirstRunDialog(None, [("libmpv", "Video player")])
    d.show_running()
    assert (d.cancel_btn.isHidden(), d.hide_btn.isHidden(), d.retry_btn.isHidden(), d.close_btn.isHidden(), d.skip_btn.isHidden()) == (False, False, True, True, True)
    d.show_running(can_skip=True)
    assert not d.skip_btn.isHidden()
    d.show_failed("nope")
    assert (d.retry_btn.isHidden(), d.hide_btn.isHidden(), d.cancel_btn.isHidden(), d.close_btn.isHidden()) == (False, False, True, True)
    d.show_done()
    assert d.close_btn.isHidden() is False and d.retry_btn.isHidden() and d.bar.value() == 100
    d.deleteLater()


def test_unknown_rows_and_wild_progress_values_are_ignored_safely(qapp):
    d = FirstRunDialog(None, [("libmpv", "Video player")])
    d.set_row("nothing", frd.DONE, "x")                              # not a row: no crash
    d.set_progress(-20)
    assert d.bar.value() == 0
    d.set_progress(900)
    assert d.bar.value() == 100
    d.deleteLater()
