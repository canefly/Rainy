import os
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar, QSizePolicy
)
from qfluentwidgets import (
    PushButton, ToolButton, TransparentToolButton, HyperlinkButton, CheckBox, FluentIcon, SmoothScrollArea
)

from ...theme import ThemeManager
from ....core.render_queue import render_queue, RenderJob
from ....core.unfurl_engine import format_bytes_display


def _format_eta(sec: float) -> str:
    if sec <= 0:
        return ""
    mins = int(sec // 60)
    secs = int(sec % 60)
    return f"ETA {mins}:{secs:02d}"


# Tone -> dot color for the dock's status items
_TONES = {
    "ok": "#34d399",
    "warn": "#fbbf24",
    "bad": "#f87171",
    "busy": "#60a5fa",
    "idle": "#6f737b",
}


class _DockDot(QLabel):
    """Small flat status dot (no pulse) used by every dock item so they read as one set."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(7, 7)
        self.set_tone("idle")

    def set_tone(self, tone: str):
        self._tone = tone
        self.setStyleSheet(f"background-color: {_TONES.get(tone, _TONES['idle'])}; border-radius: 3px; border: none;")

    def set_color(self, color: str, _halo: Optional[str] = None):
        # Back-compat with StatusDot.set_color callers
        self.setStyleSheet(f"background-color: {color}; border-radius: 3px; border: none;")


class QueueStrip(QFrame):
    """
    Shared activity dock pinned to the bottom of the Footage & Media window.
    The collapsed bar is identical on every tab: render queue, voiceover, and footage status.
    Expanding it (caret) shows the render job list with its controls, plus a transcription summary.
    Strictly no emojis.
    """
    open_transcribe_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("RenderQueueStrip")
        self._expanded = False
        self._vo_detail = "Voiceover not set."
        self._text_col = ThemeManager.get_palette().get("text", "#ffffff")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # ---------------------------------------------------------------
        # 1. Collapsed bar: three status items + caret
        # ---------------------------------------------------------------
        self.bar = QWidget(self)
        self.bar.setObjectName("ActivityDockBar")
        self.bar.setFixedHeight(38)
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(24, 0, 12, 0)
        bar_layout.setSpacing(8)

        self.status_dot = _DockDot(self.bar)
        bar_layout.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        self.summary_lbl = QLabel("Render idle · 0 jobs", self.bar)
        self.summary_lbl.setObjectName("QueueStripSummary")
        self.summary_lbl.setTextFormat(Qt.RichText)
        bar_layout.addWidget(self.summary_lbl, 0, Qt.AlignVCenter)

        self.active_bar = QProgressBar(self.bar)
        self.active_bar.setRange(0, 100)
        self.active_bar.setValue(0)
        self.active_bar.setFixedWidth(90)
        self.active_bar.setFixedHeight(4)
        self.active_bar.setTextVisible(False)
        self.active_bar.hide()
        bar_layout.addWidget(self.active_bar, 0, Qt.AlignVCenter)

        bar_layout.addSpacing(16)
        self.vo_dot = _DockDot(self.bar)
        bar_layout.addWidget(self.vo_dot, 0, Qt.AlignVCenter)
        self.vo_lbl = QLabel("Voiceover not set", self.bar)
        self.vo_lbl.setObjectName("DockVoLabel")
        self.vo_lbl.setTextFormat(Qt.RichText)
        self.vo_lbl.setCursor(Qt.PointingHandCursor)
        self.vo_lbl.setToolTip("Open Transcribe")
        self.vo_lbl.mousePressEvent = lambda e: self.open_transcribe_requested.emit()
        bar_layout.addWidget(self.vo_lbl, 0, Qt.AlignVCenter)

        bar_layout.addSpacing(16)
        self.footage_dot = _DockDot(self.bar)
        bar_layout.addWidget(self.footage_dot, 0, Qt.AlignVCenter)
        self.footage_lbl = QLabel("Footage not scanned", self.bar)
        self.footage_lbl.setObjectName("DockFootageLabel")
        self.footage_lbl.setTextFormat(Qt.RichText)
        bar_layout.addWidget(self.footage_lbl, 0, Qt.AlignVCenter)

        bar_layout.addStretch(1)

        self.expand_btn = TransparentToolButton(FluentIcon.UP, self.bar)
        self.expand_btn.setFixedSize(28, 28)
        self.expand_btn.setToolTip("Show activity")
        self.expand_btn.clicked.connect(self.toggle_expanded)
        bar_layout.addWidget(self.expand_btn, 0, Qt.AlignVCenter)

        self.main_layout.addWidget(self.bar)

        # ---------------------------------------------------------------
        # 2. Expanded panel: render queue (left) | transcription (right)
        # ---------------------------------------------------------------
        self.drawer = QFrame(self)
        self.drawer.setObjectName("ActivityDockDrawer")
        drawer_layout = QHBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(0)

        # Left: render queue
        self.render_col = QWidget(self.drawer)
        rc = QVBoxLayout(self.render_col)
        rc.setContentsMargins(24, 12, 16, 12)
        rc.setSpacing(8)

        rc_head = QHBoxLayout()
        rc_head.setContentsMargins(0, 0, 0, 0)
        rc_head.setSpacing(8)
        self.render_title = QLabel("RENDER QUEUE", self.render_col)
        rc_head.addWidget(self.render_title, 1, Qt.AlignVCenter)

        self.pause_cb = CheckBox("Pause after current", self.render_col)
        self.pause_cb.setChecked(render_queue.pause_after_current)
        self.pause_cb.stateChanged.connect(
            lambda st: render_queue.set_pause_after_current(st == Qt.Checked.value or st == True)
        )
        rc_head.addWidget(self.pause_cb, 0, Qt.AlignVCenter)

        self.retry_failed_btn = HyperlinkButton("", "Retry failed", self.render_col)
        self.retry_failed_btn.setFixedHeight(26)
        self.retry_failed_btn.clicked.connect(render_queue.retry_all_failed)
        self.retry_failed_btn.hide()
        rc_head.addWidget(self.retry_failed_btn, 0, Qt.AlignVCenter)

        self.clear_btn = HyperlinkButton("", "Clear finished", self.render_col)
        self.clear_btn.setFixedHeight(26)
        self.clear_btn.clicked.connect(render_queue.clear_finished)
        rc_head.addWidget(self.clear_btn, 0, Qt.AlignVCenter)
        rc.addLayout(rc_head)

        self.scroll = SmoothScrollArea(self.render_col)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(120)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.jobs_container = QWidget(self.scroll)
        self.jobs_container.setStyleSheet("background: transparent;")
        self.jobs_layout = QVBoxLayout(self.jobs_container)
        self.jobs_layout.setContentsMargins(0, 0, 4, 0)
        self.jobs_layout.setSpacing(0)
        self.jobs_layout.addStretch(1)

        self.scroll.setWidget(self.jobs_container)
        rc.addWidget(self.scroll)
        drawer_layout.addWidget(self.render_col, 3)

        self.drawer_split = QFrame(self.drawer)
        self.drawer_split.setFixedWidth(1)
        drawer_layout.addWidget(self.drawer_split)

        # Right: transcription summary
        self.vo_col = QWidget(self.drawer)
        vc = QVBoxLayout(self.vo_col)
        vc.setContentsMargins(16, 12, 24, 12)
        vc.setSpacing(8)
        self.vo_title = QLabel("TRANSCRIPTION", self.vo_col)
        vc.addWidget(self.vo_title)
        self.vo_detail_lbl = QLabel(self._vo_detail, self.vo_col)
        self.vo_detail_lbl.setWordWrap(True)
        vc.addWidget(self.vo_detail_lbl)
        self.open_vo_btn = HyperlinkButton("", "Open Transcribe", self.vo_col)
        self.open_vo_btn.setFixedHeight(26)
        self.open_vo_btn.clicked.connect(self.open_transcribe_requested.emit)
        vc.addWidget(self.open_vo_btn, 0, Qt.AlignLeft)
        vc.addStretch(1)
        drawer_layout.addWidget(self.vo_col, 2)

        self.drawer.hide()
        self.main_layout.addWidget(self.drawer)

        # Wire live RenderQueue signals
        render_queue.queue_changed.connect(self.refresh)
        render_queue.job_progress.connect(self._on_live_progress)

        self.apply_theme()
        self.refresh()

    # ------------------------------------------------------------------
    # Status items fed by the dialog
    # ------------------------------------------------------------------
    def _state(self, text: str) -> str:
        """Emphasised state word inside a dock item (rich text; QSS can't style <b>)."""
        return f"<span style='color:{self._text_col}; font-weight:600'>{text}</span>"

    def _item_html(self, name: str, state: str) -> str:
        return f"{name} {self._state(state)}"

    def set_vo_status(self, name: str, state: str, tone: str = "idle", detail: str = ""):
        self.vo_dot.set_tone(tone)
        self.vo_lbl.setText(self._item_html(name, state))
        if detail:
            self._vo_detail = detail
        else:
            self._vo_detail = {
                "ok": "Transcript ready. Segments are loaded into the workspace.",
                "warn": "Audio is added but not transcribed yet.",
                "busy": f"Transcribing ({state.replace('transcribing', '').strip()}).",
                "bad": "The last transcription failed. Open Transcribe to see the log.",
            }.get(tone, "No voiceover audio added yet.")
        self.vo_detail_lbl.setText(self._vo_detail)

    def set_footage_status(self, name: str, state: str, tone: str = "idle"):
        self.footage_dot.set_tone(tone)
        self.footage_lbl.setText(self._item_html(name, state))

    # ------------------------------------------------------------------
    # Expand / collapse
    # ------------------------------------------------------------------
    def toggle_expanded(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self.drawer.setVisible(expanded)
        self.expand_btn.setIcon(FluentIcon.DOWN if expanded else FluentIcon.UP)
        self.expand_btn.setToolTip("Hide activity" if expanded else "Show activity")

    # ------------------------------------------------------------------
    # Render queue summary + job rows
    # ------------------------------------------------------------------
    def _on_live_progress(self, job: RenderJob, pct: float, status_text: str):
        self._update_summary_text()
        if self._expanded:
            self._update_running_row_widgets(job)

    def _update_summary_text(self):
        counts = render_queue.get_summary_counts()
        running_job = next((j for j in render_queue.jobs if j.status == "RUNNING"), None)
        total = counts["total"]
        done = counts["done"]
        queued = counts["queued"]
        failed = counts["failed"]

        self.pause_cb.blockSignals(True)
        self.pause_cb.setChecked(render_queue.pause_after_current)
        self.pause_cb.blockSignals(False)

        self.retry_failed_btn.setVisible(failed > 0)
        self.clear_btn.setEnabled((done + failed) > 0 or any(j.status == "CANCELLED" for j in render_queue.jobs))

        if running_job:
            idx_num = done + 1
            active_total = done + 1 + queued
            eta = _format_eta(running_job.eta_seconds)
            tail = f" · {eta}" if eta else ""
            self.summary_lbl.setText(
                f"Render {self._state(f'{running_job.progress:.0f}%')} · {idx_num} of {active_total}{tail}"
            )
            fn = os.path.basename(running_job.source_path)
            self.summary_lbl.setToolTip(f"Rendering {fn}")
            self.active_bar.setValue(int(round(running_job.progress)))
            self.active_bar.show()
            self.status_dot.set_tone("busy")
        elif queued > 0:
            state_tag = "paused" if render_queue.pause_after_current else "queued"
            self.summary_lbl.setText(f"Render {self._state(state_tag)} · {queued} waiting")
            self.summary_lbl.setToolTip("")
            self.active_bar.hide()
            self.status_dot.set_tone("warn")
        elif total > 0:
            msg = f"Render {self._state('done')} · {done} finished"
            if failed > 0:
                msg += f", {failed} failed"
            self.summary_lbl.setText(msg)
            self.summary_lbl.setToolTip("")
            self.active_bar.hide()
            self.status_dot.set_tone("warn" if failed > 0 else "ok")
        else:
            self.summary_lbl.setText(f"Render {self._state('idle')} · 0 jobs")
            self.summary_lbl.setToolTip("")
            self.active_bar.hide()
            self.status_dot.set_tone("idle")

    def _update_running_row_widgets(self, job: RenderJob):
        for i in range(self.jobs_layout.count()):
            w = self.jobs_layout.itemAt(i).widget()
            if w and w.property("job_id") == job.id:
                bar = w.findChild(QProgressBar, "JobRowBar")
                if bar:
                    bar.setValue(int(round(job.progress)))
                stat = w.findChild(QLabel, "JobRowStat")
                if stat:
                    eta = _format_eta(job.eta_seconds)
                    txt = f"{job.progress:.0f}% · {job.speed:.1f}x"
                    if eta:
                        txt += f" · {eta}"
                    stat.setText(txt)

    def refresh(self):
        self._update_summary_text()
        self._rebuild_job_rows()

    def _rebuild_job_rows(self):
        while self.jobs_layout.count() > 1:
            it = self.jobs_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        line_col = "rgba(255, 255, 255, 0.06)" if pal.get("is_dark", True) else "rgba(0, 0, 0, 0.07)"

        if not render_queue.jobs:
            empty_lbl = QLabel("No jobs. Select clips in Library and re-encode them to queue a job.", self.jobs_container)
            empty_lbl.setWordWrap(True)
            empty_lbl.setStyleSheet(f"color: {muted_col}; font-size: 12px; padding: 4px 0px;")
            self.jobs_layout.insertWidget(0, empty_lbl)
            return

        for job in render_queue.jobs:
            row = QFrame(self.jobs_container)
            row.setObjectName("DockJobRow")
            row.setProperty("job_id", job.id)
            row.setFixedHeight(30)
            row.setStyleSheet(f"""
                QFrame#DockJobRow {{
                    background-color: transparent;
                    border: none;
                    border-bottom: 1px solid {line_col};
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)

            # Status chip
            badge = QLabel(job.status, row)
            badge.setFixedHeight(18)
            badge.setAlignment(Qt.AlignCenter)
            badge_colors = {
                "RUNNING": ("rgba(96, 165, 250, 0.14)", "#60a5fa"),
                "QUEUED": ("rgba(148, 163, 184, 0.14)", "#cbd5e1"),
                "DONE": ("rgba(52, 211, 153, 0.12)", "#34d399"),
                "FAILED": ("rgba(248, 113, 113, 0.14)", "#f87171"),
                "INTERRUPTED": ("rgba(251, 191, 36, 0.14)", "#fbbf24"),
                "CANCELLED": ("rgba(148, 163, 184, 0.10)", "#94a3b8"),
            }
            bg_c, fg_c = badge_colors.get(job.status, ("rgba(148, 163, 184, 0.14)", "#94a3b8"))
            badge.setStyleSheet(
                f"background-color: {bg_c}; color: {fg_c}; border-radius: 4px; font-size: 10px;"
                " font-weight: 700; padding: 0px 6px; border: none;"
            )
            rl.addWidget(badge, 0, Qt.AlignVCenter)

            fn = os.path.basename(job.source_path)
            name_lbl = QLabel(fn, row)
            name_lbl.setToolTip(f"{job.source_path}\nCRF {job.config.crf} · Encoder: {job.config.encoder}")
            name_lbl.setStyleSheet(f"color: {text_col}; font-size: 12px; border: none; background: transparent;")
            name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            rl.addWidget(name_lbl, 1)

            if job.status == "RUNNING":
                bar = QProgressBar(row)
                bar.setObjectName("JobRowBar")
                bar.setRange(0, 100)
                bar.setValue(int(round(job.progress)))
                bar.setFixedWidth(80)
                bar.setFixedHeight(4)
                bar.setTextVisible(False)
                bar.setStyleSheet("""
                    QProgressBar { background-color: rgba(255,255,255,0.08); border: none; border-radius: 2px; }
                    QProgressBar::chunk { background-color: #60a5fa; border-radius: 2px; }
                """)
                rl.addWidget(bar, 0, Qt.AlignVCenter)

            stat_lbl = QLabel(row)
            stat_lbl.setObjectName("JobRowStat")
            stat_lbl.setStyleSheet(f"color: {muted_col}; font-size: 11px; border: none; background: transparent;")
            if job.status == "RUNNING":
                eta = _format_eta(job.eta_seconds)
                stat_lbl.setText(f"{job.progress:.0f}% · {job.speed:.1f}x" + (f" · {eta}" if eta else ""))
            elif job.status == "DONE":
                sz_b = format_bytes_display(job.size_before) if job.size_before > 0 else ""
                sz_a = format_bytes_display(job.size_after) if job.size_after > 0 else ""
                stat_lbl.setText(f"{sz_b} → {sz_a}" if (sz_b and sz_a) else "Complete")
            elif job.status in ("FAILED", "INTERRUPTED"):
                stat_lbl.setText((job.error_msg or "Failed")[:38])
                stat_lbl.setToolTip(job.error_msg)
            else:
                sz_b = format_bytes_display(job.size_before) if job.size_before > 0 else ""
                stat_lbl.setText(f"CRF {job.config.crf}" + (f" · {sz_b}" if sz_b else ""))
            rl.addWidget(stat_lbl, 0, Qt.AlignVCenter)

            def _btn(icon, tip, slot):
                b = TransparentToolButton(icon, row)
                b.setFixedSize(24, 24)
                b.setToolTip(tip)
                b.clicked.connect(slot)
                rl.addWidget(b, 0, Qt.AlignVCenter)

            if job.status == "QUEUED":
                _btn(FluentIcon.UP, "Move up", lambda _=False, jid=job.id: render_queue.move_job(jid, -1))
                _btn(FluentIcon.DOWN, "Move down", lambda _=False, jid=job.id: render_queue.move_job(jid, 1))
            if job.status in ("FAILED", "CANCELLED", "INTERRUPTED"):
                _btn(FluentIcon.SYNC, "Retry", lambda _=False, jid=job.id: render_queue.retry_job(jid))
            if job.status in ("RUNNING", "QUEUED"):
                _btn(FluentIcon.CLOSE, "Cancel", lambda _=False, jid=job.id: render_queue.cancel_job(jid))
            else:
                _btn(FluentIcon.DELETE, "Remove from list", lambda _=False, jid=job.id: render_queue.remove_job(jid))

            self.jobs_layout.insertWidget(self.jobs_layout.count() - 1, row)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        surface = pal.get("surface", "#18181b")
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        line_col = "rgba(255, 255, 255, 0.07)" if pal.get("is_dark", True) else "rgba(0, 0, 0, 0.08)"

        # Flat band across the card's bottom edge: top hairline only, no rounded box
        self.setStyleSheet(f"""
            QFrame#RenderQueueStrip {{
                background-color: {surface};
                border: none;
                border-top: 1px solid {line_col};
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }}
            QFrame#ActivityDockDrawer {{
                background-color: transparent;
                border: none;
                border-top: 1px solid {line_col};
            }}
        """)
        self.drawer_split.setStyleSheet(f"background-color: {line_col}; border: none;")
        item_css = f"color: {muted}; font-size: 12px; border: none; background: transparent;"
        self._text_col = text_col
        for lbl in (self.summary_lbl, self.vo_lbl, self.footage_lbl):
            lbl.setStyleSheet(item_css)
        title_css = (
            f"color: {muted}; font-size: 10.5px; font-weight: 600; letter-spacing: 0.5px;"
            " border: none; background: transparent;"
        )
        self.render_title.setStyleSheet(title_css)
        self.vo_title.setStyleSheet(title_css)
        self.vo_detail_lbl.setStyleSheet(f"color: {muted}; font-size: 12px; border: none; background: transparent;")
        self.active_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #60a5fa;
                border-radius: 2px;
            }
        """)
