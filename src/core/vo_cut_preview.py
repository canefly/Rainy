"""
Cut preview for the Review tab: renders one VO part's kept ranges (the VO clips),
butted together, into a temporary WAV so the editor hears exactly what the
timeline will get. The source file is never modified.

Also maps between the two time axes of that preview:
  source time = seconds inside the original part file
  cut time    = seconds inside the rendered preview (silence and drops removed)
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

# (cut_start, src_in, src_out) per kept range, in order
TimeMap = List[Tuple[float, float, float]]

MAX_INLINE_FILTER_CHARS = 6000


def build_time_map(ranges: Sequence[Dict[str, Any]]) -> TimeMap:
    """Kept ranges of one part (dicts with in/out) -> time map, sorted, zero-length dropped."""
    out: TimeMap = []
    cut = 0.0
    for r in sorted(ranges, key=lambda r: float(r["in"])):
        a, b = float(r["in"]), float(r["out"])
        if b - a <= 1e-6:
            continue
        out.append((cut, a, b))
        cut += b - a
    return out


def cut_duration(tmap: TimeMap) -> float:
    return (tmap[-1][0] + tmap[-1][2] - tmap[-1][1]) if tmap else 0.0


def source_to_cut(t: float, tmap: TimeMap) -> Optional[float]:
    """Source time -> cut time. Time inside a deleted gap maps to the start of the next clip;
    after the last clip returns None (nothing left to play)."""
    for c0, a, b in tmap:
        if t < a:
            return c0
        if t < b:
            return c0 + (t - a)
    return None


def cut_to_source(c: float, tmap: TimeMap) -> float:
    """Cut time -> source time (clamped to the last clip's out)."""
    if not tmap:
        return 0.0
    for c0, a, b in tmap:
        if c < c0 + (b - a):
            return a + max(0.0, c - c0)
    return tmap[-1][2]


def build_atrim_filter(tmap: TimeMap) -> str:
    """Sample-accurate filter graph: split the input once per clip, trim each, concat."""
    n = len(tmap)
    if n == 1:
        _, a, b = tmap[0]
        return f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[out]"
    parts = [f"[0:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, (_, a, b) in enumerate(tmap):
        parts.append(f"[s{i}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[t{i}]")
    parts.append("".join(f"[t{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]")
    return ";".join(parts)


def preview_cache_dir() -> str:
    d = os.environ.get("KNOT_CUT_PREVIEW_DIR") or os.path.join(tempfile.gettempdir(), "knot_cut_preview")
    os.makedirs(d, exist_ok=True)
    return d


def preview_path_for(src_path: str, tmap: TimeMap) -> str:
    """Stable file name per (source file, its mtime/size, exact ranges)."""
    try:
        st = os.stat(src_path)
        stamp = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        stamp = "0"
    key = src_path + "|" + stamp + "|" + ";".join(f"{a:.4f}-{b:.4f}" for _, a, b in tmap)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(preview_cache_dir(), f"cut_{digest}.wav")


def render_cut_preview(src_path: str, tmap: TimeMap, out_path: Optional[str] = None,
                       ffmpeg_bin: Optional[str] = None) -> str:
    """Renders the kept ranges into a WAV (original sample rate and channels). Returns its path.
    Reuses an existing render of the same ranges."""
    if not tmap:
        raise ValueError("Nothing to preview: no VO clips in this part.")
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError(f"VO file not found: {src_path}")
    out_path = out_path or preview_path_for(src_path, tmap)
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
        return out_path
    if ffmpeg_bin is None:
        from .ffmpeg_encoder import get_ffmpeg_path
        ffmpeg_bin = get_ffmpeg_path()
    if not ffmpeg_bin:
        raise RuntimeError("FFmpeg not found; cannot build the cut preview.")

    graph = build_atrim_filter(tmap)
    script_path = None
    cmd = [ffmpeg_bin, "-v", "error", "-nostdin", "-y", "-i", src_path]
    if len(graph) <= MAX_INLINE_FILTER_CHARS:
        cmd += ["-filter_complex", graph]
    else:
        # Long packs would overflow the Windows command line: hand the graph over as a file.
        fd, script_path = tempfile.mkstemp(suffix=".txt", dir=os.path.dirname(out_path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(graph)
        cmd += ["-filter_complex_script", script_path]
    tmp_out = out_path + ".part.wav"
    cmd += ["-map", "[out]", "-vn", "-acodec", "pcm_s16le", tmp_out]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              creationflags=(0x08000000 if sys.platform == "win32" else 0))
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"FFmpeg failed to build the cut preview: {err[:240]}")
        os.replace(tmp_out, out_path)
    finally:
        for p in (script_path, tmp_out):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return out_path
