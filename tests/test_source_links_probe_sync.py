import os
import tempfile
import pytest
from PySide6.QtWidgets import QApplication, QLabel

from src.core.models import ScriptItem, DocumentState
from src.core.database import db
from src.core.unfurl_engine import unfurl_engine, LinkMetadata
from src.core.parser import normalize_link
from src.ui.components.timeline_list import TimelineList
from src.ui.components.timeline_card import TimelineCard
from src.ui.components.drawers import LinksDrawer
from src.ui.components.link_info_dialog import LinkMetadataDialog
from src.ui.views.workspace_view import WorkspaceView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_timeline_uniform_spacing_and_compact_height(qapp):
    """Verifies that cards maintain a uniform 44px height and 48px slot spacing (4px gap)
    in both normal and compact modes, eliminating wide jumps and bloated card margins."""
    tl = TimelineList()
    assert tl.slot_height == 48
    assert tl.card_height == 44
    assert tl.minimumWidth() == 285

    # Test compact mode
    tl._set_compact_all(True)
    assert tl.slot_height == 48
    assert tl.card_height == 44

    # Create range card and single timestamp card
    item_range = ScriptItem(
        id="item-1",
        row_index=1,
        sync_id=1,
        raw_timestamp="00:01:00:00 - 00:01:10:00",
        start_timecode="00:01:00:00",
        start_seconds=60.0,
        end_timecode="00:01:10:00",
        end_seconds=70.0,
        duration_seconds=10.0,
        duration_display="10s",
        text="Range timestamp line",
        is_single=False,
        meta="SCENE 1"
    )
    item_single = ScriptItem(
        id="item-2",
        row_index=2,
        sync_id=2,
        raw_timestamp="00:02:00:00",
        start_timecode="00:02:00:00",
        start_seconds=120.0,
        end_timecode="",
        end_seconds=120.0,
        duration_seconds=0.0,
        duration_display="0s",
        text="Single timestamp line",
        is_single=True,
        meta="STILL 1"
    )

    card_range = TimelineCard(item_range)
    card_single = TimelineCard(item_single)

    # In regular mode
    assert card_range.height() == 44
    assert card_range.tc_container.height() == 32
    assert card_single.height() == 44
    assert card_single.tc_container.height() == 32

    # In compact mode
    card_range.set_compact_mode(True)
    card_single.set_compact_mode(True)

    assert card_range.height() == 44
    assert card_range.tc_container.height() == 32
    assert not card_range.index_lbl.isHidden()
    assert card_single.height() == 44
    assert card_single.tc_container.height() == 32
    assert not card_single.index_lbl.isHidden()


def test_source_links_probe_sync_to_unfurl_and_drawer(qapp):
    """Verifies that probed technical specs from local footage (resolution, duration, size)
    propagate directly to unfurl_engine, LinksDrawer stat badge, and LinkMetadataDialog."""
    test_url = "https://drive.google.com/file/d/test_probe_sync_123/view"
    
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(b"sample video stream mock bytes")
        local_path = os.path.normpath(tf.name)

    try:
        sz = os.path.getsize(local_path)
        mt = os.path.getmtime(local_path)

        # Store mock probe in database
        db.store_local_media_probe(
            file_path=local_path,
            file_size=sz,
            mtime=mt,
            duration_seconds=862.4,
            duration_display="14m 22s",
            width=1920,
            height=1080,
            resolution="1080p",
            frame_rate=23.976,
            audio_channels=2,
            sample_rate=48000,
            video_codec="h264",
            audio_codec="aac"
        )

        ws = WorkspaceView()
        doc = DocumentState(
            id="doc-test-1",
            file_signature="sig-12345",
            file_path="",
            name="Test Probe Sync Doc",
            raw_html="<p>Test</p>",
            items=[],
            links=[test_url],
            checked_links={},
        )
        ws.load_document(doc)

        # Relink footage to local path
        mapping = {test_url: local_path}
        ws.set_footage_folder(os.path.dirname(local_path), mapping)

        # 1. Verify unfurl_engine received probed metadata
        cached = unfurl_engine.get_cached(test_url)
        assert cached is not None
        assert cached.resolution == "1080p"
        assert cached.duration_display == "14m 22s"
        assert cached.duration_seconds == 862.4

        # 2. Verify LinksDrawer badge displays both resolution and duration
        drawer = ws.links_drawer
        assert len(drawer.link_cards) == 1
        card = drawer.link_cards[0]
        stat_badge = card.property("stat_badge")
        assert stat_badge is not None
        badge_text = stat_badge.text()
        assert "1080p" in badge_text
        assert "14m 22s" in badge_text

        # 3. Verify LinkMetadataDialog displays probed specs and linked status
        dlg = LinkMetadataDialog(test_url, parent=ws, local_path=local_path)
        assert "1080p" in dlg.res_row.text()
        assert "14m 22s" in dlg.dur_row.text()
        assert dlg.status_badge.text() == "LINKED"
        assert os.path.basename(local_path) in dlg.local_row.text()

        dlg.close()
        ws.close_drawers()
        ws.close()
    finally:
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass
