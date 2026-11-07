import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.parser import (
    parse_time_single, process_time_span, normalize_link,
    parse_docx_file, save_blocks_to_docx, update_timecode_in_docx, compute_file_hash,
    build_raw_html
)
from src.core.database import db
from src.core.models import EditorNote
import copy
import shutil
import tempfile

def test_single_timestamp_typos():
    # 00;09 (semicolon typo)
    tc, sec, has_typo = parse_time_single("00;09")
    assert tc == "00:00:09:00"
    assert sec == 9.0
    assert has_typo is True

    # 00.09 (dot typo)
    tc, sec, has_typo = parse_time_single("00.09")
    assert tc == "00:00:09:00"
    assert sec == 9.0
    assert has_typo is True

    # 00 09 (space typo)
    tc, sec, has_typo = parse_time_single("00 09")
    assert tc == "00:00:09:00"
    assert sec == 9.0
    assert has_typo is True

    # Standard 01:24
    tc, sec, has_typo = parse_time_single("01:24")
    assert tc == "00:01:24:00"
    assert sec == 84.0
    assert has_typo is False

def test_time_span_ranges():
    # 20:48-20:50 (2s duration)
    start_tc, start_sec, end_tc, end_sec, dur_sec, dur_disp, warn, _ = process_time_span("20:48-20:50")
    assert start_tc == "00:20:48:00"
    assert end_tc == "00:20:50:00"
    assert dur_sec == 2.0
    assert dur_disp == "2s"
    assert warn is False

    # 00;09 - 00;15 (typos in span)
    start_tc, start_sec, end_tc, end_sec, dur_sec, dur_disp, warn, reason = process_time_span("00;09 - 00;15")
    assert start_tc == "00:00:09:00"
    assert end_tc == "00:00:15:00"
    assert dur_sec == 6.0
    assert dur_disp == "6s"
    assert warn is True

def test_link_normalization():
    # YouTube with timestamp and query params
    yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"
    assert normalize_link(yt) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Google Drive view strip
    gdrive = "https://drive.google.com/file/d/1B2C3D4E5F6G7H8I9J/view?usp=sharing"
    assert normalize_link(gdrive) == "https://drive.google.com/file/d/1B2C3D4E5F6G7H8I9J"

def test_real_docx_parsing():
    sample_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "clickables-parser", "test_docs", "Every Time Family Guy Felt Like Another Show.docx"))
    if os.path.exists(sample_path):
        doc = parse_docx_file(sample_path)
        assert len(doc.items) == 165
        assert len(doc.links) == 11
        assert len(doc.link_colors) == 11
        assert doc.name == "Every Time Family Guy Felt Like Another Show.docx"

        # Check that clips sharing the same drive file have the EXACT same link color
        s10e20_items = [it for it in doc.items if it.meta == "S10E20"]
        assert len(s10e20_items) >= 5
        first_col = doc.link_colors.get(s10e20_items[0].links[0])
        assert first_col is not None
        for it in s10e20_items:
            assert doc.link_colors.get(it.links[0]) == first_col

        # Check timestamp parsing does NOT eat the episode number 20 into the hours
        assert s10e20_items[0].start_timecode == "00:00:37:00"
        assert s10e20_items[0].end_timecode == "00:00:48:00"
        assert s10e20_items[0].duration_seconds == 11.0

        assert s10e20_items[1].start_timecode == "00:01:36:00"
        assert s10e20_items[1].end_timecode == "00:01:49:00"
        assert s10e20_items[1].duration_seconds == 13.0

        # Check that HTML preserves headers and span formatting
        assert "<h1" in doc.raw_html
        assert "<span style=" in doc.raw_html
        assert "width_pt" in doc.page_geometry
        assert doc.page_geometry["width_pt"] > 300
        assert len(doc.blocks) > 50


def test_two_way_docx_edit_and_stateful_reparse():
    sample_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "clickables-parser", "test_docs", "Every Time Family Guy Felt Like Another Show.docx"))
    if not os.path.exists(sample_path):
        pytest.skip("Sample docx not found")

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_docx = os.path.join(tmpdir, "editable_script.docx")
        shutil.copy2(sample_path, temp_docx)

        # 1. Initial parse
        doc_initial = parse_docx_file(temp_docx)
        old_sig = doc_initial.file_signature
        initial_count = len(doc_initial.items)
        assert initial_count == 165

        # 2. Mark item #0 as checked and add a custom note
        doc_initial.items[0].is_checked = True
        checked_item_id = doc_initial.items[0].id
        doc_initial.custom_notes.append(EditorNote(id="custom-1", block_id=0, text="Keep intro tight"))
        db.save_document(doc_initial)

        # 3. Edit the first heading AND append a new @editor timestamp block
        edited_blocks = copy.deepcopy(doc_initial.blocks)
        edited_blocks[0]["text"] = "Every Time Family Guy Felt Like Another Showwwssss"
        edited_blocks[0]["spans"] = [{
            "text": "Every Time Family Guy Felt Like Another Showwwssss",
            "color": None,
            "bold": True,
            "italic": False,
            "size": None,
            "link": None
        }]
        edited_blocks.append({
            "id": None,
            "text": "@editor: play 04:10 - 04:22 https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "spans": [{"text": "@editor: play 04:10 - 04:22 https://www.youtube.com/watch?v=dQw4w9WgXcQ", "color": None, "bold": False, "italic": False, "size": None, "link": None}],
            "images": [],
            "isEditorLine": True,
            "isHeader": False,
            "headerLevel": None,
        })

        # 4. Save blocks back to .docx and re-parse
        doc_updated = save_blocks_to_docx(temp_docx, edited_blocks, previous_doc=doc_initial)

        # Verify modified heading did NOT double!
        assert doc_updated.blocks[0]["text"] == "Every Time Family Guy Felt Like Another Showwwssss"
        assert doc_updated.headings[0]["text"] == "Every Time Family Guy Felt Like Another Showwwssss"

        # Verify untouched headings (with double spaces in original DOCX) did NOT double!
        leggo_blocks = [b for b in doc_updated.blocks if "Leggo My Meg-O" in b["text"]]
        assert len(leggo_blocks) == 1
        assert leggo_blocks[0]["text"].count("Leggo My Meg-O") == 1

        # Verify SHA256 changed on disk and migrated in SQLite
        assert doc_updated.file_signature != old_sig
        assert doc_updated.file_signature == compute_file_hash(temp_docx)

        # Verify new clip was parsed (+1 item)
        assert len(doc_updated.items) == initial_count + 1
        assert doc_updated.items[-1].start_timecode == "00:04:10:00"
        assert doc_updated.items[-1].end_timecode == "00:04:22:00"

        # Verify checked state & custom note survived the .docx modification and re-parse!
        assert doc_updated.items[0].id == checked_item_id
        assert doc_updated.items[0].is_checked is True
        assert len(doc_updated.custom_notes) == 1
        assert doc_updated.custom_notes[0].text == "Keep intro tight"


def test_timeline_timecode_sync_to_docx():
    sample_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "clickables-parser", "test_docs", "Every Time Family Guy Felt Like Another Show.docx"))
    if not os.path.exists(sample_path):
        pytest.skip("Sample docx not found")

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_docx = os.path.join(tmpdir, "timecode_sync.docx")
        shutil.copy2(sample_path, temp_docx)

        doc_initial = parse_docx_file(temp_docx)
        target_item = doc_initial.items[0]
        target_item.is_checked = True
        db.save_document(doc_initial)

        # Update first item's timecode from 00:00:37:00 - 00:00:48:00 to 00:00:40:00 - 00:00:52:00
        doc_synced = update_timecode_in_docx(
            temp_docx,
            target_item,
            "00:00:40:00",
            "00:00:52:00",
            previous_doc=doc_initial
        )
        assert doc_synced.items[0].start_timecode == "00:00:40:00"
        assert doc_synced.items[0].end_timecode == "00:00:52:00"
        assert doc_synced.items[0].is_checked is True


def test_highlight_extraction_and_genuine_header_filtering():
    cynthia_path = r"C:/Users/canef/Downloads/Cynthia Wilder Script.docx"
    if not os.path.exists(cynthia_path):
        pytest.skip("Cynthia Wilder docx not found")

    doc_state = parse_docx_file(cynthia_path)

    # 1. Verify genuine headings detected without false positives
    heading_texts = [h["text"] for h in doc_state.headings]
    assert "Cynthia Wilder" in heading_texts
    assert "Hook:" in heading_texts
    assert "Body:" in heading_texts
    # Verify narrative sentences with overridden 11pt font were NOT falsely marked as headers
    for h_txt in heading_texts:
        assert not h_txt.startswith("http")
        assert not h_txt.startswith("In November of 2015")
        assert not h_txt.startswith("Detectives believe")
        assert not h_txt.startswith("With both men")

    # 2. Verify highlight backgrounds extracted on spans
    has_light_blue_shading = False
    has_light_green_shading = False
    for b in doc_state.blocks:
        for s in b.get("spans", []):
            if s.get("bg_color") == "#cfe2f3":
                has_light_blue_shading = True
            elif s.get("bg_color") == "#d9ead3":
                has_light_green_shading = True

    assert has_light_blue_shading is True
    assert has_light_green_shading is True

    # 3. Verify HTML output has background-color and does not force neon green on normal text
    html_out = build_raw_html(doc_state.blocks, doc_state.link_colors, light_paper=True, view_mode="document")
    assert "background-color: #cfe2f3" in html_out
    assert "background-color: #d9ead3" in html_out
    assert "#4ade80" not in html_out  # Neon green is disabled in Document Mode on White Paper


def test_background_unfurl_engine_and_cookie_integration(monkeypatch):
    from src.core.unfurl_engine import UnfurlEngine, extract_gdrive_id, infer_file_type_from_title, LinkMetadata
    from src.core.cookie_manager import cookie_manager

    orig_mode = cookie_manager.get_mode()
    orig_prof = cookie_manager.get_firefox_profile()
    orig_file = cookie_manager.get_cookie_file_path()
    orig_raw = cookie_manager.get_raw_cookie_text()

    engine = UnfurlEngine()
    test_drive_url = "https://drive.google.com/file/d/1PersistentCacheTest999/view?usp=sharing"
    locked_url = "https://drive.google.com/file/d/1RestrictedEntry888/view"

    try:
        # 1. Verify Google Drive ID & Folder extraction
        fid, is_folder = extract_gdrive_id("https://drive.google.com/file/d/1ds0z_7ruKHci4Fohmgjm79GZVC9OLbBZ/view?usp=sharing")
        assert fid == "1ds0z_7ruKHci4Fohmgjm79GZVC9OLbBZ"
        assert is_folder is False

        fold_id, is_fold = extract_gdrive_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz")
        assert fold_id == "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        assert is_fold is True

        assert infer_file_type_from_title("Richie Wilder Jr. 3-4-16 INTERVIEW.mp4") == "VIDEO"
        assert infer_file_type_from_title("911_dispatch_call.wav") == "AUDIO"
        assert infer_file_type_from_title("Transcript.pdf") == "DOC"
        assert infer_file_type_from_title("Client Assets", is_folder=True) == "FOLDER"

        # 2. Test Restricted Google Drive link detection (when unauthenticated)
        cookie_manager.set_mode("disabled")

        def mock_restricted_fetch(url, timeout=6.0, max_bytes=196608, **kw):
            return (
                200,
                "https://accounts.google.com/ServiceLogin?service=wise",
                "<html><head><title>Sign in - Google Accounts</title></head><body></body></html>"
            )

        monkeypatch.setattr(engine, "_fetch_url_text", mock_restricted_fetch)
        monkeypatch.setattr(engine, "fetch_thumbnail_bytes", lambda url, **kw: b"fake_jpeg_bytes")

        drive_url = "https://drive.google.com/file/d/1ds0z_7ruKHci4Fohmgjm79GZVC9OLbBZ/view?usp=sharing"
        meta_locked = engine.unfurl_sync(drive_url)
        assert meta_locked.is_restricted is True
        assert "Restricted" in meta_locked.title
        assert meta_locked.provider == "Google Drive"

        # 3. Test Authenticated Google Drive Unfurling (when cookies are enabled in CookieManager)
        cookie_manager.set_mode("paste")
        cookie_manager.set_raw_cookie_text("Cookie: SID=test_sid; SAPISID=test_sapisid; __Secure-1PSID=test_psid")
        assert "SAPISID=test_sapisid" in cookie_manager.get_cookie_header(drive_url)

        def mock_authenticated_drive_fetch(url, timeout=6.0, max_bytes=196608, **kw):
            return (
                200,
                url,
                """
                <html>
                <head>
                    <title>Richie Wilder Jr. 3-4-16 INTERVIEW (REDACTED).mp4 - Google Drive</title>
                    <meta property="og:title" content="Richie Wilder Jr. 3-4-16 INTERVIEW (REDACTED).mp4">
                    <meta property="og:type" content="video">
                    <meta property="og:image" content="https://lh3.googleusercontent.com/d/1ds0z_7ruKHci4Fohmgjm79GZVC9OLbBZ=w320">
                </head>
                </html>
                """
            )

        monkeypatch.setattr(engine, "_fetch_url_text", mock_authenticated_drive_fetch)
        meta_unlocked = engine.unfurl_sync(drive_url)
        assert meta_unlocked.is_restricted is False
        assert meta_unlocked.title == "Richie Wilder Jr. 3-4-16 INTERVIEW (REDACTED).mp4"
        assert meta_unlocked.file_type == "VIDEO"
        assert meta_unlocked.provider == "Google Drive"
        assert meta_unlocked.thumbnail_bytes == b"fake_jpeg_bytes"

        # 4. Test YouTube oEmbed Unfurling
        def mock_yt_fetch(url, timeout=6.0, max_bytes=196608, **kw):
            if "oembed" in url:
                return (
                    200,
                    url,
                    '{"title": "Shooting 45 acp 100 Yards", "author_name": "Hickok45", "thumbnail_url": "https://i.ytimg.com/vi/abc/hqdefault.jpg"}'
                )
            return (200, url, "")

        monkeypatch.setattr(engine, "_fetch_url_text", mock_yt_fetch)
        yt_meta = engine.unfurl_sync("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert yt_meta.title == "Shooting 45 acp 100 Yards"
        assert yt_meta.author == "Hickok45"
        assert yt_meta.provider == "YouTube"
        assert yt_meta.file_type == "VIDEO"
        assert yt_meta.thumbnail_url == "https://i.ytimg.com/vi/abc/hqdefault.jpg"

        # 5. Test General Web Open Graph Unfurling
        def mock_web_fetch(url, timeout=6.0, max_bytes=196608, **kw):
            return (
                200,
                url,
                """
                <html>
                <head>
                    <meta property="og:title" content="North Dakota Case Archive #15-35921">
                    <meta property="og:description" content="Official court and police dispatch records.">
                    <meta property="og:site_name" content="ND Court Records">
                    <meta property="og:image" content="/assets/preview.png">
                </head>
                </html>
                """
            )

        monkeypatch.setattr(engine, "_fetch_url_text", mock_web_fetch)
        web_meta = engine.unfurl_sync("https://records.example.org/case/15-35921")
        assert web_meta.title == "North Dakota Case Archive #15-35921"
        assert web_meta.description == "Official court and police dispatch records."
        assert web_meta.provider == "ND Court Records"
        assert web_meta.thumbnail_url == "https://records.example.org/assets/preview.png"

        # 6. Test Persistent SQLite Link Metadata Cache (0ms Startup Hydration & Selective Restricted Eviction)
        meta_to_store = LinkMetadata(
            url=test_drive_url,
            title="Bodycam_Footage_01.mp4",
            description="Google Drive • VIDEO",
            provider="Google Drive",
            domain="drive.google.com",
            file_type="VIDEO",
            thumbnail_url="https://lh3.googleusercontent.com/d/1PersistentCacheTest999=w320",
            thumbnail_bytes=b"sqlite_cached_thumb_bytes",
            is_restricted=False,
        )
        engine.store_metadata(test_drive_url, meta_to_store)

        # Clear in-memory dictionary only -> verify 0ms hydration from SQLite clickables.db
        engine._cache.clear()
        hydrated = engine.get_cached(test_drive_url)
        assert hydrated is not None
        assert hydrated.title == "Bodycam_Footage_01.mp4"
        assert hydrated.file_type == "VIDEO"
        assert hydrated.thumbnail_bytes == b"sqlite_cached_thumb_bytes"

        # Store a restricted entry and verify clear_cache(restricted_only=True) evicts only restricted items
        engine.store_metadata(
            locked_url,
            LinkMetadata(
                url=locked_url,
                title="🔒 Restricted — Configure Cookies",
                provider="Google Drive",
                domain="drive.google.com",
                file_type="LINK",
                is_restricted=True,
            ),
        )
        assert engine.get_cached(locked_url) is not None
        engine.clear_cache(restricted_only=True)
        assert engine.get_cached(locked_url) is None
        assert engine.get_cached(test_drive_url) is not None
    finally:
        db.delete_link_metadata(norm_url=normalize_link(test_drive_url))
        db.delete_link_metadata(norm_url=normalize_link(locked_url))
        cookie_manager.set_firefox_profile(orig_prof)
        cookie_manager.set_cookie_file_path(orig_file)
        cookie_manager.set_raw_cookie_text(orig_raw)
        cookie_manager.set_mode(orig_mode)
        cookie_manager.load_cookies(force_reload=True)


def test_unfurl_queue_pacing_micro_jitter_and_rate_limit_backoff(monkeypatch):
    import time
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    from src.core.unfurl_engine import unfurl_engine, LinkMetadata
    from src.core.parser import normalize_link

    engine = unfurl_engine

    # 1. Clamped Dedicated Pool: Verify max concurrency is strictly 3 (not CPU core count 16)
    assert engine.thread_pool.maxThreadCount() == 3

    # Reset test queue and backoff
    engine.clear_cache()
    engine._backoff_until = 0.0
    if engine._dispatch_timer.isActive():
        engine._dispatch_timer.stop()

    try:
        # 2. Test Queue Ingestion and FIFO Order
        u1 = "https://example.com/asset_1"
        u2 = "https://example.com/asset_2"
        u3 = "https://example.com/asset_3"

        # Mock unfurl_sync to record dispatch order and keep worker busy briefly
        dispatched_urls = []
        def mock_unfurl_sync(url, fetch_thumbnail=True):
            dispatched_urls.append(url)
            return LinkMetadata(url=url, title=f"Title for {url}", loading=False)

        monkeypatch.setattr(engine, "unfurl_sync", mock_unfurl_sync)

        # 3. Test Priority Dispatch (Instant insertion to index 0, bypassing micro-jitter)
        # Put engine in backoff state so items remain in queue for inspection
        engine._backoff_until = time.time() + 10.0
        from PySide6.QtCore import QMutexLocker
        with QMutexLocker(engine._mutex):
            engine._queue.append((u1, normalize_link(u1), False))
            engine._queued_norms.add(normalize_link(u1))
            engine._queue.append((u2, normalize_link(u2), False))
            engine._queued_norms.add(normalize_link(u2))

        # Enqueue high-priority link (user hover or refresh)
        u_priority = "https://example.com/priority_manual_hover"
        engine.request_unfurl(u_priority, priority=True)

        with QMutexLocker(engine._mutex):
            # Priority link MUST be promoted to index 0 of queue ahead of u1 and u2!
            assert len(engine._queue) == 3
            assert engine._queue[0][0] == u_priority

        # 4. Test HTTP 429 Rate-Limit Backoff (Automatic cooldown on rate-limiting)
        t_now = time.time()
        engine.trigger_rate_limit_backoff(backoff_sec=7.5)
        assert engine._backoff_until >= t_now + 7.0
        assert engine._dispatch_timer.isActive()

        # In backoff state, scheduling another dispatch must respect the cooldown remaining
        remaining = engine._backoff_until - time.time()
        assert remaining > 5.0

    finally:
        engine._backoff_until = 0.0
        if engine._dispatch_timer.isActive():
            engine._dispatch_timer.stop()
        engine.clear_cache()


def test_sqlite_wal_integrity_and_backups():
    from src.core.database import db

    # 1. Verify WAL mode and NORMAL synchronous settings
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

        cursor.execute("PRAGMA synchronous;")
        sync = cursor.fetchone()[0]
        # In SQLite, 1 corresponds to NORMAL
        assert sync == 1

    # 2. Verify PRAGMA quick_check integrity
    assert db.verify_integrity() is True

    # 3. Verify automated rolling database backup
    backup_path = db.backup_database(max_backups=3)
    assert backup_path is not None
    assert os.path.exists(backup_path)
    assert os.path.getsize(backup_path) > 0

    # 4. Verify Link Metadata V3 Schema Persistence (file sizes, resolutions, durations)
    test_url = "https://drive.google.com/file/d/test_meta_persist_v3/view"
    db.save_link_metadata(
        test_url,
        {
            "title": "Persist Test Video.mp4",
            "file_type": "VIDEO",
            "file_size_bytes": 524288000,
            "file_size_display": "500.00 MB",
            "resolution": "1080p",
            "duration_seconds": 360.5,
            "duration_display": "6m 00s"
        }
    )
    cached = db.get_link_metadata(test_url)
    assert cached is not None
    assert cached["file_size_bytes"] == 524288000
    assert cached["file_size_display"] == "500.00 MB"
    assert cached["resolution"] == "1080p"
    assert cached["duration_seconds"] == 360.5
    assert cached["duration_display"] == "6m 00s"


def test_project_manager_atomic_saves_and_recovery(tmp_path):
    from src.core.project_manager import project_manager, ProjectData

    test_file = str(tmp_path / "TestProject.clickproj")
    data = ProjectData(
        format_version=1,
        project_name="TestProject",
        script_file_name="C:/Users/canef/Documents/TestScript.docx",
        file_signature="sig-abc-123",
        footage_folder="D:/Videos/Episode_42",
        checked_items=["item-1", "item-5", "item-9"],
        checked_notes=["note-2"],
        checked_links=["https://drive.google.com/file/d/xyz"],
        notes_data=[{"id": "custom-1", "text": "Cut scene at 02:30"}]
    )

    # 1. Save atomically
    assert project_manager.save_project(test_file, data) is True
    assert os.path.exists(test_file)
    assert not os.path.exists(test_file + ".tmp")  # Temporary swap file is cleaned up

    # 2. Load and verify exact state match
    loaded = project_manager.load_project(test_file)
    assert loaded is not None
    assert loaded.project_name == "TestProject"
    assert loaded.footage_folder == "D:/Videos/Episode_42"
    assert loaded.checked_items == ["item-1", "item-5", "item-9"]
    assert loaded.checked_notes == ["note-2"]
    assert loaded.checked_links == ["https://drive.google.com/file/d/xyz"]
    assert len(loaded.notes_data) == 1
    assert loaded.notes_data[0]["id"] == "custom-1"

    # 3. Test managed folder path resolver
    auto_path = project_manager.get_auto_project_path("Family Guy Episode 1.docx")
    assert auto_path.endswith("Family Guy Episode 1.clickproj")
    assert "Clickables" in auto_path
    assert "Projects" in auto_path


def test_fcp_xml_exporter():
    import xml.etree.ElementTree as ET
    from src.core.models import DocumentState, ScriptItem
    from src.core.xml_exporter import generate_fcp_xml, parse_timecode_to_frames

    # Test timecode parsing
    assert parse_timecode_to_frames("00:01:00:00", fps=30) == 1800
    assert parse_timecode_to_frames("01:00", fps=30) == 1800
    assert parse_timecode_to_frames("00:00:10:15", fps=30) == 315

    # Test XML generation
    doc = DocumentState(
        id="doc-1",
        file_signature="sig-1",
        name="XML Test Episode",
        file_path="test.docx",
        raw_html=""
    )
    doc.items = [
        ScriptItem(
            id="item-1",
            row_index=1,
            raw_timestamp="00:01:00:00",
            start_timecode="00:01:00:00",
            start_seconds=60.0,
            end_timecode="00:01:10:00",
            end_seconds=70.0,
            duration_seconds=10.0,
            duration_display="10s",
            text="First dialogue cue",
            links=["https://drive.google.com/file/d/clip1"],
            is_checked=True
        ),
        ScriptItem(
            id="item-2",
            row_index=2,
            raw_timestamp="00:01:15:00",
            start_timecode="00:01:15:00",
            start_seconds=75.0,
            end_timecode="00:01:25:00",
            end_seconds=85.0,
            duration_seconds=10.0,
            duration_display="10s",
            text="Second dialogue cue",
            links=[],
            is_checked=False
        )
    ]
    doc.name = "XML Test Episode"
    doc.items = [
        ScriptItem(
            id="item-1",
            row_index=1,
            raw_timestamp="00:01:00:00",
            start_timecode="00:01:00:00",
            start_seconds=60.0,
            end_timecode="00:01:10:00",
            end_seconds=70.0,
            duration_seconds=10.0,
            duration_display="10s",
            text="First dialogue cue",
            links=["https://drive.google.com/file/d/clip1"],
            is_checked=True
        ),
        ScriptItem(
            id="item-2",
            row_index=2,
            raw_timestamp="00:01:15:00",
            start_timecode="00:01:15:00",
            start_seconds=75.0,
            end_timecode="00:01:25:00",
            end_seconds=85.0,
            duration_seconds=10.0,
            duration_display="10s",
            text="Second dialogue cue",
            links=[],
            is_checked=False
        )
    ]

    xml_content = generate_fcp_xml(doc, footage_folder="D:/Footage", fps=30)
    assert "<?xml version=" in xml_content
    assert "<!DOCTYPE xmeml>" in xml_content
    assert '<xmeml version="4">' in xml_content
    assert "<name>XML Test Episode</name>" in xml_content
    assert "clipitem-1" in xml_content
    assert "clipitem-2" in xml_content
    assert "[DONE] First dialogue cue" in xml_content
    assert "[AROL] Second dialogue cue" in xml_content

    # Validate XML parser successfully parses document tree
    root = ET.fromstring(xml_content.split("<!DOCTYPE xmeml>\n")[-1])
    assert root.tag == "xmeml"
    seq = root.find("sequence")
    assert seq is not None
    assert seq.find("name").text == "XML Test Episode"
    track = seq.find("media/video/track")
    assert track is not None
    assert len(track.findall("clipitem")) == 2




