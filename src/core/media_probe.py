import os
import sys
import json
import time
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from PySide6.QtCore import QObject, Signal, QRunnable

from .database import db
from .logger import get_logger
from .media_inspector import format_duration_display, format_resolution, inspect_mp4_metadata
from .timecode_engine import analyze_framerate

logger = get_logger("MediaProbe")

# Windows flag to prevent flashing console windows when running background subprocesses
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class ProbedMediaItem:
    file_path: str
    file_size: int
    mtime: float
    duration_seconds: float = 0.0
    duration_display: str = ""
    width: int = 0
    height: int = 0
    resolution: str = ""
    frame_rate: float = 0.0
    audio_channels: int = 0
    sample_rate: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    start_time: float = 0.0
    start_timecode: str = ""
    is_vfr: bool = False
    avg_frame_rate: float = 0.0
    is_interlaced: bool = False
    field_order: str = "progressive"


def _parse_fraction(val: Any) -> float:
    if not val:
        return 0.0
    s_val = str(val).strip()
    if "/" in s_val:
        parts = s_val.split("/", 1)
        try:
            n, d = float(parts[0]), float(parts[1])
            if d > 0:
                return round(n / d, 3)
        except Exception:
            return 0.0
    try:
        return round(float(s_val), 3)
    except Exception:
        return 0.0


def _extract_timecode_tag(tags: Any) -> str:
    if not isinstance(tags, dict):
        return ""
    for k, v in tags.items():
        if str(k).lower() in ("timecode", "time_code"):
            return str(v).strip()
    return ""


def get_ffprobe_path() -> Optional[str]:
    """
    Resolves the standalone ffprobe binary in priority order:
    1. Bundled frozen executable directory (PyInstaller / Nuitka bundle)
    2. Integrated application bin folder (clickables-tool-native/bin/ffprobe.exe)
    3. User AppData runtime cache (%LOCALAPPDATA%\com.canefly.clickables\bin\ffprobe.exe)
    4. System PATH fallback
    """
    binary_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"

    # 1. Bundled Frozen Distribution
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        bundled = os.path.join(base_dir, "bin", binary_name)
        if os.path.isfile(bundled):
            return bundled
        direct = os.path.join(base_dir, binary_name)
        if os.path.isfile(direct):
            return direct

    # 2. Integrated Dev Workspace (clickables-tool-native/bin/ffprobe.exe)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    dev_bin = os.path.join(root_dir, "bin", binary_name)
    if os.path.isfile(dev_bin):
        return dev_bin

    # 3. User AppData Runtime Cache
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        appdata_bin = os.path.join(local_appdata, "com.canefly.clickables", "bin", binary_name)
        if os.path.isfile(appdata_bin):
            return appdata_bin

    # 4. System PATH Fallback
    which_path = shutil.which("ffprobe")
    if which_path and os.path.isfile(which_path):
        return which_path

    return None


def get_ffprobe_status() -> Tuple[bool, str, str]:
    """
    Returns (is_available, status_label, binary_path).
    E.g. (True, "Ready (v7.1.1)", "D:\\Codes\\clickables-tool-native\\bin\\ffprobe.exe")
    """
    path = get_ffprobe_path()
    if not path:
        return False, "Not Found (Using Python Header Fallback)", ""

    try:
        proc = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
            creationflags=CREATE_NO_WINDOW
        )
        first_line = proc.stdout.splitlines()[0] if proc.stdout else "ffprobe"
        version_part = first_line.split("Copyright")[0].strip()
        return True, version_part, path
    except Exception as e:
        return True, f"Detected ({os.path.basename(path)})", path


def probe_media_file(file_path: str, use_cache: bool = True) -> Optional[ProbedMediaItem]:
    """
    Probes exact duration, streams, codec, resolution, and audio channels of a local media file.
    Fast path: Queries SQLite local_media_probe cache (0.1ms).
    Slow path: Runs ffprobe with JSON output, then caches result in SQLite.
    Fallback: Header analysis for fast offline MP4 metadata if ffprobe is absent.
    """
    if not file_path or not os.path.isfile(file_path):
        return None

    norm_path = os.path.normpath(file_path)
    try:
        file_size = os.path.getsize(norm_path)
        mtime = os.path.getmtime(norm_path)
    except Exception as e:
        logger.warning("Could not stat file '%s': %s", norm_path, e)
        return None

    # 1. Fast SQLite Cache Retrieval
    if use_cache:
        cached = db.get_local_media_probe(norm_path, file_size, mtime)
        if cached:
            dur_sec = float(cached.get("duration_seconds") or 0.0)
            return ProbedMediaItem(
                file_path=norm_path,
                file_size=file_size,
                mtime=mtime,
                duration_seconds=dur_sec,
                duration_display=cached.get("duration_display") or format_duration_display(dur_sec),
                width=int(cached.get("width") or 0),
                height=int(cached.get("height") or 0),
                resolution=cached.get("resolution") or "",
                frame_rate=float(cached.get("avg_frame_rate") or 23.976) if float(cached.get("frame_rate") or 0.0) > 120.0 else float(cached.get("frame_rate") or 0.0),
                audio_channels=int(cached.get("audio_channels") or 0),
                sample_rate=int(cached.get("sample_rate") or 0),
                video_codec=cached.get("video_codec") or "",
                audio_codec=cached.get("audio_codec") or "",
                has_video=bool(cached.get("width", 0) > 0),
                has_audio=bool(cached.get("audio_channels", 0) > 0),
                start_time=float(cached.get("start_time") or 0.0),
                start_timecode=str(cached.get("start_timecode") or ""),
                is_vfr=bool(cached.get("is_vfr", 0)),
                avg_frame_rate=float(cached.get("avg_frame_rate") or 0.0),
                is_interlaced=bool(cached.get("is_interlaced", 0)),
                field_order=str(cached.get("field_order") or "progressive"),
            )

    # 2. ffprobe Extraction
    ffprobe_bin = get_ffprobe_path()
    t0 = time.perf_counter()

    if ffprobe_bin:
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries",
            "format=duration,size,bit_rate,start_time:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,field_order,duration,nb_frames,start_time,sample_rate,channels,channel_layout:stream_tags:format_tags",
            "-of", "json",
            norm_path
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8.0,
                creationflags=CREATE_NO_WINDOW
            )
            if proc.returncode == 0 and proc.stdout:
                parsed = json.loads(proc.stdout)
                streams = parsed.get("streams", [])
                fmt = parsed.get("format", {})

                # Extract Duration
                dur_raw = fmt.get("duration")
                duration_sec = 0.0
                if dur_raw:
                    try:
                        duration_sec = float(dur_raw)
                    except (ValueError, TypeError):
                        duration_sec = 0.0

                # Extract Container-Level Start Time & Tags
                fmt_start_time = 0.0
                try:
                    fmt_start_time = float(fmt.get("start_time") or 0.0)
                except (ValueError, TypeError):
                    fmt_start_time = 0.0

                start_time = fmt_start_time
                start_timecode = _extract_timecode_tag(fmt.get("tags"))
                is_vfr = False
                avg_frame_rate = 0.0
                is_interlaced = False
                field_order = "progressive"

                width = 0
                height = 0
                frame_rate = 0.0
                video_codec = ""
                audio_codec = ""
                audio_channels = 0
                sample_rate = 0
                has_video = False
                has_audio = False

                for st in streams:
                    c_type = st.get("codec_type")
                    if c_type == "video" and not has_video:
                        has_video = True
                        width = int(st.get("width") or 0)
                        height = int(st.get("height") or 0)
                        video_codec = st.get("codec_name") or ""

                        # Detect interlaced scan order (tt, bb, tb, bt vs progressive/unknown)
                        raw_fo = str(st.get("field_order") or "").strip().lower()
                        if raw_fo in ("tt", "bb", "tb", "bt"):
                            is_interlaced = True
                            field_order = raw_fo
                        elif raw_fo:
                            field_order = raw_fo

                        # Check stream start_time offset if non-zero
                        st_start = st.get("start_time")
                        if st_start is not None:
                            try:
                                val = float(st_start)
                                if abs(val) > 0.001:
                                    start_time = val
                            except (ValueError, TypeError):
                                pass

                        # Check stream-level timecode tag (e.g. broadcast/cinema metadata)
                        st_tc = _extract_timecode_tag(st.get("tags"))
                        if st_tc:
                            start_timecode = st_tc

                        # Parse exact and average frame rates to detect VFR and conform
                        r_fps = _parse_fraction(st.get("r_frame_rate", ""))
                        avg_fps = _parse_fraction(st.get("avg_frame_rate", ""))

                        # If the video stream carries its own exact duration and nb_frames,
                        # check true video-stream average FPS (immune to `-c:a copy` audio track overhang)
                        st_dur = 0.0
                        st_frames = 0
                        try:
                            st_dur = float(st.get("duration") or 0.0)
                            st_frames = int(st.get("nb_frames") or 0)
                        except (ValueError, TypeError):
                            pass
                        if st_dur > 0.5 and st_frames > 10:
                            true_video_avg_fps = round(st_frames / st_dur, 3)
                            # If r_fps and true_video_avg_fps match closely, use true_video_avg_fps
                            if r_fps <= 120.0 and abs(r_fps - true_video_avg_fps) <= 0.08:
                                avg_fps = true_video_avg_fps

                        avg_frame_rate = avg_fps

                        exact_fps, nominal_base, detected_vfr, is_std = analyze_framerate(
                            r_fps, avg_fps, duration_sec=st_dur if st_dur > 0 else duration_sec
                        )
                        frame_rate = exact_fps
                        is_vfr = detected_vfr

                    elif c_type == "audio" and not has_audio:
                        has_audio = True
                        audio_codec = st.get("codec_name") or ""
                        audio_channels = int(st.get("channels") or 0)
                        sample_rate = int(st.get("sample_rate") or 0)

                resolution = format_resolution(width, height, is_video=has_video)
                dur_disp = format_duration_display(duration_sec)
                elapsed_ms = (time.perf_counter() - t0) * 1000

                # Cache into SQLite
                db.store_local_media_probe(
                    file_path=norm_path,
                    file_size=file_size,
                    mtime=mtime,
                    duration_seconds=duration_sec,
                    duration_display=dur_disp,
                    width=width,
                    height=height,
                    resolution=resolution,
                    frame_rate=frame_rate,
                    audio_channels=audio_channels,
                    sample_rate=sample_rate,
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    start_time=start_time,
                    start_timecode=start_timecode,
                    is_vfr=is_vfr,
                    avg_frame_rate=avg_frame_rate,
                    is_interlaced=is_interlaced,
                    field_order=field_order,
                )

                logger.info(
                    "Probed '%s' via ffprobe in %.1f ms -> Dur: %s (%.1fs), Res: %s, FPS: %.2f%s%s, Offset: %.3fs, Audio: %d ch",
                    os.path.basename(norm_path),
                    elapsed_ms,
                    dur_disp or "Unknown",
                    duration_sec,
                    resolution or "None",
                    frame_rate,
                    " (VFR)" if is_vfr else "",
                    f" (Interlaced:{field_order})" if is_interlaced else "",
                    start_time,
                    audio_channels
                )

                return ProbedMediaItem(
                    file_path=norm_path,
                    file_size=file_size,
                    mtime=mtime,
                    duration_seconds=duration_sec,
                    duration_display=dur_disp,
                    width=width,
                    height=height,
                    resolution=resolution,
                    frame_rate=frame_rate,
                    audio_channels=audio_channels,
                    sample_rate=sample_rate,
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    has_video=has_video,
                    has_audio=has_audio,
                    start_time=start_time,
                    start_timecode=start_timecode,
                    is_vfr=is_vfr,
                    avg_frame_rate=avg_frame_rate,
                    is_interlaced=is_interlaced,
                    field_order=field_order,
                )
            else:
                logger.warning("ffprobe error on '%s': %s", os.path.basename(norm_path), proc.stderr.strip()[:100])
        except Exception as e:
            logger.error("Exception invoking ffprobe on '%s': %s", os.path.basename(norm_path), e)

    # 3. Python Header Fallback (MP4 fast-start)
    try:
        with open(norm_path, "rb") as f:
            header_sample = f.read(128 * 1024)
        dur, w, h = inspect_mp4_metadata(header_sample)
        if dur or (w and h):
            res = format_resolution(w or 0, h or 0, is_video=True)
            dur_disp = format_duration_display(dur)
            return ProbedMediaItem(
                file_path=norm_path,
                file_size=file_size,
                mtime=mtime,
                duration_seconds=dur or 0.0,
                duration_display=dur_disp,
                width=w or 0,
                height=h or 0,
                resolution=res,
                has_video=bool(w and h)
            )
    except Exception:
        pass

    return None


class MediaProbeWorkerSignals(QObject):
    probed = Signal(str, object)  # (file_path, ProbedMediaItem)
    progress = Signal(int, int)   # (current, total)
    finished = Signal(dict)       # {file_path: ProbedMediaItem}
    error = Signal(str, str)      # (file_path, error_msg)


class MediaProbeWorker(QRunnable):
    """
    Background batch probe worker for local footage directory assets.
    Runs asynchronously on QThreadPool without blocking GUI execution.
    """
    def __init__(self, file_paths: List[str]):
        super().__init__()
        self.file_paths = file_paths
        self.signals = MediaProbeWorkerSignals()

    def run(self):
        results = {}
        total = len(self.file_paths)
        for idx, path in enumerate(self.file_paths, 1):
            try:
                item = probe_media_file(path, use_cache=True)
                if item:
                    results[path] = item
                    self.signals.probed.emit(path, item)
            except Exception as e:
                self.signals.error.emit(path, str(e))
            self.signals.progress.emit(idx, total)

        self.signals.finished.emit(results)
