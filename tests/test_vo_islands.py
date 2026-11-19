"""
Speech-island detection + Whisper edge snapping, tested against generated audio
with known speech/silence boundaries (no real VO or Whisper model needed).
"""
import json
import os
import sys
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from src.core.vo_islands import (
    HOP_S,
    SAMPLE_RATE,
    DetectionSettings,
    analyze_and_snap_part,
    compute_envelope_db,
    detect_islands,
    estimate_noise_floor_db,
    get_envelope_cache_path,
    load_or_compute_envelope,
    slim_segments,
    snap_segments_to_islands,
    split_segments_at_long_gaps,
)

SR = SAMPLE_RATE
NOISE_DB = -70.0
SPEECH_DB = -18.0


def _db_to_amp(db: float) -> float:
    return float(10 ** (db / 20.0))


def make_vo(spans, total_s, noise_db=NOISE_DB, speech_db=SPEECH_DB, seed=7):
    """
    Room tone at noise_db, plus 'speech' (a buzzy 180 Hz tone) for each (in, out)
    in spans. Returns float32 PCM at SR.
    """
    rng = np.random.default_rng(seed)
    n = int(round(total_s * SR))
    # white noise RMS == amplitude / sqrt(3) for uniform; use gaussian with sigma = rms
    x = rng.normal(0.0, _db_to_amp(noise_db), n).astype(np.float32)
    t = np.arange(n) / SR
    tone = np.sign(np.sin(2 * np.pi * 180 * t)) * _db_to_amp(speech_db)
    for a, b in spans:
        ia, ib = int(round(a * SR)), int(round(b * SR))
        x[ia:ib] += tone[ia:ib].astype(np.float32)
    return x


def env_for(spans, total_s, **kw):
    return compute_envelope_db(make_vo(spans, total_s, **kw), SR, HOP_S)


def word(w, s, e):
    return {"w": w, "s": s, "e": e, "p": 0.9}


def seg(text, s_in, s_out, words=None):
    return {"source_in": s_in, "source_out": s_out, "text": text, "words": words or []}


# ---------------------------------------------------------------------------
# Envelope + noise floor
# ---------------------------------------------------------------------------
def test_envelope_reads_speech_and_room_tone_levels():
    env = env_for([(1.0, 2.0)], 3.0)
    assert env.shape[0] == 300
    assert abs(float(np.median(env[120:180])) - SPEECH_DB) < 1.5
    assert abs(float(np.median(env[0:80])) - NOISE_DB) < 1.5


def test_noise_floor_tracks_room_tone():
    env = env_for([(0.5, 1.5), (2.0, 3.0)], 4.0)
    floor = estimate_noise_floor_db(env)
    assert NOISE_DB - 4 < floor < NOISE_DB + 2


def test_noise_floor_ignores_digital_silence():
    pcm = make_vo([(1.0, 2.0)], 3.0)
    pcm[: int(0.5 * SR)] = 0.0  # gated / digital-zero head
    env = compute_envelope_db(pcm, SR, HOP_S)
    floor = estimate_noise_floor_db(env)
    assert floor > -80.0, "digital-zero frames must not drag the floor to -120 dB"


# ---------------------------------------------------------------------------
# Islands
# ---------------------------------------------------------------------------
def test_islands_match_known_edges_within_one_frame():
    spans = [(0.50, 1.80), (2.60, 4.10), (6.30, 7.00)]
    islands, _ = detect_islands(env_for(spans, 8.0))
    assert len(islands) == 3
    for isl, (a, b) in zip(islands, spans):
        assert abs(isl["in"] - a) <= 0.011
        assert abs(isl["out"] - b) <= 0.011


def test_short_dips_inside_a_phrase_do_not_split_it():
    # 150 ms dips between "words" < 300 ms min gap -> one island
    spans = [(1.0, 1.6), (1.75, 2.3), (2.45, 3.0)]
    islands, _ = detect_islands(env_for(spans, 4.0))
    assert len(islands) == 1
    assert abs(islands[0]["in"] - 1.0) <= 0.011
    assert abs(islands[0]["out"] - 3.0) <= 0.011


def test_clicks_shorter_than_min_island_are_dropped():
    islands, _ = detect_islands(env_for([(1.0, 1.04), (2.0, 3.0)], 4.0))
    assert len(islands) == 1
    assert abs(islands[0]["in"] - 2.0) <= 0.011


def test_threshold_setting_changes_sensitivity():
    # a quiet breath at -55 dB: 15 dB above floor
    pcm = make_vo([(1.0, 2.0)], 4.0)
    ia, ib = int(2.6 * SR), int(3.0 * SR)
    pcm[ia:ib] += np.random.default_rng(1).normal(0, _db_to_amp(-55.0), ib - ia).astype(np.float32)
    env = compute_envelope_db(pcm, SR, HOP_S)
    loose, _ = detect_islands(env, DetectionSettings(threshold_db=10.0))
    strict, _ = detect_islands(env, DetectionSettings(threshold_db=24.0))
    assert len(loose) == 2
    assert len(strict) == 1


def test_empty_and_silent_inputs():
    assert detect_islands(np.zeros(0, dtype=np.float32))[0] == []
    silent = compute_envelope_db(np.zeros(SR, dtype=np.float32), SR, HOP_S)
    assert detect_islands(silent)[0] == []


# ---------------------------------------------------------------------------
# Snapping
# ---------------------------------------------------------------------------
ISLANDS = [
    {"in": 1.00, "out": 3.00, "peak_db": -18.0},
    {"in": 4.00, "out": 6.00, "peak_db": -18.0},
    {"in": 8.00, "out": 9.50, "peak_db": -18.0},
]


def test_sloppy_whisper_edges_snap_to_islands_with_rolls():
    st = DetectionSettings()
    segs = [
        seg("First line.", 0.70, 3.40, [word("First", 0.80, 1.3), word("line.", 1.4, 3.3)]),
        seg("Second line.", 3.50, 5.70, [word("Second", 3.85, 4.5), word("line.", 4.6, 5.6)]),
    ]
    out = snap_segments_to_islands(segs, ISLANDS, st, duration_s=10.0)
    assert out[0]["source_in"] == pytest.approx(1.00 - st.pre_roll_s)
    assert out[0]["source_out"] == pytest.approx(3.00 + st.post_roll_s)
    assert out[1]["source_in"] == pytest.approx(4.00 - st.pre_roll_s)
    assert out[1]["source_out"] == pytest.approx(6.00 + st.post_roll_s)
    assert all(s["snapped"] for s in out)
    assert out[0]["whisper_in"] == 0.70 and out[0]["whisper_out"] == 3.40
    assert out[0]["gap_after_sec"] == pytest.approx(out[1]["source_in"] - out[0]["source_out"])
    assert out[1]["gap_after_sec"] is None


def test_segments_sharing_an_island_meet_in_the_middle():
    segs = [
        seg("Part one", 1.0, 2.0, [word("Part", 1.0, 1.4), word("one", 1.5, 1.9)]),
        seg("part two.", 2.0, 3.0, [word("part", 2.1, 2.5), word("two.", 2.6, 2.95)]),
    ]
    out = snap_segments_to_islands(segs, ISLANDS, DetectionSettings(), 10.0)
    assert out[0]["source_out"] == pytest.approx(2.0)
    assert out[1]["source_in"] == pytest.approx(2.0)
    assert out[0]["source_in"] < 1.0 and out[1]["source_out"] > 3.0


def test_shared_island_boundary_lands_in_the_breath_valley():
    # Two sentences inside one island, separated by a 200 ms breath at -45 dB
    # (louder than room tone, so no silence gap) centred at 2.4 s.
    pcm = make_vo([(1.0, 2.3), (2.5, 4.0)], 5.0)
    ia, ib = int(2.3 * SR), int(2.5 * SR)
    pcm[ia:ib] += np.random.default_rng(3).normal(0, _db_to_amp(-45.0), ib - ia).astype(np.float32)
    env = compute_envelope_db(pcm, SR, HOP_S)
    islands, _ = detect_islands(env)
    assert len(islands) == 1, "breath must keep it one island"
    # Whisper's words are off: first sentence 'ends' at 2.1, next 'starts' at 2.9.
    segs = [
        seg("One.", 1.0, 2.1, [word("One.", 1.05, 2.1)]),
        seg("Two.", 2.9, 4.0, [word("Two.", 2.9, 3.95)]),
    ]
    out = snap_segments_to_islands(segs, islands, DetectionSettings(), 5.0, env_db=env)
    assert out[0]["source_out"] == pytest.approx(2.4, abs=0.03)
    assert out[1]["source_in"] == out[0]["source_out"]
    # without an envelope it's the plain midpoint of the words
    mid = snap_segments_to_islands(segs, islands, DetectionSettings(), 5.0)
    assert mid[0]["source_out"] == pytest.approx(2.5)


def test_rolls_never_reach_into_neighbouring_islands():
    tight = [{"in": 1.0, "out": 2.0}, {"in": 2.05, "out": 3.0}]
    segs = [
        seg("A.", 1.0, 2.0, [word("A.", 1.1, 1.9)]),
        seg("B.", 2.05, 3.0, [word("B.", 2.1, 2.9)]),
    ]
    out = snap_segments_to_islands(segs, tight, DetectionSettings(pre_roll_s=0.04, post_roll_s=0.08), 4.0)
    assert out[0]["source_out"] <= 2.05
    assert out[1]["source_in"] >= out[0]["source_out"]


def test_early_start_in_gap_snaps_forward_not_to_previous_island():
    # Whisper starts "Second" at 3.2 — closer to island 0's end (3.0) than island 1's start (4.0),
    # but a start in a gap means speech begins at the NEXT island.
    segs = [
        seg("First.", 1.0, 3.0, [word("First.", 1.1, 2.9)]),
        seg("Second.", 3.2, 6.0, [word("Second.", 3.6, 5.9)]),
    ]
    out = snap_segments_to_islands(segs, ISLANDS, DetectionSettings(), 10.0)
    assert out[1]["source_in"] == pytest.approx(4.0 - DetectionSettings().pre_roll_s)
    assert out[0]["source_out"] == pytest.approx(3.0 + DetectionSettings().post_roll_s)


def test_text_over_silence_keeps_whisper_times():
    segs = [seg("Thank you.", 6.6, 7.4, [word("Thank", 6.6, 7.0), word("you.", 7.0, 7.4)])]
    out = snap_segments_to_islands(segs, ISLANDS, DetectionSettings(), 10.0)
    assert out[0]["source_in"] == 6.6 and out[0]["source_out"] == 7.4
    assert out[0]["snapped"] is False


def test_segments_without_words_fall_back_to_segment_times():
    out = snap_segments_to_islands([seg("No words", 0.9, 3.2)], ISLANDS, DetectionSettings(), 10.0)
    assert out[0]["snapped"] is True
    assert out[0]["source_in"] == pytest.approx(0.96)


def test_no_islands_means_untouched_but_marked():
    out = snap_segments_to_islands([seg("x", 1.0, 2.0)], [], DetectionSettings(), 10.0)
    assert out[0]["source_in"] == 1.0 and out[0]["snapped"] is False


def test_out_roll_is_clamped_to_file_duration():
    segs = [seg("End.", 8.0, 9.5, [word("End.", 8.1, 9.4)])]
    out = snap_segments_to_islands(segs, ISLANDS, DetectionSettings(), duration_s=9.52)
    assert out[0]["source_out"] == pytest.approx(9.52)


# ---------------------------------------------------------------------------
# Splitting glued sentences
# ---------------------------------------------------------------------------
def test_splits_two_sentences_glued_across_a_long_pause():
    glued = seg(
        "He left. She stayed.", 1.0, 6.0,
        [word("He", 1.0, 1.5), word("left.", 1.6, 2.9), word("She", 4.1, 4.8), word("stayed.", 4.9, 5.9)],
    )
    pieces = split_segments_at_long_gaps([glued], ISLANDS, DetectionSettings(split_gap_s=0.7))
    assert [p["text"] for p in pieces] == ["He left.", "She stayed."]
    assert all(p["split_from_whisper"] for p in pieces)


def test_no_split_without_sentence_end_or_with_short_gap():
    mid_sentence = seg(
        "He left and she stayed", 1.0, 6.0,
        [word("He", 1.0, 1.5), word("left", 1.6, 2.9), word("and", 4.1, 4.8), word("stayed", 4.9, 5.9)],
    )
    assert len(split_segments_at_long_gaps([mid_sentence], ISLANDS, DetectionSettings())) == 1
    long_gap_needed = seg(
        "He left. She stayed.", 1.0, 6.0,
        [word("He", 1.0, 1.5), word("left.", 1.6, 2.9), word("She", 4.1, 4.8), word("stayed.", 4.9, 5.9)],
    )
    assert len(split_segments_at_long_gaps([long_gap_needed], ISLANDS, DetectionSettings(split_gap_s=1.5))) == 1


def test_end_to_end_part_analysis_on_generated_audio():
    spans = [(0.50, 1.80), (2.60, 4.10)]
    env = env_for(spans, 5.0)
    segs = [
        seg("Hello there.", 0.2, 2.2, [word("Hello", 0.3, 1.0), word("there.", 1.1, 2.1)]),
        seg("General Kenobi.", 2.3, 4.6, [word("General", 2.4, 3.2), word("Kenobi.", 3.3, 4.5)]),
    ]
    out, islands, floor = analyze_and_snap_part(segs, env, DetectionSettings(), duration_s=5.0)
    assert len(islands) == 2
    assert NOISE_DB - 4 < floor < NOISE_DB + 2
    assert out[0]["source_in"] == pytest.approx(0.46, abs=0.015)
    assert out[0]["source_out"] == pytest.approx(1.88, abs=0.015)
    assert out[1]["source_in"] == pytest.approx(2.56, abs=0.015)
    assert out[1]["source_out"] == pytest.approx(4.18, abs=0.015)


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def test_slim_segments_drops_words_only():
    full = [{"id": 0, "text": "x", "words": [word("x", 0, 1)], "snapped": True}]
    slim = slim_segments(full)
    assert slim == [{"id": 0, "text": "x", "snapped": True}]
    assert "words" in full[0], "must not mutate the source"


def test_detection_settings_round_trip_ignores_junk():
    st = DetectionSettings(threshold_db=18.0, pre_roll_s=0.02)
    back = DetectionSettings.from_dict({**st.to_dict(), "bogus": 1, "min_gap_s": "nope"})
    assert back.threshold_db == 18.0 and back.pre_roll_s == 0.02
    assert back.min_gap_s == DetectionSettings().min_gap_s
    assert DetectionSettings.from_dict(None) == DetectionSettings()


def _write_wav(path, pcm):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())


def _ffmpeg_available():
    try:
        from src.core.ffmpeg_encoder import get_ffmpeg_path
        p = get_ffmpeg_path()
        return bool(p and os.path.isfile(p))
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="FFmpeg not available")


@needs_ffmpeg
def test_envelope_from_real_file_is_cached_in_isolated_dir(tmp_path):
    wav = str(tmp_path / "vo.wav")
    _write_wav(wav, make_vo([(0.5, 1.5)], 2.0))
    cache_path = get_envelope_cache_path(wav)
    assert cache_path and os.environ["KNOT_VO_ENVELOPE_DIR"] in cache_path
    env, used = load_or_compute_envelope(wav)
    assert used == cache_path and os.path.isfile(cache_path)
    islands, _ = detect_islands(env)
    assert len(islands) == 1 and abs(islands[0]["in"] - 0.5) <= 0.02
    env2, _ = load_or_compute_envelope(wav)  # served from cache
    assert np.array_equal(env, env2)


@needs_ffmpeg
def test_batch_worker_writes_v2_manifest_with_snapped_segments(tmp_path, monkeypatch):
    """Full worker run with a fake Whisper model returning sloppy times + words."""
    from src.core import vo_engine

    wav = str(tmp_path / "Part 1.wav")
    _write_wav(wav, make_vo([(0.50, 1.80), (2.60, 4.10)], 5.0))

    W = lambda t, s, e: SimpleNamespace(word=t, start=s, end=e, probability=0.95)
    fake_segments = [
        SimpleNamespace(start=0.2, end=2.2, text=" Hello there.", words=[W(" Hello", 0.3, 1.0), W(" there.", 1.1, 2.1)]),
        SimpleNamespace(start=2.3, end=4.6, text=" General Kenobi.", words=[W(" General", 2.4, 3.2), W(" Kenobi.", 3.3, 4.5)]),
    ]

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kwargs):
            assert kwargs.get("word_timestamps") is True
            return iter(fake_segments), SimpleNamespace(duration=5.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(vo_engine.WhisperModelManager, "is_model_downloaded", classmethod(lambda cls, m: True))
    monkeypatch.setattr(vo_engine.WhisperModelManager, "resolve_device_and_compute", classmethod(lambda cls: ("cpu", "int8")))

    out_json = str(tmp_path / "t.json")
    worker = vo_engine.VOBatchTranscribeWorker([wav], project_name="Test", output_json_path=out_json)
    results = {}
    worker.signals.batch_finished.connect(lambda p, m: results.update(path=p, manifest=m))
    worker.signals.error.connect(lambda e: results.update(error=e))
    worker.run()

    assert "error" not in results, results.get("error")
    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 2
    assert data["detection"]["threshold_db"] == DetectionSettings().threshold_db
    assert data["files"][0]["island_count"] == 2
    assert data["files"][0]["noise_floor_db"] is not None
    assert len(data["islands"]) == 2 and data["islands"][0]["part_id"] == 1

    s0, s1 = data["segments"]
    assert [s0["id"], s1["id"]] == [0, 1]
    assert s0["snapped"] and s1["snapped"]
    assert s0["whisper_in"] == 0.2
    assert s0["source_in"] == pytest.approx(0.46, abs=0.015)
    assert s1["source_out"] == pytest.approx(4.18, abs=0.015)
    assert s1["global_in"] == s1["source_in"]  # single part: offset 0
    assert [w["w"] for w in s0["words"]] == ["Hello", "there."]
