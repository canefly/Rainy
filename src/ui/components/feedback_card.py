"""Preferences card for feedback and privacy: report a problem, and the usage-sharing choice.

Usage sharing is opt-in and off until the editor turns it on. The wording below says exactly what is
(and isn't) sent, because that is the whole basis for asking.
"""
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, PushButton, SwitchButton

from ...core.extras_service import AnalyticsService

SHARING_EXPLANATION = (
    "When this is on, Rainy sends which features get used (for example \"exported XML\"), the app version and, if the app "
    "crashes, the kind of error and which part of the app it happened in. It never sends your scripts, footage, project or file "
    "names, error messages or your IP address. It is tied to this install's ID, not your name, and you can turn it off any time: "
    "switching it off also deletes what was sent before."
)


class FeedbackCard(QFrame):
    def __init__(self, analytics: Optional[AnalyticsService] = None, parent: Optional[QWidget] = None,
                 on_report=None):
        super().__init__(parent)
        self.setObjectName("PrefCard")
        self.analytics = analytics or AnalyticsService.instance()
        self._on_report = on_report

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        self.title_lbl = QLabel("FEEDBACK & PRIVACY", self)
        self.title_lbl.setObjectName("PrefSectionTitle")
        lay.addWidget(self.title_lbl)

        report_row = QHBoxLayout()
        report_row.setContentsMargins(0, 0, 0, 0)
        report_row.setSpacing(10)
        report_text = QLabel("Something broken or confusing? Send a report straight to Cane.", self)
        report_text.setWordWrap(True)
        report_text.setObjectName("PrefBodyText")
        report_row.addWidget(report_text, 1)
        self.report_btn = PushButton("Report a problem...", self)
        self.report_btn.setIcon(FluentIcon.FEEDBACK)
        self.report_btn.clicked.connect(self._report_clicked)
        report_row.addWidget(self.report_btn, 0, Qt.AlignVCenter)
        lay.addLayout(report_row)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(128, 128, 128, 0.25); border: none;")
        lay.addWidget(sep)

        share_row = QHBoxLayout()
        share_row.setContentsMargins(0, 0, 0, 0)
        share_row.setSpacing(10)
        share_lbl = QLabel("Share anonymous usage", self)
        share_lbl.setObjectName("PrefBodyText")
        share_row.addWidget(share_lbl, 1)
        self.share_switch = SwitchButton(self)
        self.share_switch.setOnText("On")
        self.share_switch.setOffText("Off")
        self.share_switch.setChecked(self.analytics.enabled)
        self.share_switch.checkedChanged.connect(self._share_toggled)
        share_row.addWidget(self.share_switch, 0, Qt.AlignVCenter)
        lay.addLayout(share_row)

        self.explain_lbl = QLabel(SHARING_EXPLANATION, self)
        self.explain_lbl.setWordWrap(True)
        self.explain_lbl.setObjectName("PrefCaption")
        lay.addWidget(self.explain_lbl)

        self.analytics.enabled_changed.connect(self._sync_switch)

    def _share_toggled(self, on: bool) -> None:
        if on != self.analytics.enabled:
            self.analytics.set_enabled(on)

    def _sync_switch(self, on: bool) -> None:
        if self.share_switch.isChecked() != on:
            self.share_switch.blockSignals(True)
            self.share_switch.setChecked(on)
            self.share_switch.blockSignals(False)

    def _report_clicked(self) -> None:
        if self._on_report is not None:
            self._on_report()
            return
        from .bug_report_dialog import BugReportDialog
        BugReportDialog(self.window()).exec()
