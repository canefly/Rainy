import pytest
from PySide6.QtWidgets import QApplication
from src.core.models import ScriptItem, DocumentState
from src.core.database import db
from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow
from src.ui.views.workspace_view import WorkspaceView

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_trimmer_defers_db_and_docx_writes(qapp):
    win = ARollTrimmerWindow()
    doc_sig = "test_sig_deferred_123"
    item = ScriptItem(
        id="card_defer_test",
        row_index=1,
        raw_timestamp="00:10:00:00 - 00:10:05:00",
        start_timecode="00:10:00:00",
        start_seconds=600.0,
        end_timecode="00:10:05:00",
        end_seconds=605.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="Deferred commit test",
        is_single=False
    )
    win.load_item(item, footage_path=None, doc_signature=doc_sig)

    # 1. Modify In and Out points via direct change slots
    win._on_in_changed_direct(602.0)
    win._on_out_changed_direct(608.0)

    # 2. Check that pending overrides buffered in memory
    assert "card_defer_test" in win._pending_overrides
    from src.core.parser import format_smpte
    expected_in = format_smpte(602.0, win._nominal_fps)
    assert win._pending_overrides["card_defer_test"][0] == expected_in

    # Verify db does not yet have it before flush
    overrides_before = db.get_overrides(doc_sig)
    assert "card_defer_test" not in overrides_before

    # 3. Trigger flush_trim_overrides (called on window close/exit)
    win.flush_trim_overrides()

    # 4. Verify db now has the persisted override
    overrides_after = db.get_overrides(doc_sig)
    assert "card_defer_test" in overrides_after
    assert overrides_after["card_defer_test"]["start_tc"] == expected_in

    win.close()

def test_workspace_trimmer_timecode_changed_no_docx_reload(qapp):
    ws = WorkspaceView()
    item = ScriptItem(
        id="card_ws_test",
        row_index=1,
        raw_timestamp="00:01:00:00 - 00:01:05:00",
        start_timecode="00:01:00:00",
        start_seconds=60.0,
        end_timecode="00:01:05:00",
        end_seconds=65.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="WS test line",
        is_single=False
    )
    doc = DocumentState(
        id="doc_1",
        file_signature="test_ws_sig",
        name="test",
        file_path="C:/fake/path/test.docx",
        raw_html="<p>test</p>",
        items=[item]
    )
    ws.load_document(doc)

    # Calling trimmer timecode changed should NOT reload document or touch docx
    ws._on_trimmer_timecode_changed("card_ws_test", "00:01:02:00", "00:01:08:00")

    # Card widget should have updated in memory
    card = ws.timeline_list.card_widgets.get("card_ws_test")
    assert card is not None
    assert card.item.start_timecode == "00:01:02:00"
    assert card.item.end_timecode == "00:01:08:00"
    assert getattr(ws, "_has_pending_trim_changes", False) is True

    # Closing trimmer window flushes
    ws._on_trimmer_window_closed()
    assert getattr(ws, "_has_pending_trim_changes", False) is False

    ws.close()

def test_trimmer_navigation_auto_applies_and_shortcuts(qapp):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt, QEvent

    win = ARollTrimmerWindow()
    doc_sig = "test_nav_auto_apply_sig"
    item = ScriptItem(
        id="card_nav_test",
        row_index=1,
        raw_timestamp="00:02:00:00 - 00:02:05:00",
        start_timecode="00:02:00:00",
        start_seconds=120.0,
        end_timecode="00:02:05:00",
        end_seconds=125.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="Nav test line",
        is_single=False
    )
    win.load_item(item, footage_path=None, doc_signature=doc_sig)

    # 1. Test Ctrl+E to enter Edit Mode
    ctrl_e = QKeyEvent(QEvent.KeyPress, Qt.Key_E, Qt.ControlModifier)
    win.keyPressEvent(ctrl_e)
    assert win.is_edit_mode() is True

    # 2. Modify trim in edit mode
    win._on_in_changed_direct(121.0)
    win._on_out_changed_direct(127.0)

    # 3. Test navigation (Down Arrow) auto-applies and flushes
    applied_cards = []
    win.edit_applied.connect(lambda cid: applied_cards.append(cid))
    next_card_calls = []
    win.request_next_card.connect(lambda: next_card_calls.append(True))

    down_key = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    win.keyPressEvent(down_key)

    assert len(next_card_calls) == 1
    assert "card_nav_test" in applied_cards
    # db should now have the persisted override
    ov = db.get_overrides(doc_sig)
    assert "card_nav_test" in ov

    # 4. Test Ctrl+S to exit Edit Mode
    ctrl_s = QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.ControlModifier)
    win.keyPressEvent(ctrl_s)
    assert win.is_edit_mode() is False

    win.close()


def test_timeline_canvas_paint_pulse_highlight_no_name_error(qapp):
    from PySide6.QtGui import QPaintEvent
    from PySide6.QtCore import QRect
    from src.ui.components.timeline_list import TimelineList

    tl = TimelineList()
    tl.resize(400, 600)
    item = ScriptItem(
        id="card_pulse_test",
        row_index=1,
        raw_timestamp="00:01:00:00 - 00:01:05:00",
        start_timecode="00:01:00:00",
        start_seconds=60.0,
        end_timecode="00:01:05:00",
        end_seconds=65.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="Pulse test line",
        is_single=False
    )
    tl.load_items([item], doc_signature="sig_test_pulse")
    tl.pulse_card("card_pulse_test")

    card = tl.card_widgets.get("card_pulse_test")
    assert card is not None
    assert card.is_highlighted is True
    card._highlight_alpha = 0.8

    # Render paintEvent on TimelineCanvas directly
    canvas = tl.container
    canvas.resize(380, 500)
    pe = QPaintEvent(QRect(0, 0, 380, 500))
    # Must execute cleanly without NameError or painter leaks
    canvas.paintEvent(pe)
    tl.close()


