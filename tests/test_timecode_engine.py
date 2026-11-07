import pytest
import os
from PySide6.QtWidgets import QApplication
from src.core.timecode_engine import (
    parse_timestamp_seconds,
    analyze_framerate,
    smpte_to_frames,
    seconds_to_frames,
    frames_to_smpte,
    conform_timestamp_for_clip
)
from src.core.database import db


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_parse_timestamp_seconds():
    # MM:SS formats
    assert parse_timestamp_seconds("15:30") == 930.0
    assert parse_timestamp_seconds("00:45") == 45.0
    assert parse_timestamp_seconds("0:05") == 5.0
    assert parse_timestamp_seconds("15:30.500") == 930.5

    # HH:MM:SS formats
    assert parse_timestamp_seconds("01:15:30") == 4530.0
    assert parse_timestamp_seconds("00:01:00") == 60.0

    # HH:MM:SS:FF (assuming 24fps base)
    assert parse_timestamp_seconds("00:00:01:00") == 1.0
    assert parse_timestamp_seconds("00:00:01:12") == 1.5

    # Float and integer seconds
    assert parse_timestamp_seconds("930") == 930.0
    assert parse_timestamp_seconds("930.5") == 930.5

    # Regex text patterns (e.g. 15m 30s)
    assert parse_timestamp_seconds("15m 30s") == 930.0
    assert parse_timestamp_seconds("15min") == 900.0

    # Empty / invalid
    assert parse_timestamp_seconds("") == 0.0
    assert parse_timestamp_seconds(None) == 0.0
    assert parse_timestamp_seconds("invalid") == 0.0


def test_analyze_framerate_standards():
    # 23.976 NTSC
    exact, nom, is_vfr, is_std = analyze_framerate(23.976, 23.976)
    assert exact == 23.976
    assert nom == 24
    assert not is_vfr
    assert is_std

    # 24.0 Cinema
    exact, nom, is_vfr, is_std = analyze_framerate(24.0, 24.0)
    assert exact == 24.0
    assert nom == 24
    assert not is_vfr
    assert is_std

    # 25.0 PAL
    exact, nom, is_vfr, is_std = analyze_framerate(25.0, 25.0)
    assert exact == 25.0
    assert nom == 25
    assert not is_vfr
    assert is_std

    # 29.97 NTSC
    exact, nom, is_vfr, is_std = analyze_framerate(29.97, 29.97)
    assert exact == 29.97
    assert nom == 30
    assert not is_vfr
    assert is_std

    # 59.94 NTSC
    exact, nom, is_vfr, is_std = analyze_framerate(59.94, 59.94)
    assert exact == 59.94
    assert nom == 60
    assert not is_vfr
    assert is_std


def test_analyze_framerate_vfr_detection():
    # Phone recording with varying framerate: r_fps=30.0, avg_fps=28.45
    exact, nom, is_vfr, is_std = analyze_framerate(30.0, 28.45)
    assert is_vfr is True

    # Slight clock jitter <= 0.08 should NOT trigger false positive VFR
    exact, nom, is_vfr, is_std = analyze_framerate(23.976, 23.978)
    assert is_vfr is False


def test_seconds_to_frames_mathematical_precision():
    # Exactly 1 second at 24000/1001 (23.9760239...)
    frames_1s = seconds_to_frames(1.0, 23.976, 24)
    assert frames_1s == 24

    # 1001 seconds at 23.976 should equal exactly 24,000 frames
    frames_1001s = seconds_to_frames(1001.0, 23.976, 24)
    assert frames_1001s == 24000

    # 1001 seconds at 29.97 should equal exactly 30,000 frames
    frames_1001s_29 = seconds_to_frames(1001.0, 29.97, 30)
    assert frames_1001s_29 == 30000


def test_frames_to_smpte_ndf():
    # Premiere Pro standard Non-Drop Frame (HH:MM:SS:FF)
    assert frames_to_smpte(0, 24) == "00:00:00:00"
    assert frames_to_smpte(24, 24) == "00:00:01:00"
    assert frames_to_smpte(35, 24) == "00:00:01:11"
    assert frames_to_smpte(24 * 60, 24) == "00:01:00:00"
    assert frames_to_smpte(24 * 3600, 24) == "01:00:00:00"


def test_frames_to_smpte_drop_frame():
    # SMPTE 12M Drop Frame for 29.97 fps (drops :00 and :01 each minute except every 10th)
    assert frames_to_smpte(0, 30, drop_frame=True) == "00:00:00;00"
    assert frames_to_smpte(1799, 30, drop_frame=True) == "00:00:59;29"
    # Minute 1 skips :00 and :01, landing frame 1800 on :02
    assert frames_to_smpte(1800, 30, drop_frame=True) == "00:01:00;02"


def test_conform_timestamp_for_clip_pts_to_smpte():
    # 24.0 fps CFR: 930s (15:30) is exactly 00:15:30:00
    smpte_24, meta_24 = conform_timestamp_for_clip(
        "15:30",
        probed_fps=24.0,
        container_start_time=0.0
    )
    assert smpte_24 == "00:15:30:00"
    assert meta_24["raw_seconds"] == 930.0
    assert meta_24["exact_fps"] == 24.0
    assert meta_24["nominal_fps"] == 24

    # 23.976 fps NDF: 930 real-world seconds (PTS) converts mathematically to 22298 frames -> 00:15:29:02
    smpte_23, meta_23 = conform_timestamp_for_clip(
        "15:30",
        probed_fps=23.976,
        container_start_time=0.0
    )
    assert smpte_23 == "00:15:29:02"
    assert meta_23["exact_fps"] == 23.976
    assert meta_23["nominal_fps"] == 24
    assert meta_23["is_vfr"] is False


def test_conform_timestamp_audio_preroll_drift_compensation():
    # THE 4-SECOND DRIFT MYSTERY SOLVED:
    # A bodycam / recorder file has container start_time = 4.380s (audio pre-roll multiplex delay).
    # Google Drive plays from 0:00 (PTS elapsed).
    # In Premiere Pro, the clip starts at container time 0.000, so 15:30 lands 4.380s later!
    # At 24.0 fps CFR: 930.0s + 4.380s = 934.380s -> 00:15:34:09
    smpte_24, meta_24 = conform_timestamp_for_clip(
        "15:30",
        probed_fps=24.0,
        container_start_time=4.380
    )
    assert smpte_24 == "00:15:34:09"
    assert meta_24["start_time_offset"] == 4.380
    assert meta_24["compensated_seconds"] == 934.380

    # At 23.976 fps: 934.380s conformed into 24fps NDF timecode -> 00:15:33:11
    smpte_23, meta_23 = conform_timestamp_for_clip(
        "15:30",
        probed_fps=23.976,
        container_start_time=4.380
    )
    assert smpte_23 == "00:15:33:11"


def test_conform_timestamp_with_embedded_timecode():
    # Broadcast file with camera timecode track starting at 01:00:00:00
    smpte_24, meta_24 = conform_timestamp_for_clip(
        "15:30",
        probed_fps=24.0,
        container_start_time=4.380,
        embedded_timecode="01:00:00:00"
    )
    assert smpte_24 == "01:15:34:09"
    assert meta_24["embedded_timecode"] == "01:00:00:00"


def test_database_probe_persistence():
    test_path = "D:\\MockFootage\\interrogation_cam1.mp4"
    db.store_local_media_probe(
        file_path=test_path,
        file_size=104857600,
        mtime=1700000000.0,
        duration_seconds=3600.0,
        duration_display="1h 00m",
        width=1920,
        height=1080,
        resolution="1080p",
        frame_rate=23.976,
        audio_channels=2,
        sample_rate=48000,
        video_codec="h264",
        audio_codec="aac",
        start_time=4.380,
        start_timecode="01:00:00:00",
        is_vfr=False,
        avg_frame_rate=23.976
    )

    cached = db.get_local_media_probe(test_path, 104857600, 1700000000.0)
    assert cached is not None
    assert cached["file_path"] == test_path
    assert cached["frame_rate"] == 23.976
    assert cached["start_time"] == 4.380
    assert cached["start_timecode"] == "01:00:00:00"
    assert cached["is_vfr"] == 0
    assert cached["avg_frame_rate"] == 23.976


def test_card_conforming_linked_vs_unlinked(qapp, tmp_path):
    from src.core.models import ScriptItem
    from src.ui.components.timeline_card import TimelineCueCard
    from src.ui.components.preferences_dialog import PreferencesDialog

    item = ScriptItem(
        id="item-test-1",
        row_index=1,
        raw_timestamp="20:52 - 21:07",
        start_timecode="20:52",
        start_seconds=1252.0,
        end_timecode="21:07",
        end_seconds=1267.0,
        duration_seconds=15.0,
        duration_display="15s",
        text="Sample interview cue",
        sync_id=1,
        links=["https://drive.google.com/file/d/1ABCXYZ_mock_link/view"]
    )

    card = TimelineCueCard(item)

    # 1. When NO footage is linked and auto-conform is ON:
    db.set_setting("auto_conform_smpte", "true")
    card.set_linked_footage({})
    card.update_displayed_timecodes()
    assert card.start_btn.text() == "20:52"
    assert card.end_btn.text() == "21:07"
    tc, feedback = card.get_conformed_timecode(item.start_timecode)
    assert tc == "20:52"

    # 2. When footage IS linked:
    fake_video = str(tmp_path / "mock_interview.mp4")
    with open(fake_video, "wb") as f:
        f.write(b"0" * 1024)
    stat = os.stat(fake_video)
    db.store_local_media_probe(
        file_path=fake_video,
        file_size=stat.st_size,
        mtime=stat.st_mtime,
        duration_seconds=3600.0,
        duration_display="1h",
        width=1920,
        height=1080,
        resolution="1080p",
        frame_rate=59.94,
        audio_channels=2,
        sample_rate=48000,
        video_codec="h264",
        audio_codec="aac",
        start_time=0.0,
        start_timecode=None,
        is_vfr=False,
        avg_frame_rate=59.94
    )

    card.set_linked_footage({"https://drive.google.com/file/d/1ABCXYZ_mock_link/view": fake_video})
    card.update_displayed_timecodes()
    # At 59.94 fps, 20:52 (1252s) conforms to 00:20:50:45, and 21:07 (1267s) conforms to 00:21:05:44
    assert card.start_btn.text() == "00:20:50:45"
    assert card.end_btn.text() == "00:21:05:44"
    tc, feedback = card.get_conformed_timecode(item.start_timecode)
    assert tc == "00:20:50:45"

    # 3. Toggle auto-conform OFF in preferences:
    pref_dlg = PreferencesDialog()
    signals_received = []
    pref_dlg.smpte_conforming_changed.connect(lambda chk: signals_received.append(chk))
    pref_dlg.smpte_switch.setChecked(False)

    assert len(signals_received) == 1
    assert signals_received[0] is False
    assert db.get_setting("auto_conform_smpte") == "false"

    # Refresh card (simulating workspace_view.refresh_timecodes)
    card.update_displayed_timecodes()
    assert card.start_btn.text() == "20:52"
    assert card.end_btn.text() == "21:07"
    tc, feedback = card.get_conformed_timecode(item.start_timecode)
    assert tc == "20:52"

    # 4. Toggle auto-conform back ON:
    pref_dlg.smpte_switch.setChecked(True)
    assert signals_received[-1] is True
    assert db.get_setting("auto_conform_smpte") == "true"

    card.update_displayed_timecodes()
    assert card.start_btn.text() == "00:20:50:45"
    assert card.end_btn.text() == "00:21:05:44"

    # Restore default setting
    db.set_setting("auto_conform_smpte", "true")
    pref_dlg.close()
    card.close()

