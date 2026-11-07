import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import zipfile
import shutil
from typing import List, Optional


LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
LOG_BACKUP_COUNT = 3             # Keep 3 rotated archives (max 15 MB total)
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_dir() -> str:
    """Returns the persistent logs directory under %LOCALAPPDATA% or Documents."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        log_dir = os.path.join(local_appdata, "com.canefly.clickables", "logs")
    else:
        log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Clickables", "Logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_log_file_path() -> str:
    """Returns the absolute path to the active clickables.log file."""
    return os.path.join(get_log_dir(), "clickables.log")


_file_handler: Optional[RotatingFileHandler] = None
_console_handler: Optional[logging.StreamHandler] = None
_is_initialized: bool = False


def init_logging(level: int = logging.INFO) -> None:
    """Initializes the root Clickables logger with a thread-safe rotating file handler."""
    global _file_handler, _console_handler, _is_initialized
    if _is_initialized:
        return

    log_path = get_log_file_path()
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT, DEFAULT_DATE_FORMAT)

    root_logger = logging.getLogger("Clickables")
    root_logger.setLevel(level)
    root_logger.propagate = False

    # 1. File Handler (Rotating Ring Buffer: 5 MB x 3)
    try:
        _file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True  # open file on first log message
        )
        _file_handler.setFormatter(formatter)
        _file_handler.setLevel(level)
        root_logger.addHandler(_file_handler)
    except Exception as e:
        sys.stderr.write(f"Failed to initialize rotating file logger at {log_path}: {e}\n")

    # 2. Console Handler (for dev terminal)
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(formatter)
    _console_handler.setLevel(level)
    root_logger.addHandler(_console_handler)

    _is_initialized = True
    root_logger.info("Logging engine initialized: %s (max %d MB x %d)", log_path, LOG_MAX_BYTES // (1024 * 1024), LOG_BACKUP_COUNT)


def get_logger(sub_name: Optional[str] = None) -> logging.Logger:
    """Returns a named sub-logger under the 'Clickables' namespace."""
    if not _is_initialized:
        init_logging()
    if not sub_name:
        return logging.getLogger("Clickables")
    return logging.getLogger(f"Clickables.{sub_name}")


def set_log_level(level_name: str) -> None:
    """Dynamically updates log level (DEBUG, INFO, WARNING, ERROR)."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    lvl = level_map.get(level_name.upper(), logging.INFO)
    root_logger = logging.getLogger("Clickables")
    root_logger.setLevel(lvl)
    if _file_handler:
        _file_handler.setLevel(lvl)
    if _console_handler:
        _console_handler.setLevel(lvl)
    root_logger.info("Log level set to %s", level_name.upper())


def get_log_size_bytes() -> int:
    """Returns total size of active log file in bytes."""
    path = get_log_file_path()
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except Exception:
            return 0
    return 0


def get_log_size_display() -> str:
    """Returns human-formatted size string (e.g. '142 KB / 5 MB')."""
    sz = get_log_size_bytes()
    if sz < 1024:
        sz_str = f"{sz} B"
    elif sz < 1024 * 1024:
        sz_str = f"{sz / 1024:.1f} KB"
    else:
        sz_str = f"{sz / (1024 * 1024):.2f} MB"
    return f"{sz_str} / {LOG_MAX_BYTES // (1024 * 1024)} MB"


def read_recent_logs(max_lines: int = 200) -> List[str]:
    """Reads the last N lines from the active log file without locking or blocking."""
    path = get_log_file_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [line.rstrip("\r\n") for line in lines[-max_lines:]]
    except Exception as e:
        return [f"[ERROR] Failed to read log file: {e}"]


def clear_logs() -> bool:
    """Truncates the active log file and removes rotated archives."""
    global _file_handler
    log_dir = get_log_dir()
    path = get_log_file_path()

    # Flush file handler
    if _file_handler:
        try:
            _file_handler.flush()
            _file_handler.close()
        except Exception:
            pass

    success = True
    # Remove rotated backups
    for i in range(1, LOG_BACKUP_COUNT + 1):
        bak_path = f"{path}.{i}"
        if os.path.isfile(bak_path):
            try:
                os.remove(bak_path)
            except Exception:
                success = False

    # Truncate primary log
    if os.path.isfile(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            success = False

    # Re-open file handler
    if _file_handler:
        try:
            _file_handler.stream = _file_handler._open()
        except Exception:
            pass

    log = get_logger("System")
    log.info("Log files cleared by user")
    return success


def export_diagnostics_bundle(destination_zip: Optional[str] = None) -> str:
    """
    Bundles active and rotated log files into a single zip archive for troubleshooting.
    Returns the path to the created zip file.
    """
    log_dir = get_log_dir()
    primary_log = get_log_file_path()

    if not destination_zip:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        destination_zip = os.path.join(desktop, "Clickables_Diagnostics.zip")

    with zipfile.ZipFile(destination_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if os.path.isfile(primary_log):
            zf.write(primary_log, arcname="clickables.log")
        for i in range(1, LOG_BACKUP_COUNT + 1):
            bak = f"{primary_log}.{i}"
            if os.path.isfile(bak):
                zf.write(bak, arcname=f"clickables.log.{i}")

    return destination_zip
