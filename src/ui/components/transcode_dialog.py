import os
import subprocess
from typing import Optional, Dict, Any, Tuple, List
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QCursor, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QFileDialog, QApplication
)
from qfluentwidgets import (
    MessageBoxBase, PushButton, PrimaryPushButton, ToolButton,
    CheckBox, ComboBox, Slider, FluentIcon, IconWidget, InfoBar,
    InfoBarPosition, Dialog
)

from ..theme import ThemeManager
from ...core.database import db
from ...core.media_probe import probe_media_file, ProbedMediaItem
from ...core.timecode_engine import analyze_framerate
from ...core.ffmpeg_encoder import (
    TranscodeConfig, TranscodeProgress, TranscodeWorker,
    get_ffmpeg_status, get_recommended_encoder, detect_available_encoders,
    get_hardware_status_summary, get_encoder_catalog,
    is_file_locked, perform_atomic_replace
)
from ...core.render_queue import render_queue
from ...core.logger import get_logger

logger = get_logger("TranscodeDialog")


class TranscodeDialog(MessageBoxBase):
    """
    Windows 11 Fluent Transcoding and Compression Modal Dialog.
    Allows re-encoding variable framerate (VFR) clips to constant framerate (CFR)
    and compressing bloated bodycam or interrogation footages.
    Supports both single-file and multi-file batch queueing into the global RenderQueueService.
    Strictly NO emojis in labels, titles, or messages.
    """
    transcode_completed = Signal(str)       # source_path (legacy signal emitted when job finishes)
    jobs_queued = Signal(list)              # List[RenderJob] emitted immediately when added to queue

    def __init__(
        self,
        source_path: str = "",
        parent=None,
        source_paths: Optional[List[str]] = None,
        preset_mode: str = "custom",  # "cfr" | "compress" | "custom"
    ):
        self._fallback_parent = None
        target_parent = parent or QApplication.activeWindow()
        if target_parent is None:
            self._fallback_parent = QWidget()
            self._fallback_parent.resize(800, 600)
            target_parent = self._fallback_parent
        super().__init__(parent=target_parent)

        if source_paths:
            self.source_paths: List[str] = [os.path.abspath(p) for p in source_paths if p]
        elif source_path:
            self.source_paths = [os.path.abspath(source_path)]
        else:
            self.source_paths = []

        self.source_path = self.source_paths[0] if self.source_paths else ""
        self.preset_mode = preset_mode
        self._worker: Optional[TranscodeWorker] = None
        self._probed: Optional[ProbedMediaItem] = (
            probe_media_file(self.source_path, use_cache=True) if self.source_path else None
        )

        target_parent = self.parentWidget() or parent
        p_w = target_parent.width() if target_parent else 900
        self.widget.setFixedWidth(min(680, max(520, p_w - 80)))

        self.viewLayout.setSpacing(12)
        self.viewLayout.setContentsMargins(24, 20, 24, 16)

        # 1. Header Row
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self.icon_widget = IconWidget(FluentIcon.MOVIE, self.widget)
        self.icon_widget.setFixedSize(20, 20)
        header_row.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        is_batch = len(self.source_paths) > 1
        title_text = (
            f"Queue {len(self.source_paths)} Clips for Transcode"
            if is_batch
            else "Transcode & Compress Footage"
        )
        self.title_lbl = QLabel(title_text, self.widget)
        self.title_lbl.setObjectName("DrawerDialogTitle")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.sub_lbl = QLabel(
            "Configure shared preset to enqueue sequentially in the background Render Queue."
            if is_batch
            else "Normalize variable framerates (VFR) and compress large video files.",
            self.widget
        )
        self.sub_lbl.setObjectName("DrawerDialogSubtitle")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.sub_lbl)
        header_row.addLayout(title_col, 1)
        self.viewLayout.addLayout(header_row)

        # 2. Source File Details Card
        self.source_card = QFrame(self.widget)
        self.source_card.setObjectName("SourceInfoCard")
        sc_layout = QVBoxLayout(self.source_card)
        sc_layout.setContentsMargins(14, 10, 14, 10)
        sc_layout.setSpacing(6)

        fn_row = QHBoxLayout()
        if is_batch:
            fn_lbl = QLabel(f"{len(self.source_paths)} Media Files Selected", self.source_card)
        else:
            fn_lbl = QLabel(os.path.basename(self.source_path), self.source_card)
        fn_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        fn_row.addWidget(fn_lbl)
        fn_row.addStretch(1)

        # VFR or CFR pill
        self.vfr_badge = QLabel(self.source_card)
        self._update_vfr_badge()
        fn_row.addWidget(self.vfr_badge)
        sc_layout.addLayout(fn_row)

        if is_batch:
            names_preview = ", ".join(os.path.basename(p) for p in self.source_paths[:3])
            if len(self.source_paths) > 3:
                names_preview += f" (+{len(self.source_paths) - 3} more)"
            path_lbl = QLabel(names_preview, self.source_card)
        else:
            path_lbl = QLabel(self.source_path, self.source_card)
        path_lbl.setObjectName("PrefCaption")
        path_lbl.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        sc_layout.addWidget(path_lbl)

        # Metadata Specs Pills Row
        specs_row = QHBoxLayout()
        specs_row.setSpacing(10)

        if is_batch:
            total_bytes = 0
            for p in self.source_paths:
                try:
                    if os.path.isfile(p):
                        total_bytes += os.path.getsize(p)
                except Exception:
                    pass
            sz_mb = total_bytes / (1024 * 1024)
            sz_text = f"{sz_mb / 1024:.2f} GB" if sz_mb >= 1000 else f"{sz_mb:.1f} MB"
            specs_row.addWidget(self._create_spec_chip("Batch Count", f"{len(self.source_paths)} clips"))
            specs_row.addWidget(self._create_spec_chip("Total Input Size", sz_text))
            specs_row.addWidget(self._create_spec_chip("Execution", "Sequential Queue (1 at a time)"))
            self._batch_total_bytes = total_bytes
        else:
            self._batch_total_bytes = self._probed.file_size if self._probed else 0
            dur_text = self._probed.duration_display if self._probed else "--:--"
            res_text = self._probed.resolution if self._probed else "Unknown"
            fps_val = self._probed.frame_rate if self._probed else 0.0
            v_codec = self._probed.video_codec.upper() if self._probed and self._probed.video_codec else "VIDEO"
            sz_mb = (self._probed.file_size / (1024 * 1024)) if self._probed else 0.0
            sz_text = f"{sz_mb / 1024:.2f} GB" if sz_mb >= 1000 else f"{sz_mb:.1f} MB"

            specs_row.addWidget(self._create_spec_chip("Duration", dur_text))
            specs_row.addWidget(self._create_spec_chip("Resolution", res_text))
            specs_row.addWidget(self._create_spec_chip("Framerate", f"{fps_val:.2f} fps"))
            specs_row.addWidget(self._create_spec_chip("Codec", v_codec))
            scan_txt = (
                f"Interlaced ({self._probed.field_order.upper()})"
                if (self._probed and getattr(self._probed, "is_interlaced", False))
                else "Progressive"
            )
            specs_row.addWidget(self._create_spec_chip("Scan", scan_txt))
            specs_row.addWidget(self._create_spec_chip("Size", sz_text))

        specs_row.addStretch(1)
        sc_layout.addLayout(specs_row)

        self.viewLayout.addWidget(self.source_card)

        # 3. Settings Control Card
        self.ctrl_card = QFrame(self.widget)
        self.ctrl_card.setObjectName("CtrlCard")
        cc_layout = QVBoxLayout(self.ctrl_card)
        cc_layout.setContentsMargins(14, 12, 14, 12)
        cc_layout.setSpacing(10)

        # Global Defaults Toggle
        self.use_global_cb = CheckBox("Use Global Transcode Settings", self.ctrl_card)
        self.use_global_cb.setChecked(preset_mode == "custom")
        self.use_global_cb.stateChanged.connect(self._on_use_global_changed)
        cc_layout.addWidget(self.use_global_cb)

        # Override Controls Container
        self.override_box = QWidget(self.ctrl_card)
        ob_layout = QVBoxLayout(self.override_box)
        ob_layout.setContentsMargins(0, 0, 0, 0)
        ob_layout.setSpacing(8)

        # Row 1: Hardware Encoder
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        r1_lbl = QLabel("Hardware Encoder:", self.override_box)
        r1_lbl.setFixedWidth(140)
        self.encoder_combo = ComboBox(self.override_box)
        status_headline, best_enc = get_hardware_status_summary()
        best_display = {
            "h264_nvenc": "NVIDIA NVENC H.264",
            "hevc_nvenc": "NVIDIA NVENC HEVC",
            "h264_amf": "AMD AMF H.264",
            "hevc_amf": "AMD AMF HEVC",
            "h264_qsv": "Intel QuickSync",
            "libx264": "CPU Software",
        }.get(best_enc, "CPU Software")

        self.encoder_combo.addItem(f"Auto-Detect Hardware ({best_display})", userData="auto")
        for enc_id, name, is_avail, req in get_encoder_catalog():
            tag = "Ready" if is_avail else req
            self.encoder_combo.addItem(f"{name} [{tag}]", userData=enc_id)
        r1.addWidget(r1_lbl)
        r1.addWidget(self.encoder_combo, 1)
        ob_layout.addLayout(r1)

        # Row 2: Target CFR Framerate
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        r2_lbl = QLabel("Target Framerate:", self.override_box)
        r2_lbl.setFixedWidth(140)
        self.fps_combo = ComboBox(self.override_box)
        self.fps_combo.addItem("Auto-Snap to Nearest Standard CFR", userData="0.0")
        self.fps_combo.addItem("Force 23.976 fps", userData="23.976")
        self.fps_combo.addItem("Force 24.0 fps", userData="24.0")
        self.fps_combo.addItem("Force 25.0 fps", userData="25.0")
        self.fps_combo.addItem("Force 29.97 fps", userData="29.97")
        self.fps_combo.addItem("Force 30.0 fps", userData="30.0")
        self.fps_combo.addItem("Force 59.94 fps", userData="59.94")
        self.fps_combo.addItem("Force 60.0 fps", userData="60.0")
        r2.addWidget(r2_lbl)
        r2.addWidget(self.fps_combo, 1)
        ob_layout.addLayout(r2)

        # Row 3: Quality & Compression Slider (CRF 14..28)
        r3 = QVBoxLayout()
        r3.setSpacing(4)
        r3_head = QHBoxLayout()
        r3_lbl = QLabel("Quality & Compression (CRF / CQ):", self.override_box)
        self.crf_val_lbl = QLabel("CRF 18  •  Near-Lossless", self.override_box)
        self.crf_val_lbl.setStyleSheet("font-weight: 700; color: #38bdf8;")
        r3_head.addWidget(r3_lbl)
        r3_head.addStretch(1)
        r3_head.addWidget(self.crf_val_lbl)
        r3.addLayout(r3_head)

        self.crf_slider = Slider(Qt.Horizontal, self.override_box)
        self.crf_slider.setRange(14, 28)
        self.crf_slider.setValue(18)
        self.crf_slider.valueChanged.connect(self._on_crf_slider_changed)
        r3.addWidget(self.crf_slider)

        self.savings_lbl = QLabel("Estimated Output: Calculating...", self.override_box)
        self.savings_lbl.setStyleSheet("font-size: 11px; color: #34d399;")
        r3.addWidget(self.savings_lbl)
        ob_layout.addLayout(r3)

        # Row 4: Resolution Downscale
        r4 = QHBoxLayout()
        r4.setSpacing(10)
        r4_lbl = QLabel("Max Resolution:", self.override_box)
        r4_lbl.setFixedWidth(140)
        self.res_combo = ComboBox(self.override_box)
        self.res_combo.addItem("Keep Original Resolution", userData="none")
        self.res_combo.addItem("Downscale 4K to 1080p (FHD)", userData="1080")
        self.res_combo.addItem("Downscale to 720p (HD Proxy)", userData="720")
        r4.addWidget(r4_lbl)
        r4.addWidget(self.res_combo, 1)
        ob_layout.addLayout(r4)

        # Row 5: Deinterlace / Decomb Filter
        r5 = QHBoxLayout()
        r5.setSpacing(10)
        r5_lbl = QLabel("Deinterlace / Decomb:", self.override_box)
        r5_lbl.setFixedWidth(140)
        self.deint_combo = ComboBox(self.override_box)
        self.deint_combo.addItem("Auto-Decomb Interlaced Clips (bwdif)", userData="auto")
        self.deint_combo.addItem("Force Deinterlace All (bwdif)", userData="bwdif")
        self.deint_combo.addItem("Standard Deinterlace (yadif)", userData="yadif")
        self.deint_combo.addItem("Disabled (Off)", userData="off")
        r5.addWidget(r5_lbl)
        r5.addWidget(self.deint_combo, 1)
        ob_layout.addLayout(r5)

        cc_layout.addWidget(self.override_box)
        self.viewLayout.addWidget(self.ctrl_card)

        # 4. Progress HUD (Kept for backwards compatibility)
        self.progress_box = QFrame(self.widget)
        self.progress_box.setObjectName("ProgressBox")
        pb_layout = QVBoxLayout(self.progress_box)
        pb_layout.setContentsMargins(14, 10, 14, 10)
        pb_layout.setSpacing(6)

        self.prog_bar = QProgressBar(self.progress_box)
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setFixedHeight(8)
        self.prog_bar.setTextVisible(False)
        pb_layout.addWidget(self.prog_bar)

        self.prog_status_lbl = QLabel("Initializing FFmpeg worker...", self.progress_box)
        self.prog_status_lbl.setStyleSheet("font-size: 11px; font-family: 'Consolas', monospace;")
        pb_layout.addWidget(self.prog_status_lbl)
        self.progress_box.hide()
        self.viewLayout.addWidget(self.progress_box)

        # 5. Dialog Buttons
        self.buttonGroup.setFixedHeight(60)
        self.buttonLayout.setContentsMargins(20, 8, 20, 12)

        btn_label = f"Add {len(self.source_paths)} to Queue" if is_batch else "Add to Queue"
        self.yesButton.setText(btn_label)
        self.yesButton.setIcon(FluentIcon.PLAY)
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_start_clicked)

        self.cancelButton.setText("Cancel")
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._on_cancel_clicked)

        self._load_initial_settings()
        self._apply_theme_styling()

    def _create_spec_chip(self, label: str, val: str) -> QWidget:
        w = QWidget(self.source_card)
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(1)
        l = QLabel(label, w)
        l.setStyleSheet("font-size: 9.5px; color: #a1a1aa; text-transform: uppercase; font-weight: 600;")
        v = QLabel(val, w)
        v.setStyleSheet("font-size: 11.5px; font-weight: 700;")
        vl.addWidget(l)
        vl.addWidget(v)
        return w

    def _update_vfr_badge(self):
        if len(self.source_paths) > 1:
            self.vfr_badge.setText(f"Batch ({len(self.source_paths)} files)")
            self.vfr_badge.setStyleSheet("""
                background-color: rgba(56, 189, 248, 0.15);
                color: #38bdf8;
                font-size: 10px;
                font-weight: 800;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(56, 189, 248, 0.35);
            """)
            return

        is_vfr = self._probed.is_vfr if self._probed else False
        is_interlaced = getattr(self._probed, "is_interlaced", False) if self._probed else False
        fps = self._probed.frame_rate if self._probed else 24.0
        if is_vfr and is_interlaced:
            self.vfr_badge.setText(f"VFR + Interlaced ({fps:.2f} fps)")
            self.vfr_badge.setStyleSheet("""
                background-color: rgba(251, 191, 36, 0.15);
                color: #fbbf24;
                font-size: 10px;
                font-weight: 800;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(251, 191, 36, 0.35);
            """)
        elif is_vfr:
            self.vfr_badge.setText(f"VFR Alert ({fps:.2f} fps)")
            self.vfr_badge.setStyleSheet("""
                background-color: rgba(251, 191, 36, 0.15);
                color: #fbbf24;
                font-size: 10px;
                font-weight: 800;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(251, 191, 36, 0.35);
            """)
        elif is_interlaced:
            self.vfr_badge.setText(f"Interlaced ({fps:.2f} fps)")
            self.vfr_badge.setStyleSheet("""
                background-color: rgba(251, 191, 36, 0.15);
                color: #fbbf24;
                font-size: 10px;
                font-weight: 800;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(251, 191, 36, 0.35);
            """)
        else:
            self.vfr_badge.setText(f"CFR Standard ({fps:.2f} fps)")
            self.vfr_badge.setStyleSheet("""
                background-color: rgba(52, 211, 153, 0.15);
                color: #34d399;
                font-size: 10px;
                font-weight: 800;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(52, 211, 153, 0.35);
            """)

    def _load_initial_settings(self):
        g_enc = db.get_setting("transcode_encoder", "auto")
        g_crf = int(db.get_setting("transcode_crf", "18"))
        g_fps = db.get_setting("transcode_framerate", "0.0")
        g_res = db.get_setting("transcode_max_height", "none")
        g_deint = db.get_setting("transcode_deinterlace", "auto")

        if self.preset_mode == "cfr":
            g_crf = 18
            g_fps = "0.0"
        elif self.preset_mode == "compress":
            g_crf = 23

        idx_enc = self.encoder_combo.findData(g_enc)
        if idx_enc >= 0:
            self.encoder_combo.setCurrentIndex(idx_enc)

        self.crf_slider.setValue(g_crf)
        idx_fps = self.fps_combo.findData(g_fps)
        if idx_fps >= 0:
            self.fps_combo.setCurrentIndex(idx_fps)

        idx_res = self.res_combo.findData(g_res)
        if idx_res >= 0:
            self.res_combo.setCurrentIndex(idx_res)

        idx_deint = self.deint_combo.findData(g_deint)
        if idx_deint >= 0:
            self.deint_combo.setCurrentIndex(idx_deint)

        use_global = self.use_global_cb.isChecked()
        self._on_use_global_changed(Qt.Checked.value if use_global else Qt.Unchecked.value)
        self._on_crf_slider_changed(g_crf)

    def _on_use_global_changed(self, state: int):
        use_global = (state == Qt.Checked.value or state == True)
        self.override_box.setEnabled(not use_global)
        self.override_box.setVisible(not use_global)

    def _on_crf_slider_changed(self, val: int):
        labels = {
            14: "Archival Master",
            16: "Extremely High",
            18: "Near-Lossless (VFR Standard)",
            20: "High Quality",
            23: "Footage Compressor (Bodycams)",
            26: "Aggressive Compression",
            28: "Max Space Saving",
        }
        tag = labels.get(val, "Custom")
        self.crf_val_lbl.setText(f"CRF {val}  •  {tag}")

        total_bytes = getattr(self, "_batch_total_bytes", 0)
        if total_bytes <= 0:
            self.savings_lbl.setText("")
            return

        cur_mb = total_bytes / (1024 * 1024)
        is_bloated = (
            self._probed is not None
            and ("raw" in self._probed.video_codec.lower() or "avi" in self.source_path.lower() or "prores" in self._probed.video_codec.lower())
        )

        if is_bloated:
            ratio = max(0.04, min(0.35, 0.10 * (2 ** ((val - 23) / 6))))
        else:
            ratio = max(0.15, min(1.5, (2 ** ((18 - val) / 6))))

        est_mb = cur_mb * ratio
        diff_mb = cur_mb - est_mb
        pct_saved = max(0.0, (diff_mb / cur_mb) * 100.0)

        est_str = f"{est_mb / 1024:.2f} GB" if est_mb >= 1000 else f"{est_mb:.1f} MB"
        if pct_saved > 5:
            self.savings_lbl.setText(f"Estimated Output: ~{est_str} (Save ~{pct_saved:.0f}% disk space)")
            self.savings_lbl.setStyleSheet("font-size: 11px; color: #34d399; font-weight: 600;")
        else:
            self.savings_lbl.setText(f"Estimated Output: ~{est_str}")
            self.savings_lbl.setStyleSheet("font-size: 11px; color: #a1a1aa;")

    def _apply_theme_styling(self):
        is_dark = ThemeManager.is_dark
        card_bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.03)"
        card_bd = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"
        card_qss = f"background-color: {card_bg}; border: 1px solid {card_bd}; border-radius: 8px;"
        self.source_card.setStyleSheet(f"QFrame#SourceInfoCard {{ {card_qss} }}")
        self.ctrl_card.setStyleSheet(f"QFrame#CtrlCard {{ {card_qss} }}")
        self.progress_box.setStyleSheet(f"QFrame#ProgressBox {{ {card_qss} }}")

    def build_config(self) -> TranscodeConfig:
        use_global = self.use_global_cb.isChecked()
        if use_global:
            enc = db.get_setting("transcode_encoder", "auto")
            crf = int(db.get_setting("transcode_crf", "18"))
            fps_str = db.get_setting("transcode_framerate", "0.0")
            res_str = db.get_setting("transcode_max_height", "none")
            deint_str = db.get_setting("transcode_deinterlace", "auto")
        else:
            enc = self.encoder_combo.currentData() or "auto"
            crf = int(self.crf_slider.value())
            fps_str = self.fps_combo.currentData() or "0.0"
            res_str = self.res_combo.currentData() or "none"
            deint_str = self.deint_combo.currentData() or "auto"

        target_fps = float(fps_str) if fps_str != "0.0" else 0.0
        max_h = int(res_str) if res_str.isdigit() else None

        return TranscodeConfig(
            target_fps=target_fps,
            encoder=enc,
            crf=crf,
            preset="p6",
            max_height=max_h,
            deinterlace=deint_str,
            audio_mode="copy",           # Strictly copy per Cane's instruction
            replace_original=True        # Windows Recycle Bin swap per Cane's instruction
        )

    def _on_start_clicked(self):
        # 1. Check if FFmpeg is available
        is_avail, _, _ = get_ffmpeg_status()
        if not is_avail:
            InfoBar.error(
                title="FFmpeg Missing",
                content="Could not locate ffmpeg.exe on system PATH or app bin.",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500
            )
            return

        # 2. Enqueue jobs into global RenderQueueService
        cfg = self.build_config()
        added, skipped = render_queue.enqueue(self.source_paths, cfg)

        if skipped and not added:
            reasons = "\n".join(f"• {os.path.basename(p)}: {r}" for p, r in skipped[:4])
            InfoBar.warning(
                title="Could Not Queue Files",
                content=reasons,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )
            return

        # Listen for completion of added jobs so legacy callers of transcode_completed still fire if needed
        for job in added:
            render_queue.job_finished.connect(
                lambda j, ok, out_path, target_id=job.id: (
                    self.transcode_completed.emit(out_path) if (ok and j.id == target_id) else None
                )
            )

        self.jobs_queued.emit(added)
        self.accept()

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1500)
        self.reject()

    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1500)
        super().closeEvent(e)
