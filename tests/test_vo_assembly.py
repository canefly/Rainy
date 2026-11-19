"""Phase 3: the export builds the narration from VO clips and snaps A-roll cuts onto gaps."""
import json
import xml.etree.ElementTree as ET

import pytest

from src.core import vo_assembly as A
from src.core import vo_review as R
from src.core.models import DocumentState, ScriptItem
from src.core.xml_exporter import build_default_assembly_segments_from_locks, generate_assembled_fcp_xml

LINES = [
    # id, part, in, out, text   (snapped source times: island edges + 40/80 ms handles)
    (0, 1, 0.96, 3.08, "The car was found on Tuesday morning."),
    (1, 1, 4.96, 7.00, "Nobody had seen the driver."),
    (2, 1, 7.00, 9.08, "Police searched the river."),       # shares an island with line 1 (breath at 7.0)
    (3, 1, 11.96, 14.08, "They found nothing at all."),
    (4, 2, 0.96, 4.08, "Three weeks later a letter arrived."),
    (5, 2, 5.96, 8.08, "It was signed by him."),
]
OFFSETS = {1: 0.0, 2: 20.0}


def _manifest(tmp_path, breath_part2=False, version=2):
    segs = [{"id": i, "part_id": p, "source_in": a, "source_out": b, "global_in": a + OFFSETS[p],
             "global_out": b + OFFSETS[p], "whisper_in": a, "whisper_out": b, "snapped": True, "text": t}
            for i, p, a, b, t in LINES]
    islands = [{"part_id": 1, "in": 1.0, "out": 3.0, "peak_db": -20}, {"part_id": 1, "in": 5.0, "out": 9.0, "peak_db": -20},
               {"part_id": 1, "in": 12.0, "out": 14.0, "peak_db": -20}, {"part_id": 2, "in": 1.0, "out": 4.0, "peak_db": -20},
               {"part_id": 2, "in": 6.0, "out": 8.0, "peak_db": -20}]
    if breath_part2:
        islands.append({"part_id": 2, "in": 9.0, "out": 9.3, "peak_db": -50})
    m = {
        "version": version,
        "files": [{"part_id": 1, "filename": "Part 1.wav", "path": str(tmp_path / "Part 1.wav"), "global_offset_sec": 0.0, "duration_sec": 20.0},
                  {"part_id": 2, "filename": "Part 2.wav", "path": str(tmp_path / "Part 2.wav"), "global_offset_sec": 20.0, "duration_sec": 10.0}],
        "total_duration_sec": 30.0,
        "segments": segs,
        "islands": islands if version >= 2 else [],
        "detection": {"pre_roll_s": 0.04, "post_roll_s": 0.08},
    }
    return m


def _save(tmp_path, m):
    path = tmp_path / "vo_transcript.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    return str(path)


def _item(row, cut, resume=None):
    return ScriptItem(
        id=f"aroll-{row}", row_index=row, raw_timestamp="", start_timecode="00:00:10:00", start_seconds=10.0,
        end_timecode="00:00:12:00", end_seconds=12.0, duration_seconds=2.0, duration_display="2s",
        text=f"clip {row}", meta="", sync_id=row, links=[], is_checked=True, vo_locked=True,
        vo_cut_after_sec=cut, vo_resume_sec=resume, vo_status="LOCKED",
    )


def _doc(tmp_path, items, m=None):
    m = m or _manifest(tmp_path)
    return DocumentState(id="d", file_signature="s", name="Assembly", file_path=str(tmp_path / "a.docx"), raw_html="",
                         items=items, vo_transcript_path=_save(tmp_path, m))


def _vo(segments):
    return [(s["part_id"], s["source_in_sec"], s["source_out_sec"]) for s in segments if s["type"] == "NARRATION_PACK"]


def _layout(segments):
    return ["A" if s["type"] == "AROLL_CLIP" else f"{s['part_id']}:{s['source_in_sec']}" for s in segments]


# ---------------------------------------------------------------------------
# Cut spots
# ---------------------------------------------------------------------------
def test_cut_points_cover_gaps_breaths_and_part_boundary(tmp_path):
    model = A.build_cut_model(_manifest(tmp_path))
    pts = [(p["kind"], p["cut_after_sec"], p["resume_sec"], p["after_segment_id"]) for p in model.points]
    assert pts == [
        ("start", 0.0, 0.96, None),
        ("gap", 3.08, 4.96, 0),
        ("breath", 7.0, 7.0, 1),
        ("gap", 9.08, 11.96, 2),
        ("gap", 14.08, 20.96, 3),     # Part 1 -> Part 2, on the global clock
        ("gap", 24.08, 25.96, 4),
        ("end", 28.08, 28.08, 5),
    ]


def test_snap_cut_never_lands_inside_a_word(tmp_path):
    model = A.build_cut_model(_manifest(tmp_path))
    assert A.snap_cut(3.5, model.points)["cut_after_sec"] == 3.08      # inside a gap: that gap
    assert A.snap_cut(2.9, model.points)["cut_after_sec"] == 3.08      # end of a word: the gap after it
    assert A.snap_cut(7.1, model.points)["kind"] == "breath"
    assert A.snap_cut(99.0, model.points)["kind"] == "end"
    assert A.cut_distance(3.5, A.snap_cut(3.5, model.points)) == 0.0


def test_v1_transcript_has_no_cut_model(tmp_path):
    assert A.build_cut_model(_manifest(tmp_path, version=1)) is None


# ---------------------------------------------------------------------------
# Assembly from locks
# ---------------------------------------------------------------------------
def test_narration_is_vo_clips_with_silence_deleted(tmp_path):
    doc = _doc(tmp_path, [_item(1, 3.3), _item(2, 15.0)])
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    assert _layout(segs) == ["1:0.96", "A", "1:4.96", "1:11.96", "A", "2:0.96", "2:5.96"]
    assert _vo(segs)[3] == (2, 0.96, 4.08), "Part 2 clips use Part 2's own file clock"
    p2 = [s for s in segs if s["type"] == "NARRATION_PACK" and s["part_id"] == 2][0]
    assert p2["global_in_sec"] == 20.96 and p2["source_file"].endswith("Part 2.wav")
    arolls = [s for s in segs if s["type"] == "AROLL_CLIP"]
    assert arolls[0]["vo_cut_sec"] == 3.08 and arolls[0]["vo_cut_shift_sec"] == 0.0
    assert arolls[1]["vo_cut_sec"] == 14.08 and arolls[1]["vo_resume_sec"] == 20.96


def test_cut_inside_a_word_is_moved_to_the_nearest_gap(tmp_path):
    doc = _doc(tmp_path, [_item(1, 2.5)])
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    a = next(s for s in segs if s["type"] == "AROLL_CLIP")
    assert a["vo_cut_sec"] == 3.08 and a["vo_cut_shift_sec"] == pytest.approx(0.58)
    assert _vo(segs)[0] == (1, 0.96, 3.08), "the line is never cut short"


def test_breath_cut_splits_one_clip_between_two_lines(tmp_path):
    doc = _doc(tmp_path, [_item(1, 7.0)])
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    assert _layout(segs)[:4] == ["1:0.96", "1:4.96", "A", "1:7.0"]
    before, after = segs[1], segs[3]
    assert before["source_out_sec"] == 7.0 and before["split_out"] and after["split_in"]


def test_cards_in_the_same_gap_play_back_to_back(tmp_path):
    doc = _doc(tmp_path, [_item(1, 3.2), _item(2, 3.4)])
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    assert _layout(segs)[:4] == ["1:0.96", "A", "A", "1:4.96"]


def test_resume_past_the_gap_skips_lines(tmp_path):
    doc = _doc(tmp_path, [_item(1, 3.1, resume=12.0)])   # skip lines 1-2 (e.g. a retake)
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    assert _layout(segs)[:3] == ["1:0.96", "A", "1:11.96"]


def test_review_drops_are_not_exported(tmp_path):
    m = _manifest(tmp_path, breath_part2=True)
    breath = next(f for f in R.detect_flags(m) if f["type"] == R.BREATH)
    assert any(c["global_in"] > 28.9 for c in A.build_cut_model(m).clips), "kept while undecided"
    m["review"] = {"decisions": {breath["id"]: R.DROP}}
    model = A.build_cut_model(m)
    assert not any(c["global_in"] > 28.9 for c in model.clips)
    assert model.open_flags == 0


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------
def _a1(xml):
    root = ET.fromstring(xml.split("<!DOCTYPE xmeml>\n", 1)[1])
    track = root.find("sequence").findall("./media/audio/track")[0]
    return [{k: int(c.find(k).text) for k in ("start", "end", "in", "out")} | {"name": c.find("name").text,
             "file": c.find("file").get("id") if c.find("file") is not None else ""}
            for c in track.findall("clipitem")]


def test_xml_butts_vo_clips_and_keeps_handles_whole(tmp_path):
    doc = _doc(tmp_path, [_item(1, 7.0)])
    report = {}
    xml = generate_assembled_fcp_xml(doc, fps=24.0, report=report)
    a1 = _a1(xml)
    assert len(a1) == 6 and a1[0]["name"].startswith("VO Clip 1")
    assert (a1[0]["in"], a1[0]["out"]) == (23, 74), "0.96 s floors, 3.08 s ceils: handles never trimmed"
    assert a1[1]["out"] == a1[2]["in"] == 168, "a breath split meets on one frame"
    assert a1[3]["file"] == a1[0]["file"] != a1[4]["file"], "Part 2's clips point at Part 2's file"
    # VO clips sit back to back except where the A-roll plays
    assert a1[1]["start"] == a1[0]["end"]
    assert a1[2]["start"] == a1[1]["end"] + 48, "the 2 s A-roll sits between the two halves"
    assert report["vo_mode"] == "vo_clips" and report["vo_clip_count"] == 6
    assert report["silence_deleted_sec"] > 0 and report["open_review_flags"] == 0


def test_explicit_ai_packs_become_vo_clips_with_snapped_edges(tmp_path):
    doc = _doc(tmp_path, [])
    segs = [{"type": "NARRATION_PACK", "global_in_sec": 0.0, "global_out_sec": 13.0}]
    a1 = _a1(generate_assembled_fcp_xml(doc, assembly_segments=segs, fps=24.0))
    assert len(a1) == 2, "0-13 s: the 13.0 end snaps back to the gap after line 2, not into line 3"
    assert a1[-1]["out"] == 218   # ceil(9.08 * 24)


def test_old_transcripts_keep_continuous_narration(tmp_path):
    doc = _doc(tmp_path, [_item(1, 3.3)], m=_manifest(tmp_path, version=1))
    segs = build_default_assembly_segments_from_locks(doc, fps=24.0)
    first = segs[0]
    assert not first.get("vo_clip") and first["source_in_sec"] == 0.0 and first["source_out_sec"] == 3.3


# ---------------------------------------------------------------------------
# MCP helpers
# ---------------------------------------------------------------------------
def test_locks_by_segment_id(tmp_path):
    model = A.build_cut_model(_manifest(tmp_path))
    assert A.resolve_lock_times({"after_segment_id": 0}, model) == (3.08, 4.96)
    assert A.resolve_lock_times({"after_segment_id": 1}, model) == (7.0, 7.0)
    assert A.resolve_lock_times({"after_segment_id": 3}, model) == (14.08, 20.96)
    assert A.resolve_lock_times({"after_segment_id": 0, "resume_segment_id": 3}, model) == (3.08, 11.96)
    assert A.resolve_lock_times({"vo_cut_after_sec": 5.0}, model) == (5.0, None), "raw seconds pass through"
    assert A.resolve_lock_times({"after_segment_id": 0}, None) == (None, None)


def test_cut_points_for_requested_lines(tmp_path):
    model = A.build_cut_model(_manifest(tmp_path))
    pts = A.cut_points_for_segments(model, [{"id": 3}, {"id": 4}])
    assert [p["after_segment_id"] for p in pts] == [3, 4]
    assert A.cut_points_for_segments(None, [{"id": 3}]) == []


def test_review_status_payload_and_note(tmp_path):
    model = A.build_cut_model(_manifest(tmp_path, breath_part2=True))
    payload = A.review_status_payload(model)
    assert payload["vo_mode"] == "vo_clips" and payload["open_review_flags"] == 1
    assert "1 VO review item undecided" in payload["review_note"] and "do not ask" in payload["review_note"]
    assert A.review_status_payload(None) == {"vo_mode": "continuous", "open_review_flags": 0, "review_note": ""}


def test_pause_inside_one_line_is_marked_mid_line(tmp_path):
    m = _manifest(tmp_path)
    m["islands"].append({"part_id": 1, "in": 15.0, "out": 17.0, "peak_db": -20})
    line3 = next(s for s in m["segments"] if s["id"] == 3)
    line3["source_out"] = line3["global_out"] = 17.08     # "They found ... nothing at all." spans two islands
    model = A.build_cut_model(m)
    gap = next(p for p in model.points if p["cut_after_sec"] == 14.08)
    assert gap["mid_line"] and gap["after_segment_id"] == 3
    assert not next(p for p in model.points if p["cut_after_sec"] == 3.08)["mid_line"]
    assert A.resolve_lock_times({"after_segment_id": 3}, model)[0] == 17.08, "'after line 3' means after all of it"
