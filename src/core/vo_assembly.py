"""
VO side of the timeline assembly (phase 3 of the VO pipeline, see docs/vo-pipeline.md).

  * The narration is built from the VO clips (vo_review.compute_kept_ranges): every
    speech island with its handles, minus the editor's drops, butted together. All
    silence is gone.
  * A-roll cut points chosen by the MCP AI are snapped onto valid cut spots, so
    A-roll never splits a word:
      - "gap":    the deleted silence between two VO clips (cut at the clip's out,
                  resume at the next clip's in)
      - "breath": two lines inside one clip (sentences separated only by a breath);
                  the cut lands on the quietest point between them
  * Gaps without A-roll just close up.

Two clocks (never mix them):
  source time = seconds inside one part's audio file  (in/out, source_in/source_out)
  global time = seconds on the joined VO timeline     (global_in/global_out, lock times)
Every lock time (vo_cut_after_sec / vo_resume_sec) is GLOBAL time.
"""
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import vo_review as R
from .vo_engine import load_vo_transcript_json
from .vo_islands import DetectionSettings

EPS = 1e-6
BREATH_JOIN_TOL_S = 0.02   # two lines "touch" inside one clip when their edges are this close


@dataclass
class CutModel:
    manifest: Dict[str, Any]
    clips: List[Dict[str, Any]]                 # kept ranges, global order
    points: List[Dict[str, Any]]                # valid cut spots, global order
    open_flags: int = 0
    summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_sec(self) -> float:
        files = self.manifest.get("files") or []
        if not files:
            return self.clips[-1]["global_out"] if self.clips else 0.0
        last = files[-1]
        return float(last.get("global_offset_sec", 0.0)) + float(last.get("duration_sec") or 0.0)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def has_cut_model(manifest: Optional[Dict[str, Any]]) -> bool:
    return bool(manifest) and int(manifest.get("version", 1)) >= 2 and bool(manifest.get("islands"))


def build_cut_model(manifest: Dict[str, Any]) -> Optional[CutModel]:
    """None for transcripts without speech islands (version 1): the export then keeps
    the old continuous narration."""
    if not has_cut_model(manifest):
        return None
    block = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
    review = R.ReviewSettings.from_dict(block.get("settings"))
    detection = DetectionSettings.from_dict(manifest.get("detection"))
    decisions = dict(block.get("decisions") or {})
    flags = R.detect_flags(manifest, review)
    decisions = R.prune_decisions(flags, decisions)
    clips = sorted(R.compute_kept_ranges(manifest, flags, decisions, detection), key=lambda c: c["global_in"])
    if not clips:
        return None
    return CutModel(
        manifest=manifest,
        clips=clips,
        points=cut_points(manifest, clips),
        open_flags=R.open_count(flags, decisions),
        summary=R.cut_summary(manifest, flags, decisions, detection),
    )


def load_cut_model(doc_state: Any) -> Optional[CutModel]:
    path = getattr(doc_state, "vo_transcript_path", "") or ""
    if not path or not os.path.isfile(path):
        return None
    manifest = load_vo_transcript_json(path)
    return build_cut_model(manifest) if manifest else None


# ---------------------------------------------------------------------------
# Cut spots
# ---------------------------------------------------------------------------
def _seg_global(s: Dict[str, Any], offsets: Dict[int, float]) -> tuple:
    off = offsets.get(int(s.get("part_id", 1)), 0.0)
    gi = s.get("global_in")
    go = s.get("global_out")
    gi = float(gi) if gi is not None else float(s.get("source_in", 0.0)) + off
    go = float(go) if go is not None else float(s.get("source_out", 0.0)) + off
    return gi, go


def cut_points(manifest: Dict[str, Any], clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Every place A-roll may go, in global time:
      {kind, cut_after_sec, resume_sec, after_segment_id, gap_sec}
    "start"/"end" are the edges of the narration; "gap" spans a deleted silence;
    "breath" is a single point between two lines that share one VO clip.
    after_segment_id is the last line spoken before the spot (None at the start).
    """
    offsets = {int(f.get("part_id", 1)): float(f.get("global_offset_sec", 0.0)) for f in manifest.get("files") or []}
    segs = sorted(
        ((_seg_global(s, offsets), s) for s in manifest.get("segments") or []),
        key=lambda x: x[0][0],
    )

    def last_seg_before(t: float) -> Optional[int]:
        best = None
        for (gi, go), s in segs:
            if gi < t - EPS:
                best = int(s.get("id", 0))
            else:
                break
        return best

    pts: List[Dict[str, Any]] = [{
        "kind": "start", "cut_after_sec": 0.0, "resume_sec": round(clips[0]["global_in"], 3),
        "after_segment_id": None, "gap_sec": round(clips[0]["global_in"], 3),
    }]
    ends = {int(s.get("id", 0)): go for (gi, go), s in segs}
    for a, b in zip(clips, clips[1:]):
        before = last_seg_before(a["global_out"])
        pts.append({
            "kind": "gap",
            "cut_after_sec": round(a["global_out"], 3),
            "resume_sec": round(b["global_in"], 3),
            "after_segment_id": before,
            "gap_sec": round(b["global_in"] - a["global_out"], 3),
            # a pause inside one Whisper line (the line carries on after the gap)
            "mid_line": before is not None and ends.get(before, 0.0) > b["global_in"] + BREATH_JOIN_TOL_S,
        })
    # Lines that meet inside one clip: the snap put both edges on the quietest point between them
    for ((ai, ao), sa), ((bi, bo), sb) in zip(segs, segs[1:]):
        if abs(bi - ao) > BREATH_JOIN_TOL_S:
            continue
        t = (ao + bi) / 2.0
        if any(c["global_in"] + EPS < t < c["global_out"] - EPS for c in clips):
            pts.append({"kind": "breath", "cut_after_sec": round(t, 3), "resume_sec": round(t, 3),
                        "after_segment_id": int(sa.get("id", 0)), "gap_sec": 0.0})
    end = clips[-1]["global_out"]
    pts.append({"kind": "end", "cut_after_sec": round(end, 3), "resume_sec": round(end, 3),
                "after_segment_id": last_seg_before(end), "gap_sec": 0.0})
    pts.sort(key=lambda p: (p["cut_after_sec"], p["resume_sec"]))
    return pts


def _distance(t: float, lo: float, hi: float) -> float:
    return 0.0 if lo - EPS <= t <= hi + EPS else min(abs(t - lo), abs(t - hi))


def cut_distance(t: float, spot: Dict[str, Any]) -> float:
    """How far a requested time is from a cut spot (0 anywhere inside its gap)."""
    return _distance(t, spot["cut_after_sec"], spot["resume_sec"])


def snap_cut(t: float, points: List[Dict[str, Any]], not_before: float = 0.0) -> Dict[str, Any]:
    """Nearest cut spot to a requested cut time. A time anywhere inside a gap counts as
    that gap. Spots before `not_before` (the narration already used) are skipped."""
    usable = [p for p in points if p["cut_after_sec"] >= not_before - EPS] or points[-1:]
    return min(usable, key=lambda p: (_distance(t, p["cut_after_sec"], p["resume_sec"]),
                                      0 if p["kind"] != "breath" else 1))


def snap_resume(t: float, points: List[Dict[str, Any]], not_before: float) -> float:
    """Where the narration picks up again: the nearest spot's resume time (a clip's in
    or a breath point), never before `not_before`."""
    usable = [p for p in points if p["resume_sec"] >= not_before - EPS]
    if not usable:
        return not_before
    p = min(usable, key=lambda p: (_distance(t, p["cut_after_sec"], p["resume_sec"]), p["resume_sec"]))
    return p["resume_sec"]


def point_after_segment(seg_id: int, model: CutModel) -> Optional[Dict[str, Any]]:
    """The cut spot right after a line ends (what the AI means by 'A-roll after line N')."""
    seg = next((s for s in model.manifest.get("segments") or [] if int(s.get("id", -1)) == int(seg_id)), None)
    if seg is None:
        return None
    offsets = {int(f.get("part_id", 1)): float(f.get("global_offset_sec", 0.0)) for f in model.manifest.get("files") or []}
    _, go = _seg_global(seg, offsets)
    return snap_cut(go, model.points)


def resume_at_segment(seg_id: int, model: CutModel) -> Optional[float]:
    """Where narration resumes so that line N is the first line heard again."""
    seg = next((s for s in model.manifest.get("segments") or [] if int(s.get("id", -1)) == int(seg_id)), None)
    if seg is None:
        return None
    offsets = {int(f.get("part_id", 1)): float(f.get("global_offset_sec", 0.0)) for f in model.manifest.get("files") or []}
    gi, _ = _seg_global(seg, offsets)
    return snap_resume(gi, model.points, 0.0)


# ---------------------------------------------------------------------------
# Narration segments
# ---------------------------------------------------------------------------
def narration_clips(model: CutModel, a: float, b: float, **meta) -> List[Dict[str, Any]]:
    """NARRATION_PACK segments for the VO clips inside global [a, b], one per clip (a clip
    crossing a or b is split there). Each carries both clocks plus `vo_clip: True`.
    split_in/split_out mark edges that were cut at a breath point rather than a clip edge."""
    files = {int(f.get("part_id", 1)): f for f in model.manifest.get("files") or []}
    out: List[Dict[str, Any]] = []
    for c in model.clips:
        gi, go = max(a, c["global_in"]), min(b, c["global_out"])
        if go - gi < 0.01:
            continue
        part = files.get(int(c["part_id"]), {})
        off = float(part.get("global_offset_sec", 0.0))
        out.append({
            "type": "NARRATION_PACK",
            "vo_clip": True,
            "part_id": int(c["part_id"]),
            "source_file": str(part.get("path", "")),
            "source_filename": str(part.get("filename", "") or os.path.basename(str(part.get("path", "")))),
            "source_in_sec": round(gi - off, 3),
            "source_out_sec": round(go - off, 3),
            "global_in_sec": round(gi, 3),
            "global_out_sec": round(go, 3),
            "split_in": gi > c["global_in"] + EPS,
            "split_out": go < c["global_out"] - EPS,
            "has_adlib": bool(meta.get("has_adlib", False)),
            "adlib_note": meta.get("adlib_note", ""),
            "lead_in_text": meta.get("lead_in_text", ""),
        })
    return out


def clip_frames(seg: Dict[str, Any], fps: float) -> tuple:
    """Source in/out frames of a VO clip. Clip edges round outward so the handles stay
    whole; a split at a breath point rounds to the nearest frame so the two halves meet."""
    s_in = float(seg.get("source_in_sec", 0.0)) * fps
    s_out = float(seg.get("source_out_sec", 0.0)) * fps
    f_in = int(round(s_in)) if seg.get("split_in") else int(math.floor(s_in + 1e-6))
    f_out = int(round(s_out)) if seg.get("split_out") else int(math.ceil(s_out - 1e-6))
    f_in = max(0, f_in)
    return f_in, max(f_in + 1, f_out)


def expand_narration_pack(seg: Dict[str, Any], model: CutModel) -> List[Dict[str, Any]]:
    """An explicit NARRATION_PACK (e.g. from the AI's assembly_segments) becomes the VO
    clips inside its range, so silence is deleted there too. Uses its global range when it
    has one, else its part-local range."""
    if seg.get("vo_clip"):
        return [seg]
    meta = {k: seg.get(k) for k in ("has_adlib", "adlib_note", "lead_in_text") if seg.get(k) is not None}
    if seg.get("global_in_sec") is not None and seg.get("global_out_sec") is not None:
        a, b = float(seg["global_in_sec"]), float(seg["global_out_sec"])
    else:
        files = model.manifest.get("files") or []
        part = None
        src = os.path.normcase(os.path.normpath(seg.get("source_file") or ""))
        if src:
            part = next((f for f in files if os.path.normcase(os.path.normpath(f.get("path", ""))) == src), None)
        if part is None and seg.get("part_id") is not None:
            part = next((f for f in files if int(f.get("part_id", 1)) == int(seg["part_id"])), None)
        if part is None:
            if src:
                return [seg]   # some other audio file: leave it alone
            part = files[0] if len(files) == 1 else None
        off = float(part.get("global_offset_sec", 0.0)) if part else 0.0
        a = float(seg.get("source_in_sec", 0.0)) + off
        b = float(seg.get("source_out_sec", 0.0)) + off
    # The pack's edges land on real cut spots too, so they can't split a word.
    a = snap_resume(a, model.points, 0.0)
    b = snap_cut(b, model.points)["cut_after_sec"]
    return narration_clips(model, a, b, **meta) if b > a else []


# ---------------------------------------------------------------------------
# MCP payload helpers
# ---------------------------------------------------------------------------
TIME_CLOCKS_NOTE = {
    "source_in / source_out": "seconds inside that part's audio file (source_file). Use only to describe a line.",
    "global_in / global_out": "seconds on the joined VO timeline (Part 1, then Part 2... with the part offset added). "
                              "Every lock time (vo_cut_after_sec, vo_resume_sec) is GLOBAL time.",
    "cut_points": "the only places A-roll can go, in global time. Pick one by after_segment_id. "
                  "'gap' = deleted silence between VO clips; 'breath' = two lines inside one VO clip. "
                  "All silence is deleted anyway, so a gap that gets no A-roll simply closes up. "
                  "mid_line: true marks a pause inside one line (the line continues after it); "
                  "only use it when the script really cuts mid-sentence.",
}


def cut_points_for_segments(model: Optional[CutModel], segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The cut spots that follow the given lines (or all, when no lines are given)."""
    if model is None:
        return []
    ids = {int(s.get("id", -1)) for s in segments}
    return [p for p in model.points if not ids or p["after_segment_id"] in ids
            or (p["kind"] == "start" and 0 in ids)]


def resolve_lock_times(lock: Dict[str, Any], model: Optional[CutModel]) -> tuple:
    """(vo_cut_after_sec, vo_resume_sec) for one lock. after_segment_id / resume_segment_id
    (line ids from get_vo_segments) win over raw seconds when the transcript has a cut model."""
    cut = lock.get("vo_cut_after_sec")
    res = lock.get("vo_resume_sec")
    cut = float(cut) if cut is not None else None
    res = float(res) if res is not None else None
    if model is not None:
        if lock.get("after_segment_id") is not None:
            spot = point_after_segment(int(lock["after_segment_id"]), model)
            if spot is not None:
                cut = spot["cut_after_sec"]
                if lock.get("resume_segment_id") is None:
                    res = spot["resume_sec"]
        if lock.get("resume_segment_id") is not None:
            r = resume_at_segment(int(lock["resume_segment_id"]), model)
            if r is not None:
                res = r if cut is None else max(cut, r)
    return cut, res


def review_note(open_flags: int) -> str:
    if not open_flags:
        return ""
    s = "item" if open_flags == 1 else "items"
    return (f"{open_flags} VO review {s} undecided in Footage & Media > Review (duplicate takes, false starts, "
            f"wordless sounds). Undecided items keep their audio. Mention this to the editor in one line; "
            f"do not ask before exporting.")


def review_status_payload(model: Optional[CutModel]) -> Dict[str, Any]:
    """VO cut-model fields for get_workspace_status."""
    if model is None:
        return {"vo_mode": "continuous", "open_review_flags": 0, "review_note": ""}
    return {
        "vo_mode": "vo_clips",
        "vo_clip_count": len(model.clips),
        "vo_cut_point_count": len(model.points),
        "silence_deleted_sec": model.summary.get("silence_deleted_sec", 0.0),
        "dropped_by_review_sec": model.summary.get("dropped_sec", 0.0),
        "open_review_flags": model.open_flags,
        "review_note": review_note(model.open_flags),
    }
