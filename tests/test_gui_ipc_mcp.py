import os
import json
import pytest
from PySide6.QtWidgets import QApplication
from src.core.models import DocumentState, ScriptItem
from src.ui.views.workspace_view import WorkspaceView
from src.core.mcp_server import handle_mcp_request


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_live_gui_mcp_simulates_aroll_lock_and_script_diff(qapp, tmp_path):
    from src.core.project_manager import project_manager
    auto_proj_path = project_manager.get_auto_project_path("MCP_Test_Script.docx")
    if os.path.exists(auto_proj_path):
        os.remove(auto_proj_path)

    view = WorkspaceView()
    pipe_name = "clickables-test-mcp-pipe"
    view.mcp_ipc_server.stop()
    assert view.mcp_ipc_server.start(pipe_name=pipe_name) is True

    item1 = ScriptItem(
        id="item-vo-1",
        row_index=1,
        raw_timestamp="10:00 - 10:12",
        start_timecode="00:10:00:00",
        start_seconds=600.0,
        end_timecode="00:10:12:00",
        end_seconds=612.0,
        duration_seconds=12.0,
        duration_display="12s",
        text="@editor play 10:00 - 10:12",
        meta="CLIP 1",
        sync_id=1,
        links=["https://drive.google.com/file/d/abcde"],
        is_checked=False,
    )

    blocks = [
        {"id": 0, "text": "He immediately denied owning the blue Ford F-150.", "spans": [{"text": "He immediately denied owning the blue Ford F-150."}], "isEditorLine": False, "isHeader": False, "headerLevel": None},
        {"id": 1, "text": "@editor play 10:00 - 10:12", "spans": [{"text": "@editor play 10:00 - 10:12"}], "isEditorLine": True, "isHeader": False, "headerLevel": None},
        {"id": 2, "text": "Look at how nervous he gets.", "spans": [{"text": "Look at how nervous he gets."}], "isEditorLine": False, "isHeader": False, "headerLevel": None},
    ]

    vo_segments = [
        {"id": 0, "start": 0.5, "end": 4.8, "text": "And right away the guy swears he never had that blue truck."},
        {"id": 1, "start": 5.1, "end": 7.2, "text": "And honestly guys, watch his face here."},
        {"id": 2, "start": 8.0, "end": 11.4, "text": "Look at how nervous he gets."},
    ]

    doc = DocumentState(
        id="doc-mcp-test",
        file_signature="sig-mcp-test-001",
        name="MCP_Test_Script.docx",
        file_path=str(tmp_path / "MCP_Test_Script.docx"),
        raw_html="",
        items=[item1],
        blocks=blocks,
        links=["https://drive.google.com/file/d/abcde"],
        vo_file_path=str(tmp_path / "vo_master.wav"),
        vo_segments=vo_segments,
    )

    view.load_document(doc)
    assert view.current_doc.items[0].is_checked is False

    # 1. Query status via MCP handler
    resp_status = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_workspace_status", "arguments": {}}},
        pipe_name=pipe_name,
    )
    status_data = json.loads(resp_status["result"]["content"][0]["text"])
    assert status_data["document_name"] == "MCP_Test_Script.docx"
    assert status_data["vo_segment_count"] == 3
    assert status_data["checked_aroll_items"] == 0
    assert "vo_files" in status_data
    assert "vo_files_manifest" in status_data
    assert status_data["vo_files_count"] >= 1

    # Query VO Segments via MCP
    resp_segs = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": {"name": "get_vo_segments", "arguments": {"start_id": 0, "end_id": 2}}},
        pipe_name=pipe_name,
    )
    segs_data = json.loads(resp_segs["result"]["content"][0]["text"])
    assert segs_data["returned_segments"] == 3
    assert "vo_files" in segs_data
    assert "files" in segs_data

    # 2. Apply Script VO Highlight (Modified + Ad-lib) via MCP
    resp_diff = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "apply_script_vo_highlight",
                "arguments": {
                    "diffs": [
                        {
                            "block_id": 0,
                            "vo_segment_ids": [0, 1],
                            "vo_start_sec": 0.5,
                            "vo_end_sec": 7.2,
                            "vo_actual_text": "And right away the guy swears he never had that blue truck. And honestly guys, watch his face here.",
                            "match_type": "ADLIB",
                            "adlib_note": "Added 2.1s lead-in adlib before clip",
                        }
                    ]
                },
            },
        },
        pipe_name=pipe_name,
    )
    diff_res = json.loads(resp_diff["result"]["content"][0]["text"])
    assert diff_res["updated_blocks"] == 1
    assert "0" in view.current_doc.vo_block_diffs

    # 3. Simulate A-Roll Card Lock via MCP -> Verify GUI checkbox was clicked natively!
    resp_lock = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "simulate_aroll_lock",
                "arguments": {
                    "locks": [
                        {
                            "row_index": 1,
                            "vo_cut_after_sec": 7.2,
                            "vo_resume_sec": 8.0,
                            "vo_lead_in_text": "And honestly guys, watch his face here.",
                            "vo_has_adlib": True,
                            "vo_adlib_note": "Lead-in adlib kept",
                            "vo_status": "LOCKED",
                        }
                    ]
                },
            },
        },
        pipe_name=pipe_name,
    )
    lock_res = json.loads(resp_lock["result"]["content"][0]["text"])
    assert lock_res["locked_rows"] == [1]
    assert view.current_doc.items[0].is_checked is True
    assert view.current_doc.items[0].vo_locked is True
    assert view.current_doc.items[0].vo_cut_after_sec == 7.2
    card = view.timeline_list.card_list[0]
    assert card.cb.isChecked() is True
    assert "VO @ 00:07.2" in card.vo_lock_badge.text()

    # 4. Trigger Assembled XML Export via MCP
    xml_out_path = str(tmp_path / "Assembled_Output.xml")
    resp_xml = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "trigger_xml_export",
                "arguments": {"output_path": xml_out_path},
            },
        },
        pipe_name=pipe_name,
    )
    xml_res = json.loads(resp_xml["result"]["content"][0]["text"])
    assert os.path.isfile(xml_res["exported_xml_path"])

    view.mcp_ipc_server.stop()
    view.close()
    view.deleteLater()
    qapp.processEvents()
    if os.path.exists(auto_proj_path):
        os.remove(auto_proj_path)
