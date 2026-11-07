import os
import tempfile
import pytest
from src.core.media_probe import (
    get_ffprobe_path,
    get_ffprobe_status,
    probe_media_file,
    ProbedMediaItem,
)
from src.core.database import db


def test_ffprobe_resolution():
    path = get_ffprobe_path()
    assert path is not None
    assert os.path.isfile(path)
    assert "ffprobe" in os.path.basename(path).lower()

    is_avail, label, resolved_path = get_ffprobe_status()
    assert is_avail is True
    assert "ffprobe" in label.lower()
    assert os.path.isfile(resolved_path)


def test_sqlite_media_probe_cache_lifecycle():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(b"mock video data 123456789")
        temp_path = tf.name

    try:
        norm_path = os.path.normpath(temp_path)
        sz = os.path.getsize(norm_path)
        mt = os.path.getmtime(norm_path)

        # Store mock probe
        db.store_local_media_probe(
            file_path=norm_path,
            file_size=sz,
            mtime=mt,
            duration_seconds=125.5,
            duration_display="2m 05s",
            width=1920,
            height=1080,
            resolution="1080p",
            frame_rate=23.976,
            audio_channels=2,
            sample_rate=48000,
            video_codec="h264",
            audio_codec="aac"
        )

        cached = db.get_local_media_probe(norm_path, sz, mt)
        assert cached is not None
        assert cached["duration_seconds"] == 125.5
        assert cached["resolution"] == "1080p"
        assert cached["video_codec"] == "h264"

        # Verify probe_media_file returns cached item instantly
        item = probe_media_file(norm_path, use_cache=True)
        assert item is not None
        assert item.duration_seconds == 125.5
        assert item.duration_display == "2m 05s"
        assert item.resolution == "1080p"
        assert item.frame_rate == 23.976
        assert item.has_video is True
        assert item.has_audio is True

        # Invalidation test: modified size returns None
        invalidated = db.get_local_media_probe(norm_path, sz + 10, mt)
        assert invalidated is None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
