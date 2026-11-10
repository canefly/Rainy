import os
import subprocess
from typing import List, Optional, Dict, Any
from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QFileDialog, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, ComboBox,
    FluentIcon
)

from ....core.vo_engine import (
    WhisperModelManager, VOBatchTranscribeWorker,
    save_vo_transcript_json, load_vo_transcript_json, TRANSCRIPTS_DIR
)
from ....core.logger import get_logger
from ..widgets import safe_copy_to_clipboard, StatusDot

logger = get_logger("TranscribeStage")


class TranscribeStage(QWidget):
    """
    Stage C: Dedicated Multi-Part Voiceover Transcription Hub.
    - Top: Controls to add audio files, pick installed Whisper models, and start the batch queue.
    - Middle: High-performance QTableWidget that breathes and stretches to fill the available space.
    - Bottom: Sleek collapsible Terminal strip adopting the exact design language of RenderQueueStrip.
    Strictly no emojis.
    """
    transcription_completed = Signal(str, list, list)  # (saved_json_path, vo_file_paths, all_segments)
    vo_status_changed = Signal(str)                   # Header badge update (e.g., "VO: Ready (142 segs)")

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        project_name: str = "",
        initial_vo_paths: Optional[List[str]] = None,
        initial_transcript_path: str = "",
    ):
        super().__init__(parent)
        self.project_name = project_name or "Voiceover"
        self.vo_file_paths: List[str] = [os.path.normpath(p) for p in (initial_vo_paths or []) if p and os.path.isfile(p)]
        self.transcript_json_path = initial_transcript_path or ""
        self.segments: List[Dict[str, Any]] = []
        self._active_worker: Optional[VOBatchTranscribeWorker] = None
        self._is_transcribing = False
        self._terminal_expanded = False

        # Load existing transcript if already available
        if self.transcript_json_path and os.path.isfile(self.transcript_json_path):
            existing = load_vo_transcript_json(self.transcript_json_path)
            if existing:
                self.segments = existing.get("segments", [])
                if not self.vo_file_paths and "files" in existing:
                    self.vo_file_paths = [f["path"] for f in existing["files"] if f.get("path") and os.path.isfile(f["path"])]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ---------------------------------------------------------------------
        # 1. Top Control Bar: Add Files, Installed Models, Start Queue
        # ---------------------------------------------------------------------
        ctrl_frame = QFrame(self)
        ctrl_frame.setObjectName("TranscribeControlBar")
        ctrl_frame.setFixedHeight(38)
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(0, 2, 0, 2)
        ctrl_layout.setSpacing(8)

        self.add_files_btn = PushButton("Add Audio Part(s)", ctrl_frame)
        self.add_files_btn.setIcon(FluentIcon.ADD)
        self.add_files_btn.setFixedHeight(30)
        self.add_files_btn.clicked.connect(self._on_add_audio_files)
        ctrl_layout.addWidget(self.add_files_btn, 0, Qt.AlignVCenter)

        ctrl_layout.addSpacing(6)

        model_lbl = QLabel("Model:", ctrl_frame)
        model_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #94a3b8;")
        ctrl_layout.addWidget(model_lbl, 0, Qt.AlignVCenter)

        self.model_combo = ComboBox(ctrl_frame)
        self.model_combo.setFixedHeight(30)
        self.model_combo.setMinimumWidth(180)
        self._populate_models_combo()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        ctrl_layout.addWidget(self.model_combo, 0, Qt.AlignVCenter)

        ctrl_layout.addStretch(1)

        self.overall_bar = QProgressBar(ctrl_frame)
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(0)
        self.overall_bar.setFixedWidth(110)
        self.overall_bar.setFixedHeight(6)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.hide()
        ctrl_layout.addWidget(self.overall_bar, 0, Qt.AlignVCenter)

        self.start_btn = PrimaryPushButton("Start Queue", ctrl_frame)
        self.start_btn.setIcon(FluentIcon.PLAY)
        self.start_btn.setFixedHeight(30)
        self.start_btn.clicked.connect(self._on_toggle_transcribe)
        ctrl_layout.addWidget(self.start_btn, 0, Qt.AlignVCenter)

        layout.addWidget(ctrl_frame, 0)

        # ---------------------------------------------------------------------
        # 2. Multi-Part VO Queue Table (Breathes and expands vertically)
        # ---------------------------------------------------------------------
        self.table = QTableWidget(0, 5, self)
        self.table.setObjectName("TranscribeQueueTable")
        self.table.setHorizontalHeaderLabels(["Order", "Filename", "Duration", "Status", "Actions"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setMinimumHeight(180)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("""
            QTableWidget#TranscribeQueueTable {
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                selection-background-color: rgba(255, 255, 255, 0.05);
            }
            QTableWidget#TranscribeQueueTable::item {
                padding: 1px 6px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            }
            QTableWidget#TranscribeQueueTable::item:focus {
                outline: none;
                border: none;
            }
            QHeaderView::section {
                background-color: transparent;
                color: #94a3b8;
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                padding: 4px 6px;
            }
        """)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 60)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.resizeSection(2, 80)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 110)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.resizeSection(4, 125)

        layout.addWidget(self.table, 1)  # Stretch factor 1: fills available vertical space

        # ---------------------------------------------------------------------
        # 3. Dedicated Terminal Strip (Adopting QueueStrip Design Language)
        # ---------------------------------------------------------------------
        self.terminal_strip = QFrame(self)
        self.terminal_strip.setObjectName("TerminalStrip")
        self.terminal_strip.setStyleSheet("""
            QFrame#TerminalStrip {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
            }
        """)

        strip_layout = QVBoxLayout(self.terminal_strip)
        strip_layout.setContentsMargins(12, 6, 12, 6)
        strip_layout.setSpacing(6)

        # 3a. Summary Bar
        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)

        self.status_dot = StatusDot("#94a3b8", "rgba(148, 163, 184, 0.25)", self.terminal_strip)
        bar_layout.addWidget(self.status_dot, 0, Qt.AlignVCenter)

        self.summary_lbl = QLabel("Terminal Idle · 0 segments", self.terminal_strip)
        self.summary_lbl.setObjectName("TerminalSummary")
        self.summary_lbl.setStyleSheet("font-size: 12px; font-weight: 500; color: #cbd5e1;")
        self.summary_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar_layout.addWidget(self.summary_lbl, 1, Qt.AlignVCenter)

        self.reveal_json_btn = PushButton("Reveal JSON", self.terminal_strip)
        self.reveal_json_btn.setIcon(FluentIcon.FOLDER)
        self.reveal_json_btn.setFixedHeight(26)
        self.reveal_json_btn.clicked.connect(self._on_reveal_json)
        self.reveal_json_btn.setEnabled(bool(self.transcript_json_path and os.path.isfile(self.transcript_json_path)))
        bar_layout.addWidget(self.reveal_json_btn, 0, Qt.AlignVCenter)

        self.copy_log_btn = ToolButton(FluentIcon.COPY, self.terminal_strip)
        self.copy_log_btn.setFixedSize(26, 26)
        self.copy_log_btn.setToolTip("Copy Terminal Output")
        self.copy_log_btn.clicked.connect(self._on_copy_log)
        bar_layout.addWidget(self.copy_log_btn, 0, Qt.AlignVCenter)

        self.clear_log_btn = ToolButton(FluentIcon.DELETE, self.terminal_strip)
        self.clear_log_btn.setFixedSize(26, 26)
        self.clear_log_btn.setToolTip("Clear Terminal")
        self.clear_log_btn.clicked.connect(self._on_clear_log)
        bar_layout.addWidget(self.clear_log_btn, 0, Qt.AlignVCenter)

        self.expand_btn = ToolButton(FluentIcon.CARE_UP_SOLID, self.terminal_strip)
        self.expand_btn.setFixedSize(26, 26)
        self.expand_btn.setToolTip("Expand / Collapse Terminal")
        self.expand_btn.clicked.connect(self.toggle_terminal)
        bar_layout.addWidget(self.expand_btn, 0, Qt.AlignVCenter)

        strip_layout.addLayout(bar_layout)

        # 3b. Expandable Drawer with Terminal Console
        self.terminal_drawer = QWidget(self.terminal_strip)
        drawer_layout = QVBoxLayout(self.terminal_drawer)
        drawer_layout.setContentsMargins(0, 4, 0, 0)
        drawer_layout.setSpacing(0)

        self.console = QPlainTextEdit(self.terminal_drawer)
        self.console.setObjectName("TranscribeConsole")
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 10))
        self.console.setStyleSheet("""
            QPlainTextEdit#TranscribeConsole {
                background-color: #090d16;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                border-radius: 4px;
                padding: 6px 8px;
                line-height: 1.4;
            }
        """)
        self.console.setFixedHeight(140)
        drawer_layout.addWidget(self.console)

        self.terminal_drawer.hide()  # Collapsed by default
        strip_layout.addWidget(self.terminal_drawer)

        layout.addWidget(self.terminal_strip, 0)

        self._refresh_table()
        if self.segments:
            self._render_existing_segments_in_console()
            self._update_summary()

    # -------------------------------------------------------------------------
    # Models Combo (Only Available / Downloaded Models)
    # -------------------------------------------------------------------------
    def _populate_models_combo(self):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        catalog = WhisperModelManager.get_catalog()
        active_id = WhisperModelManager.get_active_model_id()

        downloaded = [s for s in catalog if WhisperModelManager.is_model_downloaded(s.id)]
        specs_to_show = downloaded if downloaded else catalog

        active_idx = 0
        for idx, spec in enumerate(specs_to_show):
            self.model_combo.addItem(spec.name, userData=spec.id)
            if spec.id == active_id:
                active_idx = idx

        self.model_combo.setCurrentIndex(active_idx)
        self.model_combo.blockSignals(False)

        # Synchronize active model to what is visibly selected in the combo box
        visible_id = self.model_combo.currentData() or self.model_combo.itemData(active_idx)
        if visible_id:
            WhisperModelManager.set_active_model_id(str(visible_id))

    def _on_model_changed(self, idx: int):
        model_id = self.model_combo.itemData(idx) or self.model_combo.currentData()
        if model_id:
            WhisperModelManager.set_active_model_id(str(model_id))

    # -------------------------------------------------------------------------
    # Terminal Expand / Collapse (1:1 with QueueStrip pattern)
    # -------------------------------------------------------------------------
    def toggle_terminal(self):
        self._terminal_expanded = not self._terminal_expanded
        if self._terminal_expanded:
            self.expand_btn.setIcon(FluentIcon.CARE_DOWN_SOLID)
            self.terminal_drawer.show()
        else:
            self.expand_btn.setIcon(FluentIcon.CARE_UP_SOLID)
            self.terminal_drawer.hide()

    # -------------------------------------------------------------------------
    # UI State & Table Management
    # -------------------------------------------------------------------------
    def _refresh_table(self):
        self.table.setRowCount(len(self.vo_file_paths))
        for row, path in enumerate(self.vo_file_paths):
            fname = os.path.basename(path)

            order_item = QTableWidgetItem(f"Part {row + 1}")
            order_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, order_item)

            fn_item = QTableWidgetItem(fname)
            fn_item.setToolTip(path)
            self.table.setItem(row, 1, fn_item)

            dur_txt = "--:--"
            if os.path.isfile(path):
                sz_mb = os.path.getsize(path) / (1024 * 1024)
                dur_txt = f"{sz_mb:.1f} MB"
            dur_item = QTableWidgetItem(dur_txt)
            dur_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, dur_item)

            status_txt = "Ready" if self.segments else "Queued"
            status_item = QTableWidgetItem(status_txt)
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, status_item)

            # Compact Action Buttons: 20x20 with 0 margin layout so no clipping occurs
            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(0, 0, 0, 0)
            act_layout.setSpacing(4)
            act_layout.setAlignment(Qt.AlignCenter)

            up_btn = ToolButton(FluentIcon.UP, act_widget)
            up_btn.setFixedSize(20, 20)
            up_btn.setEnabled(row > 0 and not self._is_transcribing)
            up_btn.clicked.connect(lambda _, r=row: self._move_part(r, -1))
            act_layout.addWidget(up_btn)

            down_btn = ToolButton(FluentIcon.DOWN, act_widget)
            down_btn.setFixedSize(20, 20)
            down_btn.setEnabled(row < len(self.vo_file_paths) - 1 and not self._is_transcribing)
            down_btn.clicked.connect(lambda _, r=row: self._move_part(r, 1))
            act_layout.addWidget(down_btn)

            del_btn = ToolButton(FluentIcon.CLOSE, act_widget)
            del_btn.setFixedSize(20, 20)
            del_btn.setEnabled(not self._is_transcribing)
            del_btn.clicked.connect(lambda _, r=row: self._remove_part(r))
            act_layout.addWidget(del_btn)

            self.table.setCellWidget(row, 4, act_widget)

        self.start_btn.setEnabled(len(self.vo_file_paths) > 0 and not self._is_transcribing)
        self._update_summary()

    def _move_part(self, row: int, delta: int):
        new_row = row + delta
        if 0 <= new_row < len(self.vo_file_paths):
            self.vo_file_paths[row], self.vo_file_paths[new_row] = (
                self.vo_file_paths[new_row], self.vo_file_paths[row]
            )
            self._refresh_table()

    def _remove_part(self, row: int):
        if 0 <= row < len(self.vo_file_paths):
            self.vo_file_paths.pop(row)
            self._refresh_table()

    def _on_add_audio_files(self):
        start_dir = (
            os.path.dirname(self.vo_file_paths[0])
            if self.vo_file_paths
            else os.path.expanduser("~")
        )
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Voiceover Audio Part(s)",
            start_dir,
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.aac *.ogg);;All Files (*.*)"
        )
        if files:
            for f in files:
                norm_p = os.path.normpath(f)
                if norm_p not in self.vo_file_paths:
                    self.vo_file_paths.append(norm_p)
            self._refresh_table()

    # -------------------------------------------------------------------------
    # Transcription Execution
    # -------------------------------------------------------------------------
    def _on_toggle_transcribe(self):
        if self._is_transcribing:
            if self._active_worker:
                self._active_worker.cancel()
                self._active_worker.cleanup()
                self._active_worker = None
            self._is_transcribing = False
            self.start_btn.setText("Start Queue")
            self.start_btn.setIcon(FluentIcon.PLAY)
            self.overall_bar.hide()
            self.status_dot.set_color("#94a3b8", "rgba(148, 163, 184, 0.25)")
            self._refresh_table()
            return

        if not self.vo_file_paths:
            return

        self._is_transcribing = True
        self.start_btn.setText("Cancel")
        self.start_btn.setIcon(FluentIcon.CANCEL)
        self.add_files_btn.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.overall_bar.show()
        self.overall_bar.setValue(2)
        self.status_dot.set_color("#38bdf8", "rgba(56, 189, 248, 0.25)")

        # Expand terminal on start so the user can follow live progress
        if not self._terminal_expanded:
            self.toggle_terminal()

        active_model = str(
            self.model_combo.currentData()
            or self.model_combo.itemData(self.model_combo.currentIndex())
            or WhisperModelManager.get_active_model_id()
        )
        spec = WhisperModelManager.get_model_spec(active_model)
        model_name = spec.name if spec else active_model
        device, compute_type = WhisperModelManager.resolve_device_and_compute()

        self.console.clear()
        self._append_console_line(
            f"Starting Faster-Whisper: {model_name} on {device.upper()} {compute_type} ({len(self.vo_file_paths)} files)...",
            "#38bdf8"
        )
        self.segments = []
        self._update_summary()

        worker = VOBatchTranscribeWorker(
            file_paths=self.vo_file_paths,
            project_name=self.project_name,
            model_id=active_model,
        )
        worker.setAutoDelete(False)
        self._active_worker = worker

        worker.signals.file_started.connect(self._on_worker_file_started, Qt.QueuedConnection)
        worker.signals.file_progress.connect(self._on_worker_file_progress, Qt.QueuedConnection)
        worker.signals.segment_streamed.connect(self._on_worker_segment_streamed, Qt.QueuedConnection)
        worker.signals.file_finished.connect(self._on_worker_file_finished, Qt.QueuedConnection)
        worker.signals.batch_finished.connect(self._on_worker_batch_finished, Qt.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.QueuedConnection)

        QThreadPool.globalInstance().start(worker)

    def _on_worker_file_started(self, part_id: int, file_path: str, cur_idx: int, total_files: int):
        fname = os.path.basename(file_path)
        row = part_id - 1
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, 3)
            if item:
                item.setText("Transcribing...")
                item.setForeground(Qt.cyan)
        self.summary_lbl.setText(f"Transcribing Part {cur_idx} of {total_files}: {fname}")
        self._append_console_line(f"\n--- Transcribing Part {cur_idx} of {total_files}: {fname} ---", "#fbbf24")

    def _on_worker_file_progress(self, part_id: int, pct: int, status_text: str):
        self.overall_bar.setValue(pct)
        row = part_id - 1
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, 3)
            if item:
                item.setText(f"{pct}%")
        self.vo_status_changed.emit(f"VO: {pct}%")

    def _on_worker_segment_streamed(self, part_id: int, seg: dict):
        self.segments.append(seg)
        self._update_summary()
        g_in = seg.get("global_in", 0.0)
        g_out = seg.get("global_out", 0.0)
        txt = seg.get("text", "")
        line = f"[{self._fmt_tc(g_in)} -> {self._fmt_tc(g_out)}] (Part {part_id}) {txt}"
        self._append_console_line(line, "#e2e8f0")

    def _on_worker_file_finished(self, part_id: int, file_segments: list, duration: float):
        row = part_id - 1
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, 3)
            if item:
                item.setText(f"Done ({len(file_segments)} segs)")
                item.setForeground(Qt.green)

    def _on_worker_batch_finished(self, saved_json_path: str, manifest: dict):
        self._is_transcribing = False
        if self._active_worker:
            self._active_worker.cleanup()
            self._active_worker = None
        self.transcript_json_path = saved_json_path
        self.segments = manifest.get("segments", [])

        self.start_btn.setText("Re-Transcribe")
        self.start_btn.setIcon(FluentIcon.SYNC)
        self.start_btn.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.overall_bar.hide()
        self.status_dot.set_color("#34d399", "rgba(52, 211, 153, 0.25)")
        self.reveal_json_btn.setEnabled(True)

        self._update_summary()
        status_msg = f"VO: Ready ({len(self.segments)} segs, {len(self.vo_file_paths)} parts)"
        self.vo_status_changed.emit(status_msg)
        self._append_console_line(f"\n[DONE] Saved VO transcript: {saved_json_path}", "#34d399")
        model_id_used = manifest.get("model_used", "")
        spec_used = WhisperModelManager.get_model_spec(model_id_used)
        used_name = spec_used.name if spec_used else (model_id_used or "Whisper")

        self.transcription_completed.emit(self.transcript_json_path, self.vo_file_paths, self.segments)
        self._append_console_line(f"[INFO] Whisper AI model ({used_name}) offloaded from GPU (VRAM released).", "#94a3b8")

    def _on_worker_error(self, err_msg: str):
        self._is_transcribing = False
        if self._active_worker:
            self._active_worker.cleanup()
            self._active_worker = None
        self.start_btn.setText("Start Queue")
        self.start_btn.setIcon(FluentIcon.PLAY)
        self.start_btn.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.overall_bar.hide()
        self.status_dot.set_color("#ef4444", "rgba(239, 68, 68, 0.25)")

        self._append_console_line(f"\n[ERROR] {err_msg}", "#ef4444")
        self.vo_status_changed.emit("VO: Error")

    def cleanup(self):
        """Immediately halts any active transcription worker and releases VRAM/resources."""
        if self._active_worker:
            self._active_worker.cancel()
            self._active_worker.cleanup()
            self._active_worker = None
        self._is_transcribing = False
        import gc
        gc.collect()
        gc.collect()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _fmt_tc(seconds: float) -> str:
        s = max(0.0, float(seconds))
        m = int(s // 60)
        rem = s % 60
        return f"{m:02d}:{rem:05.2f}"

    def _append_console_line(self, line: str, color_hex: str = "#e2e8f0"):
        self.console.appendHtml(f"<span style='color:{color_hex};'>{line}</span>")
        self.console.moveCursor(QTextCursor.End)

    def _render_existing_segments_in_console(self):
        self.console.clear()
        self._append_console_line(f"Loaded {len(self.segments)} cached segments from independent JSON.", "#38bdf8")
        for seg in self.segments[:300]:
            p_id = seg.get("part_id", 1)
            g_in = seg.get("global_in", seg.get("source_in", 0.0))
            g_out = seg.get("global_out", seg.get("source_out", 0.0))
            txt = seg.get("text", "")
            self._append_console_line(f"[{self._fmt_tc(g_in)} -> {self._fmt_tc(g_out)}] (Part {p_id}) {txt}")
        if len(self.segments) > 300:
            self._append_console_line(f"... and {len(self.segments) - 300} more segments.", "#64748b")

    def _update_summary(self):
        if self._is_transcribing:
            return
        if self.segments and self.transcript_json_path:
            fname = os.path.basename(self.transcript_json_path)
            self.summary_lbl.setText(f"Terminal Ready · {len(self.segments)} segments ({fname})")
            self.status_dot.set_color("#34d399", "rgba(52, 211, 153, 0.25)")
            self.reveal_json_btn.setEnabled(True)
        elif self.vo_file_paths:
            self.summary_lbl.setText(f"Terminal Idle · {len(self.vo_file_paths)} parts queued")
            self.status_dot.set_color("#94a3b8", "rgba(148, 163, 184, 0.25)")
        else:
            self.summary_lbl.setText("Terminal Idle · 0 segments")
            self.status_dot.set_color("#94a3b8", "rgba(148, 163, 184, 0.25)")

    def _on_reveal_json(self):
        if self.transcript_json_path and os.path.isfile(self.transcript_json_path):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(self.transcript_json_path)}"', shell=True)

    def _on_copy_log(self):
        txt = self.console.toPlainText()
        if txt:
            safe_copy_to_clipboard(txt)

    def _on_clear_log(self):
        self.console.clear()
