"""
Speech-island detection and Whisper edge snapping for clean voiceover audio.

Whisper is good at *what* was said but sloppy about *when*: segment edges drift
100-500 ms into the surrounding silence or clip the tail of the last word. Clean
VO packs have near-silent gaps between phrases, so the waveform itself is a far
better timing source.

This module never modifies audio. It:
  1. decodes the file to mono PCM (in memory) and measures loudness every 10 ms,
  2. finds "speech islands" (runs of sound above the file's own noise floor),
  3. snaps Whisper segment in/out points to the edges of those islands.

Whisper still decides the text; the waveform decides the timing.
"""
import bisect
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import app_paths
from .logger import get_logger

logger = get_logger("vo_islands")

HOP_S = 0.01                 # 10 ms loudness frames
SAMPLE_RATE = 16000          # plenty for loudness; matches what Whisper decodes at
DIGITAL_SILENCE_DB = -120.0  # value used for all-zero frames
ENVELOPE_CACHE_VERSION = "v1"

_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")


@dataclass
class DetectionSettings:
    """Tunables for island detection and snapping. Persisted in the transcript JSON."""
    threshold_db: float = 12.0   # speech = louder than noise floor + this
    min_gap_s: float = 0.30      # silences shorter than this don't split an island
    min_island_s: float = 0.10   # sounds shorter than this are clicks, not speech
    pre_roll_s: float = 0.04     # handle kept before an island's first sound
    post_roll_s: float = 0.08    # handle kept after an island's last sound (decay/breath tail)
    split_gap_s: float = 0.70    # split a Whisper segment at a silence this long after a sentence end
    word_tolerance_s: float = 0.50  # how far a Whisper word time may sit from its island

    def to_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DetectionSettings":
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in data.items():
            if k in known:
                try:
                    kwargs[k] = float(v)
                except (TypeError, ValueError):
                    pass
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Audio -> loudness envelope
# ---------------------------------------------------------------------------
def decode_audio_mono(path: str, sample_rate: int = SAMPLE_RATE, ffmpeg_bin: Optional[str] = None) -> np.ndarray:
    """Decodes any audio/video file to mono float32 PCM in [-1, 1] via FFmpeg (in memory)."""
    if ffmpeg_bin is None:
        from .ffmpeg_encoder import get_ffmpeg_path
        ffmpeg_bin = get_ffmpeg_path()
    if not ffmpeg_bin:
        raise RuntimeError("FFmpeg not found; cannot analyse VO waveform.")

    cmd = [
        ffmpeg_bin, "-v", "error", "-nostdin",
        "-i", path,
        "-vn", "-ac", "1", "-ar", str(int(sample_rate)),
        "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        creationflags=(0x08000000 if sys.platform == "win32" else 0),
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"FFmpeg failed to decode '{os.path.basename(path)}': {err[:240]}")
    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def compute_envelope_db(samples: np.ndarray, sample_rate: int = SAMPLE_RATE, hop_s: float = HOP_S) -> np.ndarray:
    """RMS loudness in dBFS for each hop_s frame. All-zero frames read DIGITAL_SILENCE_DB."""
    samples = np.asarray(samples, dtype=np.float32)
    hop = max(1, int(round(sample_rate * hop_s)))
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)
    n_frames = int(np.ceil(samples.size / hop))
    padded = np.zeros(n_frames * hop, dtype=np.float32)
    padded[: samples.size] = samples
    frames = padded.reshape(n_frames, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(rms)
    db = np.where(np.isfinite(db), db, DIGITAL_SILENCE_DB)
    return np.maximum(db, DIGITAL_SILENCE_DB).astype(np.float32)


def get_envelope_cache_path(audio_path: str, hop_s: float = HOP_S) -> Optional[str]:
    """Cache file for an audio file's envelope, keyed by path + mtime + size."""
    if not audio_path or not os.path.isfile(audio_path):
        return None
    try:
        key = f"{ENVELOPE_CACHE_VERSION}_{os.path.abspath(audio_path)}_{os.path.getmtime(audio_path)}_{os.path.getsize(audio_path)}_{hop_s}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
        cache_dir = os.environ.get("KNOT_VO_ENVELOPE_DIR") or os.path.join(
            app_paths.cache_dir(), "vo_envelopes"
        )
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{digest}.npy")
    except Exception as e:
        logger.warning("Could not build envelope cache path for %s: %s", audio_path, e)
        return None


def load_or_compute_envelope(audio_path: str, hop_s: float = HOP_S) -> Tuple[np.ndarray, Optional[str]]:
    """Returns (envelope_db, cache_path). Reads the cache when fresh, otherwise decodes and caches."""
    cache_path = get_envelope_cache_path(audio_path, hop_s)
    if cache_path and os.path.isfile(cache_path):
        try:
            return np.load(cache_path), cache_path
        except Exception as e:
            logger.warning("Stale/corrupt envelope cache %s (%s); recomputing.", cache_path, e)

    env = compute_envelope_db(decode_audio_mono(audio_path), SAMPLE_RATE, hop_s)
    if cache_path:
        try:
            tmp = cache_path + ".tmp.npy"
            np.save(tmp, env)
            os.replace(tmp, cache_path)
        except Exception as e:
            logger.warning("Failed writing envelope cache %s: %s", cache_path, e)
            cache_path = None
    return env, cache_path


# ---------------------------------------------------------------------------
# Envelope -> speech islands
# ---------------------------------------------------------------------------
def estimate_noise_floor_db(env_db: np.ndarray) -> float:
    """
    Estimates the room-tone level. Digital-zero frames are ignored (gated VO would
    otherwise read -120 dB and make every faint hiss count as speech).
    """
    env_db = np.asarray(env_db, dtype=np.float32)
    if env_db.size == 0:
        return DIGITAL_SILENCE_DB
    audible = env_db[env_db > DIGITAL_SILENCE_DB + 10.0]
    if audible.size < 10:
        return DIGITAL_SILENCE_DB
    return float(min(float(np.percentile(audible, 5)), -20.0))  # measured room tone, unclamped below


# Detection never uses a floor lower than this: on pitch-black VO (-100 dB room tone)
# +threshold would otherwise count reverb tails and dither as speech.
MIN_DETECTION_FLOOR_DB = -90.0


def _active_runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """[(start_frame, end_frame_exclusive)] for each run of True."""
    if mask.size == 0:
        return []
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def detect_islands(
    env_db: np.ndarray,
    settings: Optional[DetectionSettings] = None,
    hop_s: float = HOP_S,
    noise_floor_db: Optional[float] = None,
) -> Tuple[List[Dict[str, float]], float]:
    """
    Finds speech islands in a loudness envelope.
    Returns ([{"in", "out", "peak_db"}...] in seconds, measured room-tone dB).
    The threshold is relative to max(room tone, MIN_DETECTION_FLOOR_DB).
    """
    settings = settings or DetectionSettings()
    env_db = np.asarray(env_db, dtype=np.float32)
    floor = estimate_noise_floor_db(env_db) if noise_floor_db is None else float(noise_floor_db)
    if env_db.size == 0:
        return [], floor

    base = max(floor, MIN_DETECTION_FLOOR_DB)
    speech_level = float(np.percentile(env_db, 95))
    if speech_level < base + 6.0:
        return [], floor  # nothing meaningfully louder than room tone: no speech
    # Never let the threshold climb into the speech itself on very dense reads.
    threshold = min(base + settings.threshold_db, speech_level - 6.0)

    runs = _active_runs(env_db > threshold)
    if not runs:
        return [], floor

    min_gap = max(1, int(round(settings.min_gap_s / hop_s)))
    merged: List[List[int]] = [list(runs[0])]
    for start, end in runs[1:]:
        if start - merged[-1][1] < min_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    min_len = max(1, int(round(settings.min_island_s / hop_s)))
    islands: List[Dict[str, float]] = []
    for start, end in merged:
        if end - start < min_len:
            continue
        islands.append({
            "in": round(start * hop_s, 3),
            "out": round(end * hop_s, 3),
            "peak_db": round(float(np.max(env_db[start:end])), 1),
        })
    return islands, floor


# ---------------------------------------------------------------------------
# Whisper segments -> snapped segments
# ---------------------------------------------------------------------------
def _island_index_for(
    t: float,
    islands: Sequence[Dict[str, float]],
    starts: List[float],
    tol: float,
    prefer: str = "next",
) -> Optional[int]:
    """
    Index of the island containing t. When t sits in a gap, a *start* time means
    speech begins at the next island (prefer="next") and an *end* time means it
    ended at the previous one (prefer="prev"); the other side is only a fallback.
    Either side must be within tol seconds.
    """
    if not islands:
        return None
    eps = 1e-6
    pos = bisect.bisect_right(starts, t + eps) - 1
    if 0 <= pos < len(islands) and islands[pos]["in"] - eps <= t <= islands[pos]["out"] + eps:
        return pos
    prev_idx = pos if pos >= 0 else None
    next_idx = pos + 1 if pos + 1 < len(islands) else None
    prev_ok = prev_idx is not None and t - islands[prev_idx]["out"] <= tol + eps
    next_ok = next_idx is not None and islands[next_idx]["in"] - t <= tol + eps
    order = ((next_ok, next_idx), (prev_ok, prev_idx)) if prefer == "next" else ((prev_ok, prev_idx), (next_ok, next_idx))
    for ok, idx in order:
        if ok:
            return idx
    return None


def _content_span(seg: Dict[str, Any]) -> Tuple[float, float]:
    words = seg.get("words") or []
    if words:
        return float(words[0]["s"]), float(words[-1]["e"])
    return float(seg["source_in"]), float(seg["source_out"])


def _join_words(words: Sequence[Dict[str, Any]]) -> str:
    return " ".join(str(w.get("w", "")).strip() for w in words if str(w.get("w", "")).strip())


def split_segments_at_long_gaps(
    segments: List[Dict[str, Any]],
    islands: Sequence[Dict[str, float]],
    settings: DetectionSettings,
) -> List[Dict[str, Any]]:
    """
    Whisper sometimes glues two sentences across a real pause. Split a segment
    where a sentence-ending word is followed by a silence >= split_gap_s, so an
    A-roll cut can land in that gap.
    """
    if not islands:
        return list(segments)
    starts = [i["in"] for i in islands]
    out: List[Dict[str, Any]] = []
    for seg in segments:
        words = seg.get("words") or []
        if len(words) < 2:
            out.append(seg)
            continue
        pieces: List[List[Dict[str, Any]]] = [[words[0]]]
        for prev_w, w in zip(words, words[1:]):
            split = False
            if _SENTENCE_END.search(str(prev_w.get("w", "")).strip()):
                a = _island_index_for(float(prev_w["e"]), islands, starts, settings.word_tolerance_s, "prev")
                b = _island_index_for(float(w["s"]), islands, starts, settings.word_tolerance_s, "next")
                if a is not None and b is not None and b > a:
                    silence = islands[b]["in"] - islands[b - 1]["out"]
                    split = silence >= settings.split_gap_s
            if split:
                pieces.append([w])
            else:
                pieces[-1].append(w)
        if len(pieces) == 1:
            out.append(seg)
            continue
        for chunk in pieces:
            piece = dict(seg)
            piece["words"] = chunk
            piece["text"] = _join_words(chunk)
            piece["source_in"] = round(float(chunk[0]["s"]), 3)
            piece["source_out"] = round(float(chunk[-1]["e"]), 3)
            piece["whisper_in"] = piece["source_in"]
            piece["whisper_out"] = piece["source_out"]
            piece["split_from_whisper"] = True
            out.append(piece)
    return out


def _quietest_point(env_db: Optional[np.ndarray], a: float, b: float, hop_s: float) -> float:
    """
    Time of the quietest 10 ms frame between a and b (the breath/dip between two
    sentences inside one island). Falls back to the midpoint without an envelope.
    Narrow or inverted windows are widened to +-150 ms around their midpoint.
    """
    mid = (a + b) / 2.0
    if env_db is None or len(env_db) == 0:
        return mid
    if b - a < 3 * hop_s:
        a, b = mid - 0.15, mid + 0.15
    i0 = max(0, int(np.floor(a / hop_s)))
    i1 = min(len(env_db), int(np.ceil(b / hop_s)) + 1)
    if i1 - i0 < 2:
        return mid
    window = np.asarray(env_db[i0:i1], dtype=np.float32)
    k = int(np.argmin(window))
    # Centre of the flat-bottomed valley around the minimum (frames within 1 dB of it).
    quiet = window <= window[k] + 1.0
    lo, hi = k, k
    while lo > 0 and quiet[lo - 1]:
        lo -= 1
    while hi < len(window) - 1 and quiet[hi + 1]:
        hi += 1
    return round((i0 + (lo + hi) / 2.0 + 0.5) * hop_s, 3)


def snap_segments_to_islands(
    segments: List[Dict[str, Any]],
    islands: Sequence[Dict[str, float]],
    settings: Optional[DetectionSettings] = None,
    duration_s: Optional[float] = None,
    env_db: Optional[np.ndarray] = None,
    hop_s: float = HOP_S,
) -> List[Dict[str, Any]]:
    """
    Returns copies of one part's segments with source_in/out snapped to island edges.

    - An edge snaps to its island's edge (plus pre/post roll) only when that island
      isn't shared with the neighbouring segment. When two segments share an island
      (sentences separated only by a breath, not silence), both meet at the quietest
      point between their words (the midpoint if no envelope is given).
    - Rolls never reach into the previous/next island.
    - Segments with no island nearby (e.g. hallucinated text over silence) keep
      Whisper's times and get snapped=False.
    Original Whisper times are kept in whisper_in/whisper_out.
    """
    settings = settings or DetectionSettings()
    segs = [dict(s) for s in segments]
    for s in segs:
        s.setdefault("whisper_in", s.get("source_in"))
        s.setdefault("whisper_out", s.get("source_out"))
    if not segs:
        return segs
    if not islands:
        for s in segs:
            s["snapped"] = False
        return segs

    starts = [i["in"] for i in islands]
    tol = settings.word_tolerance_s
    spans = [_content_span(s) for s in segs]
    in_idx = [_island_index_for(cs, islands, starts, tol, "next") for cs, _ in spans]
    out_idx = [_island_index_for(ce, islands, starts, tol, "prev") for _, ce in spans]
    end_limit = float(duration_s) if duration_s and duration_s > 0 else float(islands[-1]["out"]) + settings.post_roll_s

    n = len(segs)
    new_in: List[Optional[float]] = [None] * n
    new_out: List[Optional[float]] = [None] * n
    # Shared-island boundary between segment i and i+1, computed once so both sides agree.
    shared_cut: List[Optional[float]] = [None] * n
    for i in range(n - 1):
        if out_idx[i] is not None and in_idx[i + 1] is not None and in_idx[i + 1] <= out_idx[i]:
            shared_cut[i] = _quietest_point(env_db, spans[i][1], spans[i + 1][0], hop_s)

    for i in range(n):
        cs, ce = spans[i]
        a, b = in_idx[i], out_idx[i]

        # ---- in edge ----
        if a is not None and i > 0 and shared_cut[i - 1] is not None:
            new_in[i] = shared_cut[i - 1]                      # shared island: meet at the breath
        elif a is not None:
            floor_t = islands[a - 1]["out"] if a > 0 else 0.0
            new_in[i] = max(floor_t, islands[a]["in"] - settings.pre_roll_s, 0.0)

        # ---- out edge ----
        if b is not None and shared_cut[i] is not None:
            new_out[i] = shared_cut[i]
        elif b is not None:
            ceil_t = islands[b + 1]["in"] if b + 1 < len(islands) else end_limit
            new_out[i] = min(ceil_t, islands[b]["out"] + settings.post_roll_s, end_limit)

    prev_out = 0.0
    for i, s in enumerate(segs):
        w_in, w_out = float(s["source_in"]), float(s["source_out"])
        s_in = new_in[i] if new_in[i] is not None else w_in
        s_out = new_out[i] if new_out[i] is not None else w_out
        s_in = max(s_in, prev_out)
        if s_out - s_in < 0.02:
            s_in, s_out = max(w_in, prev_out), max(w_out, prev_out)
            snapped = False
        else:
            snapped = new_in[i] is not None and new_out[i] is not None
        s["source_in"] = round(s_in, 3)
        s["source_out"] = round(max(s_out, s_in), 3)
        s["snapped"] = bool(snapped)
        prev_out = s["source_out"]

    for i, s in enumerate(segs):
        s["gap_after_sec"] = round(segs[i + 1]["source_in"] - s["source_out"], 3) if i + 1 < n else None
    return segs


HEAVY_SEGMENT_KEYS = ("words",)


def slim_segments(segments: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Segments without per-word data. Words live only in the transcript JSON; the
    in-memory doc, project DB and MCP payloads get the slim version so a 10-minute
    VO doesn't drag thousands of word dicts through every save and AI request.
    """
    return [{k: v for k, v in s.items() if k not in HEAVY_SEGMENT_KEYS} for s in segments or []]


def analyze_and_snap_part(
    segments: List[Dict[str, Any]],
    env_db: np.ndarray,
    settings: Optional[DetectionSettings] = None,
    duration_s: Optional[float] = None,
    hop_s: float = HOP_S,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, float]], float]:
    """Convenience: islands from the envelope, split glued sentences, snap. Returns (segments, islands, floor)."""
    settings = settings or DetectionSettings()
    islands, floor = detect_islands(env_db, settings, hop_s)
    segs = split_segments_at_long_gaps(segments, islands, settings)
    return snap_segments_to_islands(segs, islands, settings, duration_s, env_db, hop_s), islands, floor
