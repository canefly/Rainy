import os
import subprocess
from typing import List, Optional, Dict, Any
from PySide6.QtCore import Qt, Signal, QThreadPool, QSize
from PySide6.QtGui import QFont, QTextCursor, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QFileDialog, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, TransparentToolButton, ComboBox,
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
    COL_PART, COL_FILE, COL_DURATION, COL_SIZE, COL_STATUS, COL_ACTIONS = range(6)
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
        self._duration_cache: Dict[str, str] = {}

        # Load existing transcript if already available
        if self.transcript_json_path and os.path.isfile(self.transcript_json_path):
            existing = load_vo_transcript_json(self.transcript_json_path)
            if existing:
                self.segments = existing.get("segments", [])
                if not self.vo_file_paths and "files" in existing:
                    self.vo_file_paths = [f["path"] for f in existing["files"] if f.get("path") and os.path.isfile(f["path"])]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ---------------------------------------------------------------------
        # 1. Top Control Bar: Add Files, Installed Models, Start Queue
        # ---------------------------------------------------------------------
        ctrl_frame = QFrame(self)
        ctrl_frame.setObjectName("TranscribeControlBar")
        ctrl_frame.setFixedHeight(32)
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(8)

        self.add_files_btn = PushButton("Add audio parts", ctrl_frame)
        self.add_files_btn.setIcon(FluentIcon.ADD)
        self.add_files_btn.setFixedHeight(32)
        self.add_files_btn.clicked.connect(self._on_add_audio_files)
        ctrl_layout.addWidget(self.add_files_btn, 0, Qt.AlignVCenter)

        self.model_combo = ComboBox(ctrl_frame)
        self.model_combo.setToolTip("Whisper model")
        self.model_combo.setFixedHeight(32)
        self.model_combo.setMinimumWidth(180)
        self._populate_models_combo()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        ctrl_layout.addWidget(self.model_combo, 0, Qt.AlignVCenter)

        ctrl_layout.addStretch(1)

        self.overall_bar = QProgressBar(ctrl_frame)
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(0)
        self.overall_bar.setFixedWidth(110)
        self.overall_bar.setFixedHeight(4)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.hide()
        ctrl_layout.addWidget(self.overall_bar, 0, Qt.AlignVCenter)

        self.start_btn = PrimaryPushButton("Start queue", ctrl_frame)
        self.start_btn.setIcon(FluentIcon.PLAY)
        self.start_btn.setFixedHeight(32)
        self.start_btn.clicked.connect(self._on_toggle_transcribe)
        ctrl_layout.addWidget(self.start_btn, 0, Qt.AlignVCenter)

        layout.addWidget(ctrl_frame, 0)

        # ---------------------------------------------------------------------
        # 2. Multi-Part VO Queue Table (Breathes and expands vertically)
        # ---------------------------------------------------------------------
        self.table = QTableWidget(0, 6, self)
        self.table.setObjectName("TranscribeQueueTable")
        self.table.setHorizontalHeaderLabels(["PART", "AUDIO FILE", "DURATION", "SIZE", "STATUS", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setMinimumHeight(120)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("""
            QTableWidget#TranscribeQueueTable {
                background-color: transparent;
                border: none;
                font-size: 12px;
                selection-background-color: rgba(96, 165, 250, 0.10);
                outline: none;
            }
            QTableWidget#TranscribeQueueTable::item {
                padding: 0px 8px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            QTableWidget#TranscribeQueueTable::item:focus {
                outline: none;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            QHeaderView { background-color: transparent; border: none; }
            QHeaderView::section {
                background-color: transparent;
                color: #94a3b8;
                font-weight: 600;
                font-size: 10.5px;
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.07);
                border-bottom: 1px solid rgba(255, 255, 255, 0.07);
                padding: 0px 8px;
            }
        """)

        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setFixedHeight(30)
        header.setHighlightSections(False)
        for col, width in ((self.COL_PART, 56), (self.COL_DURATION, 84), (self.COL_SIZE, 84),
                           (self.COL_STATUS, 130), (self.COL_ACTIONS, 96)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)
        header.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)

        layout.addWidget(self.table, 1)  # Stretch factor 1: fills available vertical space

        # ---------------------------------------------------------------------
        # 3. Dedicated Terminal Strip (Adopting QueueStrip Design Language)
        # ---------------------------------------------------------------------
        self.terminal_strip = QFrame(self)
        self.terminal_strip.setObjectName("TerminalStrip")
        self.terminal_strip.setStyleSheet("""
            QFrame#TerminalStrip {
                background-color: rgba(255, 255, 255, 0.03);
                border: none;
                border-radius: 6px;
            }
        """)

        strip_layout = QVBoxLayout(self.terminal_strip)
        strip_layout.setContentsMargins(12, 8, 8, 8)
        strip_layout.setSpacing(8)

        # 3a. Summary Bar
        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)

        self.status_dot = StatusDot("#94a3b8", "rgba(148, 163, 184, 0.25)", self.terminal_strip)
        bar_layout.addWidget(self.status_dot, 0, Qt.AlignVCenter)

        # Two-line file label: transcript file name, then segment/part detail
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        self.output_name_lbl = QLabel("No transcript yet", self.terminal_strip)
        self.output_name_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #e8e9ec; background: transparent;")
        self.output_name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text_col.addWidget(self.output_name_lbl)
        self.summary_lbl = QLabel("Add audio parts, then start the queue.", self.terminal_strip)
        self.summary_lbl.setObjectName("TerminalSummary")
        self.summary_lbl.setStyleSheet("font-size: 11.5px; color: #94a3b8; background: transparent;")
        self.summary_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text_col.addWidget(self.summary_lbl)
        bar_layout.addLayout(text_col, 1)

        self.reveal_json_btn = PushButton("Reveal", self.terminal_strip)
        self.reveal_json_btn.setToolTip("Show the transcript JSON in File Explorer")
        self.reveal_json_btn.setFixedHeight(28)
        self.reveal_json_btn.clicked.connect(self._on_reveal_json)
        self.reveal_json_btn.setEnabled(bool(self.transcript_json_path and os.path.isfile(self.transcript_json_path)))
        bar_layout.addWidget(self.reveal_json_btn, 0, Qt.AlignVCenter)

        self.copy_log_btn = TransparentToolButton(FluentIcon.COPY, self.terminal_strip)
        self.copy_log_btn.setFixedSize(28, 28)
        self.copy_log_btn.setToolTip("Copy log")
        self.copy_log_btn.clicked.connect(self._on_copy_log)
        bar_layout.addWidget(self.copy_log_btn, 0, Qt.AlignVCenter)

        self.clear_log_btn = TransparentToolButton(FluentIcon.BROOM, self.terminal_strip)
        self.clear_log_btn.setFixedSize(28, 28)
        self.clear_log_btn.setToolTip("Clear log")
        self.clear_log_btn.clicked.connect(self._on_clear_log)
        bar_layout.addWidget(self.clear_log_btn, 0, Qt.AlignVCenter)

        self.expand_btn = TransparentToolButton(FluentIcon.COMMAND_PROMPT, self.terminal_strip)
        self.expand_btn.setFixedSize(28, 28)
        self.expand_btn.setToolTip("Show log")
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
        self.console.setFixedHeight(120)
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
        self.terminal_drawer.setVisible(self._terminal_expanded)
        self.expand_btn.setToolTip("Hide log" if self._terminal_expanded else "Show log")

    # -------------------------------------------------------------------------
    # UI State & Table Management
    # -------------------------------------------------------------------------
    def _refresh_table(self):
        self.table.setRowCount(len(self.vo_file_paths))
        for row, path in enumerate(self.vo_file_paths):
            fname = os.path.basename(path)

            order_item = QTableWidgetItem(str(row + 1))
            order_item.setForeground(QColor("#94a3b8"))
            self.table.setItem(row, self.COL_PART, order_item)

            fn_item = QTableWidgetItem(fname)
            fn_item.setToolTip(path)
            self.table.setItem(row, self.COL_FILE, fn_item)

            dur_item = QTableWidgetItem(self._probe_duration(path))
            dur_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            dur_item.setForeground(QColor("#94a3b8"))
            self.table.setItem(row, self.COL_DURATION, dur_item)

            size_txt = "—"
            if os.path.isfile(path):
                size_txt = f"{os.path.getsize(path) / (1024 * 1024):.1f} MB"
            size_item = QTableWidgetItem(size_txt)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, self.COL_SIZE, size_item)

            status_item = QTableWidgetItem("Transcribed" if self.segments else "Queued")
            status_item.setForeground(QColor("#34d399") if self.segments else QColor("#94a3b8"))
            self.table.setItem(row, self.COL_STATUS, status_item)

            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(0, 0, 4, 0)
            act_layout.setSpacing(2)
            act_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            def _act(icon, tip, enabled, slot):
                b = TransparentToolButton(icon, act_widget)
                b.setFixedSize(26, 26)
                b.setIconSize(QSize(12, 12))
                b.setToolTip(tip)
                b.setEnabled(enabled)
                b.clicked.connect(slot)
                act_layout.addWidget(b)

            _act(FluentIcon.UP, "Move up", row > 0 and not self._is_transcribing,
                 lambda _=False, r=row: self._move_part(r, -1))
            _act(FluentIcon.DOWN, "Move down", row < len(self.vo_file_paths) - 1 and not self._is_transcribing,
                 lambda _=False, r=row: self._move_part(r, 1))
            _act(FluentIcon.CLOSE, "Remove", not self._is_transcribing,
                 lambda _=False, r=row: self._remove_part(r))

            self.table.setCellWidget(row, self.COL_ACTIONS, act_widget)

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
            self.start_btn.setText("Start queue")
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
            item = self.table.item(row, self.COL_STATUS)
            if item:
                item.setText("Transcribing…")
                item.setForeground(QColor("#60a5fa"))
        self.output_name_lbl.setText(f"Transcribing part {cur_idx} of {total_files}")
        self.summary_lbl.setText(fname)
        self._append_console_line(f"\n--- Transcribing Part {cur_idx} of {total_files}: {fname} ---", "#fbbf24")

    def _on_worker_file_progress(self, part_id: int, pct: int, status_text: str):
        self.overall_bar.setValue(pct)
        row = part_id - 1
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, self.COL_STATUS)
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
            item = self.table.item(row, self.COL_STATUS)
            if item:
                item.setText(f"Done · {len(file_segments)} segments")
                item.setForeground(QColor("#34d399"))

    def _on_worker_batch_finished(self, saved_json_path: str, manifest: dict):
        self._is_transcribing = False
        if self._active_worker:
            self._active_worker.cleanup()
            self._active_worker = None
        self.transcript_json_path = saved_json_path
        self.segments = manifest.get("segments", [])

        self.start_btn.setText("Transcribe again")
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
        self.start_btn.setText("Start queue")
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
        parts = len(self.vo_file_paths)
        if self.segments and self.transcript_json_path:
            self.output_name_lbl.setText(os.path.basename(self.transcript_json_path))
            self.summary_lbl.setText(
                f"{len(self.segments)} segments  ·  {parts} part{'s' if parts != 1 else ''}"
            )
            self.status_dot.set_color("#34d399", "#0034d399")
            self.reveal_json_btn.setEnabled(True)
        elif self.vo_file_paths:
            self.output_name_lbl.setText("No transcript yet")
            self.summary_lbl.setText(f"{parts} part{'s' if parts != 1 else ''} queued. Start the queue to transcribe.")
            self.status_dot.set_color("#6f737b", "#006f737b")
        else:
            self.output_name_lbl.setText("No transcript yet")
            self.summary_lbl.setText("Add audio parts, then start the queue.")
            self.status_dot.set_color("#6f737b", "#006f737b")

    def _probe_duration(self, path: str) -> str:
        """Real audio length (was showing file size in MB). Cached per path."""
        if path in self._duration_cache:
            return self._duration_cache[path]
        txt = "—"
        try:
            from ....core.media_probe import probe_media_file
            probed = probe_media_file(path, use_cache=True)
            if probed and getattr(probed, "duration_display", ""):
                txt = probed.duration_display
        except Exception:
            pass
        self._duration_cache[path] = txt
        return txt

    def _on_reveal_json(self):
        if self.transcript_json_path and os.path.isfile(self.transcript_json_path):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(self.transcript_json_path)}"', shell=True)

    def _on_copy_log(self):
        txt = self.console.toPlainText()
        if txt:
            safe_copy_to_clipboard(txt)

    def _on_clear_log(self):
        self.console.clear()
