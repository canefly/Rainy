import os
import json
import tempfile
import pytest
from PySide6.QtWidgets import QApplication

from src.core.vo_engine import (
    save_vo_transcript_json, load_vo_transcript_json,
    VOBatchTranscribeWorker, WhisperModelManager
)
from src.core.project_manager import ProjectManager, ProjectData
from src.core.models import DocumentState, ScriptItem
from src.ui.components.footage.transcribe_stage import TranscribeStage
from src.ui.components.footage_relink_dialog import FootageRelinkDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_save_and_load_independent_vo_transcript_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = os.path.join(tmp_dir, "test_case_vo_transcript.json")
        manifest = {
            "version": 1,
            "project_name": "Test_Case",
            "model_used": "distil-large-v3",
            "total_files": 2,
            "total_duration_sec": 450.5,
            "files": [
                {
                    "part_id": 1,
                    "filename": "VO_Part1.wav",
                    "path": "D:/Audio/VO_Part1.wav",
                    "duration_sec": 250.0,
                    "global_offset_sec": 0.0,
                    "segment_count": 2,
                },
                {
                    "part_id": 2,
                    "filename": "VO_Part2.wav",
                    "path": "D:/Audio/VO_Part2.wav",
                    "duration_sec": 200.5,
                    "global_offset_sec": 250.0,
                    "segment_count": 2,
                }
            ],
            "segments": [
                {
                    "id": 0,
                    "part_id": 1,
                    "source_file": "D:/Audio/VO_Part1.wav",
                    "source_in": 1.2,
                    "source_out": 5.4,
                    "global_in": 1.2,
                    "global_out": 5.4,
                    "text": "Detectives arrived at the scene."
                },
                {
                    "id": 1,
                    "part_id": 1,
                    "source_file": "D:/Audio/VO_Part1.wav",
                    "source_in": 5.8,
                    "source_out": 9.2,
                    "global_in": 5.8,
                    "global_out": 9.2,
                    "text": "The backdoor was unlocked."
                },
                {
                    "id": 2,
                    "part_id": 2,
                    "source_file": "D:/Audio/VO_Part2.wav",
                    "source_in": 0.5,
                    "source_out": 4.1,
                    "global_in": 250.5,
                    "global_out": 254.1,
                    "text": "Inside the bedroom, they found the missing phone."
                }
            ]
        }

        saved_path = save_vo_transcript_json(manifest, output_path=json_path)
        assert os.path.isfile(saved_path)
        assert saved_path == json_path

        loaded = load_vo_transcript_json(saved_path)
        assert loaded is not None
        assert loaded["project_name"] == "Test_Case"
        assert len(loaded["files"]) == 2
        assert len(loaded["segments"]) == 3
        assert loaded["segments"][2]["global_in"] == 250.5


def test_project_manager_debloats_rainyproj_with_vo_transcript():
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = os.path.join(tmp_dir, "independent_vo.json")
        manifest = {
            "version": 1,
            "project_name": "Debloat_Test",
            "files": [{"path": "D:/Audio/Part1.wav"}],
            "segments": [
                {"id": 0, "text": "Segment 1", "source_in": 0.0, "source_out": 2.0},
                {"id": 1, "text": "Segment 2", "source_in": 2.0, "source_out": 5.0},
            ]
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        proj_file = os.path.join(tmp_dir, "test.rainyproj")
        data = ProjectData(
            format_version=2,
            project_name="Debloat_Test",
            vo_file_path="D:/Audio/Part1.wav",
            vo_files=["D:/Audio/Part1.wav"],
            vo_transcript_path=json_path,
            vo_segments=[{"id": 999, "text": "In-memory representation"}],
        )

        pm = ProjectManager.instance()
        assert pm.save_project(proj_file, data) is True

        # Read the raw .rainyproj file from disk to verify vo_segments is empty (no bloat)
        with open(proj_file, "r", encoding="utf-8") as pf:
            raw_proj = json.load(pf)
        assert raw_proj["vo_transcript_path"] == json_path
        assert raw_proj["vo_segments"] == []  # Debloated!

        # When loading the project via load_project, segments are transparently populated from the JSON
        loaded_proj = pm.load_project(proj_file)
        assert loaded_proj is not None
        assert len(loaded_proj.vo_segments) == 2
        assert loaded_proj.vo_segments[0]["text"] == "Segment 1"


def test_transcribe_stage_ui_table_and_reorder(qapp):
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create dummy audio files
        f1 = os.path.join(tmp_dir, "VO_Part1.wav")
        f2 = os.path.join(tmp_dir, "VO_Part2.wav")
        f3 = os.path.join(tmp_dir, "VO_Part3.wav")
        for f in (f1, f2, f3):
            with open(f, "wb") as dummy:
                dummy.write(b"RIFFdummyWAVE")

        stage = TranscribeStage(
            project_name="UI_Test_Case",
            initial_vo_paths=[f1, f2, f3]
        )

        assert stage.table.rowCount() == 3
        assert stage.table.item(0, 1).text() == "VO_Part1.wav"
        assert stage.table.item(1, 1).text() == "VO_Part2.wav"
        assert stage.table.item(2, 1).text() == "VO_Part3.wav"

        # Re-order: Move Part 2 up to position 0
        stage._move_part(1, -1)
        assert stage.table.item(0, 1).text() == "VO_Part2.wav"
        assert stage.table.item(1, 1).text() == "VO_Part1.wav"

        # Remove Part 3
        stage._remove_part(2)
        assert stage.table.rowCount() == 2

        # Verify Terminal bottom strip & collapsible drawer
        assert hasattr(stage, "terminal_strip")
        assert stage._terminal_expanded is False
        assert stage.terminal_drawer.isHidden() is True
        stage.toggle_terminal()
        assert stage._terminal_expanded is True
        assert stage.terminal_drawer.isHidden() is False
        stage.toggle_terminal()
        assert stage._terminal_expanded is False
        assert stage.terminal_drawer.isHidden() is True

        # Verify table expands vertically (no cramped fixed-height lock)
        from PySide6.QtWidgets import QSizePolicy
        assert stage.table.sizePolicy().verticalPolicy() == QSizePolicy.Expanding


def test_footage_relink_dialog_has_three_stages_including_transcribe(qapp):
    dlg = FootageRelinkDialog(
        links=["https://example.com/footage1.mp4"],
        current_footage_dir="",
        project_name="Integration_Test",
    )

    # 1. Verify 3 tabs exist in stage_tabs
    assert hasattr(dlg, "stage_tabs")
    assert hasattr(dlg, "transcribe_stage")
    assert dlg.transcribe_stage is not None

    # 2. Switch between stages and verify RenderQueue hides on Transcribe
    dlg.switch_stage("library")
    assert dlg.stage_stack.currentWidget() == dlg.library_stage
    assert dlg.queue_strip.isHidden() is False

    dlg.switch_stage("transcribe")
    assert dlg.stage_stack.currentWidget() == dlg.transcribe_stage
    assert dlg.queue_strip.isHidden() is True
    assert "multi-part voiceovers" in dlg.header_desc.text().lower()

    dlg.switch_stage("relink")
    assert dlg.stage_stack.currentWidget() == dlg.relink_page
    assert dlg.queue_strip.isHidden() is False


def test_cuda_dll_registration_and_status():
    from src.core.vo_engine import ensure_cuda_dlls_registered, check_cuda_available, WhisperModelManager

    reg = ensure_cuda_dlls_registered()
    assert isinstance(reg, list)

    ok, msg = check_cuda_available()
    assert isinstance(ok, bool)
    assert isinstance(msg, str)

    status = WhisperModelManager.get_engine_status()
    assert "cuda_available" in status
    assert "recommended_device" in status
    assert "recommended_compute_type" in status
    if status["cuda_available"]:
        assert status["recommended_device"] == "cuda"
        assert status["recommended_compute_type"] == "float16"
    else:
        assert status["recommended_device"] == "cpu"
        assert status["recommended_compute_type"] == "int8"


def test_vo_worker_cleanup_and_model_deallocation():
    worker = VOBatchTranscribeWorker(
        file_paths=[],
        project_name="Cleanup_Test",
    )
    class MockInnerModel:
        def __init__(self):
            self.unloaded = False
        def unload_model(self):
            self.unloaded = True

    class MockModel:
        def __init__(self):
            self.model = MockInnerModel()

    worker._model = MockModel()
    inner = worker._model.model
    assert worker._model is not None
    assert inner.unloaded is False

    worker.cleanup()
    assert inner.unloaded is True
    assert worker._model is None
    assert worker._cancelled is True


def test_footage_relink_dialog_cleanup_on_close(qapp):
    dlg = FootageRelinkDialog(
        links=[],
        current_footage_dir="",
        project_name="Dialog_Cleanup_Test",
    )
    assert hasattr(dlg, "cleanup")
    assert hasattr(dlg.transcribe_stage, "cleanup")

    # Simulate active worker in transcribe stage
    class MockWorker:
        def __init__(self):
            self.cancelled = False
            self.cleaned = False
        def cancel(self):
            self.cancelled = True
        def cleanup(self):
            self.cleaned = True

    mock_w = MockWorker()
    dlg.transcribe_stage._active_worker = mock_w
    dlg.transcribe_stage._is_transcribing = True

    # Closing or rejecting the dialog must clean up the worker and stop transcription
    dlg.reject()
    assert mock_w.cancelled is True
    assert mock_w.cleaned is True
    assert dlg.transcribe_stage._active_worker is None
    assert dlg.transcribe_stage._is_transcribing is False


def test_vo_manifest_and_multi_part_slicing():
    from src.core.vo_engine import get_vo_manifest, slice_narration_pack_across_parts
    from src.core.models import DocumentState, ScriptItem
    from src.core.xml_exporter import build_default_assembly_segments_from_locks, generate_assembled_fcp_xml

    with tempfile.TemporaryDirectory() as tmp_dir:
        part1 = os.path.join(tmp_dir, "Narration_Part1.wav")
        part2 = os.path.join(tmp_dir, "Narration_Part2.wav")
        with open(part1, "wb") as f:
            f.write(b"RIFF dummy 1")
        with open(part2, "wb") as f:
            f.write(b"RIFF dummy 2")

        transcript_path = os.path.join(tmp_dir, "test_vo_transcript.json")
        manifest = {
            "version": 1,
            "project_name": "MultiPartTest",
            "total_files": 2,
            "total_duration_sec": 300.0,
            "files": [
                {
                    "part_id": 1,
                    "filename": "Narration_Part1.wav",
                    "path": part1,
                    "duration_sec": 180.0,
                    "global_offset_sec": 0.0,
                    "segment_count": 2,
                },
                {
                    "part_id": 2,
                    "filename": "Narration_Part2.wav",
                    "path": part2,
                    "duration_sec": 120.0,
                    "global_offset_sec": 180.0,
                    "segment_count": 2,
                }
            ],
            "segments": [
                {"id": 0, "part_id": 1, "source_file": part1, "source_in": 0.0, "source_out": 10.0, "global_in": 0.0, "global_out": 10.0, "text": "Part 1 start"},
                {"id": 1, "part_id": 1, "source_file": part1, "source_in": 10.0, "source_out": 180.0, "global_in": 10.0, "global_out": 180.0, "text": "Part 1 end"},
                {"id": 2, "part_id": 2, "source_file": part2, "source_in": 0.0, "source_out": 50.0, "global_in": 180.0, "global_out": 230.0, "text": "Part 2 middle"},
                {"id": 3, "part_id": 2, "source_file": part2, "source_in": 50.0, "source_out": 120.0, "global_in": 230.0, "global_out": 300.0, "text": "Part 2 end"},
            ]
        }
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        doc = DocumentState(
            id="doc-test-multipart",
            file_signature="sig-multi-01",
            name="MultiPartTest",
            file_path=os.path.join(tmp_dir, "MultiPartTest.docx"),
            raw_html="",
            vo_file_path=part1,
            vo_files=[part1, part2],
            vo_transcript_path=transcript_path,
            vo_segments=manifest["segments"],
            items=[
                ScriptItem(
                    id="item-clip-1",
                    row_index=1,
                    raw_timestamp="01:00 - 01:05",
                    start_timecode="00:01:00:00",
                    start_seconds=60.0,
                    end_timecode="00:01:05:00",
                    end_seconds=65.0,
                    duration_seconds=5.0,
                    duration_display="5s",
                    text="First clip in Part 1",
                    vo_locked=True,
                    vo_cut_after_sec=50.0,
                    vo_resume_sec=55.0,
                ),
                ScriptItem(
                    id="item-clip-2",
                    row_index=2,
                    raw_timestamp="02:00 - 02:10",
                    start_timecode="00:02:00:00",
                    start_seconds=120.0,
                    end_timecode="00:02:10:00",
                    end_seconds=130.0,
                    duration_seconds=10.0,
                    duration_display="10s",
                    text="Second clip in Part 2",
                    vo_locked=True,
                    vo_cut_after_sec=220.0,  # 40s into Part 2
                    vo_resume_sec=225.0,
                ),
            ]
        )

        # 1. Test get_vo_manifest
        vo_man = get_vo_manifest(doc)
        assert vo_man["vo_files_count"] == 2
        assert len(vo_man["files"]) == 2
        assert vo_man["files"][0]["duration_sec"] == 180.0
        assert vo_man["files"][1]["global_offset_sec"] == 180.0

        # 2. Test slicing across boundaries: Range [55.0, 220.0] spans across Part 1 into Part 2!
        slices = slice_narration_pack_across_parts(55.0, 220.0, vo_man["files"])
        assert len(slices) == 2
        # Slice 1: Part 1 from 55.0s to 180.0s (local: 55.0 to 180.0)
        assert slices[0]["part_id"] == 1
        assert slices[0]["source_file"] == part1
        assert slices[0]["source_in_sec"] == 55.0
        assert slices[0]["source_out_sec"] == 180.0
        # Slice 2: Part 2 from 180.0s to 220.0s (local: 0.0 to 40.0)
        assert slices[1]["part_id"] == 2
        assert slices[1]["source_file"] == part2
        assert slices[1]["source_in_sec"] == 0.0
        assert slices[1]["source_out_sec"] == 40.0

        # 3. Test build_default_assembly_segments_from_locks
        segs = build_default_assembly_segments_from_locks(doc)
        # Segments sequence:
        # - Pack 1: Part 1 (0.0 -> 50.0)
        # - A-Roll 1: item 1
        # - Pack 2 (Part 1 slice): (55.0 -> 180.0)
        # - Pack 3 (Part 2 slice): (0.0 -> 40.0 local, 180.0 -> 220.0 global)
        # - A-Roll 2: item 2
        # - Pack 4 (Outro Part 2 slice): (45.0 -> 120.0 local, 225.0 -> 300.0 global)
        assert any(s.get("type") == "NARRATION_PACK" and s.get("part_id") == 1 for s in segs)
        assert any(s.get("type") == "NARRATION_PACK" and s.get("part_id") == 2 for s in segs)

        # 4. Test generate_assembled_fcp_xml produces separate file definitions for both parts
        xml_str = generate_assembled_fcp_xml(doc)
        assert f"<name>{os.path.basename(part1)}</name>" in xml_str
        assert f"<name>{os.path.basename(part2)}</name>" in xml_str
        assert "VO Pack 1 (Part 1)" in xml_str
        assert "(Part 2)" in xml_str
