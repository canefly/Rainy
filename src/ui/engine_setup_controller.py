"""Shows first-run engine setup as a notice in the corner: progress while it downloads, a clear message and
Try again if it can't finish, and a short "ready" when it's done. It never blocks the app."""
from typing import Optional

from PySide6.QtCore import QObject, Qt
from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition, ProgressBar, PushButton

from ..core.engine_setup import CANCELLED, DONE, FAILED, RUNNING, EngineSetupService, SetupState


class EngineSetupController(QObject):
    def __init__(self, window, service: Optional[EngineSetupService] = None):
        super().__init__(window)
        self.window = window
        self.service = service or EngineSetupService(self)
        self._bar: Optional[InfoBar] = None
        self._progress: Optional[ProgressBar] = None
        self._started = False
        self._gated = False
        self.service.state_changed.connect(self._on_state)

    # -- from the window ---------------------------------------------------------------
    def on_license_state(self, snap) -> None:
        """Once this install is approved and unlocked, fetch whatever is missing. Only once per run: if the
        person dismisses it, Preferences > Engines is still there, and the next launch tries again."""
        if snap is not None and getattr(snap, "unlocked", False) and not self._started:
            self._started = True
            self.service.start()

    def set_gated(self, gated: bool) -> None:
        self._gated = gated
        if gated:
            self._close_bar()

    def shutdown(self) -> None:
        self._close_bar()
        self.service.shutdown()

    # -- rendering ---------------------------------------------------------------------
    def _close_bar(self) -> None:
        bar, self._bar, self._progress = self._bar, None, None
        if bar is not None:
            try:
                bar.close()
            except RuntimeError:
                pass

    def _new_bar(self, icon, title: str, text: str, *, duration: int = -1, closable: bool = True) -> InfoBar:
        self._close_bar()
        bar = InfoBar.new(icon, title, text, orient=Qt.Vertical, isClosable=closable, duration=duration,
                          position=InfoBarPosition.BOTTOM_RIGHT, parent=self.window)
        try:
            bar.contentLabel.setTextFormat(Qt.PlainText)   # text from a download error is shown as plain text, always
        except AttributeError:
            pass
        bar.closedSignal.connect(self._on_bar_closed)
        self._bar = bar
        return bar

    def _on_bar_closed(self) -> None:
        self._bar = None
        self._progress = None

    def _button(self, bar: InfoBar, text: str, slot) -> PushButton:
        btn = PushButton(text, bar)
        btn.setFixedHeight(28)
        btn.clicked.connect(slot)
        bar.addWidget(btn)
        return btn

    def _on_state(self, st: SetupState) -> None:
        if self._gated:
            return
        if st.phase == RUNNING:
            self._show_running(st)
        elif st.phase == DONE:
            self._new_bar(InfoBarIcon.SUCCESS, "Rainy is ready", "The video player and tools are installed.",
                          duration=4000)
        elif st.phase == FAILED:
            bar = self._new_bar(InfoBarIcon.ERROR, "Couldn't finish setting up Rainy",
                                f"{st.message}\nThe video player and tools are needed for playback and export. "
                                "You can try again now, or later from Preferences > Engines.")
            self._button(bar, "Try again", self.service.retry)
        elif st.phase == CANCELLED:
            bar = self._new_bar(InfoBarIcon.WARNING, "Setup was cancelled",
                                "Video playback and export won't work until the video player and tools are installed.")
            self._button(bar, "Resume", self.service.retry)

    def _show_running(self, st: SetupState) -> None:
        text = st.message or "Downloading..."
        if self._bar is None or self._progress is None:
            bar = self._new_bar(InfoBarIcon.INFORMATION, "Setting up Rainy",
                                "Downloading the video player and tools Rainy needs. This only happens once.")
            self._progress = ProgressBar(bar)
            self._progress.setRange(0, 100)
            self._progress.setFixedWidth(260)
            bar.addWidget(self._progress)
            self._button(bar, "Cancel", self.service.cancel)
        self._progress.setValue(max(0, min(100, st.percent)))
        try:
            self._bar.content = f"{text}\nThis only happens once."
            self._bar._adjustText()
        except Exception:  # noqa: BLE001  (a layout hiccup must not interrupt the download)
            pass
