import os
import pytest
from src.core.models import DocumentState, ScriptItem
from src.core.vo_engine import (
    WhisperModelManager, build_masked_script_payload, mask_urls_in_text
)


def test_whisper_model_catalog_and_settings():
    catalog = WhisperModelManager.get_catalog()
    assert len(catalog) >= 5
    ids = [m.id for m in catalog]
    assert "tiny.en" in ids
    assert "base.en" in ids
    assert "small.en" in ids
    assert "distil-large-v3" in ids
    assert "large-v3-turbo" in ids

    spec = WhisperModelManager.get_model_spec("distil-large-v3")
    assert spec.id == "distil-large-v3"
    assert "Systran" in spec.repo_id

    WhisperModelManager.set_active_model_id("small.en")
    assert WhisperModelManager.get_active_model_id() == "small.en"
    WhisperModelManager.set_active_model_id("base.en")

    status = WhisperModelManager.get_engine_status()
    assert "installed" in status
    assert "recommended_device" in status


def test_build_masked_script_payload_masks_urls_and_raw_pts():
    item1 = ScriptItem(
        id="item-1",
        row_index=1,
        raw_timestamp="20:52 - 21:07",
        start_timecode="00:20:52:00",
        start_seconds=1252.0,
        end_timecode="00:21:07:00",
        end_seconds=1267.0,
        duration_seconds=15.0,
        duration_display="15s",
        text="@editor play https://drive.google.com/file/d/12345/view 20:52 - 21:07",
        meta="S01E02",
        sync_id=2,
        links=["https://drive.google.com/file/d/12345"],
    )

    blocks = [
        {"id": 0, "text": "Intro Heading", "spans": [], "isEditorLine": False, "isHeader": True, "headerLevel": 1},
        {"id": 1, "text": "On March 4th, detectives brought Sam in for questioning.", "spans": [], "isEditorLine": False, "isHeader": False, "headerLevel": None},
        {"id": 2, "text": "@editor play https://drive.google.com/file/d/12345/view 20:52 - 21:07", "spans": [], "isEditorLine": True, "isHeader": False, "headerLevel": None},
        {"id": 3, "text": "Detective: Where were you on Tuesday? Sam: At my mom's house.", "spans": [], "isEditorLine": False, "isHeader": False, "headerLevel": None},
        {"id": 4, "text": "Look at how nervous he gets when they mention the truck.", "spans": [], "isEditorLine": False, "isHeader": False, "headerLevel": None},
    ]

    doc = DocumentState(
        id="doc-1",
        file_signature="sig-123",
        name="Sam_Case.docx",
        file_path="D:/Scripts/Sam_Case.docx",
        raw_html="",
        items=[item1],
        blocks=blocks,
        links=["https://drive.google.com/file/d/12345"],
    )

    linked_footage = {
        "https://drive.google.com/file/d/12345": "D:/Footage/Sam_Interview.mp4"
    }

    payload = build_masked_script_payload(doc, linked_footage=linked_footage, fps=24.0)
    assert payload["total_aroll_clips"] == 1
    assert len(payload["aroll_manifest"]) == 1
    assert payload["aroll_manifest"][0]["bound_file_name"] == "Sam_Interview.mp4"

    paragraphs = payload["masked_paragraphs"]
    p2 = next(p for p in paragraphs if p["block_id"] == 2)
    assert p2["type"] == "AROLL_CUE"
    assert "<<AROLL_CLIP" in p2["text"]
    assert "https://drive.google.com" not in p2["text"]
    assert 'file="Sam_Interview.mp4"' in p2["text"]
