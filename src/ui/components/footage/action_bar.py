from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel
from qfluentwidgets import PushButton, PrimaryPushButton, ToolButton, FluentIcon

from ...theme import ThemeManager


class BulkActionBar(QFrame):
    """
    Contextual action bar displayed when one or more items are selected in the Footage Library.
    Strictly no emojis.
    """
    reencode_requested = Signal()
    reveal_requested = Signal()
    open_requested = Signal()
    copy_path_requested = Signal()
    properties_requested = Signal()
    unlink_requested = Signal()
    clear_selection_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FootageBulkActionBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(6)

        self.count_lbl = QLabel("0 selected", self)
        self.count_lbl.setObjectName("BulkActionCount")
        layout.addWidget(self.count_lbl, 0, Qt.AlignVCenter)

        self.clear_sel_btn = ToolButton(FluentIcon.CLOSE, self)
        self.clear_sel_btn.setFixedSize(24, 24)
        self.clear_sel_btn.setToolTip("Clear Selection")
        self.clear_sel_btn.clicked.connect(self.clear_selection_requested.emit)
        layout.addWidget(self.clear_sel_btn, 0, Qt.AlignVCenter)

        layout.addStretch(1)

        self.reencode_btn = PrimaryPushButton("Re-encode Clips", self)
        self.reencode_btn.setIcon(FluentIcon.MOVIE)
        self.reencode_btn.setFixedHeight(28)
        self.reencode_btn.setToolTip("Convert VFR to CFR or compress selected clips in the background Render Queue")
        self.reencode_btn.clicked.connect(self.reencode_requested.emit)
        layout.addWidget(self.reencode_btn, 0, Qt.AlignVCenter)

        self.reveal_btn = PushButton("Show in Explorer", self)
        self.reveal_btn.setIcon(FluentIcon.FOLDER)
        self.reveal_btn.setFixedHeight(28)
        self.reveal_btn.clicked.connect(self.reveal_requested.emit)
        layout.addWidget(self.reveal_btn, 0, Qt.AlignVCenter)

        self.open_btn = PushButton("Open", self)
        self.open_btn.setIcon(FluentIcon.PLAY)
        self.open_btn.setFixedHeight(28)
        self.open_btn.clicked.connect(self.open_requested.emit)
        layout.addWidget(self.open_btn, 0, Qt.AlignVCenter)

        self.copy_btn = ToolButton(FluentIcon.COPY, self)
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.setToolTip("Copy Local Path(s)")
        self.copy_btn.clicked.connect(self.copy_path_requested.emit)
        layout.addWidget(self.copy_btn, 0, Qt.AlignVCenter)

        self.prop_btn = ToolButton(FluentIcon.INFO, self)
        self.prop_btn.setFixedSize(28, 28)
        self.prop_btn.setToolTip("Media Properties")
        self.prop_btn.clicked.connect(self.properties_requested.emit)
        layout.addWidget(self.prop_btn, 0, Qt.AlignVCenter)

        self.unlink_btn = ToolButton(FluentIcon.DELETE, self)
        self.unlink_btn.setFixedSize(28, 28)
        self.unlink_btn.setToolTip("Unlink Selected")
        self.unlink_btn.clicked.connect(self.unlink_requested.emit)
        layout.addWidget(self.unlink_btn, 0, Qt.AlignVCenter)

        self.apply_theme()
        self.hide()

    def set_selection_count(self, count: int, online_count: int):
        if count <= 0:
            self.hide()
            return
        self.count_lbl.setText(f"{count} selected")
        has_online = online_count > 0
        is_single_online = (count == 1 and online_count == 1)

        self.reencode_btn.setText("Re-encode Clip" if count == 1 else f"Re-encode Clips ({online_count})")
        self.reencode_btn.setEnabled(has_online)

        # Explicitly disable Show in Explorer when multiple files are selected per Cane's instruction
        self.reveal_btn.setEnabled(is_single_online)
        self.reveal_btn.setToolTip(
            "Select a single clip to reveal in File Explorer"
            if count > 1
            else "Highlight this file in Windows File Explorer"
        )

        self.open_btn.setEnabled(is_single_online)
        self.copy_btn.setEnabled(has_online)
        self.prop_btn.setEnabled(count == 1)
        self.show()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        accent = pal.get("accent", "#38bdf8")
        # Soft tint, no outline: reads as a mode of the toolbar rather than another box
        self.setStyleSheet("""
            QFrame#FootageBulkActionBar {
                background-color: rgba(96, 165, 250, 0.10);
                border: none;
                border-radius: 6px;
            }
        """)
        self.count_lbl.setStyleSheet(
            f"color: {accent}; font-size: 12px; font-weight: 600; border: none; background: transparent;"
        )
