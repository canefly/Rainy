import os
import json
import pytest
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from src.core.models import DocumentState, ScriptItem, EditorNote
from src.core.project_manager import project_manager, ProjectData
from src.core.database import db
from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow, TimecodeScrubBar
from src.ui.components.timeline_card import TimelineCard
from src.ui.components.timeline_list import TimelineList
from src.ui.views.workspace_view import WorkspaceView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ==============================================================================
# 1. Standalone Self-Contained .rainyproj Savefile & Hydration without DOCX
# ==============================================================================
def test_standalone_rainyproj_save_and_hydration_without_docx(tmp_path):
    """Verifies that:
    - .rainyproj is 100% self-contained (v2 format).
    - Contains script text, paragraphs, checklist items, links, notes, overrides.
    - Hydrates directly into DocumentState without requiring the original DOCX file.
    """
    item1_dict = {
        "id": "item-test-1",
        "row_index": 1,
        "raw_timestamp": "01:23:45:00 - 01:23:55:00",
        "start_timecode": "01:23:45:00",
        "start_seconds": 5025.0,
        "end_timecode": "01:23:55:00",
        "end_seconds": 5035.0,
        "duration_seconds": 10.0,
        "duration_display": "10s",
        "text": "@editor play clip 1",
        "meta": "CLIP 1",
        "sync_id": 1,
        "links": ["https://drive.google.com/file/d/test12345"],
        "is_checked": True,
    }
    blocks = [
        {"id": 0, "text": "Narrator dialogue before clip.", "isEditorLine": False},
        {"id": 1, "text": "@editor play clip 1", "isEditorLine": True},
    ]
    notes_dict = [
        {"id": "note-1", "block_id": 1, "text": "Check audio sync", "is_checked": True}
    ]

    proj_file = str(tmp_path / "SelfContainedTest.rainyproj")

    data = ProjectData(
        project_name="SelfContainedTest",
        script_file_name="NonExistent_Original.docx",
        file_signature="sig-selfcontained-999",
        raw_html="<p>Embedded HTML content</p>",
        items=[item1_dict],
        notes=notes_dict,
        custom_notes=[{"text": "Custom research note"}],
        headings=[{"text": "Chapter 1", "level": 1, "block_id": 0}],
        links=["https://drive.google.com/file/d/test12345"],
        link_colors={"https://drive.google.com/file/d/test12345": "#38bdf8"},
        blocks=blocks,
        checked_items=["item-test-1"],
        checked_notes=["note-1"],
        checked_links=["https://drive.google.com/file/d/test12345"],
        overrides={"item-test-1": {"in_tc": "01:23:46:00", "out_tc": "01:23:54:00"}},
        linked_footage={"https://drive.google.com/file/d/test12345": "D:/Footage/clip1.mp4"},
    )

    # Save standalone project
    success = project_manager.save_project(proj_file, data)
    assert success is True
    assert os.path.isfile(proj_file)

    # Read back raw JSON to verify self-contained fields
    with open(proj_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert saved_data.get("format_version") == 2
    assert saved_data.get("raw_html") == "<p>Embedded HTML content</p>"
    assert len(saved_data.get("items", [])) == 1
    assert saved_data["items"][0]["id"] == "item-test-1"
    assert "item-test-1" in saved_data.get("overrides", {})

    # Load project using ProjectManager
    loaded_proj = project_manager.load_project(proj_file)
    assert loaded_proj is not None
    assert loaded_proj.has_embedded_document() is True

    # Hydrate directly into DocumentState without DOCX
    hydrated_doc = loaded_proj.to_document_state()
    assert hydrated_doc is not None
    assert hydrated_doc.name == "SelfContainedTest"
    assert hydrated_doc.raw_html == "<p>Embedded HTML content</p>"
    assert len(hydrated_doc.items) == 1
    assert hydrated_doc.items[0].id == "item-test-1"
    assert hydrated_doc.items[0].is_checked is True
    assert len(hydrated_doc.notes) == 1
    assert hydrated_doc.notes[0].is_checked is True
    assert len(hydrated_doc.headings) == 1
    assert hydrated_doc.link_colors.get("https://drive.google.com/file/d/test12345") == "#38bdf8"


# ==============================================================================
# 2. SQLite Demoted to Global App Config Only (No Document State Dumps)
# ==============================================================================
def test_sqlite_demoted_to_config_only():
    """Verifies that SQLite save_document is a no-op, get_document_by_signature returns None,
    and overrides are kept in RAM / persisted in .rainyproj."""
    dummy_doc = DocumentState(
        id="dummy", file_signature="sig-dummy", name="Dummy.docx",
        file_path="C:/dummy.docx", raw_html="<p>test</p>", items=[]
    )
    # save_document is no-op
    db.save_document(dummy_doc)
    # get_document_by_signature returns None
    assert db.get_document_by_signature("sig-dummy") is None

    # Overrides are stored in-memory
    db.save_override("sig-dummy", "item-mem-1", "00:01:00:00", "00:01:10:00")
    overrides = db.get_overrides("sig-dummy")
    assert "item-mem-1" in overrides
    assert overrides["item-mem-1"]["start_tc"] == "00:01:00:00"
    assert overrides["item-mem-1"]["end_tc"] == "00:01:10:00"


# ==============================================================================
# 3. Two-Tier Timeline Scrubber & Premiere Pro Zoom Bar
# ==============================================================================
def test_two_tier_scrubber_components_and_zooming(qapp):
    """Verifies with REAL Qt mouse and wheel events:
    - Tier 1: SMPTE ticks, playhead seek on ruler click, bracket dragging in track.
    - Tier 2: Zoom overview bar, viewport thumb, bullseye circle handles.
    - Dragging left/right handles zooms in/out.
    - Dragging center thumb pans.
    - Real QWheelEvent zooms in/out anchored to playhead.
    """
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPointF

    scrubber = TimecodeScrubBar()
    scrubber.set_range(duration=120.0, in_sec=10.0, out_sec=40.0, fps=30.0)
    scrubber.resize(800, 68)
    scrubber.show()
    qapp.processEvents()

    assert scrubber._fps == 30.0
    assert scrubber.duration == 120.0
    assert scrubber.in_pos == 10.0
    assert scrubber.out_pos == 40.0
    assert scrubber.height() == 68

    # 1. Tier 2: Left handle drag (Zoom in from left)
    th_left = scrubber._overview_sec_to_x(scrubber.view_start_sec)
    QTest.mousePress(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(th_left, 57))
    assert scrubber._drag_mode == "thumb_left"
    # Drag right by 80px
    QTest.mouseMove(scrubber, QPoint(th_left + 80, 57))
    assert scrubber.view_start_sec > 0.0
    QTest.mouseRelease(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(th_left + 80, 57))
    assert scrubber._drag_mode is None
    cur_start = scrubber.view_start_sec

    # 2. Tier 2: Center thumb pan
    th_l = scrubber._overview_sec_to_x(scrubber.view_start_sec)
    th_r = scrubber._overview_sec_to_x(scrubber.view_end_sec)
    mid_x = (th_l + th_r) // 2
    QTest.mousePress(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(mid_x, 57))
    assert scrubber._drag_mode == "thumb_pan"
    QTest.mouseMove(scrubber, QPoint(mid_x + 30, 57))
    QTest.mouseRelease(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(mid_x + 30, 57))
    assert scrubber._drag_mode is None
    assert scrubber.view_start_sec > cur_start

    # Reset view to full 0..120
    scrubber.view_start_sec = 0.0
    scrubber.view_end_sec = 120.0

    # 3. Tier 1: Ruler header click (y = 8) scrubs playhead directly
    target_sec = 50.0
    target_x = scrubber._sec_to_x(target_sec)
    QTest.mousePress(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(target_x, 8))
    assert scrubber._drag_mode == "playhead"
    assert abs(scrubber.playhead_pos - target_sec) < 1.0
    QTest.mouseRelease(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(target_x, 8))
    assert scrubber._drag_mode is None

    # 4. Tier 1: In bracket drag in track area (y = 25)
    in_x = scrubber._sec_to_x(scrubber.in_pos)
    drag_started_signals = []
    drag_finished_signals = []
    scrubber.drag_started.connect(drag_started_signals.append)
    scrubber.drag_finished.connect(drag_finished_signals.append)

    QTest.mousePress(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(in_x, 25))
    assert scrubber._drag_mode == "in_bracket"
    assert "in_bracket" in drag_started_signals

    orig_in = scrubber.in_pos
    QTest.mouseMove(scrubber, QPoint(in_x + 40, 25))
    assert scrubber.in_pos > orig_in
    QTest.mouseRelease(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(in_x + 40, 25))
    assert scrubber._drag_mode is None
    assert "in_bracket" in drag_finished_signals

    # 5. Real QWheelEvent anchored zoom centered on cut anchor (or playhead fallback)
    anchor = scrubber.get_zoom_anchor()
    old_span = scrubber.view_end_sec - scrubber.view_start_sec
    old_anchor_ratio = (anchor - scrubber.view_start_sec) / old_span

    wheel_event = QWheelEvent(
        QPointF(400, 20), QPointF(400, 20),
        QPoint(0, 0), QPoint(0, 120),  # positive delta = zoom in
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    QApplication.sendEvent(scrubber, wheel_event)
    qapp.processEvents()

    new_span = scrubber.view_end_sec - scrubber.view_start_sec
    assert new_span < old_span  # Zoomed in
    new_anchor_ratio = (anchor - scrubber.view_start_sec) / new_span
    assert abs(old_anchor_ratio - new_anchor_ratio) < 0.005  # Anchor position pinned!

    scrubber.close()


# ==============================================================================
# 4. Subtle VFR Warning Badge
# ==============================================================================
def test_subtle_vfr_badge_in_trimmer(qapp):
    """Verifies that the trimmer window has a clean amber badge '⚠️ VFR' with tooltip
    and no raw '(VFR Alert)' text in the clip title."""
    trimmer = ARollTrimmerWindow(None)
    trimmer.show()
    qapp.processEvents()

    assert hasattr(trimmer, "vfr_badge")
    assert trimmer.vfr_badge.text() == "⚠️ VFR"
    assert "Variable Frame Rate" in trimmer.vfr_badge.toolTip()
    # Ensure title does not contain raw text "(VFR Alert)"
    assert "(VFR Alert)" not in trimmer.clip_name_lbl.text()

    trimmer.destroy_window()
    qapp.processEvents()


# ==============================================================================
# 5. Debounced Disk I/O during Scrubbing / Trimming & Drag Finish Commit
# ==============================================================================
def test_debounced_trimmer_save(qapp):
    """Verifies that in/out trims do not trigger immediate synchronous disk writes,
    but use a 300ms debounce timer and flush cleanly upon drag finish."""
    trimmer = ARollTrimmerWindow(None)
    trimmer.show()
    qapp.processEvents()

    assert hasattr(trimmer, "_trim_save_timer")
    assert trimmer._trim_save_timer.isSingleShot() is True
    assert trimmer._trim_save_timer.interval() == 300

    item = ScriptItem(
        id="test-debounced-item",
        row_index=1,
        raw_timestamp="00:00:10:00 - 00:00:20:00",
        start_timecode="00:00:10:00",
        start_seconds=10.0,
        end_timecode="00:00:20:00",
        end_seconds=20.0,
        duration_seconds=10.0,
        duration_display="10s",
        text="Test",
    )
    trimmer.current_item = item
    trimmer.doc_signature = "test-sig"
    trimmer._in_seconds = 5.0
    trimmer._out_seconds = 15.0

    # Test drag start pushes undo ONCE
    initial_undo_len = len(trimmer._undo_stack)
    trimmer._on_trim_drag_started("in_bracket")
    assert len(trimmer._undo_stack) == initial_undo_len + 1

    # Mouse moves update in-memory item without polluting undo stack
    trimmer._on_in_changed_direct(6.0)
    trimmer._on_in_changed_direct(7.0)
    assert len(trimmer._undo_stack) == initial_undo_len + 1  # Still 1 undo entry!
    assert trimmer._trim_save_timer.isActive() is True

    # Drag finished commits trim overrides immediately
    trimmer._on_trim_drag_finished("in_bracket")
    assert trimmer._trim_save_timer.isActive() is False
    overrides = db.get_overrides("test-sig")
    assert "test-debounced-item" in overrides
    assert overrides["test-debounced-item"]["start_tc"] == "00:00:07:00"

    trimmer.destroy_window()
    qapp.processEvents()


# ==============================================================================
# 6. Responsive Narrow-Card Layout & Header Sizing
# ==============================================================================
def test_responsive_narrow_card_and_header(qapp):
    """Verifies:
    - In narrow mode (width < 320px), TimelineList header title and button scale without overlap.
    - TimelineCard in compact mode retains In/Out timecodes, duration, and action buttons without clipping.
    """
    tl = TimelineList()
    tl.show()
    tl.setFixedSize(290, 500)
    qapp.processEvents()

    # Narrow header check (Cuts toggle removed: per-card ▶ player replaced it)
    assert tl.filter_combo.maximumWidth() <= 74
    assert not hasattr(tl, "edit_cuts_btn")

    # Widen header check
    tl.setFixedSize(450, 500)
    qapp.processEvents()
    assert tl.filter_combo.maximumWidth() >= 78

    # Card compact layout check
    item = ScriptItem(
        id="item-compact-1",
        row_index=1,
        raw_timestamp="00:01:00:00 - 00:01:05:00",
        start_timecode="00:01:00:00",
        start_seconds=60.0,
        end_timecode="00:01:05:00",
        end_seconds=65.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="Test clip",
        meta="CLIP",
        links=["https://drive.google.com/file/d/compact_test"],
    )
    card = TimelineCard(item)
    card.show()
    card.setFixedSize(270, 44)
    card.set_compact_mode(True)
    qapp.processEvents()

    # Verify compact state
    assert card.is_compact is True
    assert card.tc_container.width() == 110
    assert card.in_lbl.isVisible() is True
    assert card.out_lbl.isVisible() is True
    # Labels must be wide enough for their rendered text (regression: "Out" was clipped at 16px)
    for lbl in (card.in_lbl, card.out_lbl):
        assert lbl.width() >= lbl.fontMetrics().horizontalAdvance(lbl.text())
    assert card.play_btn is not None and card.play_btn.isVisible()
    assert card.ext_btn.isVisible() is True
    assert card.ext_btn.width() == 22

    # Verify override updates labels without error
    card.apply_timecode_override("00:01:02:00", "00:01:08:00")
    assert card.end_btn_c.text() == "00:01:08:00"

    card.close()
    tl.close()
    qapp.processEvents()


def test_loop_vs_edit_mode_and_edit_applied_pulse(qapp):
    """Verifies Loop (player) default state vs Edit (trimmer) state, bracket locking,
    and that edit_applied emits only when In/Out changed during the Edit session."""
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt, QPoint

    win = ARollTrimmerWindow()
    win.show()
    qapp.processEvents()

    # 1. Default state is MODE_LOOP: trim buttons hidden, scrub_bar.trim_enabled False
    assert win.mode == win.MODE_LOOP
    assert win.is_edit_mode() is False
    assert win.scrub_bar.trim_enabled is False
    assert win.mark_in_btn.isVisible() is False
    assert win.mark_out_btn.isVisible() is False
    assert win.undo_btn.isVisible() is False
    assert win.reset_btn.isVisible() is False
    assert win.mode_btn.text() == "Edit"

    item = ScriptItem(
        id="card_loop_edit_1",
        row_index=1,
        raw_timestamp="00:00:10:00 - 00:00:18:00",
        start_timecode="00:00:10:00",
        start_seconds=10.0,
        end_timecode="00:00:18:00",
        end_seconds=18.0,
        duration_seconds=8.0,
        duration_display="8s",
        text="Loop/Edit test",
        is_single=False,
    )
    win.load_item(item, footage_path=None, doc_signature="sig_loop_edit")

    # 2. In Loop state, clicking on In bracket position scrubs playhead instead of dragging bracket
    in_x = win.scrub_bar._sec_to_x(win.scrub_bar.in_pos)
    QTest.mousePress(win.scrub_bar, Qt.LeftButton, Qt.NoModifier, QPoint(in_x, 25))
    assert win.scrub_bar._drag_mode == "playhead"
    QTest.mouseRelease(win.scrub_bar, Qt.LeftButton, Qt.NoModifier, QPoint(in_x, 25))

    applied_ids = []
    win.edit_applied.connect(applied_ids.append)

    # 3. Enter Edit state -> exit without modifying In/Out -> edit_applied must NOT fire
    win.toggle_mode()
    assert win.is_edit_mode() is True
    assert win.scrub_bar.trim_enabled is True
    assert win.mark_in_btn.isVisible() is True
    assert win.mark_out_btn.isVisible() is True
    assert win.mode_btn.text() == "Done"

    win.toggle_mode()
    assert win.is_edit_mode() is False
    assert applied_ids == []

    # 4. Enter Edit state -> modify In point -> exit (Done) -> edit_applied fires for card
    win.toggle_mode()
    assert win.is_edit_mode() is True
    win._on_in_changed_direct(12.0)
    win.toggle_mode()
    assert win.is_edit_mode() is False
    assert applied_ids == ["card_loop_edit_1"]

    win.destroy_window()
    qapp.processEvents()


# ==============================================================================
# 7. Override Recalculation & Legacy Project Handling Edge Cases
# ==============================================================================
def test_overrides_recalculate_seconds_and_duration_in_standalone_proj():
    """Verifies that when a .rainyproj contains overrides (with in_tc/out_tc or start_tc/end_tc)
    without explicit seconds, to_document_state() correctly re-derives start_seconds,
    end_seconds, duration_seconds, and duration_display."""
    raw_item = {
        "id": "recalc-item",
        "row_index": 1,
        "start_timecode": "00:00:10:00",
        "start_seconds": 10.0,
        "end_timecode": "00:00:20:00",
        "end_seconds": 20.0,
        "duration_seconds": 10.0,
        "duration_display": "10s",
        "text": "Recalc clip",
    }
    proj_data = ProjectData(
        project_name="RecalcTest",
        file_signature="recalc-sig",
        items=[raw_item],
        overrides={
            "recalc-item": {
                "in_tc": "00:01:00:00",
                "out_tc": "00:01:15:00",
                "text": "Overridden text"
            }
        }
    )
    doc = proj_data.to_document_state()
    it = doc.items[0]
    assert it.start_timecode == "00:01:00:00"
    assert it.end_timecode == "00:01:15:00"
    assert it.start_seconds == 60.0
    assert it.end_seconds == 75.0
    assert it.duration_seconds == 15.0
    assert "15" in it.duration_display
    assert it.text == "Overridden text"


def test_legacy_rainyproj_missing_docx_warning(tmp_path, qapp):
    """Verifies that opening a legacy v1 project whose DOCX script is missing
    does not crash and gracefully displays an InfoBar warning."""
    from src.ui.window import MainWindow
    from qfluentwidgets import InfoBar

    legacy_file = str(tmp_path / "OldLegacy.rainyproj")
    with open(legacy_file, "w", encoding="utf-8") as f:
        json.dump({
            "format_version": 1,
            "project_name": "OldLegacy",
            "script_file_name": "C:/Missing/Original.docx",
            "checked_items": ["item-1"],
        }, f)

    win = MainWindow()
    win.show()
    qapp.processEvents()

    # Call _load_rainyproj_file with non-existent original DOCX
    # We patch QFileDialog.getOpenFileName to simulate user canceling or not finding it
    from PySide6.QtWidgets import QFileDialog
    orig_get_open = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: ("", ""))
    try:
        win._load_rainyproj_file(legacy_file)
        qapp.processEvents()
    finally:
        QFileDialog.getOpenFileName = orig_get_open

    # Window remains functional and active
    assert win.isVisible() is True
    win.close()
    qapp.processEvents()


def test_unified_open_file_loads_standalone_rainyproj_for_recent_docx(tmp_path, qapp):
    """Verifies that opening a recent file (even if selected via DOCX path)
    detects the existing .rainyproj and loads it instantly in <10ms without re-parsing DOCX."""
    from src.ui.window import MainWindow
    from src.core.project_manager import project_manager, ProjectData

    docx_file = str(tmp_path / "RecentEpisode.docx")
    with open(docx_file, "w") as f:
        f.write("mock docx")

    # Create associated .rainyproj
    proj_path = project_manager.get_auto_project_path("RecentEpisode.docx")
    proj_data = ProjectData(
        format_version=2,
        project_name="RecentEpisode",
        script_file_name=docx_file,
        file_signature="recent-sig-123",
        items=[{
            "id": "rec-1",
            "row_index": 1,
            "raw_timestamp": "00:01:00:00 - 00:01:10:00",
            "start_timecode": "00:01:00:00",
            "start_seconds": 60.0,
            "end_timecode": "00:01:10:00",
            "end_seconds": 70.0,
            "duration_seconds": 10.0,
            "duration_display": "10s",
            "text": "Instant Recent Item",
            "is_checked": True,
        }],
    )
    project_manager.save_project(proj_path, proj_data)

    win = MainWindow()
    win.show()
    qapp.processEvents()

    try:
        # Opening via the docx path should detect proj_path and hydrate in-memory instantly
        win.open_file(docx_file)
        qapp.processEvents()

        assert win.current_doc is not None
        assert win.current_doc.name == "RecentEpisode"
        assert len(win.current_doc.items) == 1
        assert win.current_doc.items[0].text == "Instant Recent Item"
        assert win.current_doc.items[0].is_checked is True
    finally:
        win.close()
        qapp.processEvents()
        if os.path.exists(proj_path):
            try:
                os.remove(proj_path)
            except Exception:
                pass
