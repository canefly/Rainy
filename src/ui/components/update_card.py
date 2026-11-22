"""The update card: version, check button, release notes, download progress, install.

One widget used by the About window, the Preferences "About & Updates" tab and the
"update required" screen, so they always agree. It only renders what UpdateService knows.
"""
import time
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, ProgressBar, PrimaryPushButton, PushButton

from ...core.update_core import UpdateStatus
from ...core.updater import APP_VERSION, UpdateService
from ..theme import ThemeManager

GREEN, RED = "#34d399", "#f87171"


def _mb(n: Optional[int]) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n else ""


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return ""
    s = max(0, time.time() - ts)
    if s < 45:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 86400:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} d ago"


class UpdateCard(QFrame):
    def __init__(self, service: Optional[UpdateService] = None, parent: Optional[QWidget] = None,
                 show_title: bool = True):
        super().__init__(parent)
        self.setObjectName("PrefCard")
        self.service = service or UpdateService.instance()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.title_lbl = QLabel("VERSION & RELEASE UPDATES", self)
        self.title_lbl.setObjectName("PrefSectionTitle")
        self.title_lbl.setVisible(show_title)
        head.addWidget(self.title_lbl)
        head.addStretch(1)
        self.ver_badge = QLabel("", self)
        head.addWidget(self.ver_badge)
        lay.addLayout(head)

        self.ver_desc = QLabel(f"Installed: v{APP_VERSION} (Native Standalone x64)", self)
        self.ver_desc.setWordWrap(True)
        self.ver_desc.setObjectName("PrefCaption")
        lay.addWidget(self.ver_desc)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)
        self.progress_lbl = QLabel("", self)
        self.progress_lbl.setObjectName("PrefCaption")
        self.progress_lbl.hide()
        lay.addWidget(self.progress_lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)
        self.check_btn = PushButton("Check for Updates", self)
        self.check_btn.setIcon(FluentIcon.SYNC)
        self.check_btn.clicked.connect(self._on_check)
        row.addWidget(self.check_btn)
        self.download_btn = PrimaryPushButton("Download Update", self)
        self.download_btn.setIcon(FluentIcon.DOWNLOAD)
        self.download_btn.clicked.connect(self.service.start_download)
        row.addWidget(self.download_btn)
        self.cancel_btn = PushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.service.cancel_download)
        row.addWidget(self.cancel_btn)
        self.install_btn = PrimaryPushButton("Install Now", self)
        self.install_btn.setIcon(FluentIcon.UPDATE)
        self.install_btn.clicked.connect(self._on_install)
        row.addWidget(self.install_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.changelog_lbl = QLabel(self)
        self.changelog_lbl.setWordWrap(True)
        self.changelog_lbl.setTextFormat(Qt.PlainText)   # release notes come from the network: never rich text
        self.changelog_lbl.setObjectName("PrefCaption")
        lay.addWidget(self.changelog_lbl)

        self.error_lbl = QLabel(self)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setTextFormat(Qt.PlainText)
        lay.addWidget(self.error_lbl)

        self.service.state_changed.connect(self.refresh_view)
        self.service.download_progress.connect(self._on_progress)
        self.refresh_view()

    # -- actions -----------------------------------------------------------------------
    def _on_check(self) -> None:
        self.service.check_for_updates()

    def _on_install(self) -> None:
        ok, message = self.service.install()
        if not ok:
            self._show_error(message)

    # -- rendering ---------------------------------------------------------------------
    def _style_badge(self, text: str, color: str) -> None:
        self.ver_badge.setText(text)
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        self.ver_badge.setStyleSheet(
            f"background-color: rgba({r}, {g}, {b}, 0.16); color: {color}; font-size: 11px; font-weight: 700;"
            f"padding: 3px 8px; border-radius: 4px; border: 1px solid rgba({r}, {g}, {b}, 0.38);"
        )

    def _show_error(self, text: str) -> None:
        self.error_lbl.setText(text)
        self.error_lbl.setVisible(bool(text))
        self.error_lbl.setStyleSheet(f"color: {RED}; font-size: 12px; font-weight: 600; background: transparent;")

    def apply_theme(self) -> None:
        self.refresh_view()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_view()  # keeps "checked 3 min ago" honest when the window is reopened

    def _on_progress(self, got: int, total: int) -> None:
        self.progress_bar.setValue(int(got * 100 / total) if total else 0)
        pct = f" ({int(got * 100 / total)}%)" if total else ""
        self.progress_lbl.setText(f"Downloading {_mb(got)} of {_mb(total)}{pct}" if total else f"Downloading {_mb(got)}")

    def refresh_view(self) -> None:
        s = self.service
        m = s.manifest
        accent = ThemeManager.get_palette().get("accent", "#38bdf8")

        # badge
        if s.is_downloading:
            self._style_badge("Downloading", accent)
        elif s.status is UpdateStatus.REQUIRED:
            self._style_badge("Update required", RED)
        elif s.status is UpdateStatus.AVAILABLE and m:
            self._style_badge(f"Update: v{m.version}", accent)
        elif s.last_checked is None:
            self._style_badge("Not checked yet", "#94a3b8")
        else:
            self._style_badge("Up to Date", GREEN)

        # description
        parts = [f"Installed v{s.current_version}"]
        if m and s.status is not UpdateStatus.UP_TO_DATE:
            parts.append(f"Latest v{m.version}")
        if s.last_checked:
            parts.append(f"Checked {_ago(s.last_checked)}")
        if s.status is UpdateStatus.REQUIRED and m:
            self.ver_desc.setText(" · ".join(parts) + f". This version is too old to keep working: install v{m.version} to continue.")
        elif s.status is UpdateStatus.UP_TO_DATE and s.last_checked:
            self.ver_desc.setText(f"You have the latest version installed (v{s.current_version}). " + parts[-1] + ".")
        else:
            self.ver_desc.setText(" · ".join(parts))

        # buttons
        self.check_btn.setEnabled(not s.is_checking and not s.is_downloading)
        self.check_btn.setText("Checking..." if s.is_checking else ("Check Again" if s.last_checked else "Check for Updates"))
        ready = s.downloaded_path is not None and s.status is not UpdateStatus.UP_TO_DATE
        can_dl = m is not None and s.status is not UpdateStatus.UP_TO_DATE and not s.is_downloading and not ready
        self.download_btn.setVisible(can_dl)
        if can_dl:
            self.download_btn.setText(f"Download Update ({_mb(m.size)})" if m.size else "Download Update")
        self.cancel_btn.setVisible(s.is_downloading)
        self.install_btn.setVisible(ready)
        self.install_btn.setText("Install Now (closes Rainy)")

        # progress
        self.progress_bar.setVisible(s.is_downloading)
        self.progress_lbl.setVisible(s.is_downloading)
        if s.is_downloading:
            got, total = s.progress
            self._on_progress(got, total)
        elif ready:
            self.progress_lbl.setText("Downloaded and verified. Ready to install.")
            self.progress_lbl.show()

        # notes + errors
        if m and s.status is not UpdateStatus.UP_TO_DATE and m.notes:
            self.changelog_lbl.setText("What's new:\n" + "\n".join(f"• {n}" for n in m.notes))
            self.changelog_lbl.show()
        else:
            self.changelog_lbl.hide()
        self._show_error(s.download_error or s.error)
