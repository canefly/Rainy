import os
import re
import sys
import time
import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
from PySide6.QtCore import QObject, Signal, QRunnable

# Suppress Hugging Face symlink warning on Windows Developer Mode unactivated environments
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from .models import DocumentState, ScriptItem, VOSegment
from .parser import normalize_link
from .timecode_engine import analyze_framerate
from .xml_exporter import parse_timecode_to_frames
from . import app_paths
from .database import db
from .logger import get_logger
from .vo_islands import DetectionSettings, analyze_and_snap_part, load_or_compute_envelope, slim_segments

logger = get_logger("VOEngine")

DOCUMENTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Rainy")
TRANSCRIPTS_DIR = os.path.join(DOCUMENTS_DIR, "Transcripts")
WHISPER_MODELS_DIR = os.path.join(app_paths.models_dir(), "whisper")


def ensure_cuda_dlls_registered() -> List[str]:
    """
    On Windows Python 3.8+, DLLs loaded dynamically by CTranslate2 (e.g. cublas64_12.dll,
    cublasLt64_12.dll, cudnn64_9.dll) are not automatically searched from system PATH.
    This scans Python site-packages, CUDA_PATH, DaVinci Resolve, and known NVIDIA toolkits,
    registering directories with os.add_dll_directory() so GPU acceleration works seamlessly.
    """
    registered: List[str] = []
    if sys.platform != "win32":
        return registered

    import glob
    import site

    candidates: List[str] = []

    # 1. Python environment site-packages (nvidia-cublas-cu12, nvidia-cudnn-cu12, torch)
    site_dirs: List[str] = []
    try:
        site_dirs.extend(site.getsitepackages())
    except Exception:
        pass
    site_dirs.append(sys.prefix)

    for sp in site_dirs:
        for sub in ("cublas", "cudnn"):
            cand = os.path.join(sp, "Lib", "site-packages", "nvidia", sub, "bin")
            if os.path.isdir(cand):
                candidates.append(cand)
            cand_alt = os.path.join(sp, "nvidia", sub, "bin")
            if os.path.isdir(cand_alt):
                candidates.append(cand_alt)
        cand_torch = os.path.join(sp, "Lib", "site-packages", "torch", "lib")
        if os.path.isdir(cand_torch):
            candidates.append(cand_torch)

    # 2. CUDA_PATH environment variables
    for k, v in os.environ.items():
        if k.startswith("CUDA_PATH") and os.path.isdir(v):
            bin_path = os.path.join(v, "bin")
            if os.path.isdir(bin_path):
                candidates.append(bin_path)

    # 3. Standard Windows NVIDIA CUDA installations and professional creative tools
    for p in glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin"):
        candidates.append(p)
    candidates.extend([
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
        r"C:\Program Files\Blace Plugins",
    ])

    for d in candidates:
        if os.path.isdir(d):
            has_cuda_dll = any(
                os.path.isfile(os.path.join(d, dll))
                for dll in ("cublas64_12.dll", "cublas64_11.dll", "cudart64_12.dll", "cudnn64_9.dll", "cudnn64_8.dll")
            )
            if has_cuda_dll:
                try:
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(d)
                    if d not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                    registered.append(d)
                except Exception:
                    pass

    return registered


# Auto-register CUDA runtime DLLs on module load
ensure_cuda_dlls_registered()


def check_cuda_available() -> Tuple[bool, str]:
    """
    Verifies that an NVIDIA GPU is physically present and that cuBLAS runtime DLLs
    can actually be loaded into the Python process.
    """
    ensure_cuda_dlls_registered()
    try:
        import ctranslate2
        cuda_cnt = ctranslate2.get_cuda_device_count()
        if cuda_cnt <= 0:
            return (False, "No NVIDIA GPU detected")
    except Exception as e:
        return (False, f"CTranslate2 CUDA query failed: {e}")

    try:
        import ctypes
        try:
            ctypes.CDLL("cublas64_12.dll")
        except Exception:
            ctypes.CDLL("cublas64_11.dll")
        return (True, "CUDA Hardware Acceleration Active")
    except Exception as e:
        return (False, f"cuBLAS library not found: {e}")


@dataclass
class WhisperModelSpec:
    id: str
    name: str
    repo_id: str
    size_display: str
    size_mb: int
    speed_badge: str
    accuracy_badge: str
    description: str


WHISPER_CATALOG: List[WhisperModelSpec] = [
    WhisperModelSpec(
        id="tiny.en",
        name="Tiny (English)",
        repo_id="Systran/faster-whisper-tiny.en",
        size_display="~75 MB",
        size_mb=75,
        speed_badge="32x Realtime",
        accuracy_badge="Draft",
        description="Ultra-lightweight model for instant rough checks on low-end CPUs."
    ),
    WhisperModelSpec(
        id="base.en",
        name="Base (English)",
        repo_id="Systran/faster-whisper-base.en",
        size_display="~145 MB",
        size_mb=145,
        speed_badge="16x Realtime",
        accuracy_badge="Standard",
        description="Fast everyday English voiceover transcription with minimal RAM usage."
    ),
    WhisperModelSpec(
        id="small.en",
        name="Small (English)",
        repo_id="Systran/faster-whisper-small.en",
        size_display="~485 MB",
        size_mb=485,
        speed_badge="8x Realtime",
        accuracy_badge="High",
        description="Strong accuracy for fast narrators, names, and casual ad-libs."
    ),
    WhisperModelSpec(
        id="distil-large-v3",
        name="Distil-Large v3 (Recommended)",
        repo_id="Systran/faster-distil-whisper-large-v3",
        size_display="~1.5 GB",
        size_mb=1510,
        speed_badge="6x Faster than Large",
        accuracy_badge="Studio",
        description="Best balance of speed and studio accuracy for long-form true crime & documentary VOs."
    ),
    WhisperModelSpec(
        id="large-v3-turbo",
        name="Large v3 Turbo",
        repo_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        size_display="~1.6 GB",
        size_mb=1620,
        speed_badge="4x Faster than Large",
        accuracy_badge="Maximum",
        description="Highest precision for heavy slang, off-script ad-libs, and multi-accent voiceovers."
    ),
]


class WhisperModelManager:
    """Manages local faster-whisper models, engine health checks, and background downloads."""

    @staticmethod
    def ensure_models_dir() -> str:
        os.makedirs(WHISPER_MODELS_DIR, exist_ok=True)
        return WHISPER_MODELS_DIR

    @staticmethod
    def get_catalog() -> List[WhisperModelSpec]:
        return WHISPER_CATALOG

    @staticmethod
    def get_model_spec(model_id: str) -> WhisperModelSpec:
        for spec in WHISPER_CATALOG:
            if spec.id == model_id:
                return spec
        return WHISPER_CATALOG[1]  # fallback to base.en

    @staticmethod
    def get_active_model_id() -> str:
        return db.get_setting("whisper_active_model", "base.en") or "base.en"

    @staticmethod
    def set_active_model_id(model_id: str):
        db.set_setting("whisper_active_model", model_id)

    @staticmethod
    def get_compute_backend() -> str:
        return db.get_setting("whisper_compute_backend", "auto") or "auto"

    @staticmethod
    def set_compute_backend(backend: str):
        db.set_setting("whisper_compute_backend", backend)

    @staticmethod
    def get_model_local_dir(model_id: str) -> str:
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", model_id)
        return os.path.join(WHISPER_MODELS_DIR, safe_id)

    @classmethod
    def is_model_downloaded(cls, model_id: str) -> bool:
        local_dir = cls.get_model_local_dir(model_id)
        if not os.path.isdir(local_dir):
            return False
        model_bin = os.path.join(local_dir, "model.bin")
        return os.path.isfile(model_bin) and os.path.getsize(model_bin) > 1024 * 100

    @classmethod
    def get_downloaded_size_display(cls, model_id: str) -> str:
        local_dir = cls.get_model_local_dir(model_id)
        if not os.path.isdir(local_dir):
            return "Not Installed"
        total = 0
        for root, _, files in os.walk(local_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        if total <= 0:
            return "Not Installed"
        mb = total / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.2f} GB on disk"
        return f"{mb:.0f} MB on disk"

    @classmethod
    def delete_model(cls, model_id: str) -> bool:
        local_dir = cls.get_model_local_dir(model_id)
        if os.path.isdir(local_dir):
            try:
                shutil.rmtree(local_dir)
                return True
            except Exception as e:
                logger.error("Failed to delete Whisper model '%s': %s", model_id, e)
                return False
        return True

    @staticmethod
    def get_engine_status() -> Dict[str, Any]:
        """Checks faster-whisper and ctranslate2 installation and hardware acceleration status."""
        status = {
            "installed": False,
            "faster_whisper_version": "Not Installed",
            "ctranslate2_version": "Not Installed",
            "cuda_available": False,
            "cuda_device_count": 0,
            "cuda_status_message": "CPU Only",
            "recommended_device": "cpu",
            "recommended_compute_type": "int8",
        }
        try:
            import faster_whisper
            status["installed"] = True
            status["faster_whisper_version"] = getattr(faster_whisper, "__version__", "1.x")
        except ImportError:
            pass

        try:
            import ctranslate2
            status["ctranslate2_version"] = getattr(ctranslate2, "__version__", "4.x")
            cuda_cnt = ctranslate2.get_cuda_device_count()
            status["cuda_device_count"] = cuda_cnt

            cuda_ok, cuda_msg = check_cuda_available()
            if cuda_cnt > 0 and cuda_ok:
                status["cuda_available"] = True
                status["cuda_status_message"] = cuda_msg
                status["recommended_device"] = "cuda"
                status["recommended_compute_type"] = "float16"
            else:
                status["cuda_available"] = False
                status["cuda_status_message"] = cuda_msg
                status["recommended_device"] = "cpu"
                status["recommended_compute_type"] = "int8"
        except Exception as e:
            status["cuda_status_message"] = str(e)

        return status

    @classmethod
    def resolve_device_and_compute(cls) -> Tuple[str, str]:
        pref = cls.get_compute_backend()
        status = cls.get_engine_status()
        if pref == "cuda_fp16" and status["cuda_available"]:
            return ("cuda", "float16")
        if pref == "cpu_int8":
            return ("cpu", "int8")
        if status["cuda_available"]:
            return ("cuda", "float16")
        return ("cpu", "int8")


class ModelDownloadSignals(QObject):
    progress = Signal(str, int, str)  # (model_id, percent, status_text)
    finished = Signal(str, bool, str) # (model_id, success, message)


class ModelDownloadWorker(QRunnable):
    """Downloads a faster-whisper CTranslate2 model from HuggingFace Hub into <app home>/models/whisper/."""
    def __init__(self, model_id: str):
        super().__init__()
        self.model_id = model_id
        self.signals = ModelDownloadSignals()

    def run(self):
        spec = WhisperModelManager.get_model_spec(self.model_id)
        target_dir = WhisperModelManager.get_model_local_dir(self.model_id)
        os.makedirs(target_dir, exist_ok=True)

        self.signals.progress.emit(self.model_id, 5, f"Connecting to HuggingFace ({spec.repo_id})...")
        try:
            from huggingface_hub import HfApi, hf_hub_download
            api = HfApi()
            repo_files = api.list_repo_files(repo_id=spec.repo_id)
            essential_files = [
                f for f in repo_files
                if f in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json", "preprocessor_config.json")
            ]
            if not essential_files:
                essential_files = repo_files

            total_files = len(essential_files)
            for idx, filename in enumerate(essential_files):
                pct = int(10 + ((idx / max(1, total_files)) * 85))
                self.signals.progress.emit(
                    self.model_id,
                    pct,
                    f"Downloading {filename} ({idx + 1}/{total_files})..."
                )
                hf_hub_download(
                    repo_id=spec.repo_id,
                    filename=filename,
                    local_dir=target_dir,
                    local_dir_use_symlinks=False,
                )

            self.signals.progress.emit(self.model_id, 100, "Model ready")
            self.signals.finished.emit(self.model_id, True, f"{spec.name} downloaded ({spec.size_display}).")
        except Exception as e:
            logger.error("Failed to download model '%s': %s", self.model_id, e)
            self.signals.finished.emit(self.model_id, False, str(e))


ENGINE_BUILT_IN_MESSAGE = "The speech engine is built into Rainy and updates when Rainy updates."


class EngineUpdateSignals(QObject):
    finished = Signal(bool, str)  # (success, output_message)


class EngineUpdateWorker(QRunnable):
    """Checks for or installs/upgrades faster-whisper and huggingface-hub in the active Python environment."""
    def __init__(self):
        super().__init__()
        self.signals = EngineUpdateSignals()

    def run(self):
        from . import app_paths
        if app_paths.is_packaged():
            # In an installed Rainy, sys.executable is Rainy.exe itself, not a Python with pip: running it with "-m pip"
            # would just open a second Rainy. The speech engine ships inside the app and updates with it.
            self.signals.finished.emit(True, ENGINE_BUILT_IN_MESSAGE)
            return
        try:
            cmd = [
                sys.executable, "-m", "pip", "install", "--upgrade",
                "faster-whisper", "huggingface-hub"
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            if proc.returncode == 0:
                self.signals.finished.emit(True, "Faster-Whisper engine packages are up to date.")
            else:
                err = (proc.stderr or proc.stdout or "Unknown pip error").strip()
                self.signals.finished.emit(False, f"Engine update failed: {err[:240]}")
        except Exception as e:
            self.signals.finished.emit(False, f"Engine update failed: {e}")


def ensure_transcripts_dir() -> str:
    """Ensures Documents/Rainy/Transcripts/ directory exists and returns it."""
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    return TRANSCRIPTS_DIR


def save_vo_transcript_json(manifest: Dict[str, Any], output_path: Optional[str] = None) -> str:
    """
    Saves the multi-part VO transcript manifest into an independent, human-readable .json file.
    Does not touch SQLite or .rainyproj.
    """
    ensure_transcripts_dir()
    if not output_path:
        proj_name = manifest.get("project_name") or manifest.get("script_name") or "voiceover"
        base_name = os.path.splitext(os.path.basename(proj_name))[0]
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', base_name).strip() or "voiceover"
        output_path = os.path.join(TRANSCRIPTS_DIR, f"{safe_name}_vo_transcript.json")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)

    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, output_path)
    logger.info("Saved VO transcript JSON to '%s' (%d segments)", output_path, len(manifest.get("segments", [])))
    return output_path


def load_vo_transcript_json(file_path: str) -> Optional[Dict[str, Any]]:
    """Loads a standalone VO transcript manifest from disk."""
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load VO transcript JSON from '%s': %s", file_path, e)
        return None


def get_vo_manifest(doc_state: DocumentState) -> Dict[str, Any]:
    """
    Extracts or computes the complete Voiceover manifest for doc_state, including
    multi-part file paths, per-part duration, global cumulative offsets, and segment counts.
    Prioritizes the independent vo_transcript_path JSON if present, falling back to
    analyzing doc_state.vo_files and doc_state.vo_segments.
    """
    vo_files = list(getattr(doc_state, "vo_files", []) or [])
    single_vo = getattr(doc_state, "vo_file_path", "")
    if not vo_files and single_vo:
        vo_files = [single_vo]

    transcript_path = getattr(doc_state, "vo_transcript_path", "")
    loaded_data = None
    if transcript_path and os.path.isfile(transcript_path):
        loaded_data = load_vo_transcript_json(transcript_path)

    if loaded_data and isinstance(loaded_data, dict) and loaded_data.get("files"):
        files_manifest = list(loaded_data["files"])
        if not vo_files:
            vo_files = [f.get("path") for f in files_manifest if f.get("path")]
        return {
            "vo_file_path": single_vo or (vo_files[0] if vo_files else ""),
            "vo_files": vo_files,
            "vo_files_count": len(vo_files),
            "vo_transcript_path": transcript_path,
            "files": files_manifest,
            "total_duration_sec": float(loaded_data.get("total_duration_sec", 0.0)),
            "total_segments": int(loaded_data.get("total_segments", len(getattr(doc_state, "vo_segments", [])))),
        }

    # Dynamic fallback if no JSON manifest file on disk
    segs = getattr(doc_state, "vo_segments", []) or []
    files_manifest: List[Dict[str, Any]] = []
    cumulative_offset = 0.0

    from .media_probe import probe_media_file

    for idx, vf in enumerate(vo_files, start=1):
        fname = os.path.basename(vf)
        file_segs = [s for s in segs if s.get("part_id") == idx or s.get("source_file") == vf]
        dur = 0.0
        try:
            p_info = probe_media_file(vf)
            if p_info and p_info.duration_seconds > 0:
                dur = round(p_info.duration_seconds, 3)
        except Exception:
            dur = 0.0

        if dur <= 0.0 and file_segs:
            dur = max(float(s.get("source_out", s.get("end", 0.0))) for s in file_segs)

        files_manifest.append({
            "part_id": idx,
            "filename": fname,
            "path": vf,
            "duration_sec": dur,
            "global_offset_sec": round(cumulative_offset, 3),
            "segment_count": len(file_segs),
        })
        cumulative_offset += dur

    total_dur = round(cumulative_offset, 3)
    if total_dur <= 0.0 and segs:
        total_dur = max(float(s.get("global_out", s.get("end", 0.0))) for s in segs)

    return {
        "vo_file_path": single_vo or (vo_files[0] if vo_files else ""),
        "vo_files": vo_files,
        "vo_files_count": len(vo_files),
        "vo_transcript_path": transcript_path,
        "files": files_manifest,
        "total_duration_sec": total_dur,
        "total_segments": len(segs),
    }


def slice_narration_pack_across_parts(
    source_in_global: float,
    source_out_global: float,
    files_manifest: List[Dict[str, Any]],
    has_adlib: bool = False,
    adlib_note: str = "",
    lead_in_text: str = "",
) -> List[Dict[str, Any]]:
    """
    Slices a continuous global narration time range [source_in_global, source_out_global]
    across multi-part audio file boundaries based on the files manifest.
    Returns one or more NARRATION_PACK segments, each bound to its respective source file
    and local in/out timecodes.
    """
    s_in = max(0.0, float(source_in_global))
    s_out = max(s_in, float(source_out_global))
    if s_out - s_in < 0.01:
        return []

    if not files_manifest:
        return [{
            "type": "NARRATION_PACK",
            "part_id": 1,
            "source_file": "",
            "source_filename": "",
            "source_in_sec": round(s_in, 3),
            "source_out_sec": round(s_out, 3),
            "global_in_sec": round(s_in, 3),
            "global_out_sec": round(s_out, 3),
            "has_adlib": has_adlib,
            "adlib_note": adlib_note,
            "lead_in_text": lead_in_text,
        }]

    slices: List[Dict[str, Any]] = []

    for idx, part in enumerate(files_manifest):
        part_offset = float(part.get("global_offset_sec", 0.0))
        part_dur = float(part.get("duration_sec", 0.0))
        part_end = part_offset + part_dur

        # If last part, extend boundary to cover narration if needed
        if idx == len(files_manifest) - 1:
            part_end = max(part_end, s_out)

        overlap_start = max(s_in, part_offset)
        overlap_end = min(s_out, part_end)

        if overlap_end - overlap_start > 0.01:
            local_in = overlap_start - part_offset
            local_out = overlap_end - part_offset
            slices.append({
                "type": "NARRATION_PACK",
                "part_id": int(part.get("part_id", idx + 1)),
                "source_file": str(part.get("path", "")),
                "source_filename": str(part.get("filename", "")),
                "source_in_sec": round(local_in, 3),
                "source_out_sec": round(local_out, 3),
                "global_in_sec": round(overlap_start, 3),
                "global_out_sec": round(overlap_end, 3),
                "has_adlib": has_adlib,
                "adlib_note": adlib_note,
                "lead_in_text": lead_in_text,
            })

    if not slices and files_manifest:
        first_part = files_manifest[0]
        slices.append({
            "type": "NARRATION_PACK",
            "part_id": int(first_part.get("part_id", 1)),
            "source_file": str(first_part.get("path", "")),
            "source_filename": str(first_part.get("filename", "")),
            "source_in_sec": round(s_in, 3),
            "source_out_sec": round(s_out, 3),
            "global_in_sec": round(s_in, 3),
            "global_out_sec": round(s_out, 3),
            "has_adlib": has_adlib,
            "adlib_note": adlib_note,
            "lead_in_text": lead_in_text,
        })

    return slices


class VOBatchTranscribeSignals(QObject):
    file_started = Signal(int, str, int, int)       # (part_id, file_path, current_1based, total_files)
    file_progress = Signal(int, int, str)           # (part_id, percent 0..100, status_text)
    segment_streamed = Signal(int, dict)           # (part_id, segment_dict) -> Real-time log streaming!
    file_finished = Signal(int, list, float)        # (part_id, file_segments, file_duration_sec)
    batch_finished = Signal(str, dict)             # (saved_json_path, manifest_dict)
    error = Signal(str)


class VOBatchTranscribeWorker(QRunnable):
    """
    Background QRunnable that transcribes an ordered queue of Voiceover audio files
    (.wav, .mp3, .m4a, etc.) into clean sentence/phrase-level JSON using faster-whisper.
    Calculates dual timestamps (source_in/out and global_in/out) and streams segments
    in real-time to the UI console while writing a dedicated independent .json manifest.
    """
    def __init__(
        self,
        file_paths: List[str],
        project_name: str = "",
        model_id: Optional[str] = None,
        output_json_path: Optional[str] = None,
        detection_settings: Optional[DetectionSettings] = None,
    ):
        super().__init__()
        self.file_paths = [os.path.abspath(p) for p in file_paths if p and os.path.isfile(p)]
        self.project_name = project_name or "Voiceover"
        self.model_id = model_id or WhisperModelManager.get_active_model_id()
        self.output_json_path = output_json_path
        self.signals = VOBatchTranscribeSignals()
        self.detection_settings = detection_settings or DetectionSettings()
        self._cancelled = False
        self._model = None

    def cancel(self):
        self._cancelled = True
        self.cleanup()

    def _snap_part_to_waveform(
        self, vo_file: str, file_segments: List[Dict[str, Any]], duration: float
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, float]], Optional[float], Optional[str]]:
        """
        Measures the part's loudness, finds speech islands, and snaps Whisper's
        segment edges to them. Never fatal: on any failure the Whisper times are
        kept as-is (snapped=False) so transcription still completes.
        """
        try:
            env_db, cache_path = load_or_compute_envelope(vo_file)
            segs, islands, floor = analyze_and_snap_part(
                file_segments, env_db, self.detection_settings, duration_s=duration
            )
            snapped = sum(1 for s in segs if s.get("snapped"))
            logger.info(
                "Snapped %d/%d segments of '%s' to %d speech islands (noise floor %.1f dB)",
                snapped, len(segs), os.path.basename(vo_file), len(islands), floor,
            )
            return segs, islands, floor, cache_path
        except Exception as e:
            logger.warning("Waveform snapping skipped for '%s': %s", vo_file, e, exc_info=True)
            fallback = []
            for s in file_segments:
                s = dict(s)
                s.setdefault("whisper_in", s.get("source_in"))
                s.setdefault("whisper_out", s.get("source_out"))
                s["snapped"] = False
                fallback.append(s)
            return fallback, [], None, None

    def cleanup(self):
        """Immediately destroys the WhisperModel instance, unloads GPU tensors, and flushes VRAM."""
        self._cancelled = True
        model = getattr(self, "_model", None)
        if model is not None:
            try:
                # Tell CTranslate2 to explicitly offload model weights from GPU memory
                if hasattr(model, "model") and hasattr(model.model, "unload_model"):
                    try:
                        model.model.unload_model()
                    except Exception:
                        pass
                if hasattr(model, "model"):
                    try:
                        del model.model
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                del self._model
            except Exception:
                pass
            self._model = None
        import gc
        gc.collect()
        gc.collect()

    @staticmethod
    def _fmt_mmss(seconds: float) -> str:
        secs = max(0, int(round(seconds)))
        m = secs // 60
        s = secs % 60
        return f"{m:02d}:{s:02d}"

    def run(self):
        if not self.file_paths:
            self.signals.error.emit("No valid voiceover audio files provided for transcription.")
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self.signals.error.emit(
                "faster-whisper is not installed yet. Open Settings -> AI Models to install or update the engine."
            )
            return

        try:
            spec = WhisperModelManager.get_model_spec(self.model_id)
            if not spec:
                self.signals.error.emit(f"Unknown Whisper model '{self.model_id}'.")
                return

            if WhisperModelManager.is_model_downloaded(self.model_id):
                model_source = WhisperModelManager.get_model_local_dir(self.model_id)
            else:
                self.signals.file_progress.emit(1, 3, f"Downloading {spec.name} ({spec.size_display})...")
                model_source = spec.repo_id

            device, compute_type = WhisperModelManager.resolve_device_and_compute()
            self.signals.file_progress.emit(1, 8, f"Loading Whisper ({spec.name} on {device.upper()} {compute_type})...")

            try:
                self._model = WhisperModel(
                    model_source,
                    device=device,
                    compute_type=compute_type,
                    download_root=WhisperModelManager.ensure_models_dir()
                )
            except Exception as init_err:
                if device == "cuda":
                    logger.warning("CUDA model initialization failed (%s). Falling back to CPU int8.", init_err)
                    self.signals.file_progress.emit(1, 8, "CUDA failed to load. Falling back to CPU (int8)...")
                    self.cleanup()
                    device, compute_type = "cpu", "int8"
                    self._model = WhisperModel(
                        model_source,
                        device="cpu",
                        compute_type="int8",
                        download_root=WhisperModelManager.ensure_models_dir()
                    )
                else:
                    raise init_err

            total_files = len(self.file_paths)
            all_segments: List[Dict[str, Any]] = []
            all_islands: List[Dict[str, Any]] = []
            files_manifest: List[Dict[str, Any]] = []
            cumulative_offset = 0.0

            for f_idx, vo_file in enumerate(self.file_paths):
                if self._cancelled:
                    self.signals.error.emit("Transcription cancelled.")
                    return

                part_id = f_idx + 1
                fname = os.path.basename(vo_file)
                self.signals.file_started.emit(part_id, vo_file, f_idx + 1, total_files)
                self.signals.file_progress.emit(part_id, 12, f"Analyzing audio stream ({f_idx + 1}/{total_files}): {fname}")

                file_segments: List[Dict[str, Any]] = []
                actual_dur = 0.0

                for attempt in range(2):
                    if self._cancelled:
                        self.signals.error.emit("Transcription cancelled.")
                        return

                    try:
                        segments_gen, info = self._model.transcribe(
                            vo_file,
                            beam_size=5,
                            word_timestamps=True,
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=300),
                        )

                        file_dur = float(getattr(info, "duration", 0.0) or 0.0)
                        file_segments = []
                        max_seg_end = 0.0

                        for seg in segments_gen:
                            if self._cancelled:
                                self.signals.error.emit("Transcription cancelled.")
                                return

                            clean_text = (seg.text or "").strip()
                            if not clean_text:
                                continue

                            s_in = round(float(seg.start), 3)
                            s_out = round(float(seg.end), 3)
                            max_seg_end = max(max_seg_end, s_out)

                            words = [
                                {
                                    "w": (w.word or "").strip(),
                                    "s": round(float(w.start), 3),
                                    "e": round(float(w.end), 3),
                                    "p": round(float(getattr(w, "probability", 0.0) or 0.0), 3),
                                }
                                for w in (getattr(seg, "words", None) or [])
                                if (w.word or "").strip()
                            ]

                            seg_dict = {
                                "id": len(all_segments) + len(file_segments),
                                "part_id": part_id,
                                "source_file": vo_file,
                                "source_filename": fname,
                                "source_in": s_in,
                                "source_out": s_out,
                                "global_in": round(s_in + cumulative_offset, 3),
                                "global_out": round(s_out + cumulative_offset, 3),
                                "text": clean_text,
                                "words": words,
                            }
                            file_segments.append(seg_dict)

                            # Real-time streaming signal to the live log / UI console
                            self.signals.segment_streamed.emit(part_id, seg_dict)

                            if file_dur > 0:
                                ratio = min(1.0, float(seg.end) / file_dur)
                                pct = int(12 + ratio * 86)
                                pos_str = f"{self._fmt_mmss(seg.end)} / {self._fmt_mmss(file_dur)}"
                                self.signals.file_progress.emit(
                                    part_id,
                                    pct,
                                    f"Transcribing Part {part_id}/{total_files} ({pct}%) [{pos_str}]"
                                )
                            else:
                                self.signals.file_progress.emit(
                                    part_id,
                                    50,
                                    f"Transcribing Part {part_id}/{total_files} ({len(file_segments)} segs)..."
                                )

                        actual_dur = file_dur if file_dur > 0 else max_seg_end
                        break

                    except Exception as trans_err:
                        err_str = str(trans_err).lower()
                        is_cuda_err = (
                            device == "cuda"
                            and attempt == 0
                            and ("cublas" in err_str or "cuda" in err_str or "out of memory" in err_str or "cudnn" in err_str or "cannot be loaded" in err_str)
                        )
                        if is_cuda_err:
                            logger.warning(
                                "CUDA error during inference: %s. Cleaning up VRAM and falling back to CPU (int8)...",
                                trans_err,
                            )
                            self.signals.file_progress.emit(
                                part_id, 10, "CUDA runtime error. Falling back to CPU int8..."
                            )
                            self.cleanup()
                            device, compute_type = "cpu", "int8"
                            self._model = WhisperModel(
                                model_source,
                                device="cpu",
                                compute_type="int8",
                                download_root=WhisperModelManager.ensure_models_dir(),
                            )
                            continue
                        else:
                            raise trans_err

                # Waveform pass: Whisper decided the words, the waveform decides the timing.
                self.signals.file_progress.emit(part_id, 99, f"Snapping Part {part_id} to speech islands...")
                file_segments, part_islands, noise_floor, env_cache = self._snap_part_to_waveform(
                    vo_file, file_segments, actual_dur
                )
                for seg_dict in file_segments:
                    seg_dict["id"] = len(all_segments)
                    seg_dict["global_in"] = round(seg_dict["source_in"] + cumulative_offset, 3)
                    seg_dict["global_out"] = round(seg_dict["source_out"] + cumulative_offset, 3)
                    all_segments.append(seg_dict)
                for isl in part_islands:
                    all_islands.append({"part_id": part_id, **isl})

                files_manifest.append({
                    "part_id": part_id,
                    "filename": fname,
                    "path": vo_file,
                    "duration_sec": round(actual_dur, 3),
                    "global_offset_sec": round(cumulative_offset, 3),
                    "segment_count": len(file_segments),
                    "noise_floor_db": None if noise_floor is None else round(noise_floor, 1),
                    "island_count": len(part_islands),
                    "envelope_cache": env_cache or "",
                })

                self.signals.file_progress.emit(part_id, 100, f"Part {part_id} Ready ({len(file_segments)} segs)")
                self.signals.file_finished.emit(part_id, file_segments, actual_dur)
                cumulative_offset += actual_dur

            manifest = {
                "version": 2,
                "project_name": self.project_name,
                "created_at": time.time(),
                "model_used": spec.id,
                "compute_backend": f"{device}_{compute_type}",
                "total_files": len(self.file_paths),
                "total_duration_sec": round(cumulative_offset, 3),
                "total_segments": len(all_segments),
                "detection": self.detection_settings.to_dict(),
                "files": files_manifest,
                "islands": all_islands,
                "segments": all_segments,
            }

            saved_json_path = save_vo_transcript_json(manifest, self.output_json_path)

            # Immediately offload model weights from GPU memory upon batch completion
            self.cleanup()

            self.signals.batch_finished.emit(saved_json_path, manifest)
        except Exception as e:
            logger.error("VOBatchTranscribeWorker failed: %s", e, exc_info=True)
            self.signals.error.emit(str(e))
        finally:
            self.cleanup()


class VOTranscribeSignals(QObject):
    progress = Signal(int, str)          # (percent 0..100, status_text)
    finished = Signal(str, list, float)  # (vo_file_path, segments_list_of_dicts, total_duration_sec)
    error = Signal(str)


class VOTranscribeWorker(QRunnable):
    """
    Single-file convenience QRunnable that transcribes a Voiceover audio file (.wav, .mp3, .m4a)
    and saves an independent .json manifest. Preserved for backward compatibility.
    """
    def __init__(self, vo_file_path: str, model_id: Optional[str] = None, project_name: str = ""):
        super().__init__()
        self.vo_file_path = os.path.abspath(vo_file_path)
        self.model_id = model_id or WhisperModelManager.get_active_model_id()
        self.project_name = project_name or os.path.splitext(os.path.basename(self.vo_file_path))[0]
        self.signals = VOTranscribeSignals()
        self._batch_worker = VOBatchTranscribeWorker(
            file_paths=[self.vo_file_path],
            project_name=self.project_name,
            model_id=self.model_id,
        )
        self._batch_worker.signals.file_progress.connect(self._on_progress)
        self._batch_worker.signals.batch_finished.connect(self._on_finished)
        self._batch_worker.signals.error.connect(self.signals.error.emit)

    def cancel(self):
        self._batch_worker.cancel()
        self._batch_worker.cleanup()

    def cleanup(self):
        self._batch_worker.cleanup()

    def _on_progress(self, part_id: int, pct: int, status_text: str):
        self.signals.progress.emit(pct, status_text)

    def _on_finished(self, json_path: str, manifest: dict):
        segs = slim_segments(manifest.get("segments", []))
        dur = manifest.get("total_duration_sec", 0.0)
        self.signals.finished.emit(self.vo_file_path, segs, dur)

    def run(self):
        self._batch_worker.run()


def mask_urls_in_text(text: str) -> str:
    """Strips raw URLs from paragraph text so AI context stays clean and token-efficient."""
    if not text:
        return ""
    cleaned = re.sub(r"https?://[^\s)\]>\"']+", "", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def build_masked_script_payload(
    doc_state: DocumentState,
    linked_footage: Optional[Dict[str, str]] = None,
    fps: float = 23.976,
    start_block: int = 0,
    end_block: int = 999999,
) -> Dict[str, Any]:
    """
    Produces a clean, context-efficient representation of the script for Antigravity MCP:
    - Numbers every paragraph block [P0]..[P_N].
    - Replaces noisy raw PTS timestamps and URLs at A-Roll cue paragraphs (sync_id)
      with structured <<AROLL_CLIP ...>> tokens containing conformed SMPTE timecodes,
      exact frame counts, and bound local file paths.
    - Preserves all surrounding narration, @editor notes, and clip transcripts with their
      [P#] index so the AI can align spoken VO segments against [P#] and naturally ignore
      unspoken lines.
    """
    linked_map = linked_footage or {}
    exact_fps, nominal_base, _, _ = analyze_framerate(fps)

    # Group A-Roll ScriptItems by their paragraph sync_id
    items_by_sync: Dict[int, List[Dict[str, Any]]] = {}
    aroll_manifest: List[Dict[str, Any]] = []

    for item in doc_state.items:
        in_frame = parse_timecode_to_frames(item.start_timecode, exact_fps)
        out_frame = parse_timecode_to_frames(item.end_timecode, exact_fps) if item.end_timecode else (in_frame + nominal_base * 2)
        dur_frames = max(nominal_base, out_frame - in_frame)
        dur_sec = round(dur_frames / exact_fps, 3)

        matched_file = ""
        for lk in (item.links or []):
            norm_lk = normalize_link(lk)
            cand = linked_map.get(norm_lk) or linked_map.get(lk)
            if cand:
                matched_file = cand
                break

        entry = {
            "item_id": item.id,
            "row_index": item.row_index,
            "sync_id": item.sync_id,
            "meta": item.meta,
            "smpte_in": item.start_timecode,
            "smpte_out": item.end_timecode or item.start_timecode,
            "in_frame": in_frame,
            "out_frame": out_frame,
            "duration_frames": dur_frames,
            "duration_seconds": dur_sec,
            "bound_file_path": matched_file,
            "bound_file_name": os.path.basename(matched_file) if matched_file else "UNLINKED",
            "is_checked": item.is_checked,
            "vo_locked": getattr(item, "vo_locked", False),
            "vo_cut_after_sec": getattr(item, "vo_cut_after_sec", None),
            "vo_resume_sec": getattr(item, "vo_resume_sec", None),
            "vo_status": getattr(item, "vo_status", "PENDING"),
        }
        aroll_manifest.append(entry)
        items_by_sync.setdefault(item.sync_id, []).append(entry)

    masked_lines: List[Dict[str, Any]] = []
    for block in doc_state.blocks:
        b_id = block.get("id", 0)
        if not isinstance(b_id, int):
            continue
        if b_id < start_block or b_id > end_block:
            continue

        raw_text = (block.get("text") or "").strip()
        cues_here = items_by_sync.get(b_id, [])

        if cues_here:
            tokens = []
            for cue in cues_here:
                tokens.append(
                    f'<<AROLL_CLIP row={cue["row_index"]} item_id="{cue["item_id"]}" '
                    f'meta="{cue["meta"]}" smpte_in="{cue["smpte_in"]}" smpte_out="{cue["smpte_out"]}" '
                    f'dur_sec={cue["duration_seconds"]} file="{cue["bound_file_name"]}">>'
                )
            masked_lines.append({
                "block_id": b_id,
                "tag": f"[P{b_id}]",
                "type": "AROLL_CUE",
                "text": " ".join(tokens),
                "aroll_rows": [c["row_index"] for c in cues_here],
            })
        else:
            clean_txt = mask_urls_in_text(raw_text)
            if not clean_txt:
                continue
            masked_lines.append({
                "block_id": b_id,
                "tag": f"[P{b_id}]",
                "type": "HEADER" if block.get("isHeader") else ("EDITOR_NOTE" if block.get("isEditorLine") else "TEXT"),
                "text": clean_txt,
            })

    return {
        "document_name": doc_state.name,
        "file_signature": doc_state.file_signature,
        "fps": exact_fps,
        "nominal_timebase": nominal_base,
        "total_blocks": len(doc_state.blocks),
        "total_aroll_clips": len(aroll_manifest),
        "masked_paragraphs": masked_lines,
        "aroll_manifest": aroll_manifest,
    }
