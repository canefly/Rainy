import os
import tempfile
import pytest
from src.core.ffmpeg_encoder import (
    get_ffmpeg_path,
    get_ffmpeg_status,
    detect_available_encoders,
    get_recommended_encoder,
    get_hardware_status_summary,
    get_encoder_catalog,
    send_to_recycle_bin,
    is_file_locked,
    TranscodeConfig,
    TranscodeProgress,
    perform_atomic_replace,
)


def test_ffmpeg_binary_discovery():
    path = get_ffmpeg_path()
    assert path is not None
    assert os.path.isfile(path)
    assert "ffmpeg" in os.path.basename(path).lower()

    is_avail, ver, p = get_ffmpeg_status()
    assert is_avail is True
    assert p == path
    assert len(ver) > 0


def test_encoder_probe():
    encoders = detect_available_encoders()
    assert isinstance(encoders, dict)
    assert "libx264" in encoders
    assert encoders["libx264"] is True

    rec = get_recommended_encoder(prefer_hevc=False)
    assert rec in ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]

    rec_hevc = get_recommended_encoder(prefer_hevc=True)
    assert rec_hevc in ["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265", "libx264"]


def test_transcode_config_defaults():
    cfg = TranscodeConfig()
    assert cfg.target_fps == 0.0
    assert cfg.encoder == "auto"
    assert cfg.crf == 18
    assert cfg.audio_mode == "copy"
    assert cfg.replace_original is True


def test_recycle_bin_and_lock_check():
    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as f:
        f.write(b"clickables_test_data")
        temp_path = f.name

    try:
        assert os.path.exists(temp_path)
        assert is_file_locked(temp_path) is False

        # Test recycle bin
        success = send_to_recycle_bin(temp_path)
        assert success is True
        assert not os.path.exists(temp_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def test_atomic_replace_protocol():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f_src:
        f_src.write(b"original_video_data_123456789")
        src_path = f_src.name

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f_temp:
        f_temp.write(b"transcoded_cfr_data_987654321")
        tmp_path = f_temp.name

    try:
        assert os.path.exists(src_path)
        assert os.path.exists(tmp_path)

        success, msg = perform_atomic_replace(src_path, tmp_path)
        assert success is True
        assert os.path.exists(src_path)
        assert not os.path.exists(tmp_path)

        # Check content is the new content
        with open(src_path, "rb") as f_res:
            assert f_res.read() == b"transcoded_cfr_data_987654321"

    finally:
        if os.path.exists(src_path):
            send_to_recycle_bin(src_path)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_encoder_catalog_and_hardware_status():
    status_headline, best_enc = get_hardware_status_summary()
    assert isinstance(status_headline, str)
    assert len(status_headline) > 0
    assert isinstance(best_enc, str)

    catalog = get_encoder_catalog()
    assert len(catalog) >= 8

    encoder_ids = [c[0] for c in catalog]
    assert "h264_nvenc" in encoder_ids
    assert "hevc_nvenc" in encoder_ids
    assert "h264_amf" in encoder_ids
    assert "hevc_amf" in encoder_ids
    assert "h264_qsv" in encoder_ids
    assert "libx264" in encoder_ids

    # Check structure
    for enc_id, label, is_avail, note in catalog:
        assert isinstance(enc_id, str)
        assert isinstance(label, str)
        assert isinstance(is_avail, bool)
        assert isinstance(note, str)
