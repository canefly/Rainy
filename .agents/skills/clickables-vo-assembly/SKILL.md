---
name: clickables-vo-assembly
description: Standard Operating Procedure (SOP) for AI-assisted Voiceover (VO) + A-Roll timeline assembly using the Clickables Native Live GUI MCP Bridge and Premiere Pro MCP. Use whenever matching Whisper VO segments against a script, identifying VO ad-libs/modifications, locking A-Roll checklist cards in Clickables, or exporting/building a pre-cut V1/A2 A-Roll + A1 Voiceover sequence.
---

# Clickables VO + A-Roll Assembly Engine — Agent SOP

This skill governs how Antigravity interacts with the **Clickables Native Live GUI MCP Bridge** (`clickables-native-mcp`) to cross-match a transcribed Voiceover (`.wav`/`.mp3`) against a parsed `.docx` script, highlight spoken VO modifications/ad-libs in the Clickables UI, check off/lock A-Roll cards, and build the final rough-cut timeline.

---

## 0. Mandatory Pre-Flight Rule (Ask the Editor First!)

Before running the full VO-to-Script assembly or exporting a timeline, **you MUST ask Cane how he wants the final sequence delivered**:
1. **Option A — Export Multi-Track FCP7 `.xml` via Clickables:** Generates a Premiere-ready `.xml` file (`V1` = A-Roll Video, `A1` = VO Narration Packs, `A2` = A-Roll Native Audio).
2. **Option B — Push Directly to Premiere Pro via `premiere-pro` MCP:** Builds the sequence directly inside the open Premiere Pro project using the exact same track assignment (`V1` + `A2` = A-Roll, `A1` = Voiceover).

---

## 1. Strict Track & Pacing Boundaries

1. **Track Layout (Non-Negotiable):**
   - **`V1` (Video Track 1):** Bound local A-Roll video clips at their conformed SMPTE `in`/`out` frames.
   - **`A1` (Audio Track 1):** Contiguous Voiceover **Narration Packs** (`source_in_sec` $\to$ `source_out_sec`), sliced and rippled around each A-Roll clip.
   - **`A2` (Audio Track 2):** Native audio from each A-Roll clip, playing during the gaps on `A1`.
2. **No Micro-Pause Cutting:**
   - Do **not** slice up natural 400ms–1s breaths inside a contiguous narration passage.
   - Only slice the VO at:
     - **A-Roll Injection Points** (between the end of the pre-A-Roll VO segment/ad-lib and the start of the post-A-Roll VO segment).
     - **False Starts / Retakes (`RETAKE_CUT`)** where the narrator stumbled and repeated a sentence.
3. **Zero External SQLite Modification:**
   - Never modify `~/.clickables/clickables.db` directly. Always call the Clickables Live GUI MCP tools (`apply_script_vo_highlight`, `simulate_aroll_lock`, `trigger_xml_export`) so the running Clickables GUI updates its own widgets and persists state natively.

---

## 2. How to Handle Unpredictable `.docx` Scripts (`@editor` Noise & Pasted Clip Transcripts)

Real scripts contain three mixed elements without explicit labels:
- **Spoken Narration** (read by the VO actor, sometimes paraphrased or followed by ad-libs).
- **Noisy `@editor` Instructions** (e.g., `@editor zoom in`, `@editor blur face`).
- **Pasted Interrogation / Clip Transcripts** (verbatim dialogue spoken by people *inside* the A-Roll video clip, pasted into the `.docx` for reference).

### The Ground-Truth Filter Rule:
- **The Whisper VO Segments (`get_vo_segments`) are the 100% ground truth of what was spoken by the narrator.**
- When aligning Whisper VO segments against the numbered script paragraphs `[P0..Pn]` from `get_masked_script_and_aroll`:
  - Any `[P#]` paragraph that matches (verbatim or semantically) a Whisper VO segment is **Spoken Narration**.
  - Any `[P#]` paragraph sitting near an `<<AROLL_CLIP ...>>` token that is **not spoken in the Whisper VO** is automatically recognized as an **unspoken `@editor` note or pasted clip transcript** and must be ignored when calculating VO Narration Pack boundaries!

---

## 3. Step-by-Step Execution Workflow

### Step 1: Inspect Active Workspace & Multi-Part VO Manifest
Call `get_workspace_status` to verify:
- A script is open (`document_name`).
- Local footage is bound (`linked_footage_count`).
- Multi-part Voiceover files are bound and transcribed (`vo_files`, `vo_files_count`, `vo_files_manifest`, `vo_segment_count > 0`).
  - Note: In multi-part VO projects, `vo_files_manifest` lists each audio part (`part_id`, `filename`, `duration_sec`, `global_offset_sec`).
  - Each Whisper VO segment (`get_vo_segments`) contains both **local source timecodes** (`source_in`, `source_out`, `source_file`) and **global timeline timecodes** (`global_in`, `global_out`).
  - Always use `global_in`/`global_out` (or the segment `start`/`end`) for relative script positioning, and Clickables automatically conforms multi-track cuts to the respective `source_file` during XML export!

### Step 2: Fetch Masked Script & Whisper VO Segments (Context-Window Safe)
- Call `get_masked_script_and_aroll(start_block=0, end_block=200)` and `get_vo_segments(start_id=0, end_id=200)` (or process in windows for very long scripts).
- Notice that `get_masked_script_and_aroll` has already stripped noisy URLs and replaced raw PTS timestamps with clean tokens:
  `[P42] <<AROLL_CLIP row=1 item_id="..." meta="S01E02" smpte_in="00:20:52:00" smpte_out="00:21:07:00" dur_sec=15.0 file="Richie_Interview.mp4">>`

### Step 3: Cross-Match Narration, Spot Ad-libs & Lock A-Roll Cards Live in the UI
For each `<<AROLL_CLIP>>` cue in sequence:
1. Locate the **Pre-A-Roll Narration Paragraph** `[P_before]` and the **Post-A-Roll Narration Paragraph** `[P_after]`.
2. Match them to the corresponding Whisper VO segments:
   - **If the VO modified words / paraphrased `[P_before]`**, or **added an unscripted ad-lib** before/after the clip, call `apply_script_vo_highlight` with `match_type="MODIFIED"` or `"ADLIB"` and `vo_actual_text` set to what the narrator actually said.
   - **Lead-in Ad-lib Rule:** If the narrator adds a lead-in remark right before the clip (e.g., *"And watch how nervous he gets here..."*), keep that ad-lib inside the preceding Narration Pack so `vo_cut_after_sec` lands at the `end` of the lead-in ad-lib segment!
   - **Retake Rule:** If the narrator repeats a sentence twice (Take 1 vs. Take 2), mark Take 1 as `RETAKE_CUT` and use Take 2.
3. Call `simulate_aroll_lock` with:
   - `row_index`: The A-Roll card's `row` number.
   - `vo_cut_after_sec`: Exact `end` timestamp (in seconds) of the VO segment right before the A-Roll slips in.
   - `vo_resume_sec`: Exact `start` timestamp (in seconds) of the VO segment where narration resumes after the A-Roll.
   - `vo_lead_in_text`: The exact VO phrase immediately preceding the cut.
   - `vo_status`: `"LOCKED"` (or `"NEEDS_REVIEW"` if the narrator skipped the section entirely).

### Step 4: Final Sequence Delivery
- **If Cane chose Option A (Export FCP7 XML):**
  Call `trigger_xml_export(output_path=...)` and report the generated `.xml` file path and summary statistics.
- **If Cane chose Option B (Direct Premiere Pro MCP):**
  Use the locked `NARRATION_PACK` (`A1`) and `AROLL_CLIP` (`V1` + `A2`) timings to place the clips directly onto the active Premiere Pro sequence via the `premiere-pro` MCP server.
