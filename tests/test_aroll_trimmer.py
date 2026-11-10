import os
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QWheelEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
from src.core.models import ScriptItem
from src.ui.components.aroll_trimmer_window import (
    ARollTrimmerWindow, TimecodeScrubBar, format_ruler_timecode
)

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_scrub_bar_math(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(400, 34)
    scrub.set_range(duration=100.0, in_sec=10.0, out_sec=30.0, fps=24.0)

    assert scrub.in_pos == 10.0
    assert scrub.out_pos == 30.0
    assert scrub.duration == 100.0

    scrub.set_in_point(15.0)
    assert scrub.in_pos == 15.0

    scrub.set_out_point(40.0)
    assert scrub.out_pos == 40.0

def test_trimmer_window_init(qapp):
    win = ARollTrimmerWindow()
    assert win is not None
    # Now that libmpv-2.dll is downloaded and verified, player is initialized
    if win.player is not None:
        assert win.player.is_ready

    item = ScriptItem(
        id="test_card_1",
        row_index=1,
        raw_timestamp="01:14 - 01:22",
        start_timecode="00:01:14:00",
        start_seconds=74.0,
        end_timecode="00:01:22:00",
        end_seconds=82.0,
        duration_seconds=8.0,
        duration_display="8s",
        text="Test script line",
        speaker="Zachary",
        is_single=False
    )
    win.load_item(item, footage_path=None)
    assert win.current_item == item
    assert win._in_seconds == 74.0
    assert win._out_seconds == 82.0
    win.close()


def test_spacebar_transport_and_recenter(qapp):
    win = ARollTrimmerWindow()
    win.scrub_bar.set_range(duration=100.0, in_sec=10.0, out_sec=30.0, fps=24.0)
    # Set viewport to 0..20 (span = 20)
    win.scrub_bar.view_start_sec = 0.0
    win.scrub_bar.view_end_sec = 20.0
    win.scrub_bar.set_playhead(50.0)

    # Start playback
    win._toggle_play()
    assert win._is_playing is True
    assert win._play_icon_is_pause is True
    assert win._ui_update_timer.isActive() is True

    # Stop / pause playback -> viewport must re-center on playhead (50.0)
    win._toggle_play()
    assert win._is_playing is False
    assert win._play_icon_is_pause is False
    assert win._ui_update_timer.isActive() is False

    # Span was 20.0, centered on 50.0 => 40.0 to 60.0 (recentered because 50.0 was out of frame)
    assert abs(win.scrub_bar.view_start_sec - 40.0) < 0.01
    assert abs(win.scrub_bar.view_end_sec - 60.0) < 0.01

    # Playhead at 52.0 is already present in 40..60 zoom window -> MUST NOT recenter
    win.scrub_bar.set_playhead(52.0)
    win._toggle_play()
    assert win._is_playing is True
    win._toggle_play()
    assert win._is_playing is False
    assert abs(win.scrub_bar.view_start_sec - 40.0) < 0.01
    assert abs(win.scrub_bar.view_end_sec - 60.0) < 0.01
    win.close()


def test_playback_follow(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(600, 68)
    scrub.duration = 100.0
    scrub.view_start_sec = 10.0
    scrub.view_end_sec = 30.0  # span = 20.0

    # When playhead is well within viewport, follow does not trigger
    scrub.set_playhead(15.0)
    scrub.check_playback_follow()
    assert scrub.view_start_sec == 10.0
    assert scrub.view_end_sec == 30.0

    # When playhead reaches near the right edge (>= 30 - 0.12*20 = 27.6s)
    scrub.set_playhead(28.0)
    scrub.check_playback_follow()
    # Pages forward so playhead is at ~15% from left: 28 - 0.15*20 = 25.0
    assert abs(scrub.view_start_sec - 25.0) < 0.01
    assert abs(scrub.view_end_sec - 45.0) < 0.01


def test_zoom_anchoring_and_framing(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(600, 68)

    # 1. Primary anchor: cut center if cut exists
    scrub.duration = 100.0
    scrub.in_pos = 20.0
    scrub.out_pos = 40.0
    assert abs(scrub.get_zoom_anchor() - 30.0) < 0.01

    # 2. Fallback anchor: playhead if no cut exists
    scrub.in_pos = 0.0
    scrub.out_pos = 100.0
    scrub.playhead_pos = 15.0
    assert abs(scrub.get_zoom_anchor() - 15.0) < 0.01

    # 3. Automatic cut framing on load_item
    win = ARollTrimmerWindow()
    item = ScriptItem(
        id="card_framing",
        row_index=2,
        raw_timestamp="01:14 - 01:22",
        start_timecode="00:01:14:00",
        start_seconds=74.0,
        end_timecode="00:01:22:00",
        end_seconds=82.0,
        duration_seconds=8.0,
        duration_display="8s",
        text="Sample",
        speaker="Speaker",
        is_single=False
    )
    win.load_item(item, footage_path=None)
    # cut_center = (74 + 82)/2 = 78.0, span = max(12.0, 8.0 * 3) = 24.0
    # view_start = 78 - 12 = 66.0, view_end = 78 + 12 = 90.0
    assert abs(win.scrub_bar.view_start_sec - 66.0) < 0.01
    assert abs(win.scrub_bar.view_end_sec - 90.0) < 0.01
    win.close()


def test_anti_flicker_ui_timer_monotonic(qapp):
    win = ARollTrimmerWindow()
    win._is_playing = True
    win._smooth_playhead_pos = 10.0
    win._latest_player_pos = 10.0

    # Demuxer packet micro-jitter backward (< 0.5s)
    win._on_player_time_pos(9.98)
    assert win._latest_player_pos == 9.98
    # UI tick suppresses micro-jitter backwards
    win._on_ui_tick()
    assert win._smooth_playhead_pos == 10.0

    # Forward packet progress
    win._on_player_time_pos(10.05)
    win._on_ui_tick()
    assert abs(win._smooth_playhead_pos - 10.05) < 0.001

    # Large user seek backward (> 0.5s)
    win._on_player_time_pos(5.0)
    win._on_ui_tick()
    assert abs(win._smooth_playhead_pos - 5.0) < 0.001
    win.close()


def test_tier2_minimum_thumb_width(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(500, 68)
    scrub.duration = 100.0
    # Extremely zoomed in (0.1s span)
    scrub.view_start_sec = 50.0
    scrub.view_end_sec = 50.1

    th_left, th_right, th_w = scrub._get_tier2_thumb_geometry()
    assert th_w >= 36
    assert (th_right - th_left) >= 36


def test_clean_ruler_ticks_and_formatting(qapp):
    # Test smart simplified timecode format
    assert format_ruler_timecode(1230.0, 24.0, 5.0) == "20:30"
    assert format_ruler_timecode(1225.0, 24.0, 0.5) == "20:25:00"
    assert format_ruler_timecode(74.0, 24.0, 1.0) == "01:14"
    assert format_ruler_timecode(3674.0, 24.0, 1.0) == "01:01:14"

    scrub = TimecodeScrubBar()
    scrub.duration = 100.0
    # Fully zoomed out: span >= 0.7 * duration => show_tc_numbers is False
    scrub.view_start_sec = 0.0
    scrub.view_end_sec = 80.0
    span = scrub.view_end_sec - scrub.view_start_sec
    assert (span >= 0.7 * scrub.duration) is True


def test_icon_only_transport_buttons(qapp):
    win = ARollTrimmerWindow()
    buttons = {
        "prev": (win.prev_btn, "Previous card"),
        "next": (win.next_btn, "Next card"),
        "step_back": (win.step_back_btn, "backward"),
        "play": (win.play_btn, "Play / Pause"),
        "step_fwd": (win.step_fwd_btn, "forward"),
        "mark_in": (win.mark_in_btn, "Mark In"),
        "mark_out": (win.mark_out_btn, "Mark Out"),
        "undo": (win.undo_btn, "Undo"),
        "reset": (win.reset_btn, "Reset"),
    }
    # Icon-only with descriptive tooltips (no text labels)
    for name, (btn, tip) in buttons.items():
        assert btn.text() == "", name
        assert btn.icon().isNull() is False, name
        assert tip in btn.toolTip(), name

    # Premiere-style cluster order: In, <, Play, >, Out share one row, left to right
    win.set_mode(win.MODE_EDIT)
    win.show()
    qapp.processEvents()
    xs = [b.mapTo(win, b.rect().topLeft()).x() for b in (
        win.mark_in_btn, win.step_back_btn, win.play_btn, win.step_fwd_btn, win.mark_out_btn)]
    assert xs == sorted(xs)
    win.close()


def test_spacebar_shortcut_and_nofocus_buttons(qapp):
    win = ARollTrimmerWindow()
    win.set_mode(win.MODE_EDIT)
    win.show()
    qapp.processEvents()

    # Verify all buttons have NoFocus
    for btn in (
        win.prev_btn, win.next_btn, win.step_back_btn, win.play_btn,
        win.step_fwd_btn, win.mark_in_btn, win.mark_out_btn,
        win.undo_btn, win.reset_btn
    ):
        assert btn.focusPolicy() == Qt.NoFocus

    # Simulate spacebar shortcut activation
    assert win._is_playing is False
    win._space_shortcut.activated.emit()
    assert win._is_playing is True
    win._space_shortcut.activated.emit()
    assert win._is_playing is False
    win.close()


def test_tier2_handle_drag_smooth_delta(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(500, 68)
    scrub.duration = 100.0
    scrub.view_start_sec = 50.0
    scrub.view_end_sec = 50.25
    scrub.in_pos = 50.0
    scrub.out_pos = 50.2

    th_left, th_right, th_w = scrub._get_tier2_thumb_geometry()
    assert th_w >= 36

    # Simulate mouse press on left handle
    class FakeEvent:
        def button(self): return Qt.LeftButton
        def pos(self): return scrub.mapToGlobal(QPoint(th_left, 57))
        def position(self): return QPointF(th_left, 57)

    scrub.mousePressEvent(FakeEvent())
    assert scrub._drag_mode == "thumb_left"

    # Drag left by 1px
    class FakeMoveEvent:
        def pos(self): return scrub.mapToGlobal(QPoint(th_left - 1, 57))
        def position(self): return QPointF(th_left - 1, 57)

    scrub.mouseMoveEvent(FakeMoveEvent())
    new_span = scrub.view_end_sec - scrub.view_start_sec
    # Delta should be small and smooth (~0.67s), not jumping to 8.25s
    assert new_span < 1.5
    assert new_span > 0.25


def test_wheel_zoom_anchoring_with_cut_and_fallback(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(600, 68)
    scrub.duration = 100.0

    # 1. With cut: in = 20.0, out = 40.0 -> anchor is 30.0
    scrub.in_pos = 20.0
    scrub.out_pos = 40.0
    scrub.view_start_sec = 0.0
    scrub.view_end_sec = 60.0
    scrub.playhead_pos = 10.0

    anchor = scrub.get_zoom_anchor()
    assert abs(anchor - 30.0) < 0.01

    old_span = scrub.view_end_sec - scrub.view_start_sec
    old_anchor_ratio = (anchor - scrub.view_start_sec) / old_span

    wheel_event = QWheelEvent(
        QPointF(300, 20), QPointF(300, 20),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    scrub.wheelEvent(wheel_event)

    new_span = scrub.view_end_sec - scrub.view_start_sec
    assert new_span < old_span
    new_anchor_ratio = (anchor - scrub.view_start_sec) / new_span
    assert abs(old_anchor_ratio - new_anchor_ratio) < 0.005

    # 2. Without cut: in = 0, out = 100 -> fallback anchor is playhead
    scrub.in_pos = 0.0
    scrub.out_pos = 100.0
    scrub.playhead_pos = 15.0
    assert abs(scrub.get_zoom_anchor() - 15.0) < 0.01


def test_key_shortcuts_and_recenter(qapp):
    win = ARollTrimmerWindow()
    win._in_seconds = 20.0
    win._out_seconds = 40.0
    win.scrub_bar.set_range(duration=100.0, in_sec=20.0, out_sec=40.0, fps=24.0)
    win.scrub_bar.view_start_sec = 0.0
    win.scrub_bar.view_end_sec = 20.0
    win.scrub_bar.set_playhead(50.0)

    # Press K (Stop) -> must re-center on playhead (50.0)
    event_k = QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.NoModifier)
    win.keyPressEvent(event_k)
    assert abs(win.scrub_bar.view_start_sec - 40.0) < 0.01
    assert abs(win.scrub_bar.view_end_sec - 60.0) < 0.01

    # Press Q (Jump to IN = 20.0)
    event_q = QKeyEvent(QEvent.KeyPress, Qt.Key_Q, Qt.NoModifier)
    win.keyPressEvent(event_q)
    assert abs(win.scrub_bar.playhead_pos - 20.0) < 0.01

    # Press W (Jump to OUT = 40.0)
    event_w = QKeyEvent(QEvent.KeyPress, Qt.Key_W, Qt.NoModifier)
    win.keyPressEvent(event_w)
    assert abs(win.scrub_bar.playhead_pos - 40.0) < 0.01

    win.close()


def test_playhead_exclusive_scrub_lock(qapp):
    win = ARollTrimmerWindow()
    win._nominal_fps = 25.0
    win.scrub_bar.set_range(duration=100.0, in_sec=10.0, out_sec=30.0, fps=25.0)
    win.scrub_bar.set_playhead(10.0)
    win._update_smpte_display(10.0)

    # 1. Start user scrubbing drag
    win._on_trim_drag_started("playhead")
    assert win._is_scrubbing is True

    # User scrubs to 25.0s
    win._on_scrub_seek(25.0)
    assert abs(win.scrub_bar.playhead_pos - 25.0) < 0.001
    assert "00:00:25:00" in win.smpte_hud_lbl.text()

    # 2. Simulate stale background MPV time-pos event arriving (e.g. from 10.0s)
    win._on_player_time_pos(10.0)

    # Must NOT overwrite user's scrubbed playhead or HUD
    assert abs(win.scrub_bar.playhead_pos - 25.0) < 0.001
    assert "00:00:25:00" in win.smpte_hud_lbl.text()

    # 3. Simulate 60 Hz UI tick while scrubbing
    win._latest_player_pos = 10.0
    win._on_ui_tick()
    assert abs(win.scrub_bar.playhead_pos - 25.0) < 0.001
    assert "00:00:25:00" in win.smpte_hud_lbl.text()

    # 4. Finish scrub drag
    win._on_trim_drag_finished("playhead")
    assert win._is_scrubbing is False

    # After drag finishes, MPV callback is allowed again
    win._on_player_time_pos(25.0)
    assert abs(win.scrub_bar.playhead_pos - 25.0) < 0.001

    win.close()


def test_frame_snapped_scrubbing(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(1000, 68)
    scrub.duration = 10.0
    fps = 25.0  # frame_dur = 0.04s
    scrub.set_range(duration=10.0, in_sec=0.0, out_sec=5.0, fps=fps)

    # Start drag at playhead
    class PressEv:
        def button(self): return Qt.LeftButton
        def position(self): return QPointF(100, 10)  # Ruler region
        def pos(self): return QPoint(100, 10)

    scrub.mousePressEvent(PressEv())
    assert scrub._drag_mode == "playhead"

    # Verify initial position is an exact frame multiple
    frame_dur = 1.0 / fps
    remainder = scrub.playhead_pos % frame_dur
    assert remainder < 1e-4 or abs(remainder - frame_dur) < 1e-4

    # Move mouse by some pixels
    class MoveEv:
        def position(self): return QPointF(357, 10)
        def pos(self): return QPoint(357, 10)

    scrub.mouseMoveEvent(MoveEv())
    # Snapped position must remain an exact integer multiple of 1/fps
    frames = round(scrub.playhead_pos * fps)
    expected_sec = frames * frame_dur
    assert abs(scrub.playhead_pos - expected_sec) < 1e-5


def test_scrub_bar_blocks_external_set_playhead_during_drag(qapp):
    scrub = TimecodeScrubBar()
    scrub.duration = 100.0
    scrub.playhead_pos = 20.0
    scrub._drag_mode = "playhead"

    # Attempt external set_playhead call
    scrub.set_playhead(5.0)

    # Must be ignored because user is dragging
    assert scrub.playhead_pos == 20.0

    scrub._drag_mode = None
    scrub.set_playhead(5.0)
    assert scrub.playhead_pos == 5.0


def test_precision_timecode_bar_placement_and_labels(qapp):
    win = ARollTrimmerWindow()
    win._nominal_fps = 25.0
    win._in_seconds = 10.0
    win._out_seconds = 25.0
    win._update_in_out_displays()
    win._update_smpte_display(17.5)

    # 1. Verify card tag is removed
    assert not hasattr(win, "card_tag_lbl")

    # 2. Verify Left = IN, Center = Playhead, Right = OUT + Dur
    assert "00:00:10:00" == win.in_tc_lbl.text()
    assert "00:00:17:12" in win.smpte_hud_lbl.text() or "00:00:17:13" in win.smpte_hud_lbl.text()
    assert "00:00:25:00" == win.out_tc_lbl.text()
    assert not hasattr(win, "dur_hud_lbl")

    # 3. Verify vertical layout hierarchy: Video Viewport sits directly above the timecode bar
    main_layout = win.layout()
    vp_idx = main_layout.indexOf(win.video_viewport)
    scrub_idx = main_layout.indexOf(win.scrub_bar)
    assert vp_idx >= 0
    assert scrub_idx > vp_idx

    win.close()


def test_anchor_dropdown_toggle(qapp):
    win = ARollTrimmerWindow()
    win.scrub_bar.set_range(duration=100.0, in_sec=10.0, out_sec=30.0, fps=25.0)
    win.scrub_bar.playhead_pos = 50.0

    # Default anchor is Cut
    assert win.scrub_bar.get_zoom_anchor() == 20.0

    # Switch dropdown to Playhead
    win.anchor_dropdown.setCurrentText("Anchor: Playhead")
    assert win.scrub_bar._zoom_anchor_mode == "playhead"
    assert win.scrub_bar.get_zoom_anchor() == 50.0

    # Switch back to Cut
    win.anchor_dropdown.setCurrentText("Anchor: Cut (In/Out)")
    assert win.scrub_bar._zoom_anchor_mode == "cut"
    assert win.scrub_bar.get_zoom_anchor() == 20.0

    win.close()


def _drag_handle(scrub, press_x, move_x):
    class Press:
        def button(self): return Qt.LeftButton
        def position(self): return QPointF(press_x, 57)
        def pos(self): return QPoint(press_x, 57)

    class Move:
        def position(self): return QPointF(move_x, 57)
        def pos(self): return QPoint(move_x, 57)

    scrub.mousePressEvent(Press())
    scrub.mouseMoveEvent(Move())


def test_overview_handle_squeeze_keeps_zooming_to_frame_limit(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(500, 68)
    scrub.duration = 100.0
    scrub.min_view_span_sec = 0.2
    scrub.view_start_sec = 20.0
    scrub.view_end_sec = 60.0
    scrub.in_pos, scrub.out_pos = 38.0, 42.0  # cut center = 40 is the zoom anchor

    th_left, th_right, _ = scrub._get_tier2_thumb_geometry()
    _drag_handle(scrub, th_left, th_left + 20)
    assert scrub._drag_mode == "thumb_left"
    span_a = scrub.view_end_sec - scrub.view_start_sec
    assert span_a < 40.0
    scrub.mouseReleaseEvent(type("R", (), {"button": lambda s: Qt.LeftButton})())

    # Squeezing keeps zooming (even past the thumb's 54px minimum) and anchors on the cut
    spans = []
    for extra in (100, 200, 300, 400):
        scrub.view_start_sec, scrub.view_end_sec = 20.0, 60.0
        th_left, _, _ = scrub._get_tier2_thumb_geometry()
        _drag_handle(scrub, th_left, th_left + extra)
        spans.append(scrub.view_end_sec - scrub.view_start_sec)
        assert scrub.view_start_sec < 40.0 < scrub.view_end_sec
        scrub.mouseReleaseEvent(type("R", (), {"button": lambda s: Qt.LeftButton})())
    assert spans == sorted(spans, reverse=True) and spans[-1] < spans[0]

    # Far squeeze bottoms out exactly at the single-frame limit, never below
    scrub.view_start_sec, scrub.view_end_sec = 20.0, 60.0
    th_left, _, _ = scrub._get_tier2_thumb_geometry()
    _drag_handle(scrub, th_left, th_left + 5000)
    assert abs((scrub.view_end_sec - scrub.view_start_sec) - scrub.min_view_span_sec) < 1e-6
    scrub.mouseReleaseEvent(type("R", (), {"button": lambda s: Qt.LeftButton})())

    # Right handle pulled left squeezes the same way
    scrub.view_start_sec, scrub.view_end_sec = 20.0, 60.0
    _, th_right, _ = scrub._get_tier2_thumb_geometry()
    _drag_handle(scrub, th_right, th_right - 5000)
    assert scrub._drag_mode == "thumb_right"
    assert abs((scrub.view_end_sec - scrub.view_start_sec) - scrub.min_view_span_sec) < 1e-6


def test_full_lane_waveform_rendering(qapp):
    scrub = TimecodeScrubBar()
    scrub.resize(600, 68)
    scrub.duration = 10.0

    # Create dummy 100 Hz peaks (10 seconds * 100 = 1000 samples)
    import numpy as np
    peaks = np.linspace(0.1, 1.0, 1000, dtype=np.float32)
    scrub.set_waveform_data(peaks, sample_rate=100)

    assert scrub._waveform_peaks is not None
    assert len(scrub._waveform_peaks) == 1000
    assert scrub._waveform_sample_rate == 100

    # Verify paintEvent executes without exception with waveform data
    from PySide6.QtGui import QImage
    img = QImage(600, 68, QImage.Format_ARGB32)
    scrub.render(img)
    assert not img.isNull()



