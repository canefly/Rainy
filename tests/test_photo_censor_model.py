"""Lifecycle of the photo auto-censor model: download, cancel, resume, verify, delete, first launch."""
import hashlib
import os
import sys
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import photo_censor as pc
from src.core.database import db


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class FakeResponse:
    def __init__(self, data: bytes, status: int, on_read=None):
        self._data, self._pos, self.status, self._on_read = data, 0, status, on_read

    def read(self, n):
        if self._on_read:
            self._on_read(self._pos)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def model_env(tmp_path, monkeypatch, qapp):
    """A tiny stand-in model served by a fake download server, in an isolated folder."""
    payloads = {
        pc.MODEL_VISION_FILE: os.urandom(400_000),
        pc.MODEL_TEXT_FILE: os.urandom(250_000),
        pc.MODEL_TOKENIZER_FILE: os.urandom(20_000),
    }
    manifest = tuple((name, len(data), hashlib.sha256(data).hexdigest()) for name, data in payloads.items())
    monkeypatch.setattr(pc, "MODEL_DIR", str(tmp_path / "model"))
    monkeypatch.setattr(pc, "MODEL_MANIFEST", manifest)
    monkeypatch.setattr(pc, "MODEL_TOTAL_BYTES", sum(size for _, size, _ in manifest))

    env = type("Env", (), {})()
    env.payloads, env.requests, env.on_read, env.served = payloads, [], None, dict(payloads)

    def fake_open(task, name, have):
        env.requests.append((name, have))
        return FakeResponse(env.served[name][have:], 206 if have else 200, env.on_read)

    monkeypatch.setattr(pc._DownloadTask, "_open", fake_open)

    svc = pc.photo_censor
    svc.set_scorer(None)
    svc._downloading = False
    db.set_setting("photo_censor_auto", "1")
    db.set_setting("photo_censor_autodownload", pc.AUTODL_PENDING)
    db.set_setting("photo_censor_sensitivity", "balanced")
    env.svc = svc
    env.finished = []
    record = lambda outcome, msg: env.finished.append((outcome, msg))
    svc.model_download_finished.connect(record)

    def wait(timeout=10.0):
        end = time.monotonic() + timeout
        while svc.is_downloading() and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()
        assert not svc.is_downloading(), "download never finished"

    env.wait = wait
    yield env
    if svc.is_downloading():
        svc._download_cancel.set()
        wait()
    svc.model_download_finished.disconnect(record)
    db.set_setting("photo_censor_autodownload", pc.AUTODL_PENDING)


def _file(name):
    return pc._model_file(name)


def test_download_verifies_and_marks_ready(model_env):
    svc = model_env.svc
    assert not pc.model_ready()
    progress = []
    svc.model_download_progress.connect(lambda pct, text: progress.append(pct))

    assert svc.download_model() is True
    assert svc.is_downloading()
    assert svc.download_model() is False          # no double start
    model_env.wait()

    assert model_env.finished == [(pc.DOWNLOAD_OK, "Automatic photo censoring is ready.")]
    assert pc.model_ready()
    for name, data in model_env.payloads.items():
        assert open(_file(name), "rb").read() == data
        assert not os.path.exists(_file(name) + ".part")
    assert progress and progress == sorted(progress) and max(progress) <= 99
    assert db.get_setting("photo_censor_autodownload") == pc.AUTODL_DONE
    assert svc.download_model() is False          # already have it


def test_cancel_keeps_partial_and_resume_finishes(model_env, qapp):
    svc = model_env.svc
    reached, release = threading.Event(), threading.Event()

    def stall(pos):
        if pos >= 2 * pc._DownloadTask.CHUNK:
            reached.set()
            release.wait(10)

    model_env.on_read = stall
    svc.download_model()
    assert reached.wait(10)
    svc.cancel_download()                          # what the notice's Cancel button calls
    release.set()
    model_env.wait()

    assert model_env.finished == [(pc.DOWNLOAD_CANCELLED, "Download cancelled.")]
    assert not pc.model_ready()
    part = _file(pc.MODEL_VISION_FILE) + ".part"
    kept = os.path.getsize(part)
    assert 0 < kept < len(model_env.payloads[pc.MODEL_VISION_FILE])
    # Cancelling is a decision: the app must not start downloading again on its own.
    assert db.get_setting("photo_censor_autodownload") == pc.AUTODL_DECLINED

    model_env.on_read = None
    model_env.requests.clear()
    assert svc.download_model() is True            # manual retry from Preferences
    model_env.wait()
    assert model_env.finished[-1][0] == pc.DOWNLOAD_OK
    assert model_env.requests[0] == (pc.MODEL_VISION_FILE, kept)   # resumed, not restarted
    assert pc.model_ready()
    assert open(_file(pc.MODEL_VISION_FILE), "rb").read() == model_env.payloads[pc.MODEL_VISION_FILE]


def test_corrupted_download_is_rejected(model_env):
    svc = model_env.svc
    bad = bytearray(model_env.payloads[pc.MODEL_TEXT_FILE])
    bad[100] ^= 0xFF
    model_env.served[pc.MODEL_TEXT_FILE] = bytes(bad)

    svc.download_model()
    model_env.wait()
    outcome, message = model_env.finished[-1]
    assert outcome == pc.DOWNLOAD_FAILED and "corrupted" in message
    assert not pc.model_ready()
    assert not os.path.exists(_file(pc.MODEL_TEXT_FILE))
    assert not os.path.exists(_file(pc.MODEL_TEXT_FILE) + ".part")
    # A failure is not a refusal: the next launch may try again.
    assert db.get_setting("photo_censor_autodownload") == pc.AUTODL_PENDING


def test_offline_failure_reports_plainly(model_env, monkeypatch):
    import urllib.error

    def offline(task, name, have):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(pc._DownloadTask, "_open", offline)
    model_env.svc.download_model()
    model_env.wait()
    outcome, message = model_env.finished[-1]
    assert outcome == pc.DOWNLOAD_FAILED and "internet connection" in message


def test_first_launch_auto_download_rules(model_env, monkeypatch):
    svc = model_env.svc
    # Test runs are opted out globally.
    assert svc.maybe_auto_download() is False
    monkeypatch.delenv("KNOT_NO_AUTO_DOWNLOAD")

    db.set_setting("photo_censor_auto", "0")       # user switched auto-censor off
    assert svc.maybe_auto_download() is False
    db.set_setting("photo_censor_auto", "1")

    db.set_setting("photo_censor_autodownload", pc.AUTODL_DECLINED)   # cancelled / deleted earlier
    assert svc.maybe_auto_download() is False
    db.set_setting("photo_censor_autodownload", pc.AUTODL_PENDING)

    assert svc.maybe_auto_download() is True       # genuine first launch
    model_env.wait()
    assert pc.model_ready()
    assert svc.maybe_auto_download() is False      # nothing to do on later launches


def test_delete_removes_model_and_stops_auto_redownload(model_env, monkeypatch):
    svc = model_env.svc
    svc.download_model()
    model_env.wait()
    assert pc.model_ready() and pc.model_disk_bytes() == pc.MODEL_TOTAL_BYTES

    states = []
    svc.model_state_changed.connect(lambda: states.append(pc.model_ready()))
    assert svc.delete_model() is True
    assert not pc.model_ready() and not os.path.exists(pc.MODEL_DIR)
    assert states[-1] is False

    monkeypatch.delenv("KNOT_NO_AUTO_DOWNLOAD")
    assert svc.maybe_auto_download() is False      # deleting means "I don't want it"
    assert svc.download_model() is True            # but an explicit download still works
    model_env.wait()
    assert pc.model_ready()


def test_verdict_on_file_survives_model_removal(model_env, tmp_path, qapp):
    from PySide6.QtGui import QColor, QImage
    svc = model_env.svc
    photo = tmp_path / "gore_scene.png"
    img = QImage(200, 120, QImage.Format_RGB32)
    img.fill(QColor("#cc3344"))
    assert img.save(str(photo))
    other = tmp_path / "unchecked.png"
    assert img.save(str(other))

    svc.set_scorer(lambda p: 0.9)
    assert svc.is_hidden(str(photo))               # queued
    svc._pool.waitForDone()
    qapp.processEvents()
    assert svc.auto_score(str(photo)) == pytest.approx(0.9)

    svc.set_scorer(None)                            # model gone (test folder is empty)
    assert not svc.model_ready()
    assert svc.is_flagged(str(photo)) is True       # known-graphic photo stays censored
    assert svc.is_flagged(str(other)) is False      # never checked: nothing to go on

    # Sensitivity presets move the line without re-checking anything.
    svc._record(svc._key(str(photo)))["score"] = 0.55
    assert svc.is_flagged(str(photo)) is True
    svc.set_sensitivity("relaxed")
    assert svc.is_flagged(str(photo)) is False
    svc.set_sensitivity("strict")
    assert svc.is_flagged(str(photo)) is True
    svc.set_sensitivity("balanced")


def test_preferences_card_follows_model_state(model_env, qapp):
    from src.ui.components.preferences_dialog import PreferencesDialog
    svc = model_env.svc
    dlg = PreferencesDialog(None, initial_tab=0)
    try:
        assert dlg.censor_badge.text().startswith("Model needed")
        assert dlg.censor_dl_btn.text() == "Download" and dlg.censor_dl_btn.isEnabled()
        assert not dlg.censor_del_btn.isEnabled()

        reached, release = threading.Event(), threading.Event()
        model_env.on_read = lambda pos: (reached.set(), release.wait(10)) if pos >= pc._DownloadTask.CHUNK else None
        dlg.censor_dl_btn.click()                   # Download
        assert reached.wait(10)
        qapp.processEvents()
        assert dlg.censor_badge.text().startswith("Downloading")
        assert dlg.censor_dl_btn.text() == "Cancel"
        assert not dlg.censor_dl_progress.isHidden()
        assert not dlg.censor_del_btn.isEnabled()

        dlg.censor_dl_btn.click()                   # Cancel
        release.set()
        model_env.wait()
        assert model_env.finished[-1][0] == pc.DOWNLOAD_CANCELLED
        assert dlg.censor_dl_btn.text() == "Resume"
        assert dlg.censor_status_lbl.text() == "Partly Downloaded"
        assert dlg.censor_del_btn.isEnabled()       # can clear the partial download

        model_env.on_read = None
        dlg.censor_dl_btn.click()                   # Resume
        model_env.wait()
        assert dlg.censor_badge.text() == "Active"
        assert dlg.censor_dl_btn.text() == "Downloaded" and not dlg.censor_dl_btn.isEnabled()
        assert dlg.censor_dl_progress.isHidden()

        dlg.censor_enable_switch.setChecked(False)
        qapp.processEvents()
        assert not svc.auto_enabled() and dlg.censor_badge.text().startswith("Off")
        dlg.censor_enable_switch.setChecked(True)

        dlg.censor_sensitivity_combo.setCurrentIndex(2)
        assert svc.sensitivity() == "strict"
        dlg.censor_sensitivity_combo.setCurrentIndex(1)

        dlg.censor_del_btn.click()                  # Delete
        qapp.processEvents()
        assert not pc.model_ready()
        assert dlg.censor_dl_btn.text() == "Download" and dlg.censor_status_lbl.text() == "Not Downloaded"
    finally:
        dlg.close()


def test_main_window_shows_cancellable_download_notice(model_env, qapp):
    from qfluentwidgets import PushButton
    from src.ui.window import MainWindow
    svc = model_env.svc
    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()
        assert win._censor_dl_bar is None

        reached, release = threading.Event(), threading.Event()
        model_env.on_read = lambda pos: (reached.set(), release.wait(10)) if pos >= pc._DownloadTask.CHUNK else None
        svc.download_model()
        assert reached.wait(10)
        qapp.processEvents()

        bar = win._censor_dl_bar
        assert bar is not None and "automatic photo censoring" in bar.title.lower()
        assert "%" in bar.content                   # live progress reached the notice
        cancel = next(b for b in bar.findChildren(PushButton) if b.text() == "Cancel Download")
        cancel.click()
        release.set()
        model_env.wait()

        assert model_env.finished[-1][0] == pc.DOWNLOAD_CANCELLED
        assert win._censor_dl_bar is None
        assert db.get_setting("photo_censor_autodownload") == pc.AUTODL_DECLINED
    finally:
        win.close()
