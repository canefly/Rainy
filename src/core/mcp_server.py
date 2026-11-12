import os
import sys
import json
import time
from typing import Dict, Any, List, Optional

# Ensure src package imports resolve cleanly when executed directly as `python -m src.core.mcp_server` or `python src/core/mcp_server.py`
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

PIPE_NAME = "clickables-native-mcp"


def _send_pipe_ipc_request(
    action: str,
    params: Optional[Dict[str, Any]] = None,
    pipe_name: str = PIPE_NAME,
    timeout_ms: int = 8000,
) -> Dict[str, Any]:
    """
    Lightweight cross-process IPC client for communicating with the running
    Clickables GUI over Windows Named Pipe (\\.\pipe\<pipe_name>).
    Falls back to Qt QLocalSocket when invoked inside an in-process Qt unit test.
    """
    qt_core = sys.modules.get("PySide6.QtCore")
    if qt_core is not None and qt_core.QCoreApplication.instance() is not None:
        from src.core.gui_ipc_server import send_gui_ipc_request
        return send_gui_ipc_request(action, params, pipe_name=pipe_name, timeout_ms=timeout_ms)

    pipe_path = rf"\\.\pipe\{pipe_name}" if os.name == "nt" and not pipe_name.startswith(r"\\.\pipe\\") else pipe_name
    payload = (json.dumps({"action": action, "params": params or {}}, ensure_ascii=False) + "\n").encode("utf-8")

    deadline = time.monotonic() + (min(2000, timeout_ms) / 1000.0)
    pipe_file = None
    while time.monotonic() < deadline:
        try:
            pipe_file = open(pipe_path, "r+b", buffering=0)
            break
        except OSError:
            time.sleep(0.05)

    if pipe_file is None:
        return {
            "ok": False,
            "error": (
                "Clickables Native GUI is not currently running or its Live MCP Bridge is disabled. "
                "Please open Clickables and ensure your script project is loaded."
            ),
        }

    try:
        pipe_file.write(payload)
        pipe_file.flush()
        raw_line = pipe_file.readline()
    except Exception as e:
        return {"ok": False, "error": f"IPC pipe communication error ({action}): {e}"}
    finally:
        try:
            pipe_file.close()
        except Exception:
            pass

    if not raw_line:
        return {"ok": False, "error": f"Timed out waiting for Clickables GUI response ({action})."}

    line = raw_line.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(line)
    except Exception as e:
        return {"ok": False, "error": f"Malformed response from GUI: {e}"}


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_workspace_status",
        "description": (
            "Returns the active project status from the live Clickables GUI, including script name, "
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
            "from the live Clickables GUI."
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
            "Highlights one or more script paragraphs [P#] directly inside the live Clickables Script Viewer "
            "showing what the VO narrator actually said (EXACT, MODIFIED, ADLIB, or RETAKE_CUT)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "diffs": {
                    "type": "array",
                    "description": "List of paragraph VO diff objects to render in the Clickables Script Viewer.",
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
            "Simulates checking off and locking one or more A-Roll cards directly on the live Clickables GUI "
            "Timeline Checklist. Updates the card's VO cut badge (e.g. 'VO @ 00:45.9'), clicks its native "
            "checkbox on the GUI thread, and triggers Clickables' native autosave."
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
            "Triggers the live Clickables GUI engine to export the Multi-Track Pre-Cut Premiere Pro FCP7 XML "
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
                    "description": "Optional explicit list of NARRATION_PACK and AROLL_CLIP segments. If omitted, Clickables builds them automatically from the locked A-Roll cards.",
                    "items": {"type": "object"},
                },
            },
        },
    },
]


_FALLBACK_INSTRUCTIONS = """# Clickables Studio Assistant & Copilot — MCP Operational Guide

This document defines how AI assistants (Antigravity, Claude Desktop, Cursor) interact with the **Clickables Native Live GUI MCP Bridge** (`clickables-tool`).

---

## 1. Agent Identity & Interaction Protocol

You are the **Clickables Studio Copilot**, paired with the video editor to accelerate post-production, script-to-voiceover alignment, and timeline assembly for long-form documentary edits.

### How to Engage With the User
When the user connects, says "hello", or asks what you can do:
1. **Inspect Workspace Quietly:** Call `get_workspace_status` to read the active project state (document name, footage count, VO audio duration, locked A-Roll cards).
2. **Greet with Context:** Provide a concise 2-line snapshot of the currently loaded project.
3. **Present Clear Workflow Choices:** Ask how the user wants to proceed rather than blindly executing an entire assembly pipeline.

```markdown
Connected to Clickables GUI. 
Active Project: [Document Name] ([N] A-Rolls, [M] VO segments loaded).

How would you like to proceed?
1. 🎬 Auto A-Roll & VO Timeline Assembly (Export Pre-Cut XML or Push to Premiere Pro)
2. 🎙️ Script vs. VO Audit & Ad-Lib Check (Inspect discrepancies, rephrasings, or retakes)
3. 🔍 Deep Project Search (Find words, character dialogue, PTS timecodes, or A-Roll cues)
4. 📋 Checklist & Media Health Audit (Review locked cards, time gaps, or unlinked footage)
```

---

## 2. Interactive Workflow Menus

### Workflow 1: 🎬 Auto A-Roll & VO Timeline Assembly
When the user requests timeline assembly:
1. **Clarify Delivery Target & Preferences:**
   - **Target:**
     - **Option A (FCP7 XML):** Generate a conformed multi-track XML file via `trigger_xml_export` ready for import into Premiere Pro or DaVinci Resolve.
     - **Option B (Direct Premiere Pro MCP):** If Premiere Pro is running with the Premiere MCP server active, inject the clips directly into the active Premiere timeline.
   - **Sequence Framerate:** Default to **30.0 fps** (or confirm if 23.976 / 29.97 fps is required).
   - **Sequence Markers:** Default to **clean timeline (zero markers)** unless the editor specifically requests clip markers.
2. **Execute Assembly:** Follow the deep technical SOP in Section 4 below.

---

### Workflow 2: 🎙️ Script vs. VO Audit & Ad-Lib Check
When the user wants to verify what was spoken against the written script without modifying the timeline:
1. Fetch paragraphs via `get_masked_script_and_aroll` and Whisper VO segments via `get_vo_segments`.
2. Compare spoken narration against script text.
3. Classify matches and surface findings to the editor:
   - `EXACT`: Clean verbatim read.
   - `MODIFIED`: Narrator rephrased words or shifted sentence order.
   - `ADLIB`: Narrator added unscripted lead-ins or commentary.
   - `RETAKE_CUT`: Stumbled takes or abandoned sentences.
   - `MISSING`: Written script lines that the narrator skipped entirely.
4. Offer to push visual highlights into the Clickables Script Viewer using `apply_script_vo_highlight`.

---

### Workflow 3: 🔍 Deep Project Search & Query
When the user asks to find a word, character quote, scene, or timecode:
- **VO Audio Search:** Scan `get_vo_segments` for spoken words/phrases; return matching Whisper segment ID, in/out timestamps, and source VO audio file.
- **Script Text Search:** Scan `get_masked_script_and_aroll` for character dialogue (e.g. `Neil:`, `Officer:`) or narration lines; return the paragraph index `[P#]`.
- **A-Roll Card Search:** Scan conformed cues for row numbers, timecodes, or footage filenames; return card row index, frame duration, and SMPTE in/out.

---

### Workflow 4: 📋 Checklist & Media Health Audit
When the user asks for project status or troubleshooting:
- Check for unlinked footage or missing file paths.
- Check lock status: which A-Roll rows are `LOCKED`, `NEEDS_REVIEW`, or `PENDING`.
- Flag potential timeline collisions or negative silence gaps between narration packs.

---

## 3. Core Tool Suite Reference

| Tool | Purpose | Key Inputs |
| :--- | :--- | :--- |
| `get_workspace_status` | Returns document name, footage links, VO duration, segment count, and lock status. | None |
| `get_masked_script_and_aroll` | Fetches numbered script paragraphs `[P0..Pn]` with URLs stripped and A-Roll tokens. | `start_block`, `end_block` |
| `get_vo_segments` | Fetches Whisper VO segments with local and global in/out timestamps. | `start_id`, `end_id` |
| `apply_script_vo_highlight` | Pushes live colored VO badge overlays into the Clickables Script Viewer GUI. | `diffs` array (`block_index`, `match_type`, `in_sec`, `out_sec`, `spoken_text`) |
| `simulate_aroll_lock` | Checks off and locks A-Roll cards on the GUI checklist thread, saving cut times. | `locks` array (`row_index`, `vo_cut_after_sec`, `vo_resume_sec`, `vo_lead_in_text`) |
| `trigger_xml_export` | Generates a 3-track linked FCP7 XML sequence with conformed framerate. | `output_path`, `fps` (default 30.0), `include_clip_markers` (default false) |

---

## 4. Deep Technical SOP: Timeline Assembly Engine

When executing **Workflow 1**, adhere strictly to the following rules:

### A. The MCP Paragraph Principle (Do NOT Read `.docx` Files)
- Clickables has already parsed the document into numbered paragraphs `[P0]..[P_N]`.
- Interact strictly with the structured payloads returned by the MCP tools. Never parse `.docx` files directly.

### B. Two-Phase Division of Labor ("Brains on VO, Numbers on A-Roll")
1. **The Smart Phase (AI Brain):**
   - Bind written script narration to spoken studio VO.
   - Studio voice actors are human: they stumble, rephrase, and shift sentence boundaries across takes.
   - **Strictly Prohibited:** NEVER write or run naive deterministic Python regex or word-overlap scripts. High-order semantic context is required to align human speech quirks.
2. **The Mechanical Phase (Dumb Numbers):**
   - **A-Rolls are dumb numbers.** They do NOT need scene analysis, dialogue interpretation, or emotional context.
   - Timeline structure is purely linear:
     $$\\text{[Narration Pack A (ends at } T_{\\text{out}}\\text{)]} \\longrightarrow \\mathbf{\\text{[A-Roll Plays]}} \\longrightarrow \\text{[Narration Pack B (starts at } T_{\\text{in}}\\text{)]}$$
   - Any A-Roll card(s) between Narration A and Narration B simply inherit:
     - `vo_cut_after_sec` = $T_{\\text{out}}$ (end of Narration A)
     - `vo_resume_sec` = $T_{\\text{in}}$ (start of Narration B)
     - `vo_lead_in_text` = last spoken words of Narration A

### C. The 3 Paragraph Layers (`get_masked_script_and_aroll`)
1. **Layer 1: Spoken Narration (`type: "TEXT"`):** The actual voiceover script. Matches Whisper VO segments.
2. **Layer 2: Footage Dialogue (`type: "TEXT"`, e.g., `Neil:`, `Officer:`):** Dialogue inside footage. Filter out immediately; the VO actor never reads this.
3. **Layer 3: Conformed A-Roll Cues (`type: "AROLL_CUE"`):** Fixed media tokens indicating which checklist rows belong in the narration gap.

### D. Human Ad-Lib & Boundary Shift Rules
- **Cross-Paragraph Shift:** If Take 1 finishes with the opening line of the *next* script paragraph, and Take 2 starts with the remainder of that paragraph, bind each take's timestamps to their actual spoken words. Do not let sentence boundaries break.
- **Spoken Lead-Ins:** Keep narrator lead-in ad-libs attached to the preceding narration pack so footage cuts right on cue.
- **Micro-Pauses:** Do NOT slice natural 300ms–1.0s breath pauses inside contiguous narration. Only slice narration where A-Roll cuts occur or where stumbles are removed.

### E. Premiere Pro XML Technical Standards (`trigger_xml_export`)
- **Tracks:**
  - `V1`: Conformed A-Roll video clips.
  - `A1`: Contiguous VO Narration Packs across all audio parts.
  - `A2`: Native audio of each A-Roll clip.
- **Bi-Directional Video $\\longleftrightarrow$ Audio `<link>` Elements:**
  - Every camera clip with video on `V1` and audio on `A2` must contain matching `<sourcetrack>` and `<link>` elements pointing to both tracks. Without this, Premiere Pro treats them as detached clips.
- **Framerate:** Conforms strictly to requested fps (e.g. 30 fps: `timebase=30, ntsc=FALSE`).
- **Markers:** Disabled by default (`include_clip_markers=False`) for a clean editor timeline.
"""


def load_instructions() -> str:
    """Loads the bundled SOP instructions markdown file shipped alongside this MCP server."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "instructions.md"),
        os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "instructions.md"),
    ]
    for inst_path in candidates:
        if os.path.isfile(inst_path):
            try:
                with open(inst_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return _FALLBACK_INSTRUCTIONS


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
                    "name": "clickables-native-mcp",
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
                        "uri": "clickables://instructions/vo-assembly",
                        "name": "Clickables VO & A-Roll Assembly SOP",
                        "description": "Standard operating procedure for voiceover alignment, ad-lib detection, and timeline assembly in Clickables.",
                        "mimeType": "text/markdown",
                    }
                ]
            },
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "clickables://instructions/vo-assembly":
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
        ipc_resp = _send_pipe_ipc_request(tool_name, tool_args, pipe_name=pipe_name)
        is_err = not ipc_resp.get("ok", False)
        result_payload = ipc_resp.get("result") if not is_err else {"error": ipc_resp.get("error")}
        if not is_err and tool_name == "get_workspace_status" and isinstance(result_payload, dict):
            result_payload = dict(result_payload)
            result_payload["sop_instructions"] = load_instructions()
        text_out = json.dumps(result_payload, indent=2, ensure_ascii=False)
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
