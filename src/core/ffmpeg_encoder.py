import os
import sys
import time
import shutil
import subprocess
import threading
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from PySide6.QtCore import QObject, Signal, QThread

from .logger import get_logger

logger = get_logger("FFmpegEncoder")

# Windows flag to prevent flashing console windows when running background subprocesses
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Cache detected hardware encoders so we only probe once per session
_DETECTED_ENCODERS: Optional[Dict[str, bool]] = None
_ENCODER_LOCK = threading.Lock()


def get_ffmpeg_path() -> Optional[str]:
    """
    Resolves the standalone ffmpeg binary in priority order:
    1. Bundled frozen executable directory (PyInstaller / Nuitka bundle)
    2. Integrated application bin folder (knot-tool-native/bin/ffmpeg.exe)
    3. User AppData runtime cache (%LOCALAPPDATA%\\com.canefly.knot\\bin\\ffmpeg.exe)
    4. System PATH fallback (Chocolatey / WinGet / Manual PATH)
    """
    binary_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    # 1. Bundled Frozen Distribution
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        bundled = os.path.join(base_dir, "bin", binary_name)
        if os.path.isfile(bundled):
            return bundled
        direct = os.path.join(base_dir, binary_name)
        if os.path.isfile(direct):
            return direct

    # 2. Integrated Dev Workspace (knot-tool-native/bin/ffmpeg.exe)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    dev_bin = os.path.join(root_dir, "bin", binary_name)
    if os.path.isfile(dev_bin):
        return dev_bin

    # 3. User AppData Runtime Cache
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        appdata_bin = os.path.join(local_appdata, "com.canefly.knot", "bin", binary_name)
        if os.path.isfile(appdata_bin):
            return appdata_bin

    # 4. System PATH Fallback
    which_path = shutil.which("ffmpeg")
    if which_path and os.path.isfile(which_path):
        return which_path

    return None


def get_ffmpeg_status() -> Tuple[bool, str, str]:
    """Returns (is_available, version_label, binary_path)."""
    path = get_ffmpeg_path()
    if not path:
        return False, "Not Found", ""

    try:
        proc = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
            creationflags=CREATE_NO_WINDOW
        )
        first_line = proc.stdout.splitlines()[0] if proc.stdout else "ffmpeg"
        version_part = first_line.split("Copyright")[0].strip()
        return True, version_part, path
    except Exception as e:
        return True, f"Detected ({os.path.basename(path)})", path


def detect_available_encoders(force_refresh: bool = False) -> Dict[str, bool]:
    """
    Probes ffmpeg to identify compiled and operational hardware/software encoders.
    Returns a dict e.g. {'h264_nvenc': True, 'hevc_nvenc': True, 'h264_qsv': False, 'libx264': True}.
    Persists results to SQLite settings cache so subsequent app launches load in <0.5ms without running subprocesses.
    """
    global _DETECTED_ENCODERS
    with _ENCODER_LOCK:
        if _DETECTED_ENCODERS is not None and not force_refresh:
            return dict(_DETECTED_ENCODERS)

        if not force_refresh:
            try:
                from .database import db
                cached_raw = db.get_setting("hw_encoders_cache")
                if cached_raw:
                    cached_dict = json.loads(cached_raw)
                    if isinstance(cached_dict, dict) and cached_dict:
                        _DETECTED_ENCODERS = cached_dict
                        return dict(_DETECTED_ENCODERS)
            except Exception:
                pass

        path = get_ffmpeg_path()
        results = {
            "h264_nvenc": False,
            "hevc_nvenc": False,
            "h264_qsv": False,
            "hevc_qsv": False,
            "h264_amf": False,
            "hevc_amf": False,
            "libx264": False,
            "libx265": False,
        }

        if not path:
            _DETECTED_ENCODERS = results
            return results

        try:
            # 1. Check compiled encoders list
            proc = subprocess.run(
                [path, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=4.0,
                creationflags=CREATE_NO_WINDOW
            )
            out = proc.stdout.lower()
            for enc in results.keys():
                if f" {enc} " in out:
                    results[enc] = True

            # 2. Verify operational viability of hardware encoders with 0.1s dummy test
            # Note: NVIDIA NVENC requires >= 144x144 frame dimension; using 256x256 testsrc
            hw_tests = ["h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"]
            for hw in hw_tests:
                if results.get(hw):
                    try:
                        test_p = subprocess.run(
                            [
                                path, "-hide_banner", "-v", "error",
                                "-f", "lavfi", "-i", "testsrc=duration=0.1:size=256x256:rate=24",
                                "-c:v", hw, "-f", "null", "-"
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=2.5,
                            creationflags=CREATE_NO_WINDOW
                        )
                        if test_p.returncode != 0:
                            results[hw] = False
                    except Exception:
                        results[hw] = False

        except Exception as e:
            logger.warning(f"Failed to probe encoders via FFmpeg: {e}")

        _DETECTED_ENCODERS = results
        try:
            from .database import db
            db.set_setting("hw_encoders_cache", json.dumps(results))
        except Exception:
            pass
        logger.info(f"Hardware encoder probe complete: {results}")
        return results


def get_hardware_status_summary() -> Tuple[str, str]:
    """
    Returns (status_headline, best_encoder_name) for UI display.
    e.g. ("NVIDIA NVENC Ready", "h264_nvenc") or ("AMD AMF Ready", "h264_amf")
    """
    encoders = detect_available_encoders()
    if encoders.get("h264_nvenc") or encoders.get("hevc_nvenc"):
        return "NVIDIA NVENC Ready", "h264_nvenc"
    if encoders.get("h264_amf") or encoders.get("hevc_amf"):
        return "AMD AMF Ready", "h264_amf"
    if encoders.get("h264_qsv") or encoders.get("hevc_qsv"):
        return "Intel QuickSync Ready", "h264_qsv"
    return "CPU Software (No GPU Detected)", "libx264"


def get_encoder_catalog() -> List[Tuple[str, str, bool, str]]:
    """
    Returns full list of supported encoder definitions:
    (encoder_id, display_label, is_supported_on_current_machine, requirement_note)
    """
    encoders = detect_available_encoders()
    return [
        ("h264_nvenc", "NVIDIA NVENC H.264", bool(encoders.get("h264_nvenc")), "Requires NVIDIA GPU"),
        ("hevc_nvenc", "NVIDIA NVENC HEVC / H.265", bool(encoders.get("hevc_nvenc")), "Requires NVIDIA GPU"),
        ("h264_amf", "AMD AMF H.264", bool(encoders.get("h264_amf")), "Requires AMD Radeon GPU"),
        ("hevc_amf", "AMD AMF HEVC / H.265", bool(encoders.get("hevc_amf")), "Requires AMD Radeon GPU"),
        ("h264_qsv", "Intel QuickSync H.264", bool(encoders.get("h264_qsv")), "Requires Intel Arc / iGPU"),
        ("hevc_qsv", "Intel QuickSync HEVC", bool(encoders.get("hevc_qsv")), "Requires Intel Arc / iGPU"),
        ("libx264", "CPU Software H.264 (libx264)", True, "Universal CPU Fallback"),
        ("libx265", "CPU Software HEVC (libx265)", True, "Universal CPU Fallback"),
    ]


def get_recommended_encoder(prefer_hevc: bool = False) -> str:
    """Returns the best available encoder for the local machine."""
    encoders = detect_available_encoders()
    if prefer_hevc:
        if encoders.get("hevc_nvenc"):
            return "hevc_nvenc"
        if encoders.get("hevc_qsv"):
            return "hevc_qsv"
        if encoders.get("hevc_amf"):
            return "hevc_amf"
        if encoders.get("libx265"):
            return "libx265"

    if encoders.get("h264_nvenc"):
        return "h264_nvenc"
    if encoders.get("h264_qsv"):
        return "h264_qsv"
    if encoders.get("h264_amf"):
        return "h264_amf"
    return "libx264"


def send_to_recycle_bin(file_path: str) -> bool:
    """
    Moves a file safely to the Windows Recycle Bin using native Win32 shell32.SHFileOperationW.
    Zero external dependencies, supports Windows Ctrl+Z undo.
    """
    if not os.path.exists(file_path):
        return True

    if os.name != "nt":
        try:
            os.remove(file_path)
            return True
        except Exception:
            return False

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 0x0003
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    FOF_NOERRORUI = 0x0400

    try:
        # Win32 double-null terminated string requirement
        double_null_path = os.path.abspath(file_path) + "\0\0"
        file_op = SHFILEOPSTRUCTW()
        file_op.wFunc = FO_DELETE
        file_op.pFrom = double_null_path
        file_op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(file_op))
        success = (result == 0 and not file_op.fAnyOperationsAborted and not os.path.exists(file_path))
        if success:
            logger.info(f"File moved to Windows Recycle Bin: {file_path}")
        else:
            logger.warning(f"SHFileOperationW returned code {result} for {file_path}")
        return success
    except Exception as e:
        logger.error(f"Error moving file to Recycle Bin: {e}")
        return False


def is_file_locked(file_path: str) -> bool:
    """Tests if another application (like Premiere Pro or Media Player) has an exclusive lock on the file."""
    if not os.path.exists(file_path):
        return False
    try:
        # Attempt to open file for append without truncating
        with open(file_path, "r+b"):
            pass
        return False
    except (PermissionError, IOError):
        return True


@dataclass
class TranscodeConfig:
    target_fps: float = 0.0          # 0.0 = auto-snap to nearest standard CFR
    encoder: str = "auto"            # "auto", "h264_nvenc", "hevc_nvenc", "h264_qsv", "libx264"
    crf: int = 18                    # 14..28 (18 = Near-Lossless, 23 = Bodycam Space-Saver)
    preset: str = "p6"               # nvenc: p4..p7, x264: medium/slow
    max_height: Optional[int] = None # None = keep original, 1080 = downscale 4K to 1080p
    deinterlace: str = "auto"        # "auto" (bwdif if interlaced), "bwdif" (force), "yadif", "off"
    audio_mode: str = "copy"         # Strictly copy per Cane's instruction
    replace_original: bool = True    # Send original to Recycle Bin and swap
    custom_args: List[str] = field(default_factory=list)


@dataclass
class TranscodeProgress:
    frame: int = 0
    fps: float = 0.0
    size_kb: int = 0
    time_seconds: float = 0.0
    total_seconds: float = 0.0
    speed: float = 0.0
    percent: float = 0.0
    eta_seconds: float = 0.0


class TranscodeWorker(QThread):
    """Asynchronous background worker running FFmpeg with machine-readable progress parsing."""
    started = Signal(str)                                # source_path
    progress = Signal(float, str, object)                # (percent 0..100, status_text, TranscodeProgress)
    finished = Signal(bool, str, str)                    # (success, final_path_or_error, summary_msg)
    cancelled = Signal()

    def __init__(self, source_path: str, config: TranscodeConfig, output_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.source_path = os.path.abspath(source_path)
        self.config = config
        self.output_path = os.path.abspath(output_path) if output_path else None
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._temp_path: Optional[str] = None

    def run(self):
        ffmpeg_bin = get_ffmpeg_path()
        if not ffmpeg_bin:
            self.finished.emit(False, "FFmpeg executable not found", "Could not locate ffmpeg.exe.")
            return

        if not os.path.isfile(self.source_path):
            self.finished.emit(False, f"Source file does not exist: {self.source_path}", "Source file missing.")
            return

        # 1. Probe source duration and target FPS
        from .media_probe import probe_media_file
        probed = probe_media_file(self.source_path)
        total_duration = probed.duration_seconds if probed else 0.0
        source_fps = probed.frame_rate if probed else 24.0

        # Determine target CFR framerate
        target_fps = self.config.target_fps
        if target_fps <= 0.0:
            from .timecode_engine import analyze_framerate
            exact_fps, nom_fps, is_vfr, is_std = analyze_framerate(source_fps, probed.avg_frame_rate if probed else 0.0)
            target_fps = exact_fps

        # Resolve encoder
        encoder = self.config.encoder
        if encoder == "auto":
            encoder = get_recommended_encoder()

        # Generate temporary output sibling file
        src_dir = os.path.dirname(self.source_path)
        src_name, _ = os.path.splitext(os.path.basename(self.source_path))
        self._temp_path = os.path.join(src_dir, f"{src_name}.click_transcode.mp4")

        # Build FFmpeg CLI command
        cmd = [
            ffmpeg_bin, "-y", "-v", "error",
            "-progress", "pipe:1",
            "-i", self.source_path,
            "-fps_mode", "cfr",
            "-r", f"{target_fps:.3f}".rstrip("0").rstrip("."),
        ]

        # Video Codec & Quality Options
        if "nvenc" in encoder:
            cmd.extend([
                "-c:v", encoder,
                "-preset", self.config.preset or "p6",
                "-cq", str(self.config.crf),
                "-b:v", "0",
            ])
        elif "amf" in encoder:
            cmd.extend([
                "-c:v", encoder,
                "-quality", "quality",
                "-rc", "cqp",
                "-qp_i", str(self.config.crf),
                "-qp_p", str(self.config.crf),
            ])
        elif "qsv" in encoder:
            cmd.extend([
                "-c:v", encoder,
                "-global_quality", str(self.config.crf),
            ])
        elif "libx265" in encoder:
            cmd.extend([
                "-c:v", "libx265",
                "-preset", "medium",
                "-crf", str(self.config.crf),
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", str(self.config.crf),
            ])

        # Video Filter Chain: Deinterlace / Decomb + Optional Resolution Downscaling
        vf_filters: List[str] = []
        deint_mode = (self.config.deinterlace or "auto").lower()
        is_src_interlaced = bool(probed and getattr(probed, "is_interlaced", False))

        if deint_mode == "auto":
            # Default: motion-adaptive bwdif decomb only on frames marked interlaced (deint=1)
            # If stream metadata explicitly flagged interlaced, apply bwdif=mode=send_frame:parity=auto:deint=all
            if is_src_interlaced:
                vf_filters.append("bwdif=mode=0:parity=-1:deint=0")
            else:
                vf_filters.append("bwdif=mode=0:parity=-1:deint=1")
        elif deint_mode in ("bwdif", "force", "always"):
            vf_filters.append("bwdif=mode=0:parity=-1:deint=0")
        elif deint_mode == "yadif":
            vf_filters.append("yadif=mode=0:parity=-1:deint=0")

        if self.config.max_height and probed and probed.height > self.config.max_height:
            vf_filters.append(f"scale=-2:{self.config.max_height}")

        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        # Audio Track: STRICTLY COPY per Cane's instruction
        cmd.extend(["-c:a", "copy"])

        # Web / Seek Optimization
        cmd.extend(["-movflags", "+faststart"])

        # Extra custom arguments
        if self.config.custom_args:
            cmd.extend(self.config.custom_args)

        cmd.append(self._temp_path)

        logger.info(f"Starting transcode: {' '.join(cmd)}")
        self.started.emit(self.source_path)

        t_start = time.time()
        prog = TranscodeProgress(total_seconds=total_duration)

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                creationflags=CREATE_NO_WINDOW
            )

            # Read machine-readable stdout progress in real-time
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                line = line.strip()
                if not line or "=" not in line:
                    continue

                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()

                if key == "frame":
                    try:
                        prog.frame = int(val)
                    except ValueError:
                        pass
                elif key == "fps":
                    try:
                        prog.fps = float(val)
                    except ValueError:
                        pass
                elif key == "total_size":
                    try:
                        prog.size_kb = int(val) // 1024
                    except ValueError:
                        pass
                elif key == "out_time_us":
                    try:
                        prog.time_seconds = max(0.0, float(val) / 1_000_000.0)
                        if total_duration > 0:
                            prog.percent = min(99.0, (prog.time_seconds / total_duration) * 100.0)
                            if prog.speed > 0:
                                rem_sec = max(0.0, (total_duration - prog.time_seconds) / prog.speed)
                                prog.eta_seconds = rem_sec
                    except ValueError:
                        pass
                elif key == "speed":
                    try:
                        speed_val = val.rstrip("x").strip()
                        prog.speed = float(speed_val) if speed_val != "N/A" else 1.0
                    except ValueError:
                        pass
                elif key == "progress" and val == "end":
                    prog.percent = 100.0
                    prog.eta_seconds = 0.0

                # Format status string
                status_txt = f"{prog.percent:.1f}% | {prog.speed:.1f}x ({prog.fps:.0f} FPS)"
                if prog.eta_seconds > 0:
                    mins = int(prog.eta_seconds // 60)
                    secs = int(prog.eta_seconds % 60)
                    status_txt += f" | ETA {mins:02d}:{secs:02d}"

                self.progress.emit(prog.percent, status_txt, prog)

            self._proc.wait()

            if self._cancelled:
                self._cleanup_temp()
                self.cancelled.emit()
                return

            if self._proc.returncode != 0:
                err = self._proc.stderr.read() if self._proc.stderr else "Unknown error"
                self._cleanup_temp()
                self.finished.emit(False, f"FFmpeg failed with exit code {self._proc.returncode}: {err}", "Transcode failed.")
                return

            # Verification: Check temp file health
            if not os.path.isfile(self._temp_path) or os.path.getsize(self._temp_path) < 1000:
                self._cleanup_temp()
                self.finished.emit(False, "Generated file is empty or missing", "Invalid output file.")
                return

            # If replace_original is False, rename to final output_path or sibling _CFR.mp4
            if not self.config.replace_original:
                dest = self.output_path or os.path.join(src_dir, f"{src_name}_CFR.mp4")
                if os.path.exists(dest):
                    send_to_recycle_bin(dest)
                os.replace(self._temp_path, dest)
                dur_ms = time.time() - t_start
                self.finished.emit(True, dest, f"Transcoded successfully in {dur_ms:.1f}s ({prog.speed:.1f}x speed)")
                return

            # Perform atomic replace with Windows Recycle Bin
            success, msg = perform_atomic_replace(self.source_path, self._temp_path)
            if success:
                dur_ms = time.time() - t_start
                self.finished.emit(True, self.source_path, f"Replaced with CFR file in {dur_ms:.1f}s ({prog.speed:.1f}x speed)")
            else:
                self.finished.emit(False, msg, "Failed to replace original file.")

        except Exception as e:
            logger.error(f"Transcode exception: {e}")
            self._cleanup_temp()
            self.finished.emit(False, str(e), "Exception during transcode.")

    def cancel(self):
        """Immediately terminates the FFmpeg process and cleans up."""
        self._cancelled = True
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.kill()
            except Exception:
                pass
        self._cleanup_temp()

    def _cleanup_temp(self):
        if self._temp_path and os.path.isfile(self._temp_path):
            try:
                os.remove(self._temp_path)
            except Exception:
                pass


def perform_atomic_replace(source_path: str, temp_path: str) -> Tuple[bool, str]:
    """
    Safely replaces source_path with temp_path:
    1. Tests whether source_path is locked by Premiere Pro or another process.
    2. Moves source_path to Windows Recycle Bin via native Win32 ctypes.
    3. Renames temp_path to source_path.
    4. Invalidates media probe caches.
    """
    if not os.path.isfile(temp_path):
        return False, "Temporary transcoded file does not exist."

    if is_file_locked(source_path):
        return False, f"File is currently open or locked by Premiere Pro / Media Player: {os.path.basename(source_path)}"

    # 1. Send original to Windows Recycle Bin
    if not send_to_recycle_bin(source_path):
        return False, f"Could not move original file to Windows Recycle Bin: {source_path}"

    # 2. Rename temporary file to original path
    try:
        os.replace(temp_path, source_path)
    except Exception as e:
        logger.error(f"Failed to rename temp file to source: {e}")
        return False, f"Failed to rename transcode output to original: {e}"

    # 3. Invalidate MediaProbe database cache
    try:
        from .media_probe import probe_media_file
        from .database import db
        # Invalidate cached SQLite probed item so new duration / CFR framerate is refreshed
        db.delete_media_probe(source_path)
        # Re-probe new file
        probe_media_file(source_path, use_cache=False)
    except Exception as e:
        logger.warning(f"Could not refresh media probe cache after replacement: {e}")

    logger.info(f"Successfully replaced original file with CFR transcode: {source_path}")
    return True, "Replaced successfully"
