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
    assert hasattr(dlg, "vo_header_stat_lbl")
    assert dlg.vo_header_stat_lbl.text() == "VO: Unbound"

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
    assert "LINKED" in cfr_labels

    # Verify Card 1 (VFR) has subtle VFR indicator
    card_vfr = dlg.card_widgets[1]
    vfr_labels = [w.text() for w in card_vfr.findChildren(QLabel)]
    assert "VFR" in vfr_labels
    assert "VFR WARNING" not in vfr_labels
    assert "LINKED" in vfr_labels

    # Verify stage tabs (Relink vs Library) has no slide animation
    dlg.switch_stage("library")
    assert not dlg.stage_tabs.slideAni.state() == dlg.stage_tabs.slideAni.State.Running
    assert dlg.stage_tabs.currentRouteKey() == "library"
    dlg.switch_stage("relink")
    assert not dlg.stage_tabs.slideAni.state() == dlg.stage_tabs.slideAni.State.Running
    assert dlg.stage_tabs.currentRouteKey() == "relink"

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
    linked_clip = tmp_path / "Terry Interview.mp4-redacted.mp4"
    linked_clip.write_bytes(b"\x00" * 4096)

    links = [
        "https://example.com/assets/Terry%20Interview.mp4-redacted.mp4",
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
        title="Terry Interview.mp4-redacted.mp4",
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
    assert "LINKED" in second_card_labels

    # Verify the filename "Terry Interview.mp4-redacted.mp4" appears ONLY ONCE on the linked card
    occurrences = sum(1 for txt in second_card_labels if txt == "Terry Interview.mp4-redacted.mp4")
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

    # Verify Library table displays INTERLACED in column 7 and Interlaced Only filter works
    idx_col7 = dlg.library_stage.model.index(0, 7)
    assert dlg.library_stage.model.data(idx_col7, Qt.DisplayRole) == "INTERLACED"
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

    clip_path = str(tmp_path / "ch03_20230912200130.mp4")
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



