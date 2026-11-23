"""In an installed Rainy the speech-engine "update" buttons must never run pip through the app's own exe
(that would open a second Rainy). Running from source still updates with pip."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.core import app_paths, engine_manager, vo_engine


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def no_subprocess(monkeypatch):
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        raise AssertionError("pip must not run")
    monkeypatch.setattr(vo_engine.subprocess, "run", fake_run)
    monkeypatch.setattr(engine_manager.subprocess, "run", fake_run)
    return calls


def test_ai_models_update_button_does_not_run_pip_when_installed(qapp, monkeypatch, no_subprocess):
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    got = []
    w = vo_engine.EngineUpdateWorker()
    w.signals.finished.connect(lambda ok, msg: got.append((ok, msg)))
    w.run()
    assert got == [(True, vo_engine.ENGINE_BUILT_IN_MESSAGE)] and no_subprocess == []


def test_core_engines_update_does_not_run_pip_when_installed(qapp, monkeypatch, no_subprocess):
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    w = engine_manager.EngineDownloadWorker("whisper")
    got = []
    w.finished.connect(lambda eid, ok, msg: got.append((eid, ok, msg)))
    w._update_whisper()
    assert got and got[0][0] == "whisper" and got[0][1] is True and "built into Rainy" in got[0][2]
    assert no_subprocess == []


def test_from_source_the_update_still_uses_pip(qapp, monkeypatch):
    monkeypatch.setattr(app_paths, "is_packaged", lambda: False)
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **k):
        seen.append(cmd)
        return Done()
    monkeypatch.setattr(vo_engine.subprocess, "run", fake_run)
    got = []
    w = vo_engine.EngineUpdateWorker()
    w.signals.finished.connect(lambda ok, msg: got.append(ok))
    w.run()
    assert seen and seen[0][1:4] == ["-m", "pip", "install"] and got == [True]
