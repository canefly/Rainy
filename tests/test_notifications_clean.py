import os
import sys
import pytest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ui.window import MainWindow
from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow
from src.core.models import ScriptItem, DocumentState


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_trimmer_reset_to_raw_no_infobar(qapp):
    win = ARollTrimmerWindow()
    item = ScriptItem(
        id="item-1",
        row_index=1,
        raw_timestamp="00:01:00:00 - 00:01:05:00",
        start_timecode="00:01:00:00",
        start_seconds=60.0,
        end_timecode="00:01:05:00",
        end_seconds=65.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="Sample dialog",
    )
    win.current_item = item
    win._in_seconds = 80.0
    win._out_seconds = 85.0
    win.set_mode(win.MODE_EDIT)

    # Calling reset_to_raw should revert In/Out to original timecode without raising or creating InfoBar
    win.reset_to_raw()
    assert abs(win._in_seconds - 60.0) < 0.1
    assert abs(win._out_seconds - 65.0) < 0.1
    win.close()


def test_mainwindow_tray_notification_and_shortcuts(qapp):
    win = MainWindow()
    
    # Tray notification system initialized
    if QSystemTrayIcon.isSystemTrayAvailable():
        assert win.tray_icon is not None
        assert isinstance(win.tray_icon, QSystemTrayIcon)
    
    # show_system_notification callable without errors
    win.show_system_notification("Test Render", "Batch finished safely", is_error=False)

    # F1 Shortcuts opens Preferences on tab 5
    win._on_menu_shortcuts()
    assert hasattr(win, "pref_window") and win.pref_window is not None
    assert win.pref_window.stack.currentIndex() == 5
    win.pref_window.close()
    win.close()


def test_docx_parse_finished_no_hanging_loading_bar(qapp):
    win = MainWindow()
    doc_state = DocumentState(
        id="doc-1",
        file_signature="sig-1",
        name="Test Doc",
        file_path="C:/dummy/test.docx",
        raw_html="<p>Test</p>",
        items=[],
        links=[],
        notes=[]
    )

    # Calling _on_parse_finished does not set or leave hanging InfoBar
    win._on_parse_finished(doc_state)
    assert not hasattr(win, "loading_bar") or win.loading_bar is None
    win.close()
