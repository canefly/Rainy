import os
import tempfile
import pytest
from src.core.footage_matcher import (
    FootageMatcher,
    FootageMatchItem,
    sanitize_stem,
)
from src.core.unfurl_engine import unfurl_engine, LinkMetadata
from src.core.project_manager import ProjectData, ProjectManager
from src.core.xml_exporter import generate_fcp_xml
from src.core.models import DocumentState, ScriptItem


def test_footage_matcher_exact_filename():
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_file = os.path.join(tmpdir, "15-35921 Christopher Jackson.mp4")
        with open(mock_file, "wb") as f:
            f.write(b"mock video content 12345")

        url = "https://drive.google.com/file/d/test_exact_123"
        unfurl_engine.store_metadata(url, LinkMetadata(
            url=url,
            title="15-35921 Christopher Jackson.mp4",
            provider="Google Drive",
            domain="drive.google.com",
            file_type="VIDEO",
            loading=False,
        ), persist_sqlite=False)

        matcher = FootageMatcher(tmpdir)
        results = matcher.match_links([url])

        assert len(results) == 1
        item = results[0]
        assert item.is_matched
        assert os.path.normpath(item.matched_path) == os.path.normpath(mock_file)
        assert item.match_tier == "EXACT_NAME"


def test_footage_matcher_exact_size_for_renamed_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        # File on disk was renamed by video editor to "Jackson_Interview_Edit.mp4",
        # but byte size matches the cloud metadata probe exactly (8,192 bytes)
        renamed_file = os.path.join(tmpdir, "Jackson_Interview_Edit.mp4")
        with open(renamed_file, "wb") as f:
            f.write(b"A" * 8192)

        url = "https://drive.google.com/file/d/test_size_456"
        unfurl_engine.store_metadata(url, LinkMetadata(
            url=url,
            title="15-35921 Christopher Jackson.mp4",
            provider="Google Drive",
            domain="drive.google.com",
            file_type="VIDEO",
            file_size_bytes=8192,
            file_size_display="8.0 KB",
            loading=False,
        ), persist_sqlite=False)

        matcher = FootageMatcher(tmpdir)
        results = matcher.match_links([url])

        assert len(results) == 1
        item = results[0]
        assert item.is_matched
        assert os.path.normpath(item.matched_path) == os.path.normpath(renamed_file)
        assert item.match_tier == "EXACT_SIZE"


def test_footage_matcher_fuzzy_stem():
    with tempfile.TemporaryDirectory() as tmpdir:
        # File has extra redaction / quality tags like [RAW] and (REDACTED)
        fuzzy_file = os.path.join(tmpdir, "15-35921 Christopher Jackson [RAW].mp4")
        with open(fuzzy_file, "wb") as f:
            f.write(b"fuzzy data content")

        url = "https://drive.google.com/file/d/test_fuzzy_789"
        unfurl_engine.store_metadata(url, LinkMetadata(
            url=url,
            title="15-35921 Christopher Jackson.mp4",
            provider="Google Drive",
            domain="drive.google.com",
            file_type="VIDEO",
            loading=False,
        ), persist_sqlite=False)

        matcher = FootageMatcher(tmpdir)
        results = matcher.match_links([url])

        assert len(results) == 1
        item = results[0]
        assert item.is_matched
        assert os.path.normpath(item.matched_path) == os.path.normpath(fuzzy_file)
        assert item.match_tier == "FUZZY_NAME"


def test_footage_matcher_manual_override():
    with tempfile.TemporaryDirectory() as tmpdir:
        custom_file = os.path.join(tmpdir, "Custom_Footage.mp4")
        with open(custom_file, "wb") as f:
            f.write(b"custom")

        url = "https://drive.google.com/file/d/test_override_000"
        overrides = {url: custom_file}

        matcher = FootageMatcher(tmpdir)
        results = matcher.match_links([url], manual_overrides=overrides)

        assert len(results) == 1
        item = results[0]
        assert item.is_matched
        assert os.path.normpath(item.matched_path) == os.path.normpath(custom_file)
        assert item.match_tier == "MANUAL"


def test_project_manager_linked_footage_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_path = os.path.join(tmpdir, "test.clickproj")
        pm = ProjectManager.instance()

        proj = ProjectData(
            project_name="test",
            script_file_name="Script.docx",
            linked_footage={
                "https://drive.google.com/file/d/xyz": "C:/Media/Video.mp4"
            }
        )

        pm.save_project(proj_path, proj)

        loaded = pm.load_project(proj_path)
        assert loaded is not None
        assert loaded.linked_footage == {
            "https://drive.google.com/file/d/xyz": "C:/Media/Video.mp4"
        }


def test_xml_export_with_linked_media():
    item = ScriptItem(
        id="item-1",
        row_index=1,
        raw_timestamp="20:52-21:07",
        start_timecode="00:20:52:00",
        start_seconds=1252.0,
        end_timecode="00:21:07:00",
        end_seconds=1267.0,
        duration_seconds=15.0,
        duration_display="15s",
        text="Dialogue text for scene 1",
        meta="CLIP 1",
        links=["https://drive.google.com/file/d/mock123"],
    )

    doc_state = DocumentState(
        id="doc-1",
        file_signature="sig-1",
        name="TestSeq",
        file_path="C:/test/script.docx",
        raw_html="<p>Test</p>",
        items=[item],
        links=["https://drive.google.com/file/d/mock123"],
    )

    linked = {
        "https://drive.google.com/file/d/mock123": "D:/Footage/Scene_1_Raw.mp4"
    }

    xml_text = generate_fcp_xml(doc_state, linked_footage=linked)

    assert "<pathurl>file://localhost/D:/Footage/Scene_1_Raw.mp4</pathurl>" in xml_text
    assert "<file id=\"file-1\">" in xml_text
    assert "Scene_1_Raw.mp4" in xml_text
