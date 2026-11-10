import os
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar, QSizePolicy
)
from qfluentwidgets import (
    PushButton, ToolButton, CheckBox, FluentIcon, SmoothScrollArea
)

from ...theme import ThemeManager
from ....core.render_queue import render_queue, RenderJob
from ....core.unfurl_engine import format_bytes_display
from ..widgets import StatusDot


def _format_eta(sec: float) -> str:
    if sec <= 0:
        return ""
    mins = int(sec // 60)
    secs = int(sec % 60)
    return f"ETA {mins}:{secs:02d}"


class QueueStrip(QFrame):
    """
    Always-visible compact queue strip at the bottom of the Footage modal.
    Expands (▴/▾) into a full interactive list of queued, running, done, and failed jobs.
    Strictly no emojis.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("RenderQueueStrip")
        self._expanded = False

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 8, 12, 8)
        self.main_layout.setSpacing(8)

        # 1. Compact Summary Bar
        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(10)

        self.status_dot = StatusDot("#94a3b8", "rgba(148, 163, 184, 0.25)", self)
        bar_layout.addWidget(self.status_dot, 0, Qt.AlignVCenter)

        self.summary_lbl = QLabel("Render Queue Idle", self)
        self.summary_lbl.setObjectName("QueueStripSummary")
        self.summary_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar_layout.addWidget(self.summary_lbl, 1, Qt.AlignVCenter)

        self.active_bar = QProgressBar(self)
        self.active_bar.setRange(0, 100)
        self.active_bar.setValue(0)
        self.active_bar.setFixedWidth(130)
        self.active_bar.setFixedHeight(6)
        self.active_bar.setTextVisible(False)
        self.active_bar.hide()
        bar_layout.addWidget(self.active_bar, 0, Qt.AlignVCenter)

        self.pause_cb = CheckBox("Pause after current", self)
        self.pause_cb.setChecked(render_queue.pause_after_current)
        self.pause_cb.stateChanged.connect(
            lambda st: render_queue.set_pause_after_current(st == Qt.Checked.value or st == True)
        )
        bar_layout.addWidget(self.pause_cb, 0, Qt.AlignVCenter)

        self.retry_failed_btn = PushButton("Retry Failed", self)
        self.retry_failed_btn.setFixedHeight(26)
        self.retry_failed_btn.clicked.connect(render_queue.retry_all_failed)
        self.retry_failed_btn.hide()
        bar_layout.addWidget(self.retry_failed_btn, 0, Qt.AlignVCenter)

        self.clear_btn = PushButton("Clear Finished", self)
        self.clear_btn.setFixedHeight(26)
        self.clear_btn.clicked.connect(render_queue.clear_finished)
        bar_layout.addWidget(self.clear_btn, 0, Qt.AlignVCenter)

        self.expand_btn = ToolButton(FluentIcon.CARE_UP_SOLID, self)
        self.expand_btn.setFixedSize(26, 26)
        self.expand_btn.setToolTip("Expand / Collapse Render Queue Details")
        self.expand_btn.clicked.connect(self.toggle_expanded)
        bar_layout.addWidget(self.expand_btn, 0, Qt.AlignVCenter)

        self.main_layout.addLayout(bar_layout)

        # 2. Expandable Job List Drawer
        self.drawer = QWidget(self)
        drawer_layout = QVBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(0, 4, 0, 0)
        drawer_layout.setSpacing(6)

        self.scroll = SmoothScrollArea(self.drawer)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(150)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.jobs_container = QWidget(self.scroll)
        self.jobs_container.setStyleSheet("background: transparent;")
        self.jobs_layout = QVBoxLayout(self.jobs_container)
        self.jobs_layout.setContentsMargins(0, 0, 4, 0)
        self.jobs_layout.setSpacing(4)
        self.jobs_layout.addStretch(1)

        self.scroll.setWidget(self.jobs_container)
        drawer_layout.addWidget(self.scroll)
        self.drawer.hide()
        self.main_layout.addWidget(self.drawer)

        # Wire live RenderQueue signals
        render_queue.queue_changed.connect(self.refresh)
        render_queue.job_progress.connect(self._on_live_progress)

        self.apply_theme()
        self.refresh()

    def toggle_expanded(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self.drawer.setVisible(expanded)
        self.expand_btn.setIcon(FluentIcon.CARE_DOWN_SOLID if expanded else FluentIcon.CARE_UP_SOLID)

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
            fn = os.path.basename(running_job.source_path)
            if len(fn) > 30:
                fn = fn[:27] + "..."
            spd = f"{running_job.speed:.1f}x" if running_job.speed > 0 else "starting"
            eta = _format_eta(running_job.eta_seconds)
            parts = [
                f"Rendering {idx_num}/{active_total}",
                fn,
                f"{running_job.progress:.0f}%",
                spd,
            ]
            if eta:
                parts.append(eta)
            self.summary_lbl.setText("  ·  ".join(parts))
            self.active_bar.setValue(int(round(running_job.progress)))
            self.active_bar.show()
            self.status_dot.set_color("#38bdf8", "rgba(56, 189, 248, 0.35)")
        elif queued > 0:
            state_tag = "Paused" if render_queue.pause_after_current else "Queued"
            self.summary_lbl.setText(f"Render Queue {state_tag}  ·  {queued} waiting, {done} done")
            self.active_bar.hide()
            self.status_dot.set_color("#fbbf24", "rgba(251, 191, 36, 0.35)")
        elif total > 0:
            msg = f"Render Queue Complete  ·  {done} finished"
            if failed > 0:
                msg += f", {failed} failed/interrupted"
            self.summary_lbl.setText(msg)
            self.active_bar.hide()
            if failed > 0:
                self.status_dot.set_color("#fbbf24", "rgba(251, 191, 36, 0.35)")
            else:
                self.status_dot.set_color("#34d399", "rgba(52, 211, 153, 0.35)")
        else:
            self.summary_lbl.setText("Render Queue Idle  ·  0 jobs")
            self.active_bar.hide()
            self.status_dot.set_color("#94a3b8", "rgba(148, 163, 184, 0.20)")

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
        border_col = pal.get("border", "#27272a")

        if not render_queue.jobs:
            empty_lbl = QLabel("No render jobs in queue. Select clips in Library to convert to CFR or compress.", self.jobs_container)
            empty_lbl.setStyleSheet(f"color: {muted_col}; font-size: 11px; font-style: italic; padding: 8px 4px;")
            self.jobs_layout.insertWidget(0, empty_lbl)
            return

        for job in render_queue.jobs:
            row = QFrame(self.jobs_container)
            row.setProperty("job_id", job.id)
            row.setFixedHeight(32)
            row.setStyleSheet(f"""
                QFrame {{
                    background-color: rgba(255, 255, 255, 0.03);
                    border: 1px solid {border_col};
                    border-radius: 5px;
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 2, 8, 2)
            rl.setSpacing(8)

            # Status Badge
            badge = QLabel(job.status, row)
            badge.setFixedWidth(74)
            badge.setFixedHeight(18)
            badge.setAlignment(Qt.AlignCenter)
            badge_colors = {
                "RUNNING": ("rgba(56, 189, 248, 0.16)", "#38bdf8"),
                "QUEUED": ("rgba(148, 163, 184, 0.16)", "#cbd5e1"),
                "DONE": ("rgba(16, 185, 129, 0.16)", "#34d399"),
                "FAILED": ("rgba(239, 68, 68, 0.18)", "#f87171"),
                "INTERRUPTED": ("rgba(245, 158, 11, 0.18)", "#fbbf24"),
                "CANCELLED": ("rgba(148, 163, 184, 0.12)", "#94a3b8"),
            }
            bg_c, fg_c = badge_colors.get(job.status, ("rgba(148, 163, 184, 0.14)", "#94a3b8"))
            badge.setStyleSheet(
                f"background-color: {bg_c}; color: {fg_c}; border-radius: 3px; font-size: 9.5px; font-weight: 700;"
            )
            rl.addWidget(badge)

            # File Name & Preset Info
            fn = os.path.basename(job.source_path)
            name_lbl = QLabel(fn, row)
            name_lbl.setToolTip(f"{job.source_path}\nCRF {job.config.crf} · Encoder: {job.config.encoder}")
            name_lbl.setStyleSheet(f"color: {text_col}; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
            name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            rl.addWidget(name_lbl, 1)

            # Progress Bar for running job
            if job.status == "RUNNING":
                bar = QProgressBar(row)
                bar.setObjectName("JobRowBar")
                bar.setRange(0, 100)
                bar.setValue(int(round(job.progress)))
                bar.setFixedWidth(90)
                bar.setFixedHeight(5)
                bar.setTextVisible(False)
                bar.setStyleSheet("""
                    QProgressBar { background-color: rgba(255,255,255,0.08); border: none; border-radius: 2px; }
                    QProgressBar::chunk { background-color: #38bdf8; border-radius: 2px; }
                """)
                rl.addWidget(bar)

            # Stats / Size Before -> After
            stat_lbl = QLabel(row)
            stat_lbl.setObjectName("JobRowStat")
            stat_lbl.setStyleSheet(f"color: {muted_col}; font-size: 10.5px; border: none; background: transparent;")
            if job.status == "RUNNING":
                eta = _format_eta(job.eta_seconds)
                stat_lbl.setText(f"{job.progress:.0f}% · {job.speed:.1f}x" + (f" · {eta}" if eta else ""))
            elif job.status == "DONE":
                sz_b = format_bytes_display(job.size_before) if job.size_before > 0 else ""
                sz_a = format_bytes_display(job.size_after) if job.size_after > 0 else ""
                if sz_b and sz_a:
                    stat_lbl.setText(f"{sz_b} → {sz_a}")
                else:
                    stat_lbl.setText("Complete")
            elif job.status in ("FAILED", "INTERRUPTED"):
                err_short = (job.error_msg or "Failed")[:38]
                stat_lbl.setText(err_short)
                stat_lbl.setToolTip(job.error_msg)
            else:
                sz_b = format_bytes_display(job.size_before) if job.size_before > 0 else ""
                stat_lbl.setText(f"CRF {job.config.crf}" + (f" · {sz_b}" if sz_b else ""))
            rl.addWidget(stat_lbl)

            # Row Action Buttons (Reorder / Retry / Cancel / Remove)
            if job.status == "QUEUED":
                up_btn = ToolButton(FluentIcon.UP, row)
                up_btn.setFixedSize(22, 22)
                up_btn.setToolTip("Move Up in Queue")
                up_btn.clicked.connect(lambda _, jid=job.id: render_queue.move_job(jid, -1))
                rl.addWidget(up_btn)

                dn_btn = ToolButton(FluentIcon.DOWN, row)
                dn_btn.setFixedSize(22, 22)
                dn_btn.setToolTip("Move Down in Queue")
                dn_btn.clicked.connect(lambda _, jid=job.id: render_queue.move_job(jid, 1))
                rl.addWidget(dn_btn)

            if job.status in ("FAILED", "CANCELLED", "INTERRUPTED"):
                retry_btn = ToolButton(FluentIcon.SYNC, row)
                retry_btn.setFixedSize(22, 22)
                retry_btn.setToolTip("Retry Job")
                retry_btn.clicked.connect(lambda _, jid=job.id: render_queue.retry_job(jid))
                rl.addWidget(retry_btn)

            if job.status in ("RUNNING", "QUEUED"):
                cancel_btn = ToolButton(FluentIcon.CLOSE, row)
                cancel_btn.setFixedSize(22, 22)
                cancel_btn.setToolTip("Cancel Job")
                cancel_btn.clicked.connect(lambda _, jid=job.id: render_queue.cancel_job(jid))
                rl.addWidget(cancel_btn)
            else:
                rm_btn = ToolButton(FluentIcon.DELETE, row)
                rm_btn.setFixedSize(22, 22)
                rm_btn.setToolTip("Remove from List")
                rm_btn.clicked.connect(lambda _, jid=job.id: render_queue.remove_job(jid))
                rl.addWidget(rm_btn)

            self.jobs_layout.insertWidget(self.jobs_layout.count() - 1, row)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        surface = pal.get("surface", "#18181b")
        border = pal.get("border", "#27272a")
        text_col = pal.get("text", "#ffffff")

        self.setStyleSheet(f"""
            QFrame#RenderQueueStrip {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)
        self.summary_lbl.setStyleSheet(f"color: {text_col}; font-size: 11.5px; font-weight: 600; border: none;")
        self.active_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #38bdf8;
                border-radius: 3px;
            }
        """)
