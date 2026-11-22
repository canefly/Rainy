"""Updates and pinned engine downloads (no Qt).

Everything that decides *what code runs on this machine* lives here, so it is small and heavily tested:

- The update feed and the engine list are signed with the server key. We only believe them if the
  signature checks out against a public key compiled into the app (see license_core).
- A download is accepted only if its SHA-256 (and size) match the signed manifest. A mismatch deletes
  the file; nothing is ever run or extracted from an unverified download.
- Downloads must be https (plain http to localhost is allowed only when running from source), and a
  redirect may never downgrade to http.
- The installer is re-verified immediately before it is launched, so swapping the file after the
  download finishes doesn't help an attacker.

The wire format is documented in docs/rainy-api-plan.md.
"""
import hashlib
import hmac
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .license_core import (
    ApiRejected, ApiUnreachable, ManifestError, dev_overrides_allowed, verify_manifest,
)

FEED_MAX_AGE_SECONDS = 30 * 86400      # a replayed older feed can't hide a newer release for longer than this
ENGINE_MAX_AGE_SECONDS = 7 * 86400
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3
SIZE_SLACK_BYTES = 1024 * 1024         # more than the signed size + this and we stop reading

_SEMVER_RE = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
ENGINES = ("libmpv", "ffmpeg")
ENGINE_FORMATS = ("zip", "7z")


# ---------------------------------------------------------------------------- versions
def parse_version(v_str: str) -> Tuple[int, int, int]:
    """Lenient: 'v1.2.0', '1.2', '1.3.0-rc1' all work. Used for the version we are running."""
    clean = re.sub(r"^[^\d]*", "", (v_str or "").strip())
    parts = []
    for segment in clean.split("."):
        digits = re.match(r"^\d+", segment)
        parts.append(int(digits.group(0)) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _strict_semver(value) -> Tuple[int, int, int]:
    m = _SEMVER_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise ManifestError("malformed")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


# ---------------------------------------------------------------------------- the update feed
@dataclass(frozen=True)
class UpdateManifest:
    version: str
    released_at: str
    url: str
    sha256: str
    size: Optional[int]
    notes: Tuple[str, ...]
    min_supported: str


class UpdateStatus(str, Enum):
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    REQUIRED = "required"      # this version is below the minimum supported one: update before using


def manifest_from_payload(p: dict) -> Optional[UpdateManifest]:
    """None when nothing has been published. Anything malformed in a *signed* payload is refused."""
    if p.get("version") is None:
        return None
    version = p["version"]
    _strict_semver(version)
    min_supported = p.get("min_supported", "0.0.0")
    _strict_semver(min_supported)
    url, sha, size, notes = p.get("url"), p.get("sha256"), p.get("size"), p.get("notes", [])
    if not isinstance(url, str) or not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
        raise ManifestError("malformed")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
        raise ManifestError("malformed")
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise ManifestError("malformed")
    released = p.get("released_at")
    return UpdateManifest(version, released if isinstance(released, str) else "", url, sha, size,
                          tuple(n[:300] for n in notes[:50]), min_supported)


def parse_feed(reply: dict, keys: Mapping[str, Ed25519PublicKey], now: float) -> Optional[UpdateManifest]:
    token = reply.get("manifest") if isinstance(reply, dict) else None
    if not isinstance(token, str):
        raise ManifestError("malformed")
    payload = verify_manifest(token, keys, now, kind="app", max_age_seconds=FEED_MAX_AGE_SECONDS)
    return manifest_from_payload(payload)


def evaluate(current_version: str, manifest: Optional[UpdateManifest]) -> UpdateStatus:
    if manifest is None:
        return UpdateStatus.UP_TO_DATE
    cur, latest = parse_version(current_version), parse_version(manifest.version)
    if latest <= cur:
        return UpdateStatus.UP_TO_DATE
    if cur < parse_version(manifest.min_supported):
        return UpdateStatus.REQUIRED
    return UpdateStatus.AVAILABLE


# ---------------------------------------------------------------------------- downloading
class DownloadError(Exception):
    """code: bad_url, network, too_big, size_mismatch, hash_mismatch, cancelled, disk, bad_file."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def friendly_download_error(e: DownloadError) -> str:
    return {
        "bad_url": "The download address isn't a secure https link, so it was refused.",
        "network": "Couldn't download the file. Check your connection and try again.",
        "too_big": "The download was larger than expected, so it was stopped.",
        "size_mismatch": "The download was the wrong size, so it was discarded.",
        "hash_mismatch": "The download didn't match its signed fingerprint, so it was discarded and not used.",
        "cancelled": "Download cancelled.",
        "disk": "Couldn't save the file to disk.",
        "bad_file": "The downloaded file isn't where it should be, so it wasn't used.",
    }.get(e.code, e.message)


def check_download_url(url: str, *, allow_http_local: Optional[bool] = None) -> None:
    allow = dev_overrides_allowed() if allow_http_local is None else allow_http_local
    u = urlparse(url or "")
    ok = u.scheme == "https" or (allow and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))
    if not ok or not u.hostname or any(ord(c) < 33 for c in url):
        raise DownloadError("bad_url", "Only https download addresses are accepted.")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never to anything check_download_url would refuse (no https -> http)."""

    def __init__(self, allow_http_local: Optional[bool]):
        self._allow = allow_http_local

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_download_url(newurl, allow_http_local=self._allow)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener(allow_http_local: Optional[bool] = None) -> Callable:
    return urllib.request.build_opener(_SafeRedirects(allow_http_local)).open


def sha256_file(path: Path) -> Tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def verify_file(path: Path, sha256: str, size: Optional[int] = None) -> None:
    """Raises DownloadError unless the file matches. Used right before anything is run or extracted."""
    try:
        digest, n = sha256_file(path)
    except OSError as e:
        raise DownloadError("bad_file", str(e)) from None
    if size is not None and n != size:
        raise DownloadError("size_mismatch")
    if not hmac.compare_digest(digest, sha256.lower()):
        raise DownloadError("hash_mismatch")


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def download_verified(url: str, dest, *, sha256: str, size: Optional[int] = None,
                      progress: Optional[Callable[[int, int], None]] = None,
                      cancelled: Optional[Callable[[], bool]] = None, opener: Optional[Callable] = None,
                      max_bytes: int = MAX_DOWNLOAD_BYTES, timeout: float = 30.0,
                      allow_http_local: Optional[bool] = None) -> Path:
    """Download `url` to `dest`, and only keep it if its SHA-256 (and size) match the signed values.

    The data goes to `<dest>.part` first and is renamed into place only after it verifies, so a
    half-finished or tampered download is never sitting at the final path."""
    check_download_url(url, allow_http_local=allow_http_local)
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    limit = min(max_bytes, size + SIZE_SLACK_BYTES) if size else max_bytes
    opener = opener or default_opener(allow_http_local)
    req = urllib.request.Request(url, headers={"User-Agent": "RainyTools Native"})
    h, got = hashlib.sha256(), 0
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with opener(req, timeout=timeout) as res, open(part, "wb") as out:
            headers = getattr(res, "headers", None)
            declared = headers.get("content-length") if headers is not None else None
            total = size or (int(declared) if declared and str(declared).isdigit() else 0)
            while True:
                if cancelled is not None and cancelled():
                    raise DownloadError("cancelled")
                data = res.read(64 * 1024)
                if not data:
                    break
                got += len(data)
                if got > limit:
                    raise DownloadError("too_big")
                h.update(data)
                out.write(data)
                if progress is not None:
                    progress(got, total)
    except DownloadError:
        _remove_quietly(part)
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        _remove_quietly(part)
        raise DownloadError("network", str(e)) from None
    except OSError as e:
        _remove_quietly(part)
        raise DownloadError("disk", str(e)) from None

    if size is not None and got != size:
        _remove_quietly(part)
        raise DownloadError("size_mismatch")
    if not hmac.compare_digest(h.hexdigest(), sha256.lower()):
        _remove_quietly(part)
        raise DownloadError("hash_mismatch")
    try:
        os.replace(part, dest)
    except OSError as e:
        _remove_quietly(part)
        raise DownloadError("disk", str(e)) from None
    return dest


# ---------------------------------------------------------------------------- installer files
def updates_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".knot")
    return Path(base) / "com.canefly.knot" / "updates"


def installer_path_for(manifest: UpdateManifest, directory: Optional[Path] = None) -> Path:
    """Where this release's installer is saved. The name comes from the version, never from the URL."""
    ext = Path(urlparse(manifest.url).path).suffix.lower()
    ext = ext if ext in (".exe", ".msi") else ".exe"
    return (directory or updates_dir()) / f"RainyTools_Setup_v{manifest.version}{ext}"


def prune_updates(directory: Path, keep: Optional[Path] = None) -> None:
    """Delete leftovers from earlier updates (best effort)."""
    try:
        for p in directory.iterdir():
            if p.is_file() and p != keep and (p.name.startswith("RainyTools_Setup_v") or p.name.endswith(".part")):
                _remove_quietly(p)
    except OSError:
        pass


def find_verified_download(manifest: UpdateManifest, directory: Optional[Path] = None) -> Optional[Path]:
    path = installer_path_for(manifest, directory)
    if not path.is_file():
        return None
    try:
        verify_file(path, manifest.sha256, manifest.size)
    except DownloadError:
        return None
    return path


def default_starter(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606  (ShellExecute handles the installer's UAC prompt)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], close_fds=True)
    else:
        subprocess.Popen([str(path)], close_fds=True)


def launch_installer(path, manifest: UpdateManifest, *, directory: Optional[Path] = None,
                     starter: Optional[Callable[[Path], None]] = None) -> None:
    """Run the downloaded installer, after checking once more that it is exactly the signed file and
    that it sits in our updates folder (not somewhere an attacker could have pointed us)."""
    path = Path(path)
    base = (directory or updates_dir()).resolve()
    if path.is_symlink() or not path.is_file() or path.resolve().parent != base:
        raise DownloadError("bad_file")
    verify_file(path, manifest.sha256, manifest.size)
    (starter or default_starter)(path)


# ---------------------------------------------------------------------------- pinned engines
@dataclass(frozen=True)
class EngineEntry:
    engine: str
    platform: str
    version: str
    url: str
    sha256: str
    size: int
    format: str


class EngineManifestError(Exception):
    """code: unreachable, not_registered, not_approved, rejected, invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def platform_id() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win-x64" if machine in ("amd64", "x86_64") else f"win-{machine}"
    if sys.platform == "darwin":
        return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64"
    return f"linux-{'x64' if machine in ('amd64', 'x86_64') else machine}"


def parse_engine_manifest(reply: dict, keys: Mapping[str, Ed25519PublicKey], now: float) -> List[EngineEntry]:
    token = reply.get("manifest") if isinstance(reply, dict) else None
    if not isinstance(token, str):
        raise ManifestError("malformed")
    payload = verify_manifest(token, keys, now, kind="engines", max_age_seconds=ENGINE_MAX_AGE_SECONDS)
    raw = payload.get("engines")
    if not isinstance(raw, list):
        raise ManifestError("malformed")
    out = []
    for e in raw:
        try:
            entry = EngineEntry(e["engine"], e["platform"], str(e["version"]), e["url"], e["sha256"], e["size"], e["format"])
        except (KeyError, TypeError):
            raise ManifestError("malformed") from None
        if (entry.engine not in ENGINES or entry.format not in ENGINE_FORMATS or not isinstance(entry.platform, str)
                or not isinstance(entry.url, str) or not isinstance(entry.sha256, str) or not _SHA256_RE.fullmatch(entry.sha256)
                or not isinstance(entry.size, int) or isinstance(entry.size, bool) or entry.size <= 0):
            raise ManifestError("malformed")
        out.append(entry)
    return out


def pick_engine(entries: List[EngineEntry], engine: str, plat: Optional[str] = None) -> Optional[EngineEntry]:
    plat = plat or platform_id()
    return next((e for e in entries if e.engine == engine and e.platform == plat), None)


def load_engine_entries(controller, now: float) -> List[EngineEntry]:
    """Fetch and verify the signed engine list using the license controller (device key + server key).
    Blocking: call from a worker thread. Raises EngineManifestError with a message fit to show."""
    try:
        reply = controller.fetch_engine_manifest()
    except ApiUnreachable:
        raise EngineManifestError("unreachable", "Can't reach the server to get the verified download list. Check your connection and try again.") from None
    except ApiRejected as e:
        if e.code == "not_registered":
            raise EngineManifestError("not_registered", "This install isn't registered yet.") from None
        if e.code == "not_approved":
            raise EngineManifestError("not_approved", "Engines can be downloaded once this install is approved.") from None
        raise EngineManifestError("rejected", "The server refused the request. Try again in a moment.") from None
    try:
        return parse_engine_manifest(reply, controller.keys, now)
    except ManifestError:
        raise EngineManifestError("invalid", "The download list from the server failed verification, so nothing was downloaded.") from None
