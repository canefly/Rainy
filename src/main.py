import sys
import os


def _report_startup_failure() -> None:
    """The app is a windowed program, so an error while starting used to just close it with nothing on screen.
    Write the reason to the log and tell the person where it is. Never raises."""
    import traceback
    text = traceback.format_exc()
    last_line = (text.strip().splitlines() or ["unknown error"])[-1]
    log_path = ""
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from src.core.logger import get_log_file_path, get_logger
        get_logger("startup").critical("Rainy could not start:\n%s", text)
        log_path = get_log_file_path()
    except Exception:
        pass
    try:
        import ctypes
        message = "Rainy could not start.\n\n" + last_line[:300]
        if log_path:
            message += "\n\nMore detail was saved to:\n" + log_path
        ctypes.windll.user32.MessageBoxW(0, message, "Rainy", 0x10)   # MB_ICONERROR
    except Exception:
        pass


def main():
    from PySide6.QtCore import Qt, QLoggingCategory
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import qconfig

    # Add project root to sys.path
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

    # Suppress harmless internal font fallback probing warnings (e.g. script 11 / Devanagari)
    QLoggingCategory.setFilterRules("qt.text.font.db=false")

    from src.ui.theme import disable_menu_animations
    from src.ui.window import MainWindow

    # Enable high DPI scaling for crisp 4K / 1440p rendering
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    disable_menu_animations()

    font_families = [
        "Segoe UI Variable Text", "Segoe UI", "Nirmala UI",
        "Microsoft YaHei", "sans-serif"
    ]
    font = QFont()
    font.setFamilies(font_families)
    font.setPointSize(10)
    app.setFont(font)
    qconfig.set(qconfig.fontFamilies, font_families)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        _report_startup_failure()
        sys.exit(1)
