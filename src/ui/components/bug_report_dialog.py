"""'Report a problem' dialog.

Sends only when the editor presses Send, says exactly what goes along with it, and can show the log
text that would be attached (after personal paths, the Windows user name and e-mail addresses were
removed) so nothing leaves the machine unseen.
"""
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextEdit, QWidget
from qfluentwidgets import (
    CheckBox, FluentIcon, InfoBar, InfoBarPosition, LineEdit, MessageBoxBase, PushButton, TextEdit,
)

from ...core.extras_core import BUG_DESCRIPTION_MAX, BUG_TITLE_MAX
from ...core.extras_service import BugReportService


class BugReportDialog(MessageBoxBase):
    def __init__(self, parent=None, service: Optional[BugReportService] = None, log_provider=None):
        super().__init__(parent=parent)
        self.service = service or BugReportService.instance()
        self._log_provider = log_provider or BugReportService.preview_log
        self.widget.setFixedWidth(560)
        self.viewLayout.setSpacing(8)
        self.viewLayout.setContentsMargins(20, 20, 20, 12)

        title = QLabel("Report a problem", self.widget)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.viewLayout.addWidget(title)

        intro = QLabel("Tell me what went wrong and I'll look into it. Reports go straight to Cane.", self.widget)
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        self.title_edit = LineEdit(self.widget)
        self.title_edit.setPlaceholderText("Short summary (required)")
        self.title_edit.setMaxLength(BUG_TITLE_MAX)
        self.title_edit.setClearButtonEnabled(True)
        self.title_edit.textChanged.connect(self._clear_error)
        self.viewLayout.addWidget(self.title_edit)

        self.desc_edit = TextEdit(self.widget)
        self.desc_edit.setPlaceholderText("What were you doing, and what happened instead?")
        self.desc_edit.setFixedHeight(120)
        self.desc_edit.textChanged.connect(self._limit_description)
        self.viewLayout.addWidget(self.desc_edit)

        self.log_check = CheckBox("Include my recent app log (helps a lot)", self.widget)
        self.log_check.setChecked(True)
        self.log_check.toggled.connect(self._sync_preview_button)
        self.viewLayout.addWidget(self.log_check)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.preview_btn = PushButton(FluentIcon.VIEW, "Show what will be sent", self.widget)
        self.preview_btn.clicked.connect(self._toggle_preview)
        row.addWidget(self.preview_btn, 0)
        row.addStretch(1)
        self.viewLayout.addLayout(row)

        self.preview = QTextEdit(self.widget)
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(170)
        self.preview.setStyleSheet(
            "QTextEdit { background-color: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 6px;"
            " font-family: 'Consolas', 'Cascadia Code', monospace; font-size: 11px; padding: 6px; }")
        self.preview.hide()
        self.viewLayout.addWidget(self.preview)

        self.privacy = QLabel(
            "Also sent: your editor name, app version and Windows version. Personal folder names, your Windows "
            "user name and e-mail addresses are removed from the log first. Reports are kept for 6 months.", self.widget)
        self.privacy.setWordWrap(True)
        self.privacy.setStyleSheet("color: gray; font-size: 11px;")
        self.viewLayout.addWidget(self.privacy)

        self.status = QLabel("", self.widget)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #f87171;")
        self.status.hide()
        self.viewLayout.addWidget(self.status)

        self.yesButton.setText("Send report")
        self.cancelButton.setText("Cancel")
        self.service.finished.connect(self._on_finished)

    # -- state ------------------------------------------------------------------------------
    def _limit_description(self) -> None:
        text = self.desc_edit.toPlainText()
        if len(text) > BUG_DESCRIPTION_MAX:
            cursor = self.desc_edit.textCursor()
            self.desc_edit.setPlainText(text[:BUG_DESCRIPTION_MAX])
            self.desc_edit.setTextCursor(cursor)
        self._clear_error()

    def _clear_error(self) -> None:
        if not self.service.sending:
            self.status.hide()

    def _show_error(self, message: str) -> None:
        self.status.setStyleSheet("color: #f87171;")
        self.status.setText(message)
        self.status.show()

    def _sync_preview_button(self, checked: bool) -> None:
        self.preview_btn.setEnabled(checked)
        if not checked:
            self.preview.hide()
            self.preview_btn.setText("Show what will be sent")

    def _toggle_preview(self) -> None:
        if self.preview.isVisible():
            self.preview.hide()
            self.preview_btn.setText("Show what will be sent")
            return
        try:
            text = self._log_provider() or "(the log is empty)"
        except Exception:
            text = "(couldn't read the log)"
        self.preview.setPlainText(text)
        sb = self.preview.verticalScrollBar()
        sb.setValue(sb.maximum())          # the newest lines are the interesting ones
        self.preview.show()
        self.preview_btn.setText("Hide")

    # -- sending ----------------------------------------------------------------------------
    def validate(self) -> bool:
        """Called by the Send button. Never closes the dialog itself: the answer arrives later."""
        if self.service.sending:
            return False
        if not self.title_edit.text().strip():
            self._show_error("Please add a short summary first.")
            self.title_edit.setFocus()
            return False
        self.yesButton.setEnabled(False)
        self.yesButton.setText("Sending...")
        self.title_edit.setEnabled(False)
        self.desc_edit.setEnabled(False)
        self.log_check.setEnabled(False)
        self.status.hide()
        self.service.send(self.title_edit.text(), self.desc_edit.toPlainText(), self.log_check.isChecked())
        return False

    def _on_finished(self, ok: bool, message: str, report_id: str) -> None:
        if not self.isVisible():
            return
        if ok:
            suffix = f" Reference: {report_id}." if report_id else ""
            InfoBar.success("Report sent", message + suffix, parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=5000)
            self.accept()
            return
        self.yesButton.setEnabled(True)
        self.yesButton.setText("Try again")
        self.title_edit.setEnabled(True)
        self.desc_edit.setEnabled(True)
        self.log_check.setEnabled(True)
        self._show_error(message)

    def done(self, code):
        try:
            self.service.finished.disconnect(self._on_finished)
        except (RuntimeError, TypeError):
            pass
        super().done(code)
