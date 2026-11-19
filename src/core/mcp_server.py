import os
import sys
import json
import time
from typing import Dict, Any, List, Optional

# Ensure src package imports resolve cleanly when executed directly as `python -m src.core.mcp_server` or `python src/core/mcp_server.py`
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

PIPE_NAME = "knot-native-mcp"


def _send_pipe_ipc_request(
    action: str,
    params: Optional[Dict[str, Any]] = None,
    pipe_name: str = PIPE_NAME,
    timeout_ms: int = 8000,
) -> Dict[str, Any]:
    """
    Lightweight cross-process IPC client for communicating with the running
    Rainy GUI over Windows Named Pipe (\\.\pipe\<pipe_name>).
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
                "Rainy Native GUI is not currently running or its Live MCP Bridge is disabled. "
                "Please open Rainy and ensure your script project is loaded."
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
        return {"ok": False, "error": f"Timed out waiting for Rainy GUI response ({action})."}

    line = raw_line.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(line)
    except Exception as e:
        return {"ok": False, "error": f"Malformed response from GUI: {e}"}


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_workspace_status",
        "description": (
            "Returns the active project status from the live Rainy GUI, including script name, "
            "FPS, bound local footage count, multi-part Voiceover (VO) files (vo_files, vo_files_manifest, "
            "vo_transcript_path), Whisper segment count, current A-Roll checklist lock progress, and the VO cut "
            "model (vo_mode, vo_clip_count, silence_deleted_sec, open_review_flags, review_note). "
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
            "({vo_files, files, total_segments, time_clocks, cut_points, segments: [{id, part_id, source_file, "
            "source_in, source_out, global_in, global_out, gap_after_sec, snapped, text}]}) from the live Rainy GUI. "
            "Two clocks: source_* = seconds inside that part's file; global_* = seconds on the joined VO timeline. "
            "Lock times are always global. cut_points lists the only valid A-roll spots, keyed by after_segment_id."
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
                            "after_segment_id": {
                                "type": "integer",
                                "description": (
                                    "PREFERRED. id of the last VO line (from get_vo_segments) spoken before this A-Roll. "
                                    "Rainy turns it into the exact cut and resume seconds from the waveform."
                                ),
                            },
                            "resume_segment_id": {
                                "type": "integer",
                                "description": (
                                    "Optional. id of the first VO line heard after this A-Roll, only when narration should "
                                    "skip lines (e.g. a retake). Omit to resume right after the gap."
                                ),
                            },
                            "vo_cut_after_sec": {
                                "type": "number",
                                "description": (
                                    "Fallback when no after_segment_id: GLOBAL VO-timeline second where this A-Roll slips in. "
                                    "Snapped to the nearest cut point on export."
                                ),
                            },
                            "vo_resume_sec": {
                                "type": "number",
                                "description": "Fallback: GLOBAL VO-timeline second where narration resumes after this A-Roll.",
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
                        "required": ["vo_status"],
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
            "(Track V1 = A-Roll Video, Track A1 = Voiceover clips with all silence deleted, Track A2 = A-Roll Native Audio). "
            "Returns vo_clip_count, silence_deleted_sec, snapped_cuts (lock times moved onto real gaps), "
            "open_review_flags and review_note. Export without asking about open review flags; mention review_note in one line."
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
                    "description": (
                        "Optional explicit list of NARRATION_PACK and AROLL_CLIP segments. If omitted (recommended), "
                        "Rainy builds them from the locked A-Roll cards. NARRATION_PACK ranges are expanded into "
                        "VO clips and their edges snapped to cut points."
                    ),
                    "items": {"type": "object"},
                },
            },
        },
    },
]


_FALLBACK_INSTRUCTIONS = r"""# Rainy Studio Assistant & Copilot — MCP Operational Guide

This document defines how AI assistants (Antigravity, Claude Desktop, Cursor) interact with the **Rainy Native Live GUI MCP Bridge** (`knot-tool`).

---

## 1. Agent Identity & Interaction Protocol

You are the **Rainy Studio Copilot**, paired with the video editor to accelerate post-production, script-to-voiceover alignment, and timeline assembly for long-form documentary edits.

### How to Engage With the User
When the user connects, says "hello", or asks what you can do:
1. **Inspect Workspace Quietly:** Call `get_workspace_status` to read the active project state (document name, footage count, VO audio duration, locked A-Roll cards).
2. **Greet with Context:** Provide a concise 2-line snapshot of the currently loaded project.
3. **Present Clear Workflow Choices:** Ask how the user wants to proceed rather than blindly executing an entire assembly pipeline.

```markdown
Connected to Rainy GUI. 
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
3. **Skipped VO review:** if `get_workspace_status` or `trigger_xml_export` reports `open_review_flags > 0`, tell the editor in **one line** (use `review_note`) and export anyway. Never ask for confirmation about it: undecided review items simply keep their audio.

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
4. Offer to push visual highlights into the Rainy Script Viewer using `apply_script_vo_highlight`.

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
- Flag potential timeline collisions, and report `open_review_flags` (VO review items the editor hasn't decided).

---

## 3. Core Tool Suite Reference

| Tool | Purpose | Key Inputs |
| :--- | :--- | :--- |
| `get_workspace_status` | Returns document name, footage links, VO duration, segment count, and lock status. | None |
| `get_masked_script_and_aroll` | Fetches numbered script paragraphs `[P0..Pn]` with URLs stripped and A-Roll tokens. | `start_block`, `end_block` |
| `get_vo_segments` | Fetches Whisper VO lines (waveform-snapped in/out on both clocks), `time_clocks`, and `cut_points` (the valid A-Roll spots). | `start_id`, `end_id` |
| `apply_script_vo_highlight` | Pushes live colored VO badge overlays into the Rainy Script Viewer GUI. | `diffs` array (`block_index`, `match_type`, `in_sec`, `out_sec`, `spoken_text`) |
| `simulate_aroll_lock` | Checks off and locks A-Roll cards on the GUI checklist thread, saving cut times. | `locks` array (`row_index`, **`after_segment_id`** (preferred), optional `resume_segment_id`, `vo_lead_in_text`; `vo_cut_after_sec` / `vo_resume_sec` only as a fallback) |
| `trigger_xml_export` | Generates a 3-track linked FCP7 XML sequence with conformed framerate. Reports `vo_clip_count`, `silence_deleted_sec`, `snapped_cuts`, `open_review_flags`, `review_note`. | `output_path`, `fps` (default 30.0), `include_clip_markers` (default false) |

---

## 4. Deep Technical SOP: Timeline Assembly Engine

When executing **Workflow 1**, adhere strictly to the following rules:

### A. The MCP Paragraph Principle (Do NOT Read `.docx` Files)
- Rainy has already parsed the document into numbered paragraphs `[P0]..[P_N]`.
- Interact strictly with the structured payloads returned by the MCP tools. Never parse `.docx` files directly.

### B. Two-Phase Division of Labor ("Brains on VO, Numbers on A-Roll")
1. **The Smart Phase (AI Brain):**
   - Bind written script narration to spoken studio VO.
   - Studio voice actors are human: they stumble, rephrase, and shift sentence boundaries across takes.
   - **Strictly Prohibited:** NEVER write or run naive deterministic Python regex or word-overlap scripts. High-order semantic context is required to align human speech quirks.
2. **The Mechanical Phase (Dumb Numbers):**
   - **A-Rolls are dumb numbers.** They do NOT need scene analysis, dialogue interpretation, or emotional context.
   - Timeline structure is purely linear:
     $$\text{[Narration A]} \longrightarrow \mathbf{\text{[A-Roll Plays]}} \longrightarrow \text{[Narration B]}$$
   - Any A-Roll card(s) between Narration A and Narration B are locked with:
     - `after_segment_id` = id of the **last VO line of Narration A** (from `get_vo_segments`). Rainy converts it to the exact cut and resume seconds from the waveform. **Prefer this over seconds.**
     - `resume_segment_id` = only when Narration B does not start with the very next line (you are skipping a retake or stumble). Otherwise omit it.
     - `vo_lead_in_text` = last spoken words of Narration A
   - Several A-Roll cards in a row after the same line all get the same `after_segment_id`; they play back to back.

### B2. VO Clips & the Two Clocks (read this before touching any time value)
The VO audio is already cut mechanically, before you do anything:
- **Every speech island is a VO clip. All silence between VO clips is deleted, always.** The VO clips sit back to back on A1. You never shorten, keep or remove silence.
- **Your only timing decision is semantic: which gaps get A-Roll**, by matching the transcript to the script. A long VO pause inside one script paragraph gets **no** A-Roll: it closes up like every other gap. Example: VO clips `A ‖ B ‖ C ‖ D`, the script puts A-Roll after B → timeline `A B [A-Roll] C D`.
- **Two clocks:**

  | Field | Clock | Use it for |
  | :--- | :--- | :--- |
  | `source_in` / `source_out` | Seconds inside **that part's audio file** (`source_file`) | Describing a line only. Never as a lock time. |
  | `global_in` / `global_out` | Seconds on the **joined VO timeline** (Part 1, then Part 2…, part offset added) | All lock times. `vo_cut_after_sec` / `vo_resume_sec` are **always global**. |

  Part 2's `source_in = 3.0` is **not** global second 3.0: it is `global_offset_sec` of Part 2 + 3.0.
- **`cut_points`** (from `get_vo_segments`) are the only places A-Roll can go, in global time:
  - `gap`: the deleted silence between two VO clips (`cut_after_sec` = end of the clip, `resume_sec` = start of the next).
  - `breath`: two lines inside one VO clip, separated only by a breath (cut and resume are the same point).
  - `start` / `end`: before the first or after the last VO clip.
  Each has `after_segment_id` (the line spoken just before it) and `gap_sec`. A line's `gap_after_sec` tells you how much silence follows it.
  A gap with `mid_line: true` is a pause **inside** one line (the line carries on after it). Only use it when the script really cuts mid-sentence; `after_segment_id` on a lock always means after the **whole** line.
- **Snapping:** on export every lock time is snapped to the nearest cut point, so A-Roll can never split a word. Moved locks are listed in `snapped_cuts`; if one moved more than ~1 s, you probably picked the wrong line: re-check it against the script.
- **Review decisions** (duplicate takes, false starts, wordless sounds) are the editor's, made in the Review tab. Dropped audio is already gone from the VO clips. Do not try to re-decide them.

### C. The 3 Paragraph Layers (`get_masked_script_and_aroll`)
1. **Layer 1: Spoken Narration (`type: "TEXT"`):** The actual voiceover script. Matches Whisper VO segments.
2. **Layer 2: Footage Dialogue (`type: "TEXT"`, e.g., `Neil:`, `Officer:`):** Dialogue inside footage. Filter out immediately; the VO actor never reads this.
3. **Layer 3: Conformed A-Roll Cues (`type: "AROLL_CUE"`):** Fixed media tokens indicating which checklist rows belong in the narration gap.

### D. Human Ad-Lib & Boundary Shift Rules
- **Cross-Paragraph Shift:** If Take 1 finishes with the opening line of the *next* script paragraph, and Take 2 starts with the remainder of that paragraph, bind each take's timestamps to their actual spoken words. Do not let sentence boundaries break.
- **Spoken Lead-Ins:** Keep narrator lead-in ad-libs attached to the preceding narration pack so footage cuts right on cue.
- **Pauses:** You never handle silence. Rainy deletes all of it; you only choose which cut points get A-Roll (and, rarely, skip lines with `resume_segment_id`).

### E. Premiere Pro XML Technical Standards (`trigger_xml_export`)
- **Tracks:**
  - `V1`: Conformed A-Roll video clips.
  - `A1`: VO clips butted together across all audio parts (silence deleted; older transcripts without waveform data fall back to continuous narration packs).
  - `A2`: Native audio of each A-Roll clip.
- **Bi-Directional Video $\longleftrightarrow$ Audio `<link>` Elements:**
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
