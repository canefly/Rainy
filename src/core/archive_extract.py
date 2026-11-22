"""Pull a few named files out of a downloaded engine archive (.zip or .7z), and nothing else.

Why this exists as its own piece:
- The libmpv and FFmpeg builds are 7z archives that use the BCJ2 filter, which py7zr cannot read. Windows 10/11
  ship `tar.exe` (bsdtar), which can, so that is what we use. A different `tar` found on PATH (Git for Windows
  ships GNU tar) cannot read 7z at all, so we only ever pick the system bsdtar, never "whatever tar is first".
- Only the files we asked for are extracted, into a scratch folder that is always removed, so no headers,
  import libraries or documentation are ever left behind in the engines folder.
- A file only counts if it landed with real content. An empty or missing file is a failure, never a success.
"""
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import List, Optional

from .logger import get_logger

logger = get_logger("archive_extract")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TAR_TIMEOUT_SECONDS = 300


def find_bsdtar() -> Optional[str]:
    """The system bsdtar, or None. Windows' own copy first; otherwise any `tar` that says it is bsdtar."""
    candidates = []
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root:
        candidates.append(os.path.join(root, "System32", "tar.exe"))
    found = shutil.which("tar") or shutil.which("bsdtar")
    if found:
        candidates.append(found)
    for exe in candidates:
        if not os.path.isfile(exe):
            continue
        try:
            out = subprocess.run([exe, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                                 creationflags=CREATE_NO_WINDOW).stdout.decode("utf-8", "replace").lower()
        except (OSError, subprocess.SubprocessError):
            continue
        if "bsdtar" in out or "libarchive" in out:
            return exe
    return None


def _safe_base(name: str) -> str:
    """The bare file name of an archive member, or '' if it isn't a plain file name."""
    base = os.path.basename(name.replace("\\", "/"))
    return "" if base in ("", ".", "..") else base


def _land(src: str, target_dir: str, base: str) -> bool:
    """Move an extracted file into place. Only a file with content counts."""
    try:
        if not os.path.isfile(src) or os.path.getsize(src) == 0:
            return False
        os.makedirs(target_dir, exist_ok=True)
        os.replace(src, os.path.join(target_dir, base))
        return True
    except OSError as e:
        logger.warning("Could not place %s in %s: %s", base, target_dir, e)
        return False


def _extract_zip(archive: str, target_dir: str, wanted: dict) -> List[str]:
    landed = []
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            base = _safe_base(member.filename)
            real = wanted.get(base.lower())
            if not real or real in landed:
                continue
            os.makedirs(target_dir, exist_ok=True)
            tmp = os.path.join(target_dir, real + ".part")
            try:
                with zf.open(member) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                if _land(tmp, target_dir, real):
                    landed.append(real)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
    return landed


def _collect(scratch: str, target_dir: str, wanted: dict, landed: List[str]) -> None:
    for root, _dirs, files in os.walk(scratch):
        for f in files:
            real = wanted.get(f.lower())
            if real and real not in landed and _land(os.path.join(root, f), target_dir, real):
                landed.append(real)


def _extract_7z(archive: str, target_dir: str, wanted: dict) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="_extract_", dir=target_dir)
    landed: List[str] = []
    try:
        tar = find_bsdtar()
        if tar:
            cmd = [tar, "-xf", archive, "-C", scratch]
            for real in wanted.values():
                cmd += ["--include", f"*/{real}", "--include", real]
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TAR_TIMEOUT_SECONDS,
                                      creationflags=CREATE_NO_WINDOW)
                bsd_note = (proc.returncode, proc.stderr.decode("utf-8", "replace")[:300])
            except (OSError, subprocess.SubprocessError) as e:
                bsd_note = (None, str(e))
            _collect(scratch, target_dir, wanted, landed)
            if bsd_note[0] != 0 and not landed:   # bsdtar also exits non-zero when one of our patterns matched nothing
                logger.warning("bsdtar found nothing (exit %s): %s", *bsd_note)
        if len(landed) < len(wanted) and not landed:
            # No usable system tar (or it found nothing). py7zr handles archives without the BCJ2 filter.
            try:
                import py7zr
                with py7zr.SevenZipFile(archive, mode="r") as z:
                    targets = [n for n in z.getnames() if _safe_base(n).lower() in wanted]
                    if targets:
                        z.extract(path=scratch, targets=targets)
                _collect(scratch, target_dir, wanted, landed)
            except Exception as e:  # noqa: BLE001  (a bad archive must become a clear failure, not a crash)
                logger.warning("py7zr could not read the archive: %s", e)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return landed


def extract_wanted(archive: str, target_dir: str, names: List[str]) -> List[str]:
    """Extract the files called `names` (matched by bare file name, anywhere in the archive, case-insensitive)
    into `target_dir`. Returns the names that landed with real content; anything not returned did not arrive."""
    wanted = {n.lower(): n for n in names}
    if archive.lower().endswith(".zip"):
        return _extract_zip(archive, target_dir, wanted)
    return _extract_7z(archive, target_dir, wanted)
