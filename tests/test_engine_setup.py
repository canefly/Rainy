"""First-run engine setup: what gets downloaded, in what order, what happens when it fails, and how it looks."""
import sys

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.core import engine_setup as es


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


def test_the_service_reports_each_engines_own_progress_and_what_is_queued(qapp):
    svc, _, _ = make(["libmpv", "ffmpeg"])
    assert svc.pending() == ["libmpv", "ffmpeg"] and svc.queue == []        # asking starts nothing
    svc.start()
    assert svc.queue == ["libmpv", "ffmpeg"]
    FakeWorker.made[0].report(40)
    assert svc.state.engine_percent == 40 and svc.state.percent == 20
    FakeWorker.made[0].report(10)                                           # never backwards
    assert svc.state.engine_percent == 40
    FakeWorker.made[0].done(True)
    assert svc.state.engine == "ffmpeg" and svc.state.engine_percent == 0   # the next engine starts from zero
