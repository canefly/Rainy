"""Window glue for announcements, the usage-sharing question and the bug report dialog.

Announcements appear as notices at the top of the window (plain text only, closed with the X, which
also marks them read). Nothing is shown while the app is locked. The usage-sharing question is asked
once, as a quiet notice with two buttons; closing it counts as "no thanks" so nobody is nagged.
"""
from typing import Dict, Optional

from PySide6.QtCore import QObject, Qt, QTimer
from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition, PushButton, PrimaryPushButton

from ..core.extras_core import Announcement

ASK_DELAY_MS = 3000
_ICONS = {"info": InfoBarIcon.INFORMATION, "warning": InfoBarIcon.WARNING, "critical": InfoBarIcon.ERROR}


class ExtrasController(QObject):
    def __init__(self, window, announcements, analytics, app_version: str, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.announcements = announcements
        self.analytics = analytics
        self.app_version = app_version
        self._bars: Dict[str, InfoBar] = {}
        self._ask_bar: Optional[InfoBar] = None
        self._suppress = False          # True while we close notices ourselves (that is not "read")
        self._unlocked = False
        self._gated = True
        self._started_session = False
        self._ask_timer = QTimer(self)
        self._ask_timer.setSingleShot(True)
        self._ask_timer.timeout.connect(self._ask_about_sharing)
        self.announcements.changed.connect(self._sync_bars)

    # -- window events -----------------------------------------------------------------------
    def on_license_state(self, snap) -> None:
        was = self._unlocked
        self._unlocked = bool(snap.unlocked)
        if self._unlocked and not was:
            self.announcements.refresh()
            self.announcements.start_periodic()
            if not self._started_session:
                self._started_session = True
                self.analytics.track("app_start", version=self.app_version)
            if not self.analytics.asked:
                self._ask_timer.start(ASK_DELAY_MS)
        self._sync_bars()

    def set_gated(self, gated: bool) -> None:
        self._gated = bool(gated)
        self._sync_bars()
        if self._gated:
            self._ask_timer.stop()
            self._close_ask_bar()
        elif self._unlocked and not self.analytics.asked:
            self._ask_timer.start(ASK_DELAY_MS)

    def open_bug_report(self) -> None:
        from .components.bug_report_dialog import BugReportDialog
        BugReportDialog(self.window).exec()

    def shutdown(self) -> None:
        self._ask_timer.stop()
        try:
            self.analytics.stop()
            self.analytics.flush_on_exit()
        except Exception:
            pass

    # -- announcements -----------------------------------------------------------------------
    def _sync_bars(self, items=None) -> None:
        if items is None:
            items = self.announcements.items
        wanted: Dict[str, Announcement] = {} if (self._gated or not self._unlocked) else {a.id: a for a in items}
        self._suppress = True
        try:
            for id_ in [i for i in self._bars if i not in wanted]:
                self._bars.pop(id_).close()
        finally:
            self._suppress = False
        for a in sorted(wanted.values(), key=lambda a: (a.created_at, a.id)):   # oldest first, so the newest ends up on top
            if a.id not in self._bars:
                self._bars[a.id] = self._make_bar(a)

    def _make_bar(self, a: Announcement) -> InfoBar:
        bar = InfoBar.new(_ICONS.get(a.severity, InfoBarIcon.INFORMATION), a.title, a.body, orient=Qt.Vertical,
                          isClosable=True, duration=-1, position=InfoBarPosition.TOP, parent=self.window)
        bar.titleLabel.setTextFormat(Qt.PlainText)      # the text is the owner's, but it is still only ever text
        bar.contentLabel.setTextFormat(Qt.PlainText)
        bar.contentLabel.setVisible(bool(a.body))
        bar.setProperty("announcement_id", a.id)
        bar.closedSignal.connect(lambda id_=a.id: self._on_bar_closed(id_))
        return bar

    def _on_bar_closed(self, announcement_id: str) -> None:
        if self._suppress:
            return
        self._bars.pop(announcement_id, None)
        self.announcements.mark_read(announcement_id)

    # -- the one-time question ---------------------------------------------------------------
    def _ask_about_sharing(self) -> None:
        if self.analytics.asked or self._gated or not self._unlocked or self._ask_bar is not None:
            return
        # Built first and shown last: the bar is placed using its size at the moment it is shown, so the
        # buttons must already be in it or it would grow past the bottom edge of the window.
        bar = InfoBar(
            InfoBarIcon.INFORMATION, "Help improve Rainy?",
            "Share which features you use and what kinds of crashes happen.\n"
            "Never your scripts, footage, file names or IP address.\n"
            "You can change this any time in Settings.",
            Qt.Vertical, True, -1, InfoBarPosition.BOTTOM_RIGHT, self.window)
        yes = PrimaryPushButton("Share anonymously", bar)
        no = PushButton("No thanks", bar)
        yes.clicked.connect(lambda: self._answer(True))
        no.clicked.connect(lambda: self._answer(False))
        bar.addWidget(yes)
        bar.addWidget(no)
        bar.titleLabel.setTextFormat(Qt.PlainText)
        bar.contentLabel.setTextFormat(Qt.PlainText)
        bar.closedSignal.connect(self._on_ask_closed)
        self._ask_bar = bar
        bar.show()

    def _answer(self, share: bool) -> None:
        self.analytics.set_enabled(share)
        if share:
            self.analytics.track("app_start", version=self.app_version)
        self._close_ask_bar()

    def _on_ask_closed(self) -> None:
        self._ask_bar = None
        if not self.analytics.asked:
            self.analytics.set_enabled(False)      # closing it without answering means no

    def _close_ask_bar(self) -> None:
        bar, self._ask_bar = self._ask_bar, None
        if bar is not None:
            try:
                bar.closedSignal.disconnect(self._on_ask_closed)
            except (RuntimeError, TypeError):
                pass
            bar.close()
