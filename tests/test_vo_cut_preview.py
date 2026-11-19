"""Cut preview: time mapping between source and cut time, and the FFmpeg render."""
import os
import wave

import numpy as np
import pytest

from src.core import vo_cut_preview as C
from src.core.ffmpeg_encoder import get_ffmpeg_path

RANGES = [{"in": 5.0, "out": 6.0}, {"in": 1.0, "out": 2.5}, {"in": 8.0, "out": 8.0}]


def test_time_map_sorts_and_skips_empty_ranges():
    tmap = C.build_time_map(RANGES)
    assert tmap == [(0.0, 1.0, 2.5), (1.5, 5.0, 6.0)]
    assert C.cut_duration(tmap) == pytest.approx(2.5)


def test_source_to_cut_jumps_over_deleted_gaps():
    tmap = C.build_time_map(RANGES)
    assert C.source_to_cut(0.2, tmap) == 0.0           # before the first clip
    assert C.source_to_cut(2.0, tmap) == pytest.approx(1.0)
    assert C.source_to_cut(3.7, tmap) == pytest.approx(1.5)  # in the gap: next clip
    assert C.source_to_cut(5.5, tmap) == pytest.approx(2.0)
    assert C.source_to_cut(7.0, tmap) is None           # nothing left to play


def test_cut_to_source_round_trips():
    tmap = C.build_time_map(RANGES)
    for t in (1.0, 1.7, 2.49, 5.0, 5.9):
        assert C.cut_to_source(C.source_to_cut(t, tmap), tmap) == pytest.approx(t)
    assert C.cut_to_source(99.0, tmap) == 6.0
    assert C.cut_to_source(0.0, []) == 0.0


def test_filter_graph_trims_each_clip_and_concats():
    g = C.build_atrim_filter(C.build_time_map(RANGES))
    assert "asplit=2[s0][s1]" in g and "concat=n=2:v=0:a=1[out]" in g
    assert "atrim=start=1.000000:end=2.500000" in g
    single = C.build_atrim_filter([(0.0, 1.0, 2.0)])
    assert "asplit" not in single and single.endswith("[out]")


def test_preview_path_is_stable_and_range_specific(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOT_CUT_PREVIEW_DIR", str(tmp_path / "cache"))
    src = tmp_path / "vo.wav"
    src.write_bytes(b"x")
    a = C.preview_path_for(str(src), C.build_time_map(RANGES))
    assert a == C.preview_path_for(str(src), C.build_time_map(RANGES))
    assert a != C.preview_path_for(str(src), C.build_time_map(RANGES[:1]))
    assert os.path.dirname(a) == str(tmp_path / "cache")


def _write_wav(path, seconds, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def _frames(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes(), w.getframerate()


@pytest.mark.skipif(not get_ffmpeg_path(), reason="FFmpeg not available")
@pytest.mark.parametrize("inline", [True, False])
def test_render_is_sample_accurate(tmp_path, monkeypatch, inline):
    monkeypatch.setenv("KNOT_CUT_PREVIEW_DIR", str(tmp_path / "cache"))
    if not inline:  # long packs hand the graph over as a file
        monkeypatch.setattr(C, "MAX_INLINE_FILTER_CHARS", 0)
    src = tmp_path / "vo.wav"
    _write_wav(src, 8.0)
    tmap = C.build_time_map([{"in": 0.5, "out": 1.25}, {"in": 3.0, "out": 4.5}, {"in": 6.0, "out": 6.1}])
    out = C.render_cut_preview(str(src), tmap)
    n, rate = _frames(out)
    assert rate == 48000, "keeps the original sample rate"
    assert abs(n - round(C.cut_duration(tmap) * rate)) <= 2
    assert not [f for f in os.listdir(tmp_path / "cache") if f.endswith((".part.wav", ".txt"))]
    mtime = os.path.getmtime(out)
    assert C.render_cut_preview(str(src), tmap) == out and os.path.getmtime(out) == mtime, "reused"


def test_render_refuses_missing_file_or_empty_map(tmp_path):
    with pytest.raises(ValueError):
        C.render_cut_preview(str(tmp_path / "x.wav"), [])
    with pytest.raises(FileNotFoundError):
        C.render_cut_preview(str(tmp_path / "x.wav"), [(0.0, 0.0, 1.0)])
