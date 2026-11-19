"""Deterministic VO review flags: detection, decisions, removals, manifest plumbing."""
import numpy as np
import pytest

from src.core import vo_review as R
from src.core.vo_islands import DetectionSettings, compute_envelope_db, HOP_S, SAMPLE_RATE


def S(i, a, b, text, words=None, part=1, snapped=None):
    seg = {"id": i, "part_id": part, "source_in": a, "source_out": b,
           "whisper_in": a, "whisper_out": b, "text": text, "words": words or []}
    if snapped is not None:
        seg["snapped"] = snapped
    return seg


def W(w, s, e):
    return {"w": w, "s": s, "e": e, "p": 0.9}


def I(a, b, peak=-20.0, part=1):
    return {"part_id": part, "in": a, "out": b, "peak_db": peak}


def manifest(segments, islands, parts=((1, 0.0),)):
    return {
        "version": 2,
        "files": [{"part_id": p, "path": f"p{p}.wav", "global_offset_sec": off, "duration_sec": 60.0} for p, off in parts],
        "segments": segments,
        "islands": islands,
    }


def types(flags):
    return [f["type"] for f in flags]


# ---------------------------------------------------------------------------
# Takes
# ---------------------------------------------------------------------------
def test_duplicate_take_groups_neighbouring_rereads():
    segs = [
        S(0, 0.0, 2.0, "It was the third night he hadn't come home."),
        S(1, 3.0, 5.0, "It was the third night he hadn't come home."),
        S(2, 6.0, 8.0, "It was the third night that he hadn't come home."),
        S(3, 9.0, 11.0, "Police were called the next morning."),
    ]
    flags = R.detect_flags(manifest(segs, [I(0, 2), I(3, 5), I(6, 8), I(9, 11)]))
    dups = [f for f in flags if f["type"] == R.DUPLICATE_TAKE]
    assert len(dups) == 1
    assert [t["segment_id"] for t in dups[0]["takes"]] == [0, 1, 2]
    assert dups[0]["in"] == 0.0 and dups[0]["out"] == 8.0


def test_short_lines_and_different_lines_are_not_duplicates():
    segs = [S(0, 0, 1, "Yes."), S(1, 2, 3, "Yes."), S(2, 4, 6, "He drove north that night."),
            S(3, 7, 9, "She drove south the next day.")]
    flags = R.detect_flags(manifest(segs, [I(0, 1), I(2, 3), I(4, 6), I(7, 9)]))
    assert R.DUPLICATE_TAKE not in types(flags)


def test_false_start_between_lines_prefix_and_dash():
    segs = [S(0, 0.0, 1.0, "The officer who"), S(1, 1.5, 4.0, "The officer who arrived first said nothing."),
            S(2, 5.0, 5.8, "She never—"), S(3, 6.2, 8.0, "She never told anyone about it.")]
    flags = R.detect_flags(manifest(segs, [I(0, 1), I(1.5, 4), I(5, 5.8), I(6.2, 8)]))
    fs = [f for f in flags if f["type"] == R.FALSE_START]
    assert [f["segment_ids"][0] for f in fs] == [0, 2]
    assert R.DUPLICATE_TAKE not in types(flags), "a false start is not also a duplicate"


def test_restart_inside_one_whisper_line_uses_word_times():
    words = [W("And", 1.0, 1.2), W("then", 1.2, 1.4), W("he", 1.4, 1.5),
             W("and", 1.9, 2.0), W("then", 2.0, 2.2), W("he", 2.2, 2.3), W("walked.", 2.3, 2.8)]
    segs = [S(0, 1.0, 2.8, "And then he and then he walked.", words)]
    flags = R.detect_flags(manifest(segs, [I(1.0, 2.8)]))
    fs = [f for f in flags if f["type"] == R.FALSE_START]
    assert len(fs) == 1 and fs[0]["inline"]
    assert fs[0]["in"] == 1.0 and fs[0]["out"] == 1.9


# ---------------------------------------------------------------------------
# Wordless islands + pauses
# ---------------------------------------------------------------------------
def test_wordless_islands_split_into_breaths_and_nonspeech():
    segs = [S(0, 0.0, 2.0, "First line here.", [W("First", 0.1, 0.5), W("here.", 1.5, 1.9)]),
            S(1, 4.0, 6.0, "Second line here.", [W("Second", 4.1, 4.5), W("here.", 5.5, 5.9)])]
    isl = [I(0.0, 2.0, -18), I(2.6, 2.9, -45), I(3.0, 3.8, -16), I(4.0, 6.0, -19)]
    flags = R.detect_flags(manifest(segs, isl))
    assert [(f["type"], f["in"]) for f in flags if f["type"] in (R.BREATH, R.NONSPEECH)] == [
        (R.BREATH, 2.6), (R.NONSPEECH, 3.0)]


def test_silence_is_never_flagged():
    isl = [I(0, 1), I(3.0, 4.0), I(7.0, 8.0)]
    segs = [S(k, i["in"], i["out"], f"line number {k} here") for k, i in enumerate(isl)]
    assert R.detect_flags(manifest(segs, isl)) == []


def test_multi_part_flags_carry_global_times():
    segs = [S(0, 0, 1, "alpha beta gamma"), S(1, 3, 4, "delta epsilon zeta", part=2)]
    isl = [I(0, 1), I(0, 1, part=2), I(1.5, 2.0, -55, part=2), I(3, 4, part=2)]
    flags = R.detect_flags(manifest(segs, isl, parts=((1, 0.0), (2, 100.0))))
    breath = [f for f in flags if f["type"] == R.BREATH][0]
    assert breath["part_id"] == 2 and breath["global_in"] == 101.5 and breath["global_out"] == 102.0


# ---------------------------------------------------------------------------
# Cut model: kept ranges (VO clips) + decisions
# ---------------------------------------------------------------------------
def _sample():
    # line times are already snapped: island edges plus 40 ms / 80 ms handles
    segs = [S(0, 0.06, 2.08, "It was the third night he hadn't come home.", snapped=True),
            S(1, 2.46, 4.58, "It was the third night he hadn't come home.", snapped=True),
            S(2, 7.5, 9.0, "Nothing else happened here.", [W("Nothing", 7.5, 8.0), W("here.", 8.5, 9.0)], snapped=True)]
    isl = [I(0.1, 2.0), I(2.5, 4.5), I(5.0, 5.4, -50), I(7.5, 9.0)]
    m = manifest(segs, isl)
    m["files"][0]["duration_sec"] = 10.0
    m["detection"] = DetectionSettings(pre_roll_s=0.04, post_roll_s=0.08).to_dict()
    return m, R.detect_flags(m)


def test_every_island_is_a_clip_and_all_silence_is_deleted():
    m, flags = _sample()
    kept = R.compute_kept_ranges(m, flags, {})
    assert [(k["in"], k["out"]) for k in kept] == [(0.06, 2.08), (2.46, 4.58), (4.96, 5.48), (7.46, 9.08)]
    s = R.cut_summary(m, flags, {})
    assert s["clips"] == 4 and s["dropped_sec"] == 0.0
    assert s["silence_deleted_sec"] == pytest.approx(10.0 - s["kept_sec"])


def test_handles_never_reach_into_the_next_island():
    m, _ = _sample()
    m["islands"] = [I(1.0, 2.0), I(2.05, 3.0)]
    kept = R.compute_kept_ranges(m, [], {})
    assert len(kept) == 1, "touching handles merge into one clip instead of overlapping"
    assert kept[0]["in"] == pytest.approx(0.96) and kept[0]["out"] == pytest.approx(3.08)


def test_unsnapped_whisper_lines_are_kept_not_deleted_as_silence():
    m, flags = _sample()
    m["segments"].append(S(3, 6.0, 6.8, "very quiet words", snapped=False))
    kept = R.compute_kept_ranges(m, flags, {})
    assert any(k["in"] == 6.0 and k["out"] == 6.8 for k in kept)


def test_decisions_remove_clips_or_parts_of_them():
    m, flags = _sample()
    dup = next(f for f in flags if f["type"] == R.DUPLICATE_TAKE)
    breath = next(f for f in flags if f["type"] == R.BREATH)
    decisions = {dup["id"]: "pick:1", breath["id"]: R.DROP}
    dropped = R.compute_dropped_ranges(flags, decisions)
    assert [(d["in"], d["out"], d["reason"]) for d in dropped] == [(0.06, 2.08, "take"), (4.96, 5.48, "breath")]
    kept = R.compute_kept_ranges(m, flags, decisions)
    # take 1 and the breath are gone completely, handles included: no slivers left behind
    assert [(k["in"], k["out"]) for k in kept] == [(2.46, 4.58), (7.46, 9.08)]
    s = R.cut_summary(m, flags, decisions)
    assert s["dropped_sec"] == pytest.approx(2.54, abs=0.01)


def test_kept_ranges_carry_global_times():
    m, flags = _sample()
    m["files"][0]["global_offset_sec"] = 60.0
    kept = R.compute_kept_ranges(m, flags, {})
    assert kept[0]["global_in"] == pytest.approx(kept[0]["in"] + 60.0)


def test_open_count_groups_breaths():
    _, flags = _sample()
    assert R.open_count(flags, {}) == 2  # duplicate + breath group
    dup = next(f for f in flags if f["type"] == R.DUPLICATE_TAKE)
    assert R.open_count(flags, {dup["id"]: R.KEEP_ALL}) == 1


def test_invalid_and_orphan_decisions_are_ignored_and_pruned():
    _, flags = _sample()
    dup = next(f for f in flags if f["type"] == R.DUPLICATE_TAKE)
    decisions = {dup["id"]: "pick:9", "LONG_PAUSE:1:99.00": "shorten"}
    assert R.effective_decision(dup, decisions) == R.KEEP_ALL
    assert R.prune_decisions(flags, decisions) == {}


def test_review_block_and_saved_open_count():
    m, flags = _sample()
    block = R.build_review_block(m, flags, {"junk": "x"}, R.ReviewSettings())
    assert block["decisions"] == {} and block["open_count"] == 2
    assert block["summary"]["clips"] == 4
    assert R.get_open_review_count({"review": block}) == 2
    assert R.get_open_review_count({}) == 0


def test_review_settings_round_trip_ignores_old_pause_keys():
    st = R.ReviewSettings.from_dict({"pause_limit_s": 1.0, "pause_keep_s": 3.0, "duplicate_window": "4"})
    assert st.duplicate_window == 4 and not hasattr(st, "pause_limit_s")
    assert R.ReviewSettings.from_dict(st.to_dict()) == st


# ---------------------------------------------------------------------------
# Timing rebuild (threshold slider)
# ---------------------------------------------------------------------------
def _tone_env(spans, total, breath=None):
    rng = np.random.default_rng(5)
    n = int(total * SAMPLE_RATE)
    x = rng.normal(0, 10 ** (-70 / 20), n).astype(np.float32)
    t = np.arange(n) / SAMPLE_RATE
    tone = (np.sign(np.sin(2 * np.pi * 180 * t)) * 10 ** (-18 / 20)).astype(np.float32)
    for a, b in spans:
        x[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)] += tone[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)]
    if breath:
        ia, ib = int(breath[0] * SAMPLE_RATE), int(breath[1] * SAMPLE_RATE)
        x[ia:ib] += rng.normal(0, 10 ** (-52 / 20), ib - ia).astype(np.float32)
    return compute_envelope_db(x, SAMPLE_RATE, HOP_S)


def test_rebuild_timing_resnaps_from_whisper_times_and_upgrades_v1():
    env = _tone_env([(0.5, 1.5), (2.8, 4.1)], 5.0, breath=(1.95, 2.25))
    v1 = {
        "version": 1,
        "files": [{"part_id": 1, "path": "x.wav", "global_offset_sec": 10.0, "duration_sec": 5.0}],
        "segments": [{"id": 0, "part_id": 1, "source_in": 0.2, "source_out": 2.2, "text": "Hello there."},
                     {"id": 1, "part_id": 1, "source_in": 2.3, "source_out": 4.6, "text": "General Kenobi."}],
    }
    assert R.needs_timing_rebuild(v1)
    loose = R.rebuild_timing(v1, DetectionSettings(threshold_db=12.0), {1: env})
    assert loose["version"] == 2 and not R.needs_timing_rebuild(loose)
    assert loose["segments"][0]["whisper_in"] == 0.2
    assert loose["segments"][1]["global_in"] == pytest.approx(loose["segments"][1]["source_in"] + 10.0)
    n_loose = len(loose["islands"])
    strict = R.rebuild_timing(loose, DetectionSettings(threshold_db=24.0), {1: env})
    assert len(strict["islands"]) < n_loose, "breath island disappears at a stricter threshold"
    assert strict["detection"]["threshold_db"] == 24.0
    assert strict["segments"][0]["source_in"] == pytest.approx(0.46, abs=0.015)
