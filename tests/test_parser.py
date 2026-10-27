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

