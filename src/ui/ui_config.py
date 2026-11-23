"""Keeps the UI library's settings file in the app folder instead of next to the program.

qfluentwidgets saves its theme settings to a relative ``config/config.json``, i.e. into the folder the app was
started from. For an install in Program Files that folder is read-only for a normal user, the save raised, and the
app quit at startup with no message. The file now lives in the app folder (see app_paths), and a failed save is
logged and skipped, because losing a theme preference must never stop the app.
"""
from pathlib import Path

from ..core import app_paths
from ..core.logger import get_logger

logger = get_logger("ui_config")
_installed = False


def install() -> None:
    global _installed
    from qfluentwidgets import qconfig
    qconfig.file = Path(app_paths.ui_config_file())
    if _installed:
        return
    _installed = True
    original = qconfig.save

    def safe_save(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except OSError as e:
            logger.warning("Could not save UI settings to %s: %s", qconfig.file, e)

    qconfig.save = safe_save
