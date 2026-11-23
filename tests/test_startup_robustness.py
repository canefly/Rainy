"""Things that made an installed Rainy die silently or not connect, found by installing it for real:
- an install in Program Files is read-only for a normal user, and the UI library wrote its settings next to the program;
- the windowed program quit with no message when anything went wrong while starting;
- the live connection should trust the bundled public roots like everything else."""
import ctypes
import logging
import os
import sys
import types
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from qfluentwidgets import qconfig

from src.core import app_paths


# -- the settings file ---------------------------------------------------------------------------------
def test_the_ui_settings_file_lives_in_the_app_folder_not_next_to_the_program():
    import src.ui.theme  # noqa: F401  (importing the theme is what points the library at the app folder)
    assert Path(qconfig.file).name == "ui_config.json"
    assert Path(qconfig.file).parent == Path(app_paths.app_home())


def test_changing_the_theme_writes_nothing_into_the_folder_the_app_was_started_from(tmp_path, monkeypatch):
    import src.ui.theme  # noqa: F401
    start_folder = tmp_path / "Program Files" / "Rainy"
    start_folder.mkdir(parents=True)
    monkeypatch.chdir(start_folder)
    qconfig.set(qconfig.themeColor, QColor("#12ab34"))               # this is what used to create config\\config.json here
    assert list(start_folder.iterdir()) == []
    assert Path(qconfig.file).is_file() and "12ab34" in Path(qconfig.file).read_text(encoding="utf-8").lower()


def test_a_settings_file_that_cannot_be_written_never_stops_the_app(tmp_path, monkeypatch, caplog):
    import src.ui.theme  # noqa: F401
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where a folder should be")
    monkeypatch.setattr(qconfig, "file", blocker / "ui_config.json")  # the parent is a file: creating it fails like a locked folder
    with caplog.at_level(logging.WARNING):
        qconfig.set(qconfig.themeColor, QColor("#56cd78"))            # must not raise
    assert "Could not save UI settings" in caplog.text
    assert blocker.read_text() == "a file where a folder should be"   # and it didn't damage anything


def test_installing_twice_does_not_wrap_the_save_twice():
    from src.ui import ui_config
    ui_config.install()
    wrapped = qconfig.save
    ui_config.install()
    assert qconfig.save is wrapped


# -- startup failures -----------------------------------------------------------------------------------
@pytest.fixture
def message_boxes(monkeypatch):
    shown = []
    fake_user32 = types.SimpleNamespace(MessageBoxW=lambda hwnd, text, caption, flags: shown.append((text, caption, flags)) or 1)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=fake_user32), raising=False)
    return shown


def test_a_startup_failure_is_logged_and_shown_instead_of_vanishing(message_boxes):
    sys.path.insert(0, str(Path(app_paths.__file__).resolve().parents[2] / "src"))
    try:
        import main as app_main
    finally:
        sys.path.pop(0)
    try:
        raise PermissionError(13, "Permission denied: 'config/config.json'")
    except PermissionError:
        app_main._report_startup_failure()
    assert len(message_boxes) == 1
    text, caption, flags = message_boxes[0]
    assert caption == "Rainy" and flags == 0x10 and "Rainy could not start" in text
    assert "PermissionError" in text and "config.json" in text and "knot.log" in text     # the reason and where to find more
    from src.core.logger import get_log_file_path
    log = Path(get_log_file_path()).read_text(encoding="utf-8", errors="replace")
    assert "Rainy could not start" in log and "Traceback" in log and "PermissionError" in log


def test_reporting_a_failure_can_never_raise_even_when_nothing_works(monkeypatch):
    sys.path.insert(0, str(Path(app_paths.__file__).resolve().parents[2] / "src"))
    try:
        import main as app_main
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(ctypes, "windll", None, raising=False)       # not Windows / no user32
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        app_main._report_startup_failure()                           # no exception escapes


# -- the live connection ---------------------------------------------------------------------------------
class FakeSocket:
    def __init__(self):
        self.configs = []

    def setSslConfiguration(self, cfg):
        self.configs.append(cfg)


def test_the_live_connection_also_trusts_the_bundled_roots():
    from src.core.live_channel import LiveChannel
    sock = FakeSocket()
    LiveChannel._trust_bundled_roots(sock)
    assert len(sock.configs) == 1 and len(sock.configs[0].caCertificates()) > 100


def test_the_live_connection_setup_never_raises(monkeypatch):
    from src.core import net_tls
    from src.core.live_channel import LiveChannel
    sock = FakeSocket()
    monkeypatch.setattr(net_tls, "bundled_cafile", lambda: None)     # no bundle available
    LiveChannel._trust_bundled_roots(sock)
    assert sock.configs == []
    monkeypatch.setattr(net_tls, "bundled_cafile", lambda: str(Path(__file__)))   # a file that is not certificates
    LiveChannel._trust_bundled_roots(sock)
    assert sock.configs == []
    monkeypatch.setattr(net_tls, "bundled_cafile", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    LiveChannel._trust_bundled_roots(sock)                            # raised inside: swallowed and logged
    assert sock.configs == []
