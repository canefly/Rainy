import os
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView
)
from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import (
    PushButton, ToolButton, FluentIcon, Dialog, SmoothScrollArea
)
from ..theme import ThemeManager
from ...core.database import db

class DataVaultWindow(FramelessWindow):
    """Independent modeless desktop window for Data Vault & Storage management."""
    document_opened = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DataVaultWindow")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("Data Vault & Storage - Clickables Tools")
        self.resize(760, 540)
        self.setMinimumSize(640, 440)

        # Set Window Icon
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # TitleBar setup
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setFixedHeight(40)
        self.titleBar.setObjectName("VaultHeader")

        # Main Layout offset by 40px for titlebar
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 40, 0, 0)
        root_layout.setSpacing(0)

        body = QWidget(self)
        b_layout = QVBoxLayout(body)
        b_layout.setContentsMargins(24, 20, 24, 20)
        b_layout.setSpacing(14)

        # Title Section
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("Data Vault & Storage", body)
        title.setObjectName("PrefTabTitle")
        title_box.addWidget(title)

        sub = QLabel("Inspect lifetime parsing statistics, manage document persistence, and open cached scripts.", body)
        sub.setStyleSheet("color: #94a3b8; font-size: 11px;")
        title_box.addWidget(sub)
        b_layout.addLayout(title_box)

        # Lifetime Stats Summary Box
        self.stats_box = QFrame(body)
        self.stats_box.setObjectName("PrefCard")
        self.sb_layout = QHBoxLayout(self.stats_box)
        self.sb_layout.setContentsMargins(16, 12, 16, 12)
        b_layout.addWidget(self.stats_box)

        # Reset Stats Button Row
        btn_row = QHBoxLayout()
        reset_btn = PushButton("Reset Lifetime Statistics", body)
        reset_btn.setFixedWidth(190)
        reset_btn.clicked.connect(self._reset_stats_clicked)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        b_layout.addLayout(btn_row)

        # Active Scripts Table Section
        table_lbl = QLabel("ACTIVE SCRIPTS IN VAULT", body)
        table_lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: 1px; margin-top: 4px;")
        b_layout.addWidget(table_lbl)

        self.scripts_table = QTableWidget(0, 3, body)
        self.scripts_table.setHorizontalHeaderLabels(["Name", "Opened", "Actions"])
        self.scripts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.scripts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.scripts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.scripts_table.verticalHeader().setVisible(False)
        b_layout.addWidget(self.scripts_table, 1)

        root_layout.addWidget(body, 1)

        # Footer
        footer = QFrame(self)
        footer.setFixedHeight(48)
        footer.setStyleSheet("background-color: transparent; border-top: 1px solid rgba(255, 255, 255, 0.08);")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(16, 0, 16, 0)
        f_layout.addStretch(1)

        done_btn = PushButton("Close", footer)
        done_btn.setFixedWidth(80)
        done_btn.clicked.connect(self.close)
        f_layout.addWidget(done_btn)
        root_layout.addWidget(footer)

        self.refresh()

    def refresh(self):
        """Refreshes stats and active scripts table from SQLite."""
        self._populate_stats()
        self._populate_scripts_table()

    def _populate_stats(self):
        while self.sb_layout.count() > 0:
            item = self.sb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        stats = db.get_stats()

        def _format_time(sec: float) -> str:
            m = int(sec // 60)
            s = int(sec % 60)
            return f"{m}m {s}s"

        items = [
            ("PARSE TIME", _format_time(getattr(stats, "lifetime_seconds", 0))),
            ("TOTAL CUTS", str(getattr(stats, "total_aroll_count", 0))),
            ("DURATION", _format_time(getattr(stats, "total_duration_logged", 0))),
        ]
        for label, val in items:
            v_box = QVBoxLayout()
            v_box.setSpacing(2)
            l_lbl = QLabel(label, self.stats_box)
            l_lbl.setStyleSheet("color: #64748b; font-size: 9px; font-weight: 800; letter-spacing: 1px;")
            v_lbl = QLabel(val, self.stats_box)
            v_lbl.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 800; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;")
            v_box.addWidget(l_lbl)
            v_box.addWidget(v_lbl)
            self.sb_layout.addLayout(v_box)
            if label != "DURATION":
                self.sb_layout.addStretch(1)

    def _populate_scripts_table(self):
        recent_files = db.get_recent_files(25)
        self.scripts_table.setRowCount(len(recent_files))
        for row, rf in enumerate(recent_files):
            name_item = QTableWidgetItem(rf.get("name", "Untitled"))
            name_item.setToolTip(rf.get("path", ""))
            self.scripts_table.setItem(row, 0, name_item)

            date_str = rf.get("last_opened", "")[:10]
            date_item = QTableWidgetItem(date_str)
            self.scripts_table.setItem(row, 1, date_item)

            open_btn = ToolButton(FluentIcon.FOLDER, self)
            open_btn.setToolTip("Open script in workspace")
            open_btn.clicked.connect(lambda _, p=rf.get("path", ""): self._on_script_open_clicked(p))
            self.scripts_table.setCellWidget(row, 2, open_btn)

    def _on_script_open_clicked(self, path: str):
        if path and os.path.exists(path):
            self.close()
            self.document_opened.emit(path)

    def _reset_stats_clicked(self):
        w = Dialog("Reset Statistics", "Are you sure you want to reset lifetime parse statistics? This cannot be undone.", self)
        if w.exec():
            db.reset_stats()
            self.refresh()

    def paintEvent(self, e):
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e):
        super().moveEvent(e)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        ThemeManager.enable_window_backdrop(self)

    def closeEvent(self, event):
        self.hide()
        event.ignore()
