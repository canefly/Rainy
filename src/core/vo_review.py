"""
Deterministic VO review flags.

After transcription + island snapping, this module looks for things an editor
may want to decide on before the A-roll/VO assembly:

  DUPLICATE_TAKE  neighbouring lines that read (nearly) the same -> pick one take
  FALSE_START     an abandoned start ("The officer who-- The officer who arrived")
  NONSPEECH       sound with no transcribed words (laugh, shout, sigh)
  BREATH          short quiet sound with no words

The cut model: every speech island (with its small handles) is a VO clip, and
ALL silence between islands is deleted. Silence is not a decision, so it is
never flagged. Which gaps get A-roll is decided semantically by the MCP AI
from the script; every other gap simply closes up.

No AI involved here: same transcript + same settings = same flags. Ignoring a
flag is always safe: every active waveform is kept.

Decisions live in the transcript JSON under "review" and are keyed by stable
flag ids, so they survive slider changes that re-run detection.
"""
import difflib
import re
import time
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .vo_islands import DetectionSettings, analyze_and_snap_part, load_or_compute_envelope

DUPLICATE_TAKE = "DUPLICATE_TAKE"
FALSE_START = "FALSE_START"
NONSPEECH = "NONSPEECH"
BREATH = "BREATH"

FLAG_TYPES = (DUPLICATE_TAKE, FALSE_START, NONSPEECH, BREATH)
GROUPED_TYPES = (BREATH,)  # shown as one collapsible row

# Decision values
KEEP = "keep"
DROP = "drop"
KEEP_ALL = "keep_all"
PICK_PREFIX = "pick:"

DEFAULT_DECISION = {
    DUPLICATE_TAKE: KEEP_ALL,
    FALSE_START: KEEP,
    NONSPEECH: KEEP,
    BREATH: KEEP,
}

_DASH_END = re.compile(r"(--|—|–|-|\.\.\.|…)$")
_TOKEN_STRIP = re.compile(r"[^\w']+", re.UNICODE)


@dataclass
class ReviewSettings:
    duplicate_ratio: float = 0.80     # text similarity for "same line read twice"
    duplicate_window: int = 3         # how many following lines to compare against
    duplicate_min_words: int = 3      # ignore very short lines ("Yes." / "No.")
    breath_max_s: float = 0.8         # wordless sounds up to this long can be breaths...
    breath_below_speech_db: float = 15.0  # ...if their peak is this far under speech level
    word_overlap_tol_s: float = 0.10  # a word this close to an island counts as inside it

    def to_dict(self) -> Dict[str, float]:
        return {k: (int(v) if isinstance(v, int) else float(v)) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ReviewSettings":
        if not isinstance(data, dict):
            return cls()
        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                try:
                    kwargs[f.name] = int(data[f.name]) if f.type in (int, "int") else float(data[f.name])
                except (TypeError, ValueError):
                    pass
        return cls(**kwargs)  # unknown keys (e.g. old pause settings) are ignored


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def tokenize(text: str) -> List[str]:
    return [t for t in (_TOKEN_STRIP.sub("", w).lower() for w in str(text or "").split()) if t]


def text_similarity(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def _is_false_start_pair(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """a is an abandoned attempt at b: a strict prefix, or a dash-cut start of b."""
    ta, tb = tokenize(a.get("text", "")), tokenize(b.get("text", ""))
    if not ta or not tb or len(ta) >= len(tb):
        return False
    if tb[: len(ta)] == ta:
        return True
    lead = min(2, len(ta))
    return bool(_DASH_END.search(str(a.get("text", "")).strip())) and ta[:lead] == tb[:lead]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _r(x: float) -> float:
    return round(float(x), 3)


def _flag(ftype: str, part_id: int, t_in: float, t_out: float, offset: float, **extra) -> Dict[str, Any]:
    return {
        "id": f"{ftype}:{part_id}:{t_in:.2f}",
        "type": ftype,
        "part_id": int(part_id),
        "in": _r(t_in),
        "out": _r(t_out),
        "global_in": _r(t_in + offset),
        "global_out": _r(t_out + offset),
        **extra,
    }


def _detect_takes(segs: List[Dict[str, Any]], part_id: int, offset: float, st: ReviewSettings) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    n = len(segs)
    false_start_of: Dict[int, int] = {}

    # Abandoned line followed by its full version
    for i in range(n - 1):
        if _is_false_start_pair(segs[i], segs[i + 1]):
            false_start_of[i] = i + 1
            a, b = segs[i], segs[i + 1]
            flags.append(_flag(
                FALSE_START, part_id, a["source_in"], a["source_out"], offset,
                segment_ids=[a.get("id"), b.get("id")],
                text=a.get("text", ""),
                detail=f'then "{b.get("text", "")}"',
            ))

    # Same line read 2+ times (union neighbouring matches into one group)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        if len(tokenize(segs[i].get("text", ""))) < st.duplicate_min_words:
            continue
        for j in range(i + 1, min(n, i + 1 + st.duplicate_window)):
            if false_start_of.get(i) == j:
                continue
            if len(tokenize(segs[j].get("text", ""))) < st.duplicate_min_words:
                continue
            if text_similarity(segs[i]["text"], segs[j]["text"]) >= st.duplicate_ratio:
                parent[find(j)] = find(i)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        takes = [{
            "segment_id": segs[m].get("id"),
            "in": _r(segs[m]["source_in"]),
            "out": _r(segs[m]["source_out"]),
            "global_in": _r(segs[m]["source_in"] + offset),
            "global_out": _r(segs[m]["source_out"] + offset),
            "text": segs[m].get("text", ""),
        } for m in members]
        flags.append(_flag(
            DUPLICATE_TAKE, part_id, takes[0]["in"], takes[-1]["out"], offset,
            segment_ids=[t["segment_id"] for t in takes],
            takes=takes,
            text=takes[-1]["text"],
            detail=f"{len(takes)} takes",
        ))

    # Restarts inside one Whisper line: "and then he and then he walked"
    for s in segs:
        words = [w for w in (s.get("words") or []) if tokenize(w.get("w", ""))]
        toks = [tokenize(w["w"])[0] for w in words]
        p = 0
        while p < len(toks):
            hit = 0
            for k in range(6, 1, -1):
                if p + 2 * k <= len(toks) and toks[p:p + k] == toks[p + k:p + 2 * k]:
                    hit = k
                    break
            if hit:
                t_in, t_out = float(words[p]["s"]), float(words[p + hit]["s"])
                flags.append(_flag(
                    FALSE_START, part_id, t_in, t_out, offset,
                    segment_ids=[s.get("id")],
                    text=" ".join(w["w"] for w in words[p:p + hit]),
                    detail="repeated inside the line",
                    inline=True,
                ))
                p += 2 * hit
            else:
                p += 1
    return flags


def _word_spans(segs: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    spans: List[Tuple[float, float]] = []
    for s in segs:
        words = s.get("words") or []
        if words:
            spans.extend((float(w["s"]), float(w["e"])) for w in words)
        else:
            spans.append((float(s["source_in"]), float(s["source_out"])))
    spans.sort()
    return spans


def _detect_island_flags(
    islands: List[Dict[str, Any]], segs: List[Dict[str, Any]], part_id: int, offset: float, st: ReviewSettings
) -> List[Dict[str, Any]]:
    spans = _word_spans(segs)
    starts = [a for a, _ in spans]
    tol = st.word_overlap_tol_s

    def has_words(isl) -> bool:
        import bisect
        lo, hi = isl["in"] - tol, isl["out"] + tol
        k = bisect.bisect_left(starts, lo)
        # a span starting before lo may still overlap
        for idx in range(max(0, k - 1), len(spans)):
            a, b = spans[idx]
            if a > hi:
                break
            if b >= lo and a <= hi:
                return True
        return False

    wordless = [isl for isl in islands if not has_words(isl)]
    speech_peaks = [float(i.get("peak_db", -30.0)) for i in islands if i not in wordless]
    speech_level = float(np.median(speech_peaks)) if speech_peaks else max(
        (float(i.get("peak_db", -30.0)) for i in islands), default=-30.0
    )

    flags: List[Dict[str, Any]] = []
    for isl in wordless:
        dur = isl["out"] - isl["in"]
        peak = float(isl.get("peak_db", speech_level))
        is_breath = dur <= st.breath_max_s and peak <= speech_level - st.breath_below_speech_db
        flags.append(_flag(
            BREATH if is_breath else NONSPEECH, part_id, isl["in"], isl["out"], offset,
            segment_ids=[],
            text="",
            detail=f"{dur:.2f} s, peak {peak:.0f} dB",
            peak_db=peak,
        ))
    return flags


def detect_flags(manifest: Dict[str, Any], review: Optional[ReviewSettings] = None) -> List[Dict[str, Any]]:
    """All flags for a v2 transcript manifest, sorted by global time."""
    st = review or ReviewSettings()
    segments = manifest.get("segments") or []
    islands = manifest.get("islands") or []
    flags: List[Dict[str, Any]] = []
    for f in manifest.get("files") or []:
        pid = int(f.get("part_id", 1))
        offset = float(f.get("global_offset_sec", 0.0))
        segs = sorted((s for s in segments if int(s.get("part_id", 1)) == pid), key=lambda s: s["source_in"])
        isl = sorted((i for i in islands if int(i.get("part_id", 1)) == pid), key=lambda i: i["in"])
        flags += _detect_takes(segs, pid, offset, st)
        flags += _detect_island_flags(isl, segs, pid, offset, st)
    flags.sort(key=lambda fl: (fl["global_in"], FLAG_TYPES.index(fl["type"])))
    return flags


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def valid_decisions(flag: Dict[str, Any]) -> List[str]:
    t = flag["type"]
    if t == DUPLICATE_TAKE:
        return [f"{PICK_PREFIX}{i}" for i in range(len(flag.get("takes") or []))] + [KEEP_ALL]
    return [KEEP, DROP]


def effective_decision(flag: Dict[str, Any], decisions: Dict[str, str]) -> str:
    d = decisions.get(flag["id"])
    return d if d in valid_decisions(flag) else DEFAULT_DECISION[flag["type"]]


def is_open(flag: Dict[str, Any], decisions: Dict[str, str]) -> bool:
    return decisions.get(flag["id"]) not in valid_decisions(flag)


def open_count(flags: Iterable[Dict[str, Any]], decisions: Dict[str, str]) -> int:
    """Undecided items; all breaths count as one item (they are one group row)."""
    n = 0
    groups_open = set()
    for f in flags:
        if not is_open(f, decisions):
            continue
        if f["type"] in GROUPED_TYPES:
            groups_open.add(f["type"])
        else:
            n += 1
    return n + len(groups_open)


def prune_decisions(flags: Iterable[Dict[str, Any]], decisions: Dict[str, str]) -> Dict[str, str]:
    by_id = {f["id"]: f for f in flags}
    return {k: v for k, v in (decisions or {}).items() if k in by_id and v in valid_decisions(by_id[k])}


def compute_dropped_ranges(
    flags: Iterable[Dict[str, Any]], decisions: Dict[str, str], detection: Optional[DetectionSettings] = None
) -> List[Dict[str, Any]]:
    """
    Local time ranges (per part) the editor chose to drop. Undecided flags drop nothing.
    Wordless islands (breath / no words) are flagged at their raw edges, so their
    drop range is widened by the clip handles; otherwise a dropped breath would
    leave 40-80 ms slivers behind as tiny clips. Take ranges are snapped line
    times, which already include the handles.
    """
    det = detection or DetectionSettings()
    out: List[Dict[str, Any]] = []

    def add(f, a, b, reason):
        if b - a > 0.005:
            out.append({"part_id": f["part_id"], "in": _r(a), "out": _r(b), "reason": reason, "flag_id": f["id"]})

    for f in flags:
        d = effective_decision(f, decisions)
        t = f["type"]
        if t == DUPLICATE_TAKE and d.startswith(PICK_PREFIX):
            pick = int(d[len(PICK_PREFIX):])
            for i, take in enumerate(f.get("takes") or []):
                if i != pick:
                    add(f, take["in"], take["out"], "take")
        elif t in (NONSPEECH, BREATH) and d == DROP:
            add(f, max(0.0, f["in"] - det.pre_roll_s), f["out"] + det.post_roll_s, t.lower())
        elif t == FALSE_START and d == DROP:
            add(f, f["in"], f["out"], t.lower())
    return _merge(out)


def _merge(ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranges = sorted(ranges, key=lambda r: (r["part_id"], r["in"]))
    merged: List[Dict[str, Any]] = []
    for r in ranges:
        if merged and merged[-1]["part_id"] == r["part_id"] and r["in"] <= merged[-1]["out"] + 1e-6:
            merged[-1]["out"] = max(merged[-1]["out"], r["out"])
        else:
            merged.append(dict(r))
    return merged


def removed_seconds(ranges: Iterable[Dict[str, Any]]) -> float:
    return round(sum(r["out"] - r["in"] for r in ranges), 3)


MIN_CLIP_S = 0.12  # leftovers shorter than this after dropping are discarded (a real clip is >= 0.22 s)


def compute_kept_ranges(
    manifest: Dict[str, Any],
    flags: Iterable[Dict[str, Any]],
    decisions: Dict[str, str],
    detection: Optional[DetectionSettings] = None,
) -> List[Dict[str, Any]]:
    """
    The VO clips the assembly uses, in order. One clip per speech island
    (plus its pre/post handles, never reaching into a neighbouring island),
    minus whatever the editor dropped. Everything else is silence and is deleted.

    Whisper lines that found no island (very quiet speech under the threshold)
    are kept at their own times, so transcribed words are never deleted unless
    the editor drops them.

    Times: "in"/"out" are seconds inside that part's audio file; "global_in"/
    "global_out" are seconds on the joined VO timeline (part offset added).
    """
    det = detection or DetectionSettings.from_dict(manifest.get("detection"))
    islands = manifest.get("islands") or []
    segments = manifest.get("segments") or []
    dropped = compute_dropped_ranges(flags, decisions, det)
    out: List[Dict[str, Any]] = []

    for f in manifest.get("files") or []:
        pid = int(f.get("part_id", 1))
        offset = float(f.get("global_offset_sec", 0.0))
        dur = float(f.get("duration_sec") or 0.0) or float("inf")
        isl = sorted((i for i in islands if int(i.get("part_id", 1)) == pid), key=lambda i: i["in"])
        ranges: List[Dict[str, Any]] = []
        for k, i in enumerate(isl):
            lo = isl[k - 1]["out"] if k > 0 else 0.0
            hi = isl[k + 1]["in"] if k + 1 < len(isl) else dur
            ranges.append({"part_id": pid, "in": max(lo, i["in"] - det.pre_roll_s, 0.0),
                           "out": min(hi, i["out"] + det.post_roll_s, dur)})
        for s in segments:
            if int(s.get("part_id", 1)) == pid and s.get("snapped") is False:
                ranges.append({"part_id": pid, "in": float(s["source_in"]), "out": float(s["source_out"])})
        ranges = _merge(ranges)

        # Subtract the editor's drops
        for d in (d for d in dropped if d["part_id"] == pid):
            nxt: List[Dict[str, Any]] = []
            for r in ranges:
                if d["out"] <= r["in"] or d["in"] >= r["out"]:
                    nxt.append(r)
                    continue
                if d["in"] > r["in"]:
                    nxt.append({**r, "out": d["in"]})
                if d["out"] < r["out"]:
                    nxt.append({**r, "in": d["out"]})
            ranges = nxt

        for r in ranges:
            if r["out"] - r["in"] >= MIN_CLIP_S:
                out.append({"part_id": pid, "in": _r(r["in"]), "out": _r(r["out"]),
                            "global_in": _r(r["in"] + offset), "global_out": _r(r["out"] + offset)})
    return out


def cut_summary(
    manifest: Dict[str, Any],
    flags: Iterable[Dict[str, Any]],
    decisions: Dict[str, str],
    detection: Optional[DetectionSettings] = None,
) -> Dict[str, Any]:
    """Clip count and how much audio the cut removes (silence vs editor drops)."""
    flags = list(flags)
    undropped = compute_kept_ranges(manifest, flags, {}, detection) if decisions else None
    kept = compute_kept_ranges(manifest, flags, decisions, detection)
    total = sum(float(f.get("duration_sec") or 0.0) for f in manifest.get("files") or [])
    kept_sec = removed_seconds(kept)
    base_sec = removed_seconds(undropped) if undropped is not None else kept_sec
    return {
        "clips": len(kept),
        "kept_sec": round(kept_sec, 3),
        "silence_deleted_sec": round(max(0.0, total - base_sec), 3),
        "dropped_sec": round(max(0.0, base_sec - kept_sec), 3),
    }


# ---------------------------------------------------------------------------
# Manifest plumbing
# ---------------------------------------------------------------------------
def load_part_envelopes(manifest: Dict[str, Any]) -> Dict[int, np.ndarray]:
    """Loudness envelopes for every part (cached on disk after transcription)."""
    envs: Dict[int, np.ndarray] = {}
    for f in manifest.get("files") or []:
        path = f.get("path")
        if path:
            envs[int(f.get("part_id", 1))] = load_or_compute_envelope(path)[0]
    return envs


def needs_timing_rebuild(manifest: Dict[str, Any]) -> bool:
    """v1 transcripts (pre waveform snapping) have no islands yet."""
    return int(manifest.get("version", 1)) < 2 or "islands" not in manifest


def rebuild_timing(
    manifest: Dict[str, Any], detection: DetectionSettings, envelopes: Dict[int, np.ndarray]
) -> Dict[str, Any]:
    """
    Re-detects islands with new detection settings and re-snaps every segment from
    Whisper's original times. Mutates and returns the manifest (ids renumbered).
    Parts without an envelope keep their current timing.
    """
    segments = manifest.get("segments") or []
    old_islands = manifest.get("islands") or []
    new_segments: List[Dict[str, Any]] = []
    new_islands: List[Dict[str, Any]] = []

    for f in manifest.get("files") or []:
        pid = int(f.get("part_id", 1))
        offset = float(f.get("global_offset_sec", 0.0))
        segs = sorted((dict(s) for s in segments if int(s.get("part_id", 1)) == pid), key=lambda s: s["source_in"])
        env = envelopes.get(pid)
        if env is None:
            new_segments += segs
            new_islands += [i for i in old_islands if int(i.get("part_id", 1)) == pid]
            continue
        for s in segs:
            s["source_in"] = float(s.get("whisper_in", s["source_in"]))
            s["source_out"] = float(s.get("whisper_out", s["source_out"]))
        snapped, islands, floor = analyze_and_snap_part(segs, env, detection, duration_s=f.get("duration_sec"))
        for s in snapped:
            s["global_in"] = _r(s["source_in"] + offset)
            s["global_out"] = _r(s["source_out"] + offset)
        new_segments += snapped
        new_islands += [{"part_id": pid, **i} for i in islands]
        f["noise_floor_db"] = round(floor, 1)
        f["island_count"] = len(islands)

    for idx, s in enumerate(new_segments):
        s["id"] = idx
    counts: Dict[int, int] = {}
    for s in new_segments:
        counts[int(s.get("part_id", 1))] = counts.get(int(s.get("part_id", 1)), 0) + 1
    for f in manifest.get("files") or []:
        f["segment_count"] = counts.get(int(f.get("part_id", 1)), 0)

    manifest["segments"] = new_segments
    manifest["islands"] = new_islands
    manifest["total_segments"] = len(new_segments)
    manifest["detection"] = detection.to_dict()
    manifest["version"] = max(2, int(manifest.get("version", 1)))
    return manifest


def build_review_block(
    manifest: Dict[str, Any],
    flags: List[Dict[str, Any]],
    decisions: Dict[str, str],
    review: ReviewSettings,
    detection: Optional[DetectionSettings] = None,
) -> Dict[str, Any]:
    decisions = prune_decisions(flags, decisions)
    return {
        "settings": review.to_dict(),
        "decisions": decisions,
        "flags": flags,
        "open_count": open_count(flags, decisions),
        "summary": cut_summary(manifest, flags, decisions, detection),
        "updated_at": time.time(),
    }


def get_open_review_count(manifest: Optional[Dict[str, Any]]) -> int:
    """Open items as last saved (used by status badges / MCP without re-detecting)."""
    if not isinstance(manifest, dict):
        return 0
    block = manifest.get("review")
    if isinstance(block, dict) and "flags" in block:
        return open_count(block.get("flags") or [], block.get("decisions") or {})
    return 0
