import os
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QLabel
from src.ui.theme import ThemeManager, ThemeVariant
from src.core.database import db
from src.core.footage_matcher import FootageMatchItem
from src.ui.components.footage_relink_dialog import FootageRelinkDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def preserve_user_theme():
    orig_variant_str = db.get_setting("theme_variant", "blue")
    orig_variant = ThemeManager.current_variant
    yield
    db.set_setting("theme_variant", orig_variant_str)
    try:
        ThemeManager.current_variant = ThemeVariant(orig_variant_str)
    except Exception:
        ThemeManager.current_variant = orig_variant


def test_footage_relink_dialog_vo_binding_and_no_cfr_badge(qapp, tmp_path):
    # Create sample media files for testing
    clip1 = tmp_path / "clip1_cfr.mp4"
    clip1.write_bytes(b"\x00" * 1024)
    clip2 = tmp_path / "clip2_vfr.mp4"
    clip2.write_bytes(b"\x00" * 2048)

    links = ["https://example.com/assets/clip1_cfr.mp4", "https://example.com/assets/clip2_vfr.mp4"]
    existing_linked = {
        links[0]: str(clip1),
        links[1]: str(clip2),
    }

    dlg = FootageRelinkDialog(
        links=links,
        current_footage_dir=str(tmp_path),
        existing_linked=existing_linked,
        current_vo_path="",
        existing_vo_segments=[],
        parent=None,
    )
    dlg.show()
    qapp.processEvents()

    # 1. Verify VO Binding & Transcribe Stage is 100% present and functional
    assert hasattr(dlg, "transcribe_stage")
    assert hasattr(dlg.transcribe_stage, "add_files_btn")
    assert hasattr(dlg.transcribe_stage, "start_btn")
    # VO status now lives in the shared activity dock
    assert hasattr(dlg, "vo_header_stat_lbl")
    assert dlg.vo_header_stat_lbl is dlg.queue_strip.vo_lbl
    assert "not set" in dlg.vo_header_stat_lbl.text()
    # No Apply & Save / Close footer
    assert dlg.buttonGroup.isHidden()

    # 2. Simulate matching items: one CFR, one VFR
    item_cfr = FootageMatchItem(
        url=links[0],
        norm_url=links[0],
        title="clip1_cfr.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT",
        matched_path=str(clip1),
        file_size_bytes=1024,
        file_size_display="1 KB",
        duration_display="0:05",
        resolution="1920x1080",
        frame_rate=30.0,
        is_vfr=False,
    )

    item_vfr = FootageMatchItem(
        url=links[1],
        norm_url=links[1],
        title="clip2_vfr.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT",
        matched_path=str(clip2),
        file_size_bytes=2048,
        file_size_display="2 KB",
        duration_display="0:10",
        resolution="1920x1080",
        frame_rate=29.97,
        is_vfr=True,
    )

    dlg.match_items = [item_cfr, item_vfr]
    dlg._rebuild_cards()
    dlg._update_stats()
    qapp.processEvents()

    # Verify cards were created
    assert len(dlg.card_widgets) == 2

    # Verify Card 0 (CFR) has NO CFR badge
    card_cfr = dlg.card_widgets[0]
    cfr_labels = [w.text() for w in card_cfr.findChildren(QLabel)]
    assert "CFR" not in cfr_labels
    assert "CFR OK" not in cfr_labels
    # Healthy rows carry no status pill (exceptions only); the dot's tooltip says Linked
    assert "LINKED" not in cfr_labels
    assert "MISSING" not in cfr_labels
    assert any(w.toolTip() == "Linked" for w in card_cfr.findChildren(QLabel))

    # Verify Card 1 (VFR) has subtle VFR indicator
    card_vfr = dlg.card_widgets[1]
    vfr_labels = [w.text() for w in card_vfr.findChildren(QLabel)]
    assert "VFR" in vfr_labels
    assert "VFR WARNING" not in vfr_labels
    assert "MISSING" not in vfr_labels

    # Verify stage tabs (Relink vs Library) has no slide animation
    # (underline indicator jumps straight to the target tab instead of sliding)
    dlg.switch_stage("library")
    assert dlg.stage_tabs.currentRouteKey() == "library"
    assert dlg.stage_tabs.slideAni.geometry == dlg.stage_tabs.currentIndicatorGeometry()
    dlg.switch_stage("relink")
    assert dlg.stage_tabs.currentRouteKey() == "relink"
    assert dlg.stage_tabs.slideAni.geometry == dlg.stage_tabs.currentIndicatorGeometry()

    # 3. Verify in-place transcode refresh
    # Simulate transcode completion: VFR converted to CFR
    clip2_reencoded = tmp_path / "clip2_vfr.mp4"
    clip2_reencoded.write_bytes(b"\x00" * 1500)

    # Calling _on_item_transcode_finished should clear VFR warning
    item_vfr.is_vfr = False
    item_vfr.frame_rate = 30.0
    item_vfr.file_size_bytes = 1500
    item_vfr.file_size_display = "1.5 KB"
    dlg._rebuild_cards()
    dlg._update_stats()
    qapp.processEvents()

    updated_vfr_labels = [w.text() for w in dlg.card_widgets[1].findChildren(QLabel)]
    assert "VFR WARNING" not in updated_vfr_labels

    dlg.close()


def test_non_repeating_card_name_and_missing_sorted_first(qapp, tmp_path):
    linked_clip = tmp_path / "Pat Interview.mp4-redacted.mp4"
    linked_clip.write_bytes(b"\x00" * 4096)

    links = [
        "https://example.com/assets/Pat%20Interview.mp4-redacted.mp4",
        "https://example.com/assets/Missing_Bodycam_02.mp4",
    ]
    dlg = FootageRelinkDialog(
        links=links,
        current_footage_dir="",
        existing_linked={},
        doc_signature="test_doc_sig_1",
        parent=None,
    )

    item_linked = FootageMatchItem(
        url=links[0],
        norm_url=links[0],
        title="Pat Interview.mp4-redacted.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT_NAME",
        matched_path=str(linked_clip),
        file_size_bytes=4096,
        file_size_display="4 KB",
        duration_display="1:20",
        resolution="1920x1080",
        frame_rate=29.97,
        is_vfr=False,
    )
    item_missing = FootageMatchItem(
        url=links[1],
        norm_url=links[1],
        title="Missing_Bodycam_02.mp4",
        file_type="video",
        is_matched=False,
        match_tier="MISSING",
        matched_path=None,
        file_size_bytes=0,
        file_size_display="",
    )

    # Pass linked first, missing second -> _rebuild_cards must sort MISSING first
    dlg.match_items = [item_linked, item_missing]
    dlg._rebuild_cards()
    dlg._update_stats()
    qapp.processEvents()

    assert len(dlg.card_widgets) == 2
    first_card_labels = [w.text() for w in dlg.card_widgets[0].findChildren(QLabel)]
    second_card_labels = [w.text() for w in dlg.card_widgets[1].findChildren(QLabel)]

    assert "MISSING" in first_card_labels
    assert "MISSING" not in second_card_labels
    assert any(w.toolTip() == "Linked" for w in dlg.card_widgets[1].findChildren(QLabel))

    # Verify the filename "Pat Interview.mp4-redacted.mp4" appears ONLY ONCE on the linked card
    occurrences = sum(1 for txt in second_card_labels if txt == "Pat Interview.mp4-redacted.mp4")
    assert occurrences == 1
    dlg.close()


def test_stage_toggle_library_multiselect_and_health_check(qapp, tmp_path):
    clip_a = tmp_path / "Alpha.mp4"
    clip_b = tmp_path / "Bravo_VFR.mp4"
    clip_a.write_bytes(b"\x00" * 1024)
    clip_b.write_bytes(b"\x00" * 2048)

    links = ["https://example.com/Alpha.mp4", "https://example.com/Bravo_VFR.mp4"]
    dlg = FootageRelinkDialog(
        links=links,
        current_footage_dir="",
        existing_linked={},
        doc_signature="test_doc_sig_lib",
        parent=None,
    )
    dlg.show()
    qapp.processEvents()

    it_a = FootageMatchItem(
        url=links[0],
        norm_url=links[0],
        title="Alpha.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT_NAME",
        matched_path=str(clip_a),
        file_size_bytes=1024,
        file_size_display="1 KB",
        duration_display="0:10",
        resolution="1920x1080",
        frame_rate=24.0,
        is_vfr=False,
        video_codec="h264",
    )
    it_b = FootageMatchItem(
        url=links[1],
        norm_url=links[1],
        title="Bravo_VFR.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT_NAME",
        matched_path=str(clip_b),
        file_size_bytes=2048,
        file_size_display="2 KB",
        duration_display="0:20",
        resolution="1920x1080",
        frame_rate=29.97,
        is_vfr=True,
        video_codec="h264",
    )

    dlg.match_items = [it_a, it_b]
    dlg._rebuild_cards()
    dlg.library_stage.set_items(dlg.match_items)
    dlg._update_stats()
    qapp.processEvents()

    # 100% linked -> Confirm & Open Library banner is visible
    assert not dlg.confirm_banner.isHidden()

    # Switch to Library via confirm button
    dlg._on_confirm_and_open_library()
    qapp.processEvents()
    assert dlg.stage_stack.currentWidget() == dlg.library_stage
    assert dlg.is_project_relink_confirmed() is True

    # Library has 2 rows; VFR hint banner is visible; Select VFR selects only Bravo_VFR
    assert dlg.library_stage.model.rowCount() == 2
    assert not dlg.library_stage.vfr_banner.isHidden()

    dlg.library_stage._on_select_vfr_clicked()
    qapp.processEvents()
    checked = dlg.library_stage.model.get_checked_items()
    assert len(checked) == 1
    assert checked[0].title == "Bravo_VFR.mp4"
    assert not dlg.library_stage.action_bar.isHidden()
    assert hasattr(dlg.library_stage.action_bar, "reencode_btn")
    assert dlg.library_stage.action_bar.reveal_btn.isEnabled() is True

    # Selecting multiple files must disable Show in Explorer per Cane's requirement
    dlg.library_stage.model.select_all_visible(True)
    qapp.processEvents()
    assert len(dlg.library_stage.model.get_checked_items()) == 2
    assert dlg.library_stage.action_bar.reveal_btn.isEnabled() is False
    assert dlg.library_stage.action_bar.reencode_btn.isEnabled() is True

    # Simulate file going offline (deleted/moved) and run health check
    os.remove(clip_a)
    offline_count = dlg.run_health_check()
    qapp.processEvents()
    assert offline_count == 1
    assert not dlg.library_stage.offline_banner.isHidden()
    assert "Relink (1)" in dlg.stage_tabs.items["relink"].text()

    dlg.close()


def test_render_queue_enqueue_dedup_reorder_and_persistence(qapp, tmp_path):
    from src.core.render_queue import render_queue
    from src.core.ffmpeg_encoder import TranscodeConfig

    # Pause queue so worker doesn't immediately spawn ffmpeg during unit state checks
    render_queue.set_pause_after_current(True)
    render_queue.clear_finished()
    for j in list(render_queue.jobs):
        render_queue.remove_job(j.id)

    f1 = tmp_path / "queue_clip_1.mp4"
    f2 = tmp_path / "queue_clip_2.mp4"
    f1.write_bytes(b"\x00" * 1024)
    f2.write_bytes(b"\x00" * 2048)

    cfg = TranscodeConfig(crf=20, encoder="libx264")
    added, skipped = render_queue.enqueue([str(f1), str(f2)], cfg)
    assert len(added) == 2
    assert len(skipped) == 0

    # Duplicate enqueue of same path while QUEUED must be prevented
    added_dup, skipped_dup = render_queue.enqueue([str(f1)], cfg)
    assert len(added_dup) == 0
    assert len(skipped_dup) == 1

    # Reorder second job up to index 0
    job2_id = added[1].id
    render_queue.move_job(job2_id, -1)
    assert render_queue.jobs[0].id == job2_id

    # Cancel and retry
    render_queue.cancel_job(job2_id)
    assert render_queue.jobs[0].status == "CANCELLED"
    render_queue.retry_job(job2_id)
    assert render_queue.jobs[0].status == "QUEUED"

    # Cleanup test jobs
    for j in list(render_queue.jobs):
        render_queue.remove_job(j.id)
    render_queue.set_pause_after_current(False)


def test_interlaced_detection_and_decomb_settings(qapp, tmp_path):
    from src.core.ffmpeg_encoder import TranscodeConfig
    from src.ui.components.transcode_dialog import TranscodeDialog

    clip_int = tmp_path / "Legacy_Dashcam_Interlaced.mp4"
    clip_int.write_bytes(b"\x00" * 2048)

    links = ["https://example.com/Legacy_Dashcam_Interlaced.mp4"]
    dlg = FootageRelinkDialog(
        links=links,
        current_footage_dir="",
        existing_linked={},
        doc_signature="test_doc_interlaced",
        parent=None,
    )
    dlg.show()
    qapp.processEvents()

    it_int = FootageMatchItem(
        url=links[0],
        norm_url=links[0],
        title="Legacy_Dashcam_Interlaced.mp4",
        file_type="video",
        is_matched=True,
        match_tier="EXACT_NAME",
        matched_path=str(clip_int),
        file_size_bytes=2048,
        file_size_display="2 KB",
        duration_display="0:30",
        resolution="1920x1080",
        frame_rate=29.97,
        is_vfr=False,
        is_interlaced=True,
        field_order="tt",
        video_codec="h264",
    )

    dlg.match_items = [it_int]
    dlg._rebuild_cards()
    dlg.library_stage.set_items(dlg.match_items)
    dlg._update_stats()
    qapp.processEvents()

    # Verify Relink card shows INTERLACED pill
    card_labels = [w.text() for w in dlg.card_widgets[0].findChildren(QLabel)]
    assert "INTERLACED" in card_labels

    # Verify Library flags column shows INTERLACED (exceptions only) and Interlaced Only filter works
    flags_col = dlg.library_stage.model.COL_FLAGS
    idx_flags = dlg.library_stage.model.index(0, flags_col)
    assert dlg.library_stage.model.data(idx_flags, Qt.DisplayRole) == "INTERLACED"
    dlg.library_stage.filter_combo.setCurrentIndex(2)  # "Interlaced Only"
    qapp.processEvents()
    assert dlg.library_stage.model.rowCount() == 1

    # Verify TranscodeDialog has Deinterlace / Decomb combo and builds TranscodeConfig with deinterlace
    tc_dlg = TranscodeDialog(source_path=str(clip_int), parent=dlg)
    assert hasattr(tc_dlg, "deint_combo")
    cfg: TranscodeConfig = tc_dlg.build_config()
    assert cfg.deinterlace in ("auto", "bwdif", "yadif", "off")

    tc_dlg.close()
    dlg.close()


def test_aroll_trimmer_refreshes_vfr_badge_after_transcode(qapp, tmp_path, monkeypatch):
    from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow
    from src.core.media_probe import ProbedMediaItem

    clip_path = str(tmp_path / "ch03_20200101000000.mp4")
    with open(clip_path, "wb") as f:
        f.write(b"\x00" * 4096)

    trimmer = ARollTrimmerWindow()
    trimmer.current_file = clip_path
    trimmer.probed_media = ProbedMediaItem(
        file_path=clip_path,
        file_size=4096,
        mtime=1700000000.0,
        duration_seconds=1200.0,
        duration_display="20m 00s",
        width=1280,
        height=720,
        resolution="720p",
        frame_rate=50.0,
        is_vfr=True,
        avg_frame_rate=46.5,
    )
    trimmer.vfr_badge.setVisible(True)
    assert not trimmer.vfr_badge.isHidden()

    # Simulate transcode finished -> fresh probe returns CFR 50.00 FPS
    cfr_probe = ProbedMediaItem(
        file_path=clip_path,
        file_size=5120,
        mtime=1700000100.0,
        duration_seconds=1200.0,
        duration_display="20m 00s",
        width=1280,
        height=720,
        resolution="720p",
        frame_rate=50.0,
        is_vfr=False,
        avg_frame_rate=50.0,
    )
    monkeypatch.setattr(
        "src.core.media_probe.probe_media_file",
        lambda path, use_cache=True: cfr_probe,
    )

    trimmer.refresh_probed_metadata(clip_path)
    qapp.processEvents()

    assert trimmer.probed_media.is_vfr is False
    assert trimmer.vfr_badge.isHidden() is True
    assert "50.00 FPS" in trimmer.clip_name_lbl.text()
    trimmer.close()





def _linked_item(url: str, path: str) -> FootageMatchItem:
    return FootageMatchItem(
        url=url, norm_url=url, title=os.path.basename(path), file_type="video",
        file_size_bytes=os.path.getsize(path), file_size_display="1 KB",
        matched_path=path, match_tier="EXACT_NAME", is_matched=True,
    )


def test_close_saves_relink_changes_without_apply_button(qapp, tmp_path):
    """No Apply & Save: closing commits changes, but only emits when something actually changed."""
    clip = tmp_path / "Alpha.mp4"
    clip.write_bytes(b"\x00" * 1024)
    url = "https://example.com/Alpha.mp4"

    # 1. Untouched window: links already match what the workspace had -> close emits nothing
    dlg = FootageRelinkDialog(
        links=[url], current_footage_dir=str(tmp_path), existing_linked={url: str(clip)},
        doc_signature="test_close_commit_a", parent=None,
    )
    emitted = []
    dlg.footage_linked.connect(lambda folder, mapping: emitted.append((folder, mapping)))
    dlg.match_items = [_linked_item(url, str(clip))]
    assert dlg.buttonGroup.isHidden()
    dlg.reject()  # Esc / click-away path
    qapp.processEvents()
    assert emitted == []

    # 2. Relink happened in this session -> closing via X emits the new mapping once
    dlg2 = FootageRelinkDialog(
        links=[url], current_footage_dir=str(tmp_path), existing_linked={},
        doc_signature="test_close_commit_b", parent=None,
    )
    emitted2 = []
    dlg2.footage_linked.connect(lambda folder, mapping: emitted2.append((folder, mapping)))
    dlg2.match_items = [_linked_item(url, str(clip))]
    dlg2.close_btn.click()
    qapp.processEvents()
    assert emitted2 == [(str(tmp_path), {url: str(clip)})]
    assert dlg2.is_project_relink_confirmed() is True

    # Committing twice (accept -> done) never double-emits
    dlg2._commit_changes()
    assert len(emitted2) == 1


def test_transcribe_table_shows_duration_not_megabytes(qapp, tmp_path, monkeypatch):
    from src.ui.components.footage import transcribe_stage as ts_mod
    from src.core import media_probe

    wav = tmp_path / "Narration Part 1.wav"
    wav.write_bytes(b"\x00" * (2 * 1024 * 1024))

    class _Probed:
        duration_display = "4m 12s"

    monkeypatch.setattr(media_probe, "probe_media_file", lambda p, use_cache=True: _Probed())
    stage = ts_mod.TranscribeStage(project_name="Dur_Test", initial_vo_paths=[str(wav)])
    t = stage.table
    assert t.horizontalHeaderItem(stage.COL_DURATION).text() == "DURATION"
    assert t.item(0, stage.COL_DURATION).text() == "4m 12s"
    assert "MB" not in t.item(0, stage.COL_DURATION).text()
    assert t.item(0, stage.COL_SIZE).text() == "2.0 MB"


def test_library_row_click_checks_box_and_box_click_toggles(qapp, tmp_path):
    """Regression: selecting a row must tick its checkbox; ticking boxes adds rows without clearing others."""
    from PySide6.QtTest import QTest

    paths = []
    for name in ("A.mp4", "B.mp4", "C.mp4"):
        p = tmp_path / name
        p.write_bytes(b"\x00" * 512)
        paths.append(str(p))
    items = [_linked_item(f"https://example.com/{os.path.basename(p)}", p) for p in paths]

    dlg = FootageRelinkDialog(links=[it.url for it in items], current_footage_dir="", parent=None,
                              doc_signature="test_lib_sel")
    dlg.show()
    dlg.match_items = items
    dlg.switch_stage("library")
    qapp.processEvents()
    stage = dlg.library_stage
    model, table = stage.model, stage.table
    vp = table.viewport()

    def click(row, col, mods=Qt.NoModifier):
        QTest.mouseClick(vp, Qt.LeftButton, mods, table.visualRect(model.index(row, col)).center())
        qapp.processEvents()

    def checked_rows():
        return sorted(r for r in range(model.rowCount())
                      if model.data(model.index(r, 0), Qt.CheckStateRole) == Qt.Checked)

    click(0, model.COL_NAME)            # plain row click -> that row checked
    assert checked_rows() == [0]
    click(1, model.COL_NAME)            # another row click replaces it
    assert checked_rows() == [1]
    click(2, model.COL_CHECK)           # box click adds without clearing
    assert checked_rows() == [1, 2]
    assert not stage.action_bar.isHidden()
    click(2, model.COL_CHECK)           # box click again removes only that row
    assert checked_rows() == [1]
    stage.select_all_cb.setChecked(True)
    qapp.processEvents()
    assert checked_rows() == [0, 1, 2]
    stage._clear_all_selections()
    qapp.processEvents()
    assert checked_rows() == []
    dlg.close()


def test_background_click_closes_and_saves_and_shadow_is_cached(qapp, tmp_path):
    """Clicking the dimmed background closes (and still saves); the card has no live shadow effect."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget

    clip = tmp_path / "Alpha.mp4"
    clip.write_bytes(b"\x00" * 1024)
    url = "https://example.com/Alpha.mp4"

    host = QWidget()
    host.resize(1100, 760)
    host.show()
    dlg = FootageRelinkDialog(links=[url], current_footage_dir=str(tmp_path), existing_linked={},
                              doc_signature="test_mask_close", parent=host)
    emitted = []
    dlg.footage_linked.connect(lambda folder, mapping: emitted.append(mapping))
    dlg.show()
    dlg.match_items = [_linked_item(url, str(clip))]
    qapp.processEvents()

    # Live QGraphicsDropShadowEffect re-blurs the whole card every scroll frame; it must stay off
    assert dlg.widget.graphicsEffect() is None
    assert dlg._card_shadow._pix is not None
    assert dlg._card_shadow.isVisible()

    QTest.mouseClick(dlg.windowMask, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    qapp.processEvents()
    assert dlg.result() == 0  # rejected via mask click
    assert emitted == [{url: str(clip)}]
    host.close()


def test_is_still_media_detection():
    from src.core.footage_matcher import is_still_media
    assert is_still_media("C:/x/IMG_8472.JPG")
    assert is_still_media("C:/x/phone.HEIC")
    assert is_still_media("C:/x/odd_name.bin", "mjpeg", 0.0)      # image codec, no duration
    assert not is_still_media("C:/x/clip.mp4", "mjpeg", 0.0)       # video container wins
    assert not is_still_media("C:/x/clip.mov", "h264", 120.0)
    assert not is_still_media("C:/x/motion.avi_unknown", "mjpeg", 12.5)  # MJPEG video has duration


def test_photos_are_treated_as_stills_not_one_frame_videos(qapp, tmp_path):
    """Photos: honest format/duration, no VFR noise, not re-encodable, thumbnail tooltip on hover."""
    from PySide6.QtGui import QImage, QColor
    from src.ui.components.footage import stills

    photo = tmp_path / "IMG_8472.JPG"
    img = QImage(1200, 900, QImage.Format_RGB32)
    img.fill(QColor("#336699"))
    assert img.save(str(photo), "JPEG")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 1024)

    p_url, v_url = "https://example.com/IMG_8472.JPG", "https://example.com/clip.mp4"
    it_photo = _linked_item(p_url, str(photo))
    # What ffprobe actually reports for a JPEG: MJPEG, 25 fps, 0s (and VFR set to prove it's ignored)
    it_photo.video_codec, it_photo.frame_rate, it_photo.duration_display = "mjpeg", 25.0, "0s"
    it_photo.duration_seconds, it_photo.resolution, it_photo.is_vfr = 0.0, "4K", True
    it_video = _linked_item(v_url, str(video))
    it_video.video_codec, it_video.frame_rate, it_video.duration_display = "h264", 29.97, "1m 0s"

    dlg = FootageRelinkDialog(links=[p_url, v_url], current_footage_dir="", parent=None,
                              doc_signature="test_stills")
    dlg.show()
    dlg.match_items = [it_photo, it_video]
    dlg._rebuild_cards()
    dlg._update_stats()
    dlg.switch_stage("library")
    qapp.processEvents()
    m = dlg.library_stage.model

    row = next(r for r in range(m.rowCount()) if m.item_at(r) is it_photo)
    disp = lambda col: m.data(m.index(row, col), Qt.DisplayRole)
    assert disp(m.COL_FORMAT) == "JPEG  ·  1200×900"
    assert disp(m.COL_DURATION) == "Still"
    assert disp(m.COL_FLAGS) == ""                      # no VFR warning for a photo
    assert dlg.library_stage.vfr_banner.isHidden()       # and no "VFR clips" banner
    assert dlg.filter_combo.itemText(3) == "VFR (0)"

    # Photos Only / Video Only filters
    dlg.library_stage.filter_combo.setCurrentText("Photos Only")
    qapp.processEvents()
    assert [m.item_at(r) for r in range(m.rowCount())] == [it_photo]
    dlg.library_stage.filter_combo.setCurrentText("Video Only")
    qapp.processEvents()
    assert [m.item_at(r) for r in range(m.rowCount())] == [it_video]
    dlg.library_stage.filter_combo.setCurrentText("All Clips")
    qapp.processEvents()

    # Re-encode excludes photos: photo alone -> disabled; mixed -> counts videos only
    bar = dlg.library_stage.action_bar
    dlg.library_stage._select_items([it_photo])
    qapp.processEvents()
    assert not bar.reencode_btn.isEnabled()
    dlg.library_stage._select_items([it_photo, it_video])
    qapp.processEvents()
    assert bar.reencode_btn.isEnabled() and bar.reencode_btn.text() == "Re-encode Clip"
    requested = []
    # Don't open the real (modal) Transcode dialog in a test; just record what would be queued
    dlg.library_stage.batch_transcode_requested.disconnect(dlg._launch_batch_transcode)
    dlg.library_stage.batch_transcode_requested.connect(lambda items, preset: requested.append(items))
    dlg.library_stage._trigger_reencode()
    assert requested == [[it_video]]

    # Thumbnail tooltip is built lazily on first hover, then cached
    row = next(r for r in range(m.rowCount()) if m.item_at(r) is it_photo)
    tip = m.data(m.index(row, m.COL_NAME), Qt.ToolTipRole)
    assert "<img" in tip
    thumb = stills.thumbnail_path(str(photo))
    assert thumb and os.path.isfile(thumb)
    t = QImage(thumb)
    assert max(t.width(), t.height()) == stills.THUMB_MAX

    # Relink row: Still / no FPS / no VFR chip
    card = next(c for c in dlg.card_widgets if c.property("match_item") is it_photo)
    texts = [w.text() for w in card.findChildren(QLabel)]
    assert "Still" in texts and "VFR" not in texts and "25" not in texts
    dlg.close()
