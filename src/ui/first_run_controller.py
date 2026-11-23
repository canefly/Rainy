"""First-run setup, as one calm window instead of a pile of corner notices.

After this install is approved and unlocked (never before), whatever Rainy still needs is fetched in order:
the video player, the video tools, then (optional) the photo-censor model. One window explains what and why, shows
each step, and never blocks the app. While it is busy, other notices (announcements, the sharing question) are held,
so a first start is not a wall of pop-ups. A failure brings the window back with a clear message and Try again.
"""
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from qfluentwidgets import InfoBar, InfoBarPosition

from ..core.engine_setup import CANCELLED, DONE, FAILED, RUNNING, EngineSetupService, SetupState
from .components import first_run_dialog as frd
from .components.first_run_dialog import FirstRunDialog

ROW_LABELS = {"libmpv": "Video player", "ffmpeg": "Video tools", "censor": "Automatic photo censoring (optional)"}
CENSOR = "censor"
AUTO_CLOSE_MS = 4000
SETTINGS_HINT = "Settings > AI Models"


class FirstRunController(QObject):
    quiet_changed = Signal(bool)     # True while the window is busy: the main window holds other notices

    def __init__(self, window, service: Optional[EngineSetupService] = None, censor=None):
        super().__init__(window)
        self.window = window
        self.service = service or EngineSetupService(self)
        self._censor = censor
        self._dialog: Optional[FirstRunDialog] = None
        self._has_censor_row = False
        self._censor_wired = False
        self._started = False
        self._gated = False
        self._active = False
        self._hidden_by_user = False
        self._finished = False
        self._quiet = False
        self.owns_censor_download = False      # the main window skips its own censor notices while this is True
        self.service.state_changed.connect(self._on_engine_state)

    @property
    def censor(self):
        if self._censor is None:
            from ..core.photo_censor import photo_censor
            self._censor = photo_censor
        return self._censor

    # -- from the main window ------------------------------------------------------------
    def on_license_state(self, snap) -> None:
        """Once this install is approved and unlocked, get ready. Only once per run: if the person hides the window,
        Settings > Core Engines is still there, and the next launch tries again."""
        if snap is not None and getattr(snap, "unlocked", False) and not self._started:
            self._started = True
            self._begin()

    def set_gated(self, gated: bool) -> None:
        self._gated = gated
        dlg = self._dialog
        if dlg is None:
            return
        if gated:
            dlg.hide()
        elif self._active and not self._hidden_by_user:
            dlg.show()

    def shutdown(self) -> None:
        if self._dialog is not None:
            self._dialog.hide()
        self.service.shutdown()

    # -- starting --------------------------------------------------------------------------
    def _begin(self) -> None:
        missing = self.service.pending()
        censor_due = bool(self.censor.auto_download_pending())
        if not missing and not censor_due:
            return                                           # nothing to fetch: no window, no notices
        rows = [(e, ROW_LABELS.get(e, e)) for e in missing]
        if censor_due:
            rows.append((CENSOR, ROW_LABELS[CENSOR]))
        self._has_censor_row = censor_due
        self._active, self._finished = True, False
        self._open_dialog(rows)
        self._set_quiet(True)
        if missing:
            self.service.start()
        else:
            self._start_censor()

    def _open_dialog(self, rows) -> None:
        dlg = FirstRunDialog(self.window, rows)
        dlg.cancel_clicked.connect(self.service.cancel)
        dlg.retry_clicked.connect(self._retry)
        dlg.skip_clicked.connect(self._skip_censor)
        dlg.close_clicked.connect(self._close_dialog)
        dlg.hide_clicked.connect(self._on_hidden)
        self._dialog = dlg
        if not self._gated:
            dlg.show()
            self._center()

    def _center(self) -> None:
        dlg, w = self._dialog, self.window
        if dlg is None or w is None:
            return
        try:
            geo = w.frameGeometry()
            dlg.move(geo.center().x() - dlg.width() // 2, geo.center().y() - dlg.sizeHint().height() // 2)
        except Exception:  # noqa: BLE001  (placement is cosmetic)
            pass

    def _set_quiet(self, quiet: bool) -> None:
        if quiet != self._quiet:
            self._quiet = quiet
            self.quiet_changed.emit(quiet)

    def _on_hidden(self) -> None:
        self._hidden_by_user = True

    def _surface(self) -> None:
        """Bring the window back (it matters: something failed)."""
        if self._dialog is not None and not self._gated:
            self._hidden_by_user = False
            self._dialog.show()
            self._dialog.raise_()

    # -- the engines -----------------------------------------------------------------------
    def _on_engine_state(self, st: SetupState) -> None:
        dlg = self._dialog
        if dlg is None or not self._active:
            return
        queue = self.service.queue
        if st.phase == RUNNING:
            for i, key in enumerate(queue):
                if i < st.step - 1:
                    dlg.set_row(key, frd.DONE, "Done")
                elif i == st.step - 1:
                    dlg.set_row(key, frd.RUNNING, f"Downloading {st.engine_percent}%")
                else:
                    dlg.set_row(key, frd.WAITING, "Waiting")
            dlg.set_progress(st.engine_percent)
            dlg.show_running(can_skip=False)
        elif st.phase == DONE:
            self._after_engines()
        elif st.phase == FAILED:
            dlg.set_row(st.engine, frd.FAILED, "Failed")
            dlg.show_failed(st.message)
            self._set_quiet(False)
            self._surface()
        elif st.phase == CANCELLED:
            dlg.set_row(st.engine, frd.SKIPPED, "Cancelled")
            dlg.show_failed("Setup was cancelled. Video playback and export won't work until the video player and "
                            "tools are installed.", retry_label="Resume")
            self._set_quiet(False)
            self._surface()

    def _after_engines(self) -> None:
        dlg = self._dialog
        for key in self.service.queue:
            dlg.set_row(key, frd.DONE, "Done")
        if self._has_censor_row and self.censor.auto_download_pending():
            self._start_censor()
        else:
            if self._has_censor_row:
                dlg.set_row(CENSOR, frd.SKIPPED, "Skipped")
            self._finish()

    def _retry(self) -> None:
        self._set_quiet(True)
        self._hidden_by_user = False
        if self._dialog is not None:
            self._dialog.show_running(can_skip=False)
        if not self.service.retry():           # nothing left to fetch: carry on with the rest
            self._after_engines()

    # -- the optional photo-censor model ---------------------------------------------------
    def _wire_censor(self) -> None:
        if self._censor_wired:
            return
        self._censor_wired = True
        self.censor.model_download_progress.connect(self._on_censor_progress)
        self.censor.model_download_finished.connect(self._on_censor_finished)

    def _start_censor(self) -> None:
        dlg = self._dialog
        self._wire_censor()
        self.owns_censor_download = True
        dlg.set_row(CENSOR, frd.RUNNING, "Starting")
        dlg.set_progress(0)
        dlg.show_running(can_skip=True)
        if not self.censor.maybe_auto_download():
            self.owns_censor_download = False
            dlg.set_row(CENSOR, frd.SKIPPED, "Skipped")
            self._finish()

    def _skip_censor(self) -> None:
        if self.owns_censor_download:
            self.censor.cancel_download()          # remembered: it will not be offered again automatically

    def _on_censor_progress(self, pct: int, status: str) -> None:
        if self.owns_censor_download and self._dialog is not None:
            self._dialog.set_row(CENSOR, frd.RUNNING, f"Downloading {pct}%")
            self._dialog.set_progress(pct)

    def _on_censor_finished(self, outcome: str, message: str) -> None:
        if not self.owns_censor_download or self._dialog is None:
            return
        # keep "owned" until every listener has seen this signal, so the main window does not show its own notice
        QTimer.singleShot(0, self._release_censor)
        from ..core import photo_censor as pc
        if outcome == pc.DOWNLOAD_OK:
            self._dialog.set_row(CENSOR, frd.DONE, "Done")
        elif outcome == pc.DOWNLOAD_CANCELLED:
            self._dialog.set_row(CENSOR, frd.SKIPPED, "Skipped")
        else:
            self._dialog.set_row(CENSOR, frd.SKIPPED, "Not downloaded")
        self._finish()

    def _release_censor(self) -> None:
        self.owns_censor_download = False

    # -- the end ---------------------------------------------------------------------------
    def _finish(self) -> None:
        if self._finished:
            return
        self._finished, self._active = True, False
        self._set_quiet(False)
        dlg = self._dialog
        if dlg is None:
            return
        skipped = self._has_censor_row and dlg.row_state(CENSOR) != frd.DONE
        dlg.show_done(f"Automatic photo censoring was not installed. You can get it any time in {SETTINGS_HINT}."
                      if skipped else "")
        if self._hidden_by_user or self._gated:
            self._notify_ready()
        else:
            QTimer.singleShot(AUTO_CLOSE_MS, self._auto_close)

    def _auto_close(self) -> None:
        if self._finished and self._dialog is not None and self._dialog.isVisible():
            self._close_dialog()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.hide()

    def _notify_ready(self) -> None:
        """The window was hidden while it worked: say so once, quietly, when it is done."""
        try:
            InfoBar.success("Rainy is ready", "The video player and tools are installed.", parent=self.window,
                            position=InfoBarPosition.BOTTOM_RIGHT, duration=4000)
        except Exception:  # noqa: BLE001
            pass
