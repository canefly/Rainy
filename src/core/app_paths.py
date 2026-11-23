"""The one place that decides where the app keeps its files.

Everything the app downloads or creates lives under a single folder, the app home:

    %LOCALAPPDATA%\\com.canefly.knot\\
        knot.db, knot.db.bak, backups\\     the database and its rolling snapshots
        bin\\                                engines (libmpv, ffmpeg, ffprobe)
        models\\                             AI models (photo_censor, whisper)
        cache\\                              waveforms and speech envelopes (safe to delete)
        logs\\                               knot.log
        updates\\                            downloaded installers

Back up that one folder and you have everything. It is writable without admin rights and survives
reinstalls and updates. Elsewhere (no LOCALAPPDATA) it is ``~/.knot``.

The environment is read on every call, so tests can aim a whole run at a temporary folder with
``KNOT_HOME``. Like every other developer switch it is ignored in compiled builds.
"""
import os
import sys
from pathlib import Path

APP_ID = "com.canefly.knot"
IS_COMPILED = "__compiled__" in globals()  # Nuitka defines this in compiled modules


def is_packaged() -> bool:
    """True for an installed/compiled build, which ignores every developer override."""
    return IS_COMPILED or bool(getattr(sys, "frozen", False))


def app_home() -> str:
    override = os.environ.get("KNOT_HOME")
    if override and not is_packaged():
        return os.path.abspath(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, APP_ID)
    return str(Path.home() / ".knot")


def _sub(name: str, create: bool) -> str:
    path = os.path.join(app_home(), name)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def bin_dir(create: bool = False) -> str:
    return _sub("bin", create)


def models_dir(create: bool = False) -> str:
    return _sub("models", create)


def cache_dir(create: bool = False) -> str:
    return _sub("cache", create)


def logs_dir(create: bool = True) -> str:
    return _sub("logs", create)


def updates_dir(create: bool = False) -> str:
    return _sub("updates", create)


def ui_config_file() -> str:
    """Where the UI library keeps its theme settings. (Its own default is a relative config/config.json in
    whatever folder the app was started from, which is the install folder and not writable for a normal user.)"""
    return os.path.join(app_home(), "ui_config.json")


# --------------------------------------------------------------------------- engines
def _source_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def dev_bin_dir() -> str:
    """The repo's own ``bin`` folder. Only used when running from source."""
    return os.path.join(_source_root(), "bin")


def engine_search_dirs() -> list:
    """Where engine binaries are looked up, in priority order."""
    dirs = []
    if not is_packaged() and os.path.isdir(dev_bin_dir()):
        dirs.append(dev_bin_dir())
    home_bin = bin_dir()
    if os.path.isdir(home_bin):
        dirs.append(home_bin)
    return dirs


def writable_bin_dir() -> str:
    """Where downloaded engines go: the repo's ``bin`` in a source checkout that has one, else the app home."""
    if not is_packaged() and os.path.isdir(dev_bin_dir()):
        return dev_bin_dir()
    return bin_dir(create=True)


def find_binary(name: str):
    """Full path of an engine binary, or None. A source checkout may also use one on PATH; an
    installed build never does, so a stray copy elsewhere on the machine can't be picked up."""
    import shutil
    for d in engine_search_dirs():
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    if not is_packaged():
        found = shutil.which(os.path.splitext(name)[0])
        if found and os.path.isfile(found):
            return found
    return None
