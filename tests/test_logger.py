import os
import tempfile
import pytest
from src.core.logger import (
    get_logger,
    get_log_file_path,
    get_log_size_bytes,
    get_log_size_display,
    read_recent_logs,
    clear_logs,
    export_diagnostics_bundle,
    set_log_level,
)


def test_logger_lifecycle_and_write():
    log = get_logger("TestLogger")
    assert log is not None

    test_msg = "Unique_Test_Message_12345"
    log.info(test_msg)

    recent = read_recent_logs(20)
    assert any(test_msg in line for line in recent)

    size_bytes = get_log_size_bytes()
    assert size_bytes > 0
    assert "B" in get_log_size_display() or "KB" in get_log_size_display()


def test_logger_level_filtering():
    set_log_level("WARNING")
    log = get_logger("FilterTest")

    debug_msg = "Should_Not_Appear_In_Warning_Level"
    warn_msg = "Should_Appear_In_Warning_Level"

    log.debug(debug_msg)
    log.warning(warn_msg)

    recent = read_recent_logs(20)
    assert not any(debug_msg in line for line in recent)
    assert any(warn_msg in line for line in recent)

    # Restore to INFO
    set_log_level("INFO")


def test_export_diagnostics_bundle():
    with tempfile.TemporaryDirectory() as tmpdir:
        target_zip = os.path.join(tmpdir, "test_diagnostics.zip")
        res_path = export_diagnostics_bundle(target_zip)
        assert os.path.isfile(res_path)
        assert os.path.getsize(res_path) > 0


def test_clear_logs():
    log = get_logger("ClearTest")
    log.info("Message before clear")
    assert get_log_size_bytes() > 0

    success = clear_logs()
    assert success is True
    # The only line should be the "Log files cleared by user" or empty
    recent = read_recent_logs(10)
    assert len(recent) <= 2
