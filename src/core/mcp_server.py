import os
import sys
import json
from typing import Dict, Any, List, Optional

# Ensure src package imports resolve cleanly when executed directly as `python -m src.core.mcp_server` or `python src/core/mcp_server.py`
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from src.core.gui_ipc_server import send_gui_ipc_request, PIPE_NAME


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_workspace_status",
        "description": (
            "Returns the active project status from the live Rainy GUI, including script name, "
            "FPS, bound local footage count, multi-part Voiceover (VO) files (vo_files, vo_files_manifest, "
            "vo_transcript_path), Whisper segment count, and current A-Roll checklist lock progress. "
            "Always call this first, and remember to ask the user whether they want to (A) Export a Pre-Cut "
            "FCP7 XML file or (B) Push directly into Premiere Pro via Premiere MCP before starting assembly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_masked_script_and_aroll",
        "description": (
            "Fetches the numbered script paragraphs [P0..Pn] with noisy URLs stripped and raw PTS "
            "timestamps replaced by structured <<AROLL_CLIP ...>> tokens, plus the full A-Roll manifest "
            "with conformed SMPTE timecodes, frame durations, and bound local file paths, as well as "
            "multi-part VO manifest metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_block": {
                    "type": "integer",
                    "description": "Starting paragraph block index (inclusive). Default 0.",
                    "default": 0,
                },
                "end_block": {
                    "type": "integer",
                    "description": "Ending paragraph block index (inclusive). Default 999999.",
                    "default": 999999,
                },
            },
        },
    },
    {
        "name": "get_vo_segments",
        "description": (
            "Fetches segment-level Whisper Voiceover (VO) transcript JSON and multi-part files manifest "
            "({vo_files, files, total_segments, segments: [{id, part_id, source_file, source_in, source_out, global_in, global_out, text}]}) "
            "from the live Rainy GUI."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_id": {
                    "type": "integer",
                    "description": "Starting segment ID (inclusive). Default 0.",
                    "default": 0,
                },
                "end_id": {
                    "type": "integer",
                    "description": "Ending segment ID (inclusive). Default 999999.",
                    "default": 999999,
                },
            },
        },
    },
    {
        "name": "apply_script_vo_highlight",
        "description": (
            "Highlights one or more script paragraphs [P#] directly inside the live Rainy Script Viewer "
            "showing what the VO narrator actually said (EXACT, MODIFIED, ADLIB, or RETAKE_CUT)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "diffs": {
                    "type": "array",
                    "description": "List of paragraph VO diff objects to render in the Rainy Script Viewer.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "integer", "description": "Paragraph block index [P#]."},
                            "vo_segment_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Matched Whisper VO segment IDs.",
                            },
                            "vo_start_sec": {"type": "number", "description": "Start second in VO audio."},
                            "vo_end_sec": {"type": "number", "description": "End second in VO audio."},
                            "vo_actual_text": {"type": "string", "description": "What the VO actually said."},
                            "match_type": {
                                "type": "string",
                                "enum": ["EXACT", "MODIFIED", "ADLIB", "RETAKE_CUT"],
                                "description": "Alignment classification.",
                            },
                            "adlib_note": {"type": "string", "description": "Optional note about ad-lib or wording change."},
                        },
                        "required": ["block_id", "vo_actual_text", "match_type"],
                    },
                }
            },
            "required": ["diffs"],
        },
    },
    {
        "name": "simulate_aroll_lock",
        "description": (
            "Simulates checking off and locking one or more A-Roll cards directly on the live Rainy GUI "
            "Timeline Checklist. Updates the card's VO cut badge (e.g. 'VO @ 00:45.9'), clicks its native "
            "checkbox on the GUI thread, and triggers Rainy' native autosave."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "locks": {
                    "type": "array",
                    "description": "List of A-Roll VO lock updates.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "row_index": {"type": "integer", "description": "1-based A-Roll card row index (e.g. 1, 2, 3)."},
                            "item_id": {"type": "string", "description": "Optional ScriptItem UUID."},
                            "vo_cut_after_sec": {
                                "type": "number",
                                "description": "Exact second in the VO audio where this A-Roll clip slips in.",
                            },
                            "vo_resume_sec": {
                                "type": "number",
                                "description": "Exact second in the VO audio where the next narration pack starts after this A-Roll.",
                            },
                            "vo_lead_in_text": {
                                "type": "string",
                                "description": "Spoken VO sentence or ad-lib immediately preceding this A-Roll cut.",
                            },
                            "vo_has_adlib": {"type": "boolean", "default": False},
                            "vo_adlib_note": {"type": "string", "default": ""},
                            "vo_status": {
                                "type": "string",
                                "enum": ["LOCKED", "NEEDS_REVIEW", "PENDING"],
                                "default": "LOCKED",
                            },
                        },
                        "required": ["vo_cut_after_sec", "vo_status"],
                    },
                }
            },
            "required": ["locks"],
        },
    },
    {
        "name": "trigger_xml_export",
        "description": (
            "Triggers the live Rainy GUI engine to export the Multi-Track Pre-Cut Premiere Pro FCP7 XML "
            "(Track V1 = A-Roll Video, Track A1 = Voiceover Narration Packs, Track A2 = A-Roll Native Audio)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {
                    "type": "string",
                    "description": "Destination .xml path. Leave empty to save alongside the active script.",
                },
                "fps": {
                    "type": "number",
                    "description": "Timeline sequence framerate (e.g. 23.976, 24, 25, 29.97, 30.0, 59.94, 60). Default 30.0.",
                },
                "include_clip_markers": {
                    "type": "boolean",
                    "description": "Whether to inject descriptive timeline clip markers with script text and links. Default False.",
                },
                "assembly_segments": {
                    "type": "array",
                    "description": "Optional explicit list of NARRATION_PACK and AROLL_CLIP segments. If omitted, Rainy builds them automatically from the locked A-Roll cards.",
                    "items": {"type": "object"},
                },
            },
        },
    },
]


def load_instructions() -> str:
    """Loads the bundled SOP instructions markdown file shipped alongside this MCP server."""
    inst_path = os.path.join(os.path.dirname(__file__), "instructions.md")
    if os.path.isfile(inst_path):
        try:
            with open(inst_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return ""


def handle_mcp_request(req: Dict[str, Any], pipe_name: str = PIPE_NAME) -> Optional[Dict[str, Any]]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        instructions_text = load_instructions()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {},
                },
                "serverInfo": {
                    "name": "knot-native-mcp",
                    "version": "1.0.0",
                },
                "instructions": instructions_text,
            },
        }

    if method.startswith("notifications/"):
        return None

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": [
                    {
                        "uri": "knot://instructions/vo-assembly",
                        "name": "Rainy VO & A-Roll Assembly SOP",
                        "description": "Standard operating procedure for voiceover alignment, ad-lib detection, and timeline assembly in Rainy.",
                        "mimeType": "text/markdown",
                    }
                ]
            },
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "knot://instructions/vo-assembly":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/markdown",
                            "text": load_instructions(),
                        }
                    ]
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": f"Resource not found: {uri}"},
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        ipc_resp = send_gui_ipc_request(tool_name, tool_args, pipe_name=pipe_name)
        is_err = not ipc_resp.get("ok", False)
        text_out = json.dumps(ipc_resp.get("result") if not is_err else {"error": ipc_resp.get("error")}, indent=2, ensure_ascii=False)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text_out}],
                "isError": is_err,
            },
        }

    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def run_stdio_mcp_server():
    """Main loop for JSON-RPC 2.0 over stdin/stdout."""
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    for line in sys.stdin:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            req = json.loads(line_str)
            resp = handle_mcp_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_mcp_server()
