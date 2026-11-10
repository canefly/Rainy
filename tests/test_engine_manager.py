import os
import pytest
from src.core.engine_manager import (
    SUPPORTED_ENGINES, get_engine_status, get_mpv_status,
    is_mpv_available, get_writable_bin_dir
)

def test_supported_engines_metadata():
    assert "libmpv" in SUPPORTED_ENGINES
    assert "ffmpeg" in SUPPORTED_ENGINES
    assert "whisper" in SUPPORTED_ENGINES

    mpv_spec = SUPPORTED_ENGINES["libmpv"]
    assert "shinchiro" in mpv_spec.repo_url
    assert mpv_spec.primary_binary in ("libmpv-2.dll", "mpv-2.dll")

    ffmpeg_spec = SUPPORTED_ENGINES["ffmpeg"]
    assert "BtbN" in ffmpeg_spec.repo_url

def test_mpv_uninstalled_status():
    # Since we have not yet installed mpv-2.dll per user request, it must safely report Not Installed
    avail, status_lbl, path, hw = get_mpv_status()
    # It must not crash
    assert isinstance(avail, bool)
    assert isinstance(status_lbl, str)
    assert isinstance(path, str)

def test_get_writable_bin_dir():
    bin_dir = get_writable_bin_dir()
    assert os.path.isdir(bin_dir) or os.path.isabs(bin_dir)

def test_engine_status_dict():
    status = get_engine_status("libmpv")
    assert "spec" in status
    assert "installed" in status
    assert "status_label" in status
    assert "hw_info" in status
