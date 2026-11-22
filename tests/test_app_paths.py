"""One home folder for everything the app downloads or creates."""
import os
import sys
from pathlib import Path

import pytest

from src.core import app_paths as ap


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("KNOT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


def test_everything_lives_under_one_folder(clean_env):
    home = Path(ap.app_home())
    assert home == clean_env / "local" / "com.canefly.knot"
    for p in (ap.bin_dir(), ap.models_dir(), ap.cache_dir(), ap.logs_dir(create=False), ap.updates_dir()):
        assert Path(p).parent == home


def test_without_localappdata_it_falls_back_to_a_dot_folder(clean_env, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA")
    assert Path(ap.app_home()) == Path.home() / ".knot"


def test_the_home_can_be_aimed_at_a_temp_folder_from_source_only(clean_env, monkeypatch):
    monkeypatch.setenv("KNOT_HOME", str(clean_env / "elsewhere"))
    assert Path(ap.app_home()) == clean_env / "elsewhere"
    monkeypatch.setattr(sys, "frozen", True, raising=False)          # an installed build ignores it
    assert Path(ap.app_home()) == clean_env / "local" / "com.canefly.knot"
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(ap, "IS_COMPILED", True)                     # so does a Nuitka build
    assert Path(ap.app_home()) == clean_env / "local" / "com.canefly.knot"


def test_logs_dir_is_created_but_the_others_only_on_request(clean_env):
    assert os.path.isdir(ap.logs_dir())
    assert not os.path.exists(ap.bin_dir()) and not os.path.exists(ap.models_dir())
    assert os.path.isdir(ap.bin_dir(create=True))


def test_database_models_logs_and_updates_share_the_home():
    """The modules that used to scatter files now agree on one root."""
    from src.core import database, logger, photo_censor, update_core, vo_engine
    home = Path(ap.app_home())
    assert Path(database.DB_DIR) == home
    assert Path(photo_censor.MODEL_DIR).parent.parent == home or "KNOT_CENSOR_MODEL_DIR" in os.environ
    assert Path(vo_engine.WHISPER_MODELS_DIR).parent == Path(ap.models_dir())
    assert Path(logger.get_log_dir()) == Path(ap.logs_dir())
    assert update_core.updates_dir() == Path(ap.updates_dir())


# ------------------------------------------------------------------------------- engines
def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def test_engines_are_found_in_the_app_home_bin(clean_env, monkeypatch):
    monkeypatch.setattr(ap, "dev_bin_dir", lambda: str(clean_env / "no-dev-bin"))
    exe = _touch(Path(ap.bin_dir()) / "ffmpeg.exe")
    assert ap.find_binary("ffmpeg.exe") == exe
    assert ap.writable_bin_dir() == ap.bin_dir()


def test_a_source_checkout_prefers_its_own_bin_then_the_home(clean_env, monkeypatch):
    dev = clean_env / "repo-bin"
    monkeypatch.setattr(ap, "dev_bin_dir", lambda: str(dev))
    home_exe = _touch(Path(ap.bin_dir()) / "ffprobe.exe")
    assert ap.find_binary("ffprobe.exe") == home_exe
    dev_exe = _touch(dev / "ffprobe.exe")
    assert ap.find_binary("ffprobe.exe") == dev_exe
    assert ap.writable_bin_dir() == str(dev)


def test_an_installed_build_ignores_the_repo_bin_and_the_system_path(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "Rainy.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "app"), raising=False)
    monkeypatch.setattr(ap, "dev_bin_dir", lambda: str(_touch(tmp_path / "repo" / "bin" / "ffmpeg.exe") and tmp_path / "repo" / "bin"))
    on_path = _touch(tmp_path / "pathdir" / "ffmpeg.exe")
    monkeypatch.setenv("PATH", str(Path(on_path).parent))
    assert ap.find_binary("ffmpeg.exe") is None                       # neither the repo copy nor PATH counts
    bundled = _touch(tmp_path / "app" / "bin" / "ffmpeg.exe")        # the install folder's copy (for now) does
    assert ap.find_binary("ffmpeg.exe") == bundled
    homed = _touch(Path(ap.bin_dir()) / "ffmpeg.exe")                # and the app home wins over it
    assert ap.find_binary("ffmpeg.exe") == homed
    assert ap.writable_bin_dir() == ap.bin_dir()


def test_the_system_path_is_a_source_checkout_convenience_only(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "dev_bin_dir", lambda: str(tmp_path / "none"))
    on_path = _touch(tmp_path / "pathdir" / "ffmpeg.exe")
    monkeypatch.setenv("PATH", str(Path(on_path).parent))
    assert os.path.normcase(ap.find_binary("ffmpeg.exe")) == os.path.normcase(on_path)


def test_the_resolvers_use_the_same_lookup(clean_env, monkeypatch):
    from src.core import engine_manager, ffmpeg_encoder, media_probe
    monkeypatch.setattr(ap, "dev_bin_dir", lambda: str(clean_env / "no-dev-bin"))
    monkeypatch.setenv("PATH", str(clean_env / "empty"))
    name_ff = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    name_fp = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    ff, fp = _touch(Path(ap.bin_dir()) / name_ff), _touch(Path(ap.bin_dir()) / name_fp)
    assert ffmpeg_encoder.get_ffmpeg_path() == ff and media_probe.get_ffprobe_path() == fp
    assert engine_manager.get_engine_search_dirs()[0] == ap.bin_dir()
    assert engine_manager.get_writable_bin_dir() == ap.bin_dir()
