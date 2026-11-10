import sys
import os
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

def main():
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
    main()
