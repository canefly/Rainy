import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.parser import parse_time_single, process_time_span, normalize_link, parse_docx_file

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
    sample_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "knot-parser", "test_docs", "Every Time Family Guy Felt Like Another Show.docx"))
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
