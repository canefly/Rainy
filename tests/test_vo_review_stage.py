"""Review tab: loading, decisions, sliders, keyboard, and dialog wiring."""
import json
import os

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from src.core import vo_review as R
from src.core.vo_engine import save_vo_transcript_json, load_vo_transcript_json
from src.core.vo_islands import HOP_S, SAMPLE_RATE, compute_envelope_db
from src.ui.components.footage.review_stage import FlagRow, GroupRow, ReviewStage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _env(spans, total, quiet=None):
    rng = np.random.default_rng(11)
    n = int(total * SAMPLE_RATE)
    x = rng.normal(0, 10 ** (-70 / 20), n).astype(np.float32)
    t = np.arange(n) / SAMPLE_RATE
    tone = (np.sign(np.sin(2 * np.pi * 180 * t)) * 10 ** (-18 / 20)).astype(np.float32)
    for a, b in spans:
        ia, ib = int(a * SAMPLE_RATE), int(b * SAMPLE_RATE)
        x[ia:ib] += tone[ia:ib]
    if quiet:
        ia, ib = int(quiet[0] * SAMPLE_RATE), int(quiet[1] * SAMPLE_RATE)
        x[ia:ib] += rng.normal(0, 10 ** (-50 / 20), ib - ia).astype(np.float32)
    return compute_envelope_db(x, SAMPLE_RATE, HOP_S)


def W(w, s, e):
    return {"w": w, "s": s, "e": e, "p": 0.9}


@pytest.fixture
def transcript(tmp_path):
    """
    One 16 s part:
      0.5-2.5  take 1 / 3.0-5.0 take 2 of the same line
      7.5-9.5  a different line (long pause before it)
      10.4-10.8 quiet wordless sound (breath)
      12.0-14.0 last line (long pause before it)
    """
    spans = [(0.5, 2.5), (3.0, 5.0), (7.5, 9.5), (12.0, 14.0)]
    env = _env(spans, 16.0, quiet=(10.4, 10.8))
    line = "It was the third night he hadn't come home."
    segs = [
        {"id": 0, "part_id": 1, "source_in": 0.4, "source_out": 2.7, "text": line, "words": [W("It", 0.45, 0.8), W("home.", 2.0, 2.6)]},
        {"id": 1, "part_id": 1, "source_in": 2.9, "source_out": 5.2, "text": line, "words": [W("It", 3.0, 3.3), W("home.", 4.5, 5.0)]},
        {"id": 2, "part_id": 1, "source_in": 7.3, "source_out": 9.6, "text": "Police were called the next morning.", "words": [W("Police", 7.5, 8.0), W("morning.", 9.0, 9.5)]},
        {"id": 3, "part_id": 1, "source_in": 11.8, "source_out": 14.2, "text": "Nobody saw him leave.", "words": [W("Nobody", 12.0, 12.6), W("leave.", 13.5, 14.0)]},
    ]
    manifest = {
        "version": 1,  # pre-snapping transcript: the tab upgrades it
        "files": [{"part_id": 1, "filename": "Part 1.wav", "path": str(tmp_path / "Part 1.wav"),
                   "global_offset_sec": 0.0, "duration_sec": 16.0}],
        "segments": segs,
    }
    path = save_vo_transcript_json(manifest, str(tmp_path / "t_vo_transcript.json"))
    return path, {1: env}


def test_empty_state_without_transcript(qapp):
    st = ReviewStage()
    counts = []
    st.open_count_changed.connect(counts.append)
    st.load("")
    assert st.pages.currentWidget() is st.empty_page
    assert counts == [0]


def test_load_upgrades_v1_and_lists_flags(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    timing, counts = [], []
    st.timing_changed.connect(lambda p, segs: timing.append(segs))
    st.open_count_changed.connect(counts.append)
    st.load(path, envelopes=envs)
    assert st.pages.currentWidget() is st.main_page

    kinds = sorted({f["type"] for f in st.flags})
    assert kinds == sorted([R.DUPLICATE_TAKE, R.BREATH]), "silence is never a flag"
    singles = [r for r in st._rows.values() if r.flag["type"] == R.DUPLICATE_TAKE]
    assert len(singles) == 1
    assert set(st._group_rows) == {R.BREATH}
    assert counts[-1] == 2

    st.save()  # flush the debounced save
    data = load_vo_transcript_json(path)
    assert data["version"] == 2 and data["islands"]
    assert data["review"]["open_count"] == 2
    assert data["review"]["summary"]["clips"] == 5
    assert timing and "words" not in timing[-1][0], "segments handed to the workspace are slim"
    assert data["segments"][0]["source_in"] == pytest.approx(0.46, abs=0.02)


def test_decisions_persist_and_reload(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    dup = next(f for f in st.flags if f["type"] == R.DUPLICATE_TAKE)
    st.decide(dup["id"], "pick:1")
    st._decide_group(R.BREATH, R.DROP)
    st.save()

    again = ReviewStage()
    counts = []
    again.open_count_changed.connect(counts.append)
    again.load(path, envelopes=envs)
    assert again.decisions[dup["id"]] == "pick:1"
    assert counts[-1] == 0
    assert again.open_lbl.text() == "All reviewed"
    dropped = R.compute_dropped_ranges(again.flags, again.decisions)
    assert {r["reason"] for r in dropped} == {"take", "breath"}
    assert "drops" in again.readout.text()


def test_one_noise_slider_and_greyed_silence(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    assert not hasattr(st, "lim_slider") and not hasattr(st, "keep_slider")
    kept = [k for k in R.compute_kept_ranges(st.manifest, st.flags, st.decisions, st.detection) if k["part_id"] == 1]
    assert len(kept) == 5 and st.wave._kept == kept, "waveform knows what is kept; the rest is drawn greyed"
    text = st.readout.text()
    assert "5 VO clips" in text and "deletes" in text and "of silence" in text
    assert "room tone -7" in text  # generated room tone sits at -70 dB, not clamped to -90


def test_threshold_slider_resnaps_and_notifies_workspace(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    st.save()
    timing = []
    st.timing_changed.connect(lambda p, segs: timing.append(segs))
    breaths_loose = [f for f in st.flags if f["type"] == R.BREATH]
    st.th_slider.setValue(30)  # strict: the -50 dB breath is now silence
    st._apply_slider_settings()
    st.save()
    assert breaths_loose and not [f for f in st.flags if f["type"] == R.BREATH]
    assert timing, "re-snapped segments are pushed to the workspace"
    assert load_vo_transcript_json(path)["detection"]["threshold_db"] == 30.0


def test_filters_and_group_expansion(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    st._set_filter("breaths")
    assert all(r.flag["type"] == R.BREATH for r in st._rows.values()) and st._rows
    st._set_filter("takes")
    assert [r.flag["type"] for r in st._rows.values()] == [R.DUPLICATE_TAKE]
    st._set_filter("all")
    assert not any(r.flag["type"] == R.BREATH for r in st._rows.values())
    st._toggle_group(R.BREATH)
    assert any(r.flag["type"] == R.BREATH for r in st._rows.values())


def _key(st, key):
    st.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


def test_keyboard_navigation_and_decisions(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    st._set_filter("breaths")
    _key(st, Qt.Key_Down)
    assert st.selected_id.startswith(R.BREATH)
    _key(st, Qt.Key_K)
    assert st.decisions[st.selected_id] == R.KEEP
    _key(st, Qt.Key_D)
    assert st.decisions[st.selected_id] == R.DROP
    _key(st, Qt.Key_Up)  # stays on the first row, no wrap
    assert st.selected_id.startswith(R.BREATH)

    st._set_filter("takes")
    _key(st, Qt.Key_Down)
    _key(st, Qt.Key_2)
    assert st.decisions[st.selected_id] == "pick:1"


def test_wave_click_opens_collapsed_group(qapp, transcript):
    path, envs = transcript
    st = ReviewStage()
    st.load(path, envelopes=envs)
    breath = next(f for f in st.flags if f["type"] == R.BREATH)
    assert breath["id"] not in st._rows
    st._on_wave_clicked(breath["id"])
    assert breath["id"] in st._rows and st.selected_id == breath["id"]


def test_dialog_has_review_tab_with_count(qapp, transcript):
    from src.ui.components.footage_relink_dialog import FootageRelinkDialog
    path, envs = transcript
    dlg = FootageRelinkDialog(links=[], current_footage_dir="", project_name="Review_Test")
    assert "review" in dlg.stage_tabs.items
    dlg.switch_stage("review")
    assert dlg.stage_stack.currentWidget() is dlg.review_stage
    dlg.vo_file_path = __file__  # any existing file: "audio is set"
    dlg.vo_segments = [{"id": 0}]
    dlg.review_stage.load(path, envelopes=envs)
    assert dlg.stage_tabs.items["review"].text() == "Review · 2"
    assert "2 to review" in dlg.queue_strip.vo_lbl.text()
    for f in dlg.review_stage.flags:
        dlg.review_stage.decide(f["id"], R.valid_decisions(f)[-1])
    assert dlg.stage_tabs.items["review"].text() == "Review"
    assert "to review" not in dlg.queue_strip.vo_lbl.text()
    dlg.cleanup()


# ---------------------------------------------------------------------------
# Quick player: zoom, scrub, transport
# ---------------------------------------------------------------------------
from PySide6.QtCore import QPoint, QPointF  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def _loaded(transcript, tmp_path=None):
    path, envs = transcript
    st = ReviewStage()
    st.resize(800, 520)
    st.load(path, envelopes=envs)
    st.show()
    QApplication.processEvents()
    return st


def test_wheel_zoom_and_fit(qapp, transcript):
    st = _loaded(transcript)
    w = st.wave
    assert not w.zoomed and w.view_end == pytest.approx(16.0)
    w.zoom_by(0.25, anchor=8.0)
    assert w.zoomed and w.view_span == pytest.approx(4.0) and w.view_start <= 8.0 <= w.view_end
    for _ in range(40):
        w.zoom_by(0.5)
    assert w.view_span == pytest.approx(w.MIN_SPAN_S), "zoom stops at half a second"
    st.fit_btn.click()
    assert not w.zoomed


def test_zoom_survives_recompute_but_resets_on_new_part(qapp, transcript):
    st = _loaded(transcript)
    st.wave.zoom_by(0.25, anchor=8.0)
    st._recompute(save=False)  # e.g. a decision or the noise slider
    assert st.wave.zoomed
    st.wave.set_data(np.zeros(100), 1.0, [])  # another part's envelope
    assert not st.wave.zoomed


def test_overview_handle_squeeze_zooms_and_double_click_resets(qapp, transcript):
    st = _loaded(transcript)
    w = st.wave
    y = int(w.OV_TOP + w.OV_H / 2)
    left, right = w._thumb()
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, QPoint(int(right), y))
    QTest.mouseMove(w, QPoint(int(right) - 300, y))
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, QPoint(int(right) - 300, y))
    assert w.zoomed and w.view_span < 16.0
    span = w.view_span
    left, right = w._thumb()
    mid = int((left + right) / 2)
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, QPoint(mid, y))
    QTest.mouseMove(w, QPoint(mid + 60, y))
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, QPoint(mid + 60, y))
    assert w.view_span == pytest.approx(span) and w.view_start > 0, "dragging the middle pans"
    QTest.mouseDClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(mid, y))
    assert not w.zoomed


def test_scrub_moves_playhead_silently_and_click_selects_flag(qapp, transcript):
    st = _loaded(transcript)
    w = st.wave
    breath = next(f for f in st.flags if f["type"] == R.BREATH)
    x = int(w._x((breath["in"] + breath["out"]) / 2))
    QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, QPoint(x, 40))
    assert st.selected_id == breath["id"]
    assert breath["in"] <= st.playhead <= breath["out"], "the click put the playhead where you clicked"
    assert not st.preview.is_playing

    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, QPoint(10, 40))
    QTest.mouseMove(w, QPoint(int(w._x(13.0)), 40))
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, QPoint(int(w._x(13.0)), 40))
    assert st.playhead == pytest.approx(13.0, abs=0.05)
    assert st.selected_id == breath["id"], "a drag only scrubs"


def test_selecting_a_flag_frames_it_and_parks_the_playhead(qapp, transcript):
    st = _loaded(transcript)
    st._set_filter("takes")
    _key(st, Qt.Key_Down)
    flag = next(f for f in st.flags if f["id"] == st.selected_id)
    assert st.wave.zoomed and st.wave.view_start <= flag["in"] and flag["out"] <= st.wave.view_end
    assert st.playhead == pytest.approx(max(0.0, flag["in"] - 1.0))


def test_zoomed_paint_draws_clip_edges(qapp, transcript):
    st = _loaded(transcript)
    st.wave.frame(7.0, 10.0)
    img = st.wave.grab().toImage()
    assert not img.isNull()
    k = next(k for k in st.wave._kept if k["in"] > 7.0)
    x = round(st.wave._x(k["in"]))
    c = img.pixelColor(x, st.wave.WAVE_H // 2)
    assert c.red() > 150 and c.green() > 150 and c.blue() > 150, "a light [ bracket at the clip's in point"


def _write_wav(path, seconds, rate=16000):
    import wave
    pcm = (np.sin(np.arange(int(seconds * rate)) * 0.1) * 3000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def test_space_plays_cut_preview_from_playhead(qapp, transcript, tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from src.core.ffmpeg_encoder import get_ffmpeg_path
    if not get_ffmpeg_path():
        pytest.skip("FFmpeg not available")
    monkeypatch.setenv("KNOT_CUT_PREVIEW_DIR", str(tmp_path / "cut"))
    st = _loaded(transcript)
    _write_wav(st._current_part()["path"], 16.0)
    calls = []
    monkeypatch.setattr(st.preview, "play", lambda *a, **k: calls.append((a, k)))

    st.playhead = 6.0  # in the silence before the 7.5 s line
    _key(st, Qt.Key_Space)
    assert st.transport_lbl.text() == "Building cut preview…"
    QThreadPool.globalInstance().waitForDone(20000)
    QApplication.processEvents()
    (out, start), kw = calls[-1]
    tmap = kw["tmap"]
    assert os.path.isfile(out) and out in st._renders
    clip = next(c for c in tmap if c[1] > 6.0)
    assert start == pytest.approx(clip[0]), "starts at the next VO clip in cut time"

    st.cut_switch.setChecked(False)
    st.playhead = 6.0
    st._start_playback()
    (path, start), kw = calls[-1]
    assert path == st._current_part()["path"] and start == 6.0 and "tmap" not in kw, "raw file"

    st.cleanup()
    assert not os.path.exists(out), "cut previews are deleted when the dialog closes"


def test_play_without_vo_file_says_so(qapp, transcript):
    st = _loaded(transcript)
    st.toggle_play()
    assert st.transport_lbl.text() == "VO file not found"
