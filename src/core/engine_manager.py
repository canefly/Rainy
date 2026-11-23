import os
import sys
import json
import time
import shutil
import ctypes
import zipfile
import subprocess
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from PySide6.QtCore import QThread, Signal

from . import app_paths, net_tls
from .archive_extract import extract_wanted
from .license_core import dev_overrides_allowed
from .logger import get_logger
from .update_core import (
    DownloadError, EngineManifestError, download_verified, friendly_download_error, load_engine_entries, pick_engine,
)

logger = get_logger("engine_manager")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

@dataclass
class EngineSpec:
    id: str
    name: str
    description: str
    repo_name: str
    repo_url: str
    maintainer: str
    license: str
    primary_binary: str
    all_binaries: List[str]


SUPPORTED_ENGINES: Dict[str, EngineSpec] = {
    "libmpv": EngineSpec(
        id="libmpv",
        name="libmpv Video Engine",
        description="Frame-accurate video playback, PTS timecode calculation, and Source Monitor transport.",
        repo_name="shinchiro/mpv-winbuild-cmake",
        repo_url="https://github.com/shinchiro/mpv-winbuild-cmake",
        maintainer="shinchiro (Official Windows mpv/libmpv toolchain)",
        license="LGPL v2.1+ / GPL v3",
        primary_binary="libmpv-2.dll",
        all_binaries=["libmpv-2.dll", "mpv-2.dll", "libmpv-1.dll", "mpv-1.dll"]
    ),
    "ffmpeg": EngineSpec(
        id="ffmpeg",
        name="FFmpeg & FFprobe",
        description="High-speed CFR footage transcoding, audio extraction, and exact stream metadata probing.",
        repo_name="BtbN/FFmpeg-Builds",
        repo_url="https://github.com/BtbN/FFmpeg-Builds",
        maintainer="BtbN (Automated daily builds with NVENC, QSV, AMF hardware acceleration)",
        license="GPL v3",
        primary_binary="ffmpeg.exe",
        all_binaries=["ffmpeg.exe", "ffprobe.exe"]
    ),
    "whisper": EngineSpec(
        id="whisper",
        name="Faster-Whisper & CTranslate2",
        description="Local GPU-accelerated speech-to-text transcription for voiceover alignment and subtitles.",
        repo_name="SYSTRAN/faster-whisper",
        repo_url="https://github.com/SYSTRAN/faster-whisper",
        maintainer="SYSTRAN / OpenNMT",
        license="MIT",
        primary_binary="faster_whisper",
        all_binaries=[]
    )
}


def get_writable_bin_dir() -> str:
    """Where downloaded engines go: the app home's bin folder (see app_paths), or the repo's own bin
    folder when running from a source checkout that has one."""
    return app_paths.writable_bin_dir()


def get_engine_search_dirs() -> List[str]:
    """Returns the directories where engine binaries may live, ordered by priority (see app_paths)."""
    return app_paths.engine_search_dirs()


def register_engine_dll_directories():
    """Registers binary directories with Windows DLL search path (Python 3.8+)."""
    if hasattr(os, "add_dll_directory"):
        for d in get_engine_search_dirs():
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
    # Also prepend to PATH
    search_dirs = get_engine_search_dirs()
    if search_dirs:
        os.environ["PATH"] = os.pathsep.join(search_dirs) + os.pathsep + os.environ.get("PATH", "")


# Run DLL registration on import
register_engine_dll_directories()


def get_mpv_path() -> Optional[str]:
    """Locates libmpv-2.dll, mpv-2.dll, or mpv-1.dll across search directories and PATH."""
    for d in get_engine_search_dirs():
        for name in ("libmpv-2.dll", "mpv-2.dll", "libmpv-1.dll", "mpv-1.dll"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    
    # Check system PATH
    for name in ("libmpv-2.dll", "mpv-2.dll", "libmpv-1.dll", "mpv-1.dll"):
        which_path = shutil.which(name)
        if which_path and os.path.isfile(which_path):
            return which_path

    return None


def is_mpv_available() -> bool:
    """Checks whether mpv DLL exists and can be cleanly loaded via ctypes."""
    path = get_mpv_path()
    if not path:
        return False
    try:
        register_engine_dll_directories()
        dll = ctypes.CDLL(path)
        return True
    except Exception as e:
        logger.warning("Found mpv dll at %s but ctypes load failed: %s", path, e)
        return False


def get_mpv_status() -> Tuple[bool, str, str, str]:
    """Returns (is_available, status_label, path, hw_accel_note)."""
    path = get_mpv_path()
    if not path:
        writable = get_writable_bin_dir()
        expected = os.path.join(writable, "libmpv-2.dll")
        return False, "Not Installed", expected, "Direct3D 11 (d3d11va) + GPU-Next Ready"
    
    try:
        register_engine_dll_directories()
        dll = ctypes.CDLL(path)
        # Attempt to get mpv version
        ver_str = "Installed"
        try:
            get_ver = getattr(dll, "mpv_client_api_version", None)
            if get_ver:
                get_ver.restype = ctypes.c_ulong
                api_ver = get_ver()
                major = api_ver >> 16
                minor = api_ver & 0xFFFF
                ver_str = f"Ready (API v{major}.{minor})"
        except Exception:
            pass
        return True, ver_str, path, "Direct3D 11 (d3d11va) + GPU-Next Active"
    except Exception as e:
        return False, f"Load Error: {str(e)[:40]}", path, "Direct3D 11"


def get_engine_status(engine_id: str) -> Dict[str, Any]:
    """Returns consolidated status metadata for the requested engine."""
    spec = SUPPORTED_ENGINES.get(engine_id)
    if not spec:
        return {}

    if engine_id == "libmpv":
        avail, status_lbl, path, hw = get_mpv_status()
        return {
            "spec": spec,
            "installed": avail,
            "status_label": status_lbl,
            "path": path,
            "hw_info": hw,
        }
    elif engine_id == "ffmpeg":
        from .ffmpeg_encoder import get_ffmpeg_status, get_ffmpeg_path, detect_available_encoders
        from .media_probe import get_ffprobe_status, get_ffprobe_path
        
        has_ffmpeg, ffmpeg_ver, ffmpeg_p = get_ffmpeg_status()
        has_probe, probe_ver, probe_p = get_ffprobe_status()

        installed = has_ffmpeg or has_probe
        if has_ffmpeg and has_probe:
            lbl = f"Ready ({ffmpeg_ver})"
        elif has_probe and not has_ffmpeg:
            lbl = "ffprobe Ready | ffmpeg Missing"
        elif has_ffmpeg and not has_probe:
            lbl = "ffmpeg Ready | ffprobe Missing"
        else:
            lbl = "Not Installed"

        # Hardware encoders summary
        encoders = detect_available_encoders()
        accel_list = []
        if encoders.get("h264_nvenc"):
            accel_list.append("NVIDIA NVENC")
        if encoders.get("h264_qsv"):
            accel_list.append("Intel QSV")
        if encoders.get("h264_amf"):
            accel_list.append("AMD AMF")
        hw_info = ", ".join(accel_list) if accel_list else "CPU Software (libx264)"

        active_path = ffmpeg_p or probe_p or os.path.join(get_writable_bin_dir(), "ffmpeg.exe")
        return {
            "spec": spec,
            "installed": installed,
            "status_label": lbl,
            "path": active_path,
            "hw_info": hw_info,
        }
    elif engine_id == "whisper":
        from .vo_engine import WhisperModelManager
        status = WhisperModelManager.get_engine_status()
        return {
            "spec": spec,
            "installed": status.get("installed", False),
            "status_label": f"Ready (v{status.get('version', '1.0')})" if status.get("installed") else "Not Installed",
            "path": status.get("path", ""),
            "hw_info": "CUDA 12.x Active (Fast GPU)" if status.get("cuda_available") else "CPU Mode",
        }
    return {}


class EngineDownloadWorker(QThread):
    """
    Background worker that fetches release assets from GitHub, downloads them with 
    progress reporting, and extracts the target binaries into the Rainy bin directory.
    """
    progress = Signal(str, int, str)       # (engine_id, percentage, status_text)
    finished = Signal(str, bool, str)      # (engine_id, success, message)

    def __init__(self, engine_id: str, parent=None):
        super().__init__(parent)
        self.engine_id = engine_id
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self.engine_id == "libmpv":
                self._download_libmpv()
            elif self.engine_id == "ffmpeg":
                self._download_ffmpeg()
            elif self.engine_id == "whisper":
                self._update_whisper()
            else:
                self.finished.emit(self.engine_id, False, f"Unknown engine: {self.engine_id}")
        except Exception as e:
            logger.exception("Error in EngineDownloadWorker for %s", self.engine_id)
            self.finished.emit(self.engine_id, False, str(e))

    def _download_libmpv(self):
        entry, problem = self._pinned_entry()
        if entry is not None:
            self._install_pinned(entry)
            return
        if not self._legacy_unpinned_allowed():
            self.finished.emit(self.engine_id, False, problem)
            return
        # Developer opt-in only (KNOT_ALLOW_UNPINNED_ENGINES=1): the old unverified GitHub path.
        import urllib.request
        self.progress.emit(self.engine_id, 5, "Checking GitHub releases for libmpv...")
        
        api_url = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Rainy-Desktop/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=net_tls.ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag = data.get("tag_name", "latest")
        assets = data.get("assets", [])

        # Find x86_64 dev package (which contains mpv-2.dll and headers)
        target_asset = None
        for a in assets:
            name = a.get("name", "")
            # Prefer standard x86_64 dev build
            if "mpv-dev-x86_64-" in name and not "v3" in name and (name.endswith(".7z") or name.endswith(".zip")):
                target_asset = a
                break
        if not target_asset:
            # Fallback to any x86_64 dev package
            for a in assets:
                name = a.get("name", "")
                if "mpv-dev-x86_64" in name and (name.endswith(".7z") or name.endswith(".zip")):
                    target_asset = a
                    break

        if not target_asset:
            self.finished.emit(self.engine_id, False, "Could not find a compatible 64-bit libmpv package in the release.")
            return

        download_url = target_asset.get("browser_download_url")
        asset_name = target_asset.get("name")
        total_size = target_asset.get("size", 0)

        target_bin_dir = get_writable_bin_dir()
        temp_archive = os.path.join(target_bin_dir, f"_temp_{asset_name}")

        self.progress.emit(self.engine_id, 10, f"Downloading {asset_name} ({total_size / (1024*1024):.1f} MB)...")
        self._download_file_with_progress(download_url, temp_archive, total_size, 10, 80)

        if self._is_cancelled:
            if os.path.exists(temp_archive):
                os.remove(temp_archive)
            self.finished.emit(self.engine_id, False, "Download cancelled by user.")
            return

        # Extraction phase
        self.progress.emit(self.engine_id, 85, f"Extracting {asset_name}...")
        extracted_dll = False

        mpv_candidate_names = ["libmpv-2.dll", "mpv-2.dll", "libmpv-1.dll", "mpv-1.dll"]
        if asset_name.endswith(".7z"):
            extracted_dll = self._extract_7z(temp_archive, target_bin_dir, mpv_candidate_names)
        elif asset_name.endswith(".zip"):
            extracted_dll = self._extract_zip(temp_archive, target_bin_dir, mpv_candidate_names)

        if os.path.exists(temp_archive):
            try:
                os.remove(temp_archive)
            except Exception:
                pass

        register_engine_dll_directories()

        if is_mpv_available():
            extracted_dll = True

        if not extracted_dll:
            self.finished.emit(self.engine_id, False, "Archive did not contain libmpv-2.dll or mpv-2.dll.")
            return

        if is_mpv_available():
            self.progress.emit(self.engine_id, 100, f"libmpv {tag} installed and verified.")
            self.finished.emit(self.engine_id, True, f"libmpv ({tag}) successfully installed and verified.")
        else:
            self.finished.emit(self.engine_id, False, "libmpv DLL extracted but failed runtime verification.")

    def _download_ffmpeg(self):
        entry, problem = self._pinned_entry()
        if entry is not None:
            self._install_pinned(entry)
            return
        if not self._legacy_unpinned_allowed():
            self.finished.emit(self.engine_id, False, problem)
            return
        # Developer opt-in only (KNOT_ALLOW_UNPINNED_ENGINES=1): the old unverified GitHub path.
        import urllib.request
        self.progress.emit(self.engine_id, 5, "Checking GitHub releases for FFmpeg...")

        api_url = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Rainy-Desktop/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=net_tls.ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag = data.get("tag_name", "latest")
        assets = data.get("assets", [])

        target_asset = None
        for a in assets:
            name = a.get("name", "")
            if "win64-gpl.zip" in name and not "shared" in name:
                target_asset = a
                break

        if not target_asset:
            self.finished.emit(self.engine_id, False, "Could not find compatible win64 FFmpeg release.")
            return

        download_url = target_asset.get("browser_download_url")
        asset_name = target_asset.get("name")
        total_size = target_asset.get("size", 0)

        target_bin_dir = get_writable_bin_dir()
        temp_archive = os.path.join(target_bin_dir, f"_temp_{asset_name}")

        self.progress.emit(self.engine_id, 10, f"Downloading {asset_name} ({total_size / (1024*1024):.1f} MB)...")
        self._download_file_with_progress(download_url, temp_archive, total_size, 10, 85)

        if self._is_cancelled:
            if os.path.exists(temp_archive):
                os.remove(temp_archive)
            self.finished.emit(self.engine_id, False, "Download cancelled by user.")
            return

        self.progress.emit(self.engine_id, 90, "Extracting ffmpeg.exe and ffprobe.exe...")
        extracted = self._extract_zip(temp_archive, target_bin_dir, ["ffmpeg.exe", "ffprobe.exe"])

        if os.path.exists(temp_archive):
            try:
                os.remove(temp_archive)
            except Exception:
                pass

        if extracted:
            self.progress.emit(self.engine_id, 100, "FFmpeg installed successfully.")
            self.finished.emit(self.engine_id, True, f"FFmpeg ({tag}) installed successfully.")
        else:
            self.finished.emit(self.engine_id, False, "Failed to extract FFmpeg binaries from archive.")

    # ------------------------------------------------------------------ pinned (verified) downloads
    def _legacy_unpinned_allowed(self) -> bool:
        """Dev only: fetch 'latest' straight from GitHub with no integrity check. Never in a shipped build."""
        return dev_overrides_allowed() and os.environ.get("KNOT_ALLOW_UNPINNED_ENGINES") == "1"

    def _get_controller(self):
        from .license_service import LicenseService
        return LicenseService.instance().controller

    def _pinned_entry(self):
        """(entry, None) when the server's signed list has this engine for this computer,
        else (None, message fit to show the user)."""
        try:
            entries = load_engine_entries(self._get_controller(), time.time())
        except EngineManifestError as e:
            return None, e.message
        except Exception as e:
            logger.warning("Engine list failed: %s", e)
            return None, "Couldn't get the verified download list."
        entry = pick_engine(entries, self.engine_id)
        if entry is None:
            return None, (f"No verified {self.engine_id} download has been published for this computer yet. "
                          "Ask Cane to publish one.")
        return entry, None

    def _install_pinned(self, entry):
        """Download exactly the file the signed list names, refuse it unless its SHA-256 and size match,
        and only then extract anything from it."""
        self.progress.emit(self.engine_id, 5, f"Downloading verified {entry.engine} build {entry.version}...")
        target_bin_dir = get_writable_bin_dir()
        temp_archive = os.path.join(target_bin_dir, f"_temp_{entry.engine}.{entry.format}")

        def on_progress(got, total):
            if total:
                pct = 10 + int(got / total * 75)
                self.progress.emit(self.engine_id, pct, f"Downloading: {got / (1024 * 1024):.1f} MB / {total / (1024 * 1024):.1f} MB ({pct}%)")

        try:
            download_verified(entry.url, temp_archive, sha256=entry.sha256, size=entry.size, progress=on_progress,
                              cancelled=lambda: self._is_cancelled)
        except DownloadError as e:
            msg = "Download cancelled by user." if e.code == "cancelled" else friendly_download_error(e)
            self.finished.emit(self.engine_id, False, msg)
            return

        self.progress.emit(self.engine_id, 88, "Verified. Extracting...")
        wanted = (["libmpv-2.dll", "mpv-2.dll", "libmpv-1.dll", "mpv-1.dll"] if entry.engine == "libmpv"
                  else ["ffmpeg.exe", "ffprobe.exe"])
        try:
            landed = extract_wanted(temp_archive, target_bin_dir, wanted)
            # libmpv ships under one of several names (any one will do); FFmpeg needs both of its programs.
            extracted = bool(landed) if entry.engine == "libmpv" else set(landed) == set(wanted)
        finally:
            try:
                os.remove(temp_archive)
            except OSError:
                pass

        if entry.engine == "libmpv":
            register_engine_dll_directories()
            if not (extracted or is_mpv_available()):
                self.finished.emit(self.engine_id, False, "The verified archive did not contain libmpv.")
            elif is_mpv_available():
                self.progress.emit(self.engine_id, 100, f"libmpv {entry.version} installed and verified.")
                self.finished.emit(self.engine_id, True, f"libmpv ({entry.version}) installed and verified.")
            else:
                self.finished.emit(self.engine_id, False, "libmpv was extracted but failed its runtime check.")
        elif extracted:
            self.progress.emit(self.engine_id, 100, "FFmpeg installed successfully.")
            self.finished.emit(self.engine_id, True, f"FFmpeg ({entry.version}) installed and verified.")
        else:
            self.finished.emit(self.engine_id, False, "The verified archive did not contain ffmpeg.exe and ffprobe.exe.")

    def _update_whisper(self):
        self.progress.emit(self.engine_id, 10, "Updating faster-whisper package...")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "faster-whisper"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        if proc.returncode == 0:
            self.progress.emit(self.engine_id, 100, "faster-whisper updated.")
            self.finished.emit(self.engine_id, True, "faster-whisper package successfully updated.")
        else:
            self.finished.emit(self.engine_id, False, f"pip upgrade failed: {proc.stderr[:100]}")

    def _download_file_with_progress(self, url: str, dest_path: str, total_bytes: int, start_pct: int, end_pct: int):
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Rainy-Desktop/1.0"})
        downloaded = 0
        chunk_size = 64 * 1024

        with urllib.request.urlopen(req, timeout=30, context=net_tls.ssl_context()) as response, open(dest_path, "wb") as out_file:
            real_total = total_bytes or int(response.headers.get("content-length", 0))
            while True:
                if self._is_cancelled:
                    break
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if real_total > 0:
                    pct = start_pct + int((downloaded / real_total) * (end_pct - start_pct))
                    mb_cur = downloaded / (1024 * 1024)
                    mb_tot = real_total / (1024 * 1024)
                    self.progress.emit(self.engine_id, pct, f"Downloading: {mb_cur:.1f} MB / {mb_tot:.1f} MB ({pct}%)")

    def _extract_zip(self, archive_path: str, target_dir: str, target_filenames: List[str]) -> bool:
        return bool(extract_wanted(archive_path, target_dir, target_filenames))

    def _extract_7z(self, archive_path: str, target_dir: str, target_filenames: List[str]) -> bool:
        return bool(extract_wanted(archive_path, target_dir, target_filenames))
