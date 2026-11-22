"""First-run engine setup: what gets downloaded, in what order, what happens when it fails, and how it looks."""
import sys

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.core import engine_setup as es
from src.ui.engine_setup_controller import EngineSetupController


class FakeWorker(QObject):
    progress = Signal(str, int, str)
    finished = Signal(str, bool, str)
    made = []

    def __init__(self, engine_id):
        super().__init__()
        self.engine_id = engine_id
        self.started = self.cancelled = self.waited = False
        FakeWorker.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        self.waited = True

    # test helpers
    def report(self, pct, text=""):
        self.progress.emit(self.engine_id, pct, text)

    def done(self, ok=True, msg=""):
        self.finished.emit(self.engine_id, ok, msg)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def fresh_workers():
    FakeWorker.made = []
    yield


def make(missing, **kw):
    need = list(missing)
    svc = es.EngineSetupService(missing=lambda: list(need), worker_factory=FakeWorker, **kw)
    states = []
    svc.state_changed.connect(states.append)
    return svc, need, states


# -- the service -------------------------------------------------------------------------------------
def test_nothing_missing_means_nothing_to_do(qapp):
    svc, _, states = make([])
    assert svc.start() is False and FakeWorker.made == [] and states == []
    assert svc.state.phase == es.DONE


def test_it_downloads_one_engine_at_a_time_and_reports_overall_progress(qapp):
    svc, _, states = make(["libmpv", "ffmpeg"])
    assert svc.start() is True
    assert [w.engine_id for w in FakeWorker.made] == ["libmpv"] and FakeWorker.made[0].started
    assert svc.state.step == 1 and svc.state.total == 2 and "1 of 2" in svc.state.message and "video player" in svc.state.message
    FakeWorker.made[0].report(50, "Downloading: 15.0 MB / 30.0 MB (50%)")
    assert svc.state.percent == 25 and svc.state.message.startswith("Downloading: 15.0 MB")
    FakeWorker.made[0].done(True, "installed")
    assert [w.engine_id for w in FakeWorker.made] == ["libmpv", "ffmpeg"]
    assert svc.state.engine == "ffmpeg" and svc.state.percent == 50 and "2 of 2" in svc.state.message
    FakeWorker.made[1].report(100)
    assert svc.state.percent == 100 and svc.state.phase == es.RUNNING
    FakeWorker.made[1].done(True, "installed")
    assert svc.state.phase == es.DONE and not svc.running
    percents = [s.percent for s in states]
    assert percents == sorted(percents)                         # never goes backwards


def test_a_stray_lower_progress_report_never_moves_the_bar_backwards(qapp):
    svc, _, _ = make(["libmpv"])
    svc.start()
    FakeWorker.made[0].report(60)
    FakeWorker.made[0].report(30)                                # out of order or a retry inside the download
    FakeWorker.made[0].report(-5)
    FakeWorker.made[0].report(250)                               # nonsense is clamped, not trusted
    assert svc.state.percent == 100
    svc2, _, _ = make(["libmpv"])
    svc2.start()
    FakeWorker.made[-1].report(60)
    FakeWorker.made[-1].report(30)
    assert svc2.state.percent == 60


def test_a_single_engine_does_not_say_one_of_one(qapp):
    svc, _, _ = make(["ffmpeg"])
    svc.start()
    assert "of" not in svc.state.message and "video tools" in svc.state.message


def test_a_failure_stops_the_queue_keeps_the_message_and_retry_starts_again(qapp):
    svc, need, _ = make(["libmpv", "ffmpeg"])
    svc.start()
    FakeWorker.made[0].done(False, "Can't reach the server to get the verified download list.")
    assert svc.state.phase == es.FAILED and svc.state.engine == "libmpv"
    assert "reach the server" in svc.state.message
    assert len(FakeWorker.made) == 1                            # ffmpeg was not attempted
    need[:] = ["ffmpeg"]                                        # libmpv turned up meanwhile
    assert svc.retry() is True
    assert [w.engine_id for w in FakeWorker.made] == ["libmpv", "ffmpeg"] and svc.state.phase == es.RUNNING


def test_cancelling_asks_the_worker_to_stop_and_ends_as_cancelled(qapp):
    svc, _, _ = make(["libmpv"])
    svc.start()
    svc.cancel()
    assert FakeWorker.made[0].cancelled and svc.running          # not over until the worker says so
    FakeWorker.made[0].done(False, "Download cancelled by user.")
    assert svc.state.phase == es.CANCELLED and "cancelled" in svc.state.message.lower()


def test_starting_twice_does_not_start_two_downloads(qapp):
    svc, _, _ = make(["libmpv"])
    assert svc.start() is True and svc.start() is False and len(FakeWorker.made) == 1


def test_late_or_foreign_signals_are_ignored(qapp):
    svc, _, _ = make(["libmpv", "ffmpeg"])
    svc.start()
    first = FakeWorker.made[0]
    first.progress.emit("ffmpeg", 90, "not mine")
    first.finished.emit("ffmpeg", True, "not mine")
    assert svc.state.engine == "libmpv" and svc.state.percent == 0 and len(FakeWorker.made) == 1
    first.done(True)
    first.done(False, "a second, stale finish")                  # after it already moved on
    assert svc.state.engine == "ffmpeg" and svc.state.phase == es.RUNNING


def test_closing_the_window_stops_a_running_download(qapp):
    svc, _, _ = make(["libmpv"])
    svc.start()
    svc.shutdown()
    assert FakeWorker.made[0].cancelled and FakeWorker.made[0].waited


def test_missing_engines_reports_what_the_app_cannot_find(qapp, monkeypatch):
    monkeypatch.setattr(es, "is_mpv_available", lambda: False)
    monkeypatch.setattr(es, "get_ffmpeg_path", lambda: "C:/x/ffmpeg.exe")
    monkeypatch.setattr(es, "get_ffprobe_path", lambda: None)    # ffprobe missing is enough to need the FFmpeg download
    assert es.missing_engines() == ["libmpv", "ffmpeg"]
    monkeypatch.setattr(es, "is_mpv_available", lambda: True)
    monkeypatch.setattr(es, "get_ffprobe_path", lambda: "C:/x/ffprobe.exe")
    assert es.missing_engines() == []


# -- the notice ---------------------------------------------------------------------------------------
class Snap:
    def __init__(self, unlocked):
        self.unlocked = unlocked


@pytest.fixture
def ctl(qapp):
    win = QWidget()
    win.resize(900, 600)
    win.show()
    svc, need, _ = make(["libmpv", "ffmpeg"])
    c = EngineSetupController(win, svc)
    yield c, svc, win
    c.shutdown()
    win.close()


def test_nothing_starts_until_the_install_is_unlocked_and_then_only_once(ctl):
    c, svc, _ = ctl
    c.on_license_state(Snap(False))
    assert FakeWorker.made == []
    c.on_license_state(Snap(True))
    c.on_license_state(Snap(True))
    assert len(FakeWorker.made) == 1


def test_a_later_license_update_never_restarts_a_finished_or_failed_setup(ctl, qapp):
    c, svc, _ = ctl
    c.on_license_state(Snap(True))
    FakeWorker.made[0].done(False, "network down")
    assert svc.state.phase == es.FAILED and len(FakeWorker.made) == 1
    for _ in range(3):
        c.on_license_state(Snap(True))                           # the license layer reports every few seconds
    assert len(FakeWorker.made) == 1 and svc.state.phase == es.FAILED    # only the person pressing Try again retries


def test_progress_shows_a_notice_with_a_bar_and_cancel(ctl, qapp):
    c, svc, _ = ctl
    c.on_license_state(Snap(True))
    qapp.processEvents()
    assert c._bar is not None and c._bar.title == "Setting up Rainy" and c._progress is not None
    FakeWorker.made[0].report(60, "Downloading: 18.0 MB / 30.0 MB (60%)")
    qapp.processEvents()
    assert c._progress.value() == 30 and "18.0 MB" in c._bar.content and "only happens once" in c._bar.content


def test_a_failure_shows_the_message_as_plain_text_with_try_again(ctl, qapp):
    c, svc, _ = ctl
    c.on_license_state(Snap(True))
    FakeWorker.made[0].done(False, "Couldn't download <b>bold</b> & <img src=x onerror=alert(1)>")
    qapp.processEvents()
    bar = c._bar
    assert bar.title.startswith("Couldn't finish") and "<b>bold</b>" in bar.content
    assert bar.contentLabel.textFormat() == Qt.PlainText         # never rendered as rich text
    from qfluentwidgets import PushButton
    retry = [b for b in bar.findChildren(PushButton) if b.text() == "Try again"]
    assert len(retry) == 1
    retry[0].click()
    qapp.processEvents()
    assert svc.running and len(FakeWorker.made) == 2 and c._bar.title == "Setting up Rainy"


def test_success_shows_a_short_ready_notice(ctl, qapp):
    c, svc, _ = ctl
    c.on_license_state(Snap(True))
    FakeWorker.made[0].done(True)
    FakeWorker.made[1].done(True)
    qapp.processEvents()
    assert svc.state.phase == es.DONE and c._bar.title == "Rainy is ready"


def test_the_lock_screen_hides_the_notice_and_keeps_it_hidden(ctl, qapp):
    c, svc, _ = ctl
    c.on_license_state(Snap(True))
    qapp.processEvents()
    assert c._bar is not None
    c.set_gated(True)
    assert c._bar is None
    FakeWorker.made[0].report(40)
    qapp.processEvents()
    assert c._bar is None                                        # still locked: nothing is shown
    c.set_gated(False)
    FakeWorker.made[0].report(50)
    qapp.processEvents()
    assert c._bar is not None
