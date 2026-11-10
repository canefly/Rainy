import os
import webbrowser
from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal, QThreadPool, QEvent
from PySide6.QtGui import QCursor, QColor, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QFileDialog, QSizePolicy, QApplication, QDialog, QProgressBar, QStackedWidget
)
from qfluentwidgets import (
    MessageBoxBase, PushButton, PrimaryPushButton, ToolButton,
    FluentIcon, ComboBox, LineEdit, SearchLineEdit, CheckBox,
    SmoothScrollArea, InfoBar, InfoBarPosition, RoundMenu, Action, MessageBox,
    SegmentedWidget
)

from ..theme import ThemeManager
from ...core.database import db
from ...core.parser import normalize_link
from ...core.footage_matcher import FootageMatcher, FootageMatchItem, FootageScanWorker
from ...core.unfurl_engine import format_bytes_display
from ...core.render_queue import render_queue, RenderJob
from .widgets import safe_copy_to_clipboard, StatusDot, GpuSmoothScroller, MiddleClickAutoScroller
from .footage import QueueStrip, FootageLibraryStage, TranscribeStage


def _elide_middle_path(path: str, max_chars: int = 62) -> str:
    if not path or len(path) <= max_chars:
        return path
    half = (max_chars - 3) // 2
    return f"{path[:half]}...{path[-half:]}"


class FootageRelinkDialog(MessageBoxBase):
    """
    Two-Stage Footage Relink & Media Audit / Library Modal.
    - Stage A (Relink): Resolves script source links to local footage, binds VO, sorts missing items first,
      and eliminates duplicate title/filename labels.
    - Stage B (Library): High-performance footage explorer (QTableView) with multi-select, active/offline
      media audit verification, and sequential background FFmpeg render queue.
    Strictly NO emojis are used in icons, labels, or badges.
    """

    footage_linked = Signal(str, dict)      # (footage_folder, linked_footage_map)
    vo_linked = Signal(str, list)           # (vo_file_path, vo_segments)
    vo_transcript_ready = Signal(str, list, list)  # (transcript_path, vo_files, vo_segments)
    vo_transcribe_requested = Signal(str)   # (vo_file_path)

    def __init__(
        self,
        links: List[str],
        current_footage_dir: str = "",
        existing_linked: Optional[Dict[str, str]] = None,
        current_vo_path: str = "",
        existing_vo_segments: Optional[list] = None,
        doc_signature: str = "",
        parent: Optional[QWidget] = None,
        project_name: str = "",
        vo_transcript_path: str = "",
        vo_files: Optional[List[str]] = None,
    ):
        self._fallback_parent = None
        target_parent = parent or QApplication.activeWindow()
        if target_parent is None:
            self._fallback_parent = QWidget()
            self._fallback_parent.resize(960, 680)
            target_parent = self._fallback_parent

        super().__init__(parent=target_parent)

        self.links = list(dict.fromkeys(links))  # deduplicate preserving order
        self.footage_dir = current_footage_dir or ""
        self.manual_overrides: Dict[str, str] = (existing_linked or {}).copy()
        self.vo_file_path = current_vo_path or ""
        self.vo_segments: list = list(existing_vo_segments or [])
        self.doc_signature = doc_signature or "default"
        self.project_name = project_name or ""
        self.vo_transcript_path = vo_transcript_path or ""
        self.vo_files: List[str] = list(vo_files or ([current_vo_path] if current_vo_path else []))
        self.match_items: List[FootageMatchItem] = []
        self.card_widgets: List[QFrame] = []
        self._initial_stage_resolved = False

        # Dialog Sizing: Wide landscape format for clear side-by-side file auditing & table columns
        parent_w = target_parent.width() if target_parent else 980
        self.widget.setFixedWidth(min(920, max(740, parent_w - 40)))

        self.viewLayout.setSpacing(8)
        self.viewLayout.setContentsMargins(20, 18, 20, 12)

        # =====================================================================
        # 1. Modal Header: Title, Inline Stats, and Stage Toggle (Relink | Library)
        # =====================================================================
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.setAlignment(Qt.AlignVCenter)

        self.header_title = QLabel("Footage Relink & Media Library", self.widget)
        self.header_title.setObjectName("RelinkDialogTitle")
        title_row.addWidget(self.header_title)

        # Inline stats container matching Top Nav stats
        self.header_stats = QWidget(self.widget)
        self.header_stats.setObjectName("RelinkHeaderStats")
        hs_layout = QHBoxLayout(self.header_stats)
        hs_layout.setContentsMargins(0, 0, 0, 0)
        hs_layout.setSpacing(8)
        hs_layout.setAlignment(Qt.AlignVCenter)

        self.header_div1 = QFrame(self.header_stats)
        self.header_div1.setFrameShape(QFrame.VLine)
        self.header_div1.setFixedHeight(12)
        hs_layout.addWidget(self.header_div1)

        self.status_dot = StatusDot("#34d399", "rgba(52, 211, 153, 0.35)", self.header_stats)
        hs_layout.addWidget(self.status_dot)

        self.linked_stat_lbl = QLabel("0 / 0 Linked", self.header_stats)
        self.linked_stat_lbl.setObjectName("RelinkHeaderLinked")
        hs_layout.addWidget(self.linked_stat_lbl)

        self.header_div2 = QFrame(self.header_stats)
        self.header_div2.setFrameShape(QFrame.VLine)
        self.header_div2.setFixedHeight(12)
        hs_layout.addWidget(self.header_div2)

        self.size_stat_lbl = QLabel("0 B Local", self.header_stats)
        self.size_stat_lbl.setObjectName("RelinkHeaderSize")
        hs_layout.addWidget(self.size_stat_lbl)

        self.header_div3 = QFrame(self.header_stats)
        self.header_div3.setFrameShape(QFrame.VLine)
        self.header_div3.setFixedHeight(12)
        hs_layout.addWidget(self.header_div3)

        self.vo_header_stat_lbl = QLabel("VO: Unbound", self.header_stats)
        self.vo_header_stat_lbl.setObjectName("RelinkHeaderVO")
        self.vo_header_stat_lbl.setCursor(Qt.PointingHandCursor)
        self.vo_header_stat_lbl.setToolTip("Click to open Voiceover Transcription")
        self.vo_header_stat_lbl.mousePressEvent = lambda e: self.switch_stage("transcribe")
        hs_layout.addWidget(self.vo_header_stat_lbl)

        title_row.addWidget(self.header_stats)
        title_row.addStretch(1)

        # Segmented Stage Toggle: [ Relink | Library ]
        self.stage_tabs = SegmentedWidget(self.widget)
        # Disable sliding transition animation for instant responsive tab switching
        def _instant_stage_tab_slide(endValue, startValue=None):
            self.stage_tabs.slideAni.stop()
            self.stage_tabs.slideAni.setValue(endValue)
            self.stage_tabs.update()
        self.stage_tabs.slideAni.startAnimation = _instant_stage_tab_slide
        self.stage_tabs.setFixedHeight(30)
        self.stage_tabs.addItem(
            routeKey="relink",
            text="Relink",
            onClick=lambda: self.switch_stage("relink"),
        )
        self.stage_tabs.addItem(
            routeKey="library",
            text="Library",
            onClick=lambda: self.switch_stage("library"),
        )
        self.stage_tabs.addItem(
            routeKey="transcribe",
            text="Transcribe",
            onClick=lambda: self.switch_stage("transcribe"),
        )
        self.stage_tabs.setCurrentItem("relink")
        title_row.addWidget(self.stage_tabs, 0, Qt.AlignVCenter)

        header_layout.addLayout(title_row)

        self.header_desc = QLabel(
            "Verifies local footage against script source links based on filenames and exact probed byte sizes.",
            self.widget
        )
        self.header_desc.setObjectName("RelinkDialogSubtitle")
        self.header_desc.setWordWrap(True)
        header_layout.addWidget(self.header_desc)

        self.viewLayout.addLayout(header_layout)

        # =====================================================================
        # 2. Central Stage Stack (Stage A: Relink | Stage B: Library)
        # =====================================================================
        self.stage_stack = QStackedWidget(self.widget)
        self.stage_stack.setContentsMargins(0, 0, 0, 0)

        # --- Stage A: Relink Container ---
        self.relink_page = QWidget(self.stage_stack)
        relink_layout = QVBoxLayout(self.relink_page)
        relink_layout.setContentsMargins(0, 0, 0, 0)
        relink_layout.setSpacing(8)

        # 2a. Directory Selector Row
        dir_layout = QHBoxLayout()
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(8)

        dir_lbl = QLabel("Footage Folder:", self.relink_page)
        dir_lbl.setObjectName("RelinkFieldLabel")
        dir_lbl.setFixedWidth(92)
        dir_layout.addWidget(dir_lbl, 0, Qt.AlignVCenter)

        self.dir_edit = LineEdit(self.relink_page)
        self.dir_edit.setText(self.footage_dir)
        self.dir_edit.setPlaceholderText("Select directory containing downloaded footage...")
        self.dir_edit.setReadOnly(True)
        dir_layout.addWidget(self.dir_edit, 1, Qt.AlignVCenter)

        self.browse_btn = PushButton("Browse...", self.relink_page)
        self.browse_btn.setIcon(FluentIcon.FOLDER)
        self.browse_btn.setFixedHeight(32)
        self.browse_btn.clicked.connect(self._on_browse_directory)
        dir_layout.addWidget(self.browse_btn, 0, Qt.AlignVCenter)

        self.rescan_btn = ToolButton(FluentIcon.SYNC, self.relink_page)
        self.rescan_btn.setFixedSize(32, 32)
        self.rescan_btn.setToolTip("Rescan Directory & Audit Active Files")
        self.rescan_btn.clicked.connect(self._run_scan)
        dir_layout.addWidget(self.rescan_btn, 0, Qt.AlignVCenter)

        relink_layout.addLayout(dir_layout)
        self._refresh_vo_header_status()

        # 2b. Confirm & Continue Banner (Appears when 100% of script assets are linked)
        self.confirm_banner = QFrame(self.relink_page)
        self.confirm_banner.setObjectName("RelinkConfirmBanner")
        self.confirm_banner.setFixedHeight(34)
        self.confirm_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cb_layout = QHBoxLayout(self.confirm_banner)
        cb_layout.setContentsMargins(10, 4, 10, 4)
        cb_layout.setSpacing(10)

        self.confirm_banner_lbl = QLabel("All footage files are linked and active on disk.", self.confirm_banner)
        cb_layout.addWidget(self.confirm_banner_lbl, 1, Qt.AlignVCenter)

        self.continue_lib_btn = PrimaryPushButton("Confirm & Open Library", self.confirm_banner)
        self.continue_lib_btn.setIcon(FluentIcon.ACCEPT)
        self.continue_lib_btn.setFixedHeight(24)
        self.continue_lib_btn.clicked.connect(self._on_confirm_and_open_library)
        cb_layout.addWidget(self.continue_lib_btn, 0, Qt.AlignVCenter)

        self.confirm_banner.hide()
        relink_layout.addWidget(self.confirm_banner, 0)

        # 2d. Filter & Search Toolbar (Stage A)
        self.relink_toolbar = QWidget(self.relink_page)
        self.relink_toolbar.setFixedHeight(32)
        self.relink_toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        filter_layout = QHBoxLayout(self.relink_toolbar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)

        self.filter_combo = ComboBox(self.relink_toolbar)
        self.filter_combo.addItems(["All Files", "Linked Only", "Missing Only", "VFR"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(145)
        self.filter_combo.setFixedHeight(30)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_combo)

        self.search_edit = SearchLineEdit(self.relink_toolbar)
        self.search_edit.setPlaceholderText("Filter files by name or path...")
        self.search_edit.setFixedHeight(30)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.search_edit, 1)

        relink_layout.addWidget(self.relink_toolbar, 0)

        # 2e. Media Items Smooth Scroll Area (Compact non-duplicate rows, missing sorted first)
        self.scroll = SmoothScrollArea(self.relink_page)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(230)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_container = QWidget(self.scroll)
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(5)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_container)
        try:
            if hasattr(self.scroll, "delegate") and self.scroll.delegate:
                from qfluentwidgets.common.smooth_scroll import SmoothMode
                self.scroll.delegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        except Exception:
            pass
        if self.scroll.viewport():
            self.scroll.viewport().setAttribute(Qt.WA_StaticContents, True)
        self.relink_smooth_scroller = GpuSmoothScroller(self.scroll, self)
        self.relink_smooth_scroller.enable_smooth_wheel = True
        self.relink_middle_scroller = MiddleClickAutoScroller(self.scroll, self)
        relink_layout.addWidget(self.scroll, 1)

        # 2f. Bottom Options Row (Stage A)
        bottom_options = QHBoxLayout()
        bottom_options.setContentsMargins(0, 0, 0, 0)

        self.auto_relink_cb = CheckBox("Automatically relink remaining missing files in same folder", self.relink_page)
        self.auto_relink_cb.setChecked(True)
        bottom_options.addWidget(self.auto_relink_cb, 1, Qt.AlignVCenter)

        self.copy_missing_btn = PushButton("Copy Missing Links", self.relink_page)
        self.copy_missing_btn.setIcon(FluentIcon.COPY)
        self.copy_missing_btn.setFixedHeight(28)
        self.copy_missing_btn.setToolTip("Copy all missing asset URLs to clipboard")
        self.copy_missing_btn.clicked.connect(self._on_copy_missing_urls)
        bottom_options.addWidget(self.copy_missing_btn, 0, Qt.AlignVCenter)

        relink_layout.addLayout(bottom_options)
        self.stage_stack.addWidget(self.relink_page)

        # --- Stage B: Footage Library Explorer ---
        self.library_stage = FootageLibraryStage(self.stage_stack)
        self.library_stage.batch_transcode_requested.connect(self._launch_batch_transcode)
        self.library_stage.reveal_requested.connect(self._reveal_in_explorer)
        self.library_stage.locate_requested.connect(self._on_locate_file)
        self.library_stage.unlink_requested.connect(self._unlink_multiple_files)
        self.library_stage.properties_requested.connect(self._show_properties_dialog)
        self.library_stage.switch_to_relink_requested.connect(lambda: self.switch_stage("relink"))
        self.stage_stack.addWidget(self.library_stage)

        # --- Stage C: Dedicated VO Transcription Hub ---
        self.transcribe_stage = TranscribeStage(
            parent=self.stage_stack,
            project_name=self.project_name,
            initial_vo_paths=self.vo_files,
            initial_transcript_path=self.vo_transcript_path,
        )
        self.transcribe_stage.vo_status_changed.connect(self._on_vo_stage_status_changed)
        self.transcribe_stage.transcription_completed.connect(self._on_vo_stage_completed)
        self.stage_stack.addWidget(self.transcribe_stage)

        self.viewLayout.addWidget(self.stage_stack)

        # =====================================================================
        # 3. Always-Visible Render Queue Strip (Bottom of Modal)
        # =====================================================================
        self.queue_strip = QueueStrip(self.widget)
        self.viewLayout.addWidget(self.queue_strip)

        # Connect global render queue completion to auto-reprobe items in place
        render_queue.job_finished.connect(self._on_queue_job_finished)

        # =====================================================================
        # 4. Action Buttons in Button Group
        # =====================================================================
        self.yesButton.setText("Apply && Save")
        self.yesButton.setIcon(FluentIcon.ACCEPT)
        self.cancelButton.setText("Close")
        self.yesButton.clicked.connect(self._on_apply_save)

        self.apply_theme()

        # Initial scan if directory is already selected
        if self.footage_dir and os.path.isdir(self.footage_dir):
            self._run_scan()
        else:
            self._populate_unscanned_list()

    # -------------------------------------------------------------------------
    # Stage Switching & Health Check
    # -------------------------------------------------------------------------
    def _confirm_setting_key(self) -> str:
        return f"footage_relink_confirmed:{self.doc_signature}"

    def is_project_relink_confirmed(self) -> bool:
        return db.get_setting(self._confirm_setting_key(), "false") == "true"

    def set_project_relink_confirmed(self, confirmed: bool):
        db.set_setting(self._confirm_setting_key(), "true" if confirmed else "false")

    def switch_stage(self, stage_key: str):
        if stage_key == "library":
            self.run_health_check()
            self.library_stage.set_items(self.match_items)
            self.stage_stack.setCurrentWidget(self.library_stage)
            self.stage_tabs.setCurrentItem("library")
            self.queue_strip.show()
            self.header_desc.setText(
                "Multi-select footage to queue CFR transcodes, compress bloated clips, or reveal in File Explorer."
            )
        elif stage_key == "transcribe":
            self.stage_stack.setCurrentWidget(self.transcribe_stage)
            self.stage_tabs.setCurrentItem("transcribe")
            self.queue_strip.hide()
            self.header_desc.setText(
                "Queue multi-part voiceovers, stream transcription segments live in real-time, and save independent JSON."
            )
        else:
            self.stage_stack.setCurrentWidget(self.relink_page)
            self.stage_tabs.setCurrentItem("relink")
            self.queue_strip.show()
            self.header_desc.setText(
                "Verifies local footage against script source links based on filenames and exact probed byte sizes."
            )

    def _on_vo_stage_status_changed(self, status_text: str):
        if hasattr(self, "vo_header_stat_lbl"):
            self.vo_header_stat_lbl.setText(status_text)
            if "Ready" in status_text:
                self.vo_header_stat_lbl.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 700;")
            elif "%" in status_text:
                self.vo_header_stat_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 700;")
            elif "Error" in status_text:
                self.vo_header_stat_lbl.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: 700;")

    def _on_vo_stage_completed(self, json_path: str, vo_files: list, segments: list):
        self.vo_transcript_path = json_path
        self.vo_files = list(vo_files)
        self.vo_segments = list(segments)
        if vo_files:
            self.vo_file_path = vo_files[0]
        self._refresh_vo_header_status()
        self.vo_linked.emit(self.vo_file_path, self.vo_segments)
        self.vo_transcript_ready.emit(self.vo_transcript_path, self.vo_files, self.vo_segments)

    def _on_confirm_and_open_library(self):
        self.set_project_relink_confirmed(True)
        self.switch_stage("library")

    def run_health_check(self) -> int:
        """
        Verifies whether linked files still exist on disk.
        Returns count of missing/offline items and updates UI badges if anything went offline.
        """
        changed = False
        for it in self.match_items:
            if it.matched_path:
                exists_now = os.path.isfile(it.matched_path)
                if it.is_matched != exists_now:
                    it.is_matched = exists_now
                    it.match_tier = "OFFLINE" if not exists_now else (it.match_tier if it.match_tier != "OFFLINE" else "MANUAL")
                    changed = True

        if changed:
            self._rebuild_cards()
            self.library_stage.set_items(self.match_items)
            self._update_stats()
            self._apply_filter()
        else:
            self.library_stage.update_banners()

        return sum(1 for it in self.match_items if not (it.is_matched and it.matched_path and os.path.isfile(it.matched_path)))

    def event(self, e: QEvent):
        if e.type() == QEvent.WindowActivate and self.match_items:
            self.run_health_check()
        return super().event(e)

    # -------------------------------------------------------------------------
    # Directory & VO Handlers
    # -------------------------------------------------------------------------
    def _on_browse_directory(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Local Footage Directory",
            self.footage_dir or os.path.expanduser("~")
        )
        if folder:
            self.footage_dir = os.path.normpath(folder)
            self.dir_edit.setText(self.footage_dir)
            self._run_scan()

    def _refresh_vo_header_status(self):
        if not hasattr(self, "vo_header_stat_lbl"):
            return
        if self.vo_file_path and os.path.isfile(self.vo_file_path):
            if self.vo_segments:
                parts_cnt = len(self.vo_files)
                parts_str = f", {parts_cnt} parts" if parts_cnt > 1 else ""
                self.vo_header_stat_lbl.setText(f"VO: Ready ({len(self.vo_segments)} segs{parts_str})")
                self.vo_header_stat_lbl.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 700;")
            else:
                self.vo_header_stat_lbl.setText("VO: Staged (Click Transcribe)")
                self.vo_header_stat_lbl.setStyleSheet("color: #fbbf24; font-size: 11px; font-weight: 700;")
        else:
            self.vo_header_stat_lbl.setText("VO: Unbound")
            self.vo_header_stat_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")

    # -------------------------------------------------------------------------
    # Directory Scan & Matching
    # -------------------------------------------------------------------------
    def _run_scan(self):
        if not self.footage_dir or not os.path.isdir(self.footage_dir):
            self._populate_unscanned_list()
            return

        self.rescan_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.header_desc.setText("Scanning local directory and calculating byte signatures...")

        worker = FootageScanWorker(self.footage_dir, self.links, self.manual_overrides)
        worker.signals.finished.connect(self._on_scan_finished)
        worker.signals.error.connect(self._on_scan_error)
        QThreadPool.globalInstance().start(worker)

    def _on_scan_finished(self, match_items: List[FootageMatchItem]):
        self.match_items = match_items
        self.rescan_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self._rebuild_cards()
        self.library_stage.set_items(self.match_items)
        self._update_stats()
        self._apply_filter()
        self._resolve_initial_stage_if_needed()

    def _on_scan_error(self, err_msg: str):
        self.rescan_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.header_desc.setText(f"Scan failed: {err_msg}")

    def _populate_unscanned_list(self):
        matcher = FootageMatcher("")
        self.match_items = matcher.match_links(self.links, self.manual_overrides)
        self._rebuild_cards()
        self.library_stage.set_items(self.match_items)
        self._update_stats()
        self._apply_filter()
        self._resolve_initial_stage_if_needed()

    def _resolve_initial_stage_if_needed(self):
        if self._initial_stage_resolved:
            return
        self._initial_stage_resolved = True
        total = len(self.match_items)
        missing = sum(
            1 for it in self.match_items
            if not (it.is_matched and it.matched_path and os.path.isfile(it.matched_path))
        )
        # Open directly in Library if all files are linked & active and project was confirmed once
        if total > 0 and missing == 0 and self.is_project_relink_confirmed():
            self.switch_stage("library")
        else:
            self.switch_stage("relink")

    # -------------------------------------------------------------------------
    # Stats, Badges, and Non-Repeating Compact Rows
    # -------------------------------------------------------------------------
    def _update_stats(self):
        total = len(self.match_items)
        linked = sum(
            1 for item in self.match_items
            if item.is_matched and item.matched_path and os.path.isfile(item.matched_path)
        )
        missing = total - linked
        vfr_count = sum(
            1 for item in self.match_items
            if item.is_matched and item.matched_path and os.path.isfile(item.matched_path) and item.is_vfr
        )
        pct = int((linked / total) * 100) if total > 0 else 0

        local_bytes = 0
        for item in self.match_items:
            if item.is_matched and item.matched_path and os.path.isfile(item.matched_path):
                try:
                    local_bytes += os.path.getsize(item.matched_path)
                except Exception:
                    pass

        size_display = format_bytes_display(local_bytes) or "0 B"
        self.size_stat_lbl.setText(f"{size_display} Local")

        # Update Segmented Toggle Relink badge with missing/offline count
        try:
            relink_tab_text = f"Relink ({missing})" if missing > 0 else "Relink"
            if "relink" in self.stage_tabs.items:
                self.stage_tabs.items["relink"].setText(relink_tab_text)
            if "library" in self.stage_tabs.items:
                self.stage_tabs.items["library"].setText(f"Library ({linked})")
        except Exception:
            pass

        if total > 0 and missing == 0:
            self.status_dot._dot_color = QColor("#34d399")
            self.status_dot._halo_color = QColor("rgba(52, 211, 153, 0.35)")
            self.linked_stat_lbl.setText(f"{linked} / {total} Linked ({pct}%)")
            self.linked_stat_lbl.setStyleSheet("color: #34d399; font-weight: 700; font-size: 11px;")
            self.confirm_banner_lbl.setText(
                f"All {total} footage files are linked and active on disk."
            )
            self.confirm_banner.show()
        elif total > 0:
            self.status_dot._dot_color = QColor("#fbbf24")
            self.status_dot._halo_color = QColor("rgba(245, 158, 11, 0.35)")
            self.linked_stat_lbl.setText(f"{linked} / {total} Linked ({pct}%)")
            self.linked_stat_lbl.setStyleSheet("color: #fbbf24; font-weight: 700; font-size: 11px;")
            self.confirm_banner.hide()
        else:
            self.status_dot._dot_color = QColor("#94a3b8")
            self.status_dot._halo_color = QColor("rgba(148, 163, 184, 0.25)")
            self.linked_stat_lbl.setText("0 / 0 Linked")
            self.linked_stat_lbl.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 11px;")
            self.confirm_banner.hide()

        self.status_dot.update()

        self.filter_combo.setItemText(0, f"All Files ({total})")
        self.filter_combo.setItemText(1, f"Linked ({linked})")
        self.filter_combo.setItemText(2, f"Missing ({missing})")
        self.filter_combo.setItemText(3, f"VFR ({vfr_count})")

        self.copy_missing_btn.setEnabled(missing > 0)
        self.copy_missing_btn.setText(f"Copy Missing Links ({missing})")

    def _rebuild_cards(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.card_widgets.clear()

        pal = ThemeManager.get_palette()
        surface = pal.get("surface", "#18181b")
        border = pal.get("border", "#27272a")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")

        # Sort MISSING/OFFLINE first while preserving relative script order within each group
        sorted_items = sorted(
            self.match_items,
            key=lambda it: 1 if (it.is_matched and it.matched_path and os.path.isfile(it.matched_path)) else 0
        )

        for item in sorted_items:
            card = QFrame(self.list_container)
            card.setObjectName("RelinkItemCard")
            card.setCursor(QCursor(Qt.PointingHandCursor))
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(10, 5, 10, 5)
            c_layout.setSpacing(10)

            is_online = bool(item.is_matched and item.matched_path and os.path.isfile(item.matched_path))

            # 1. Status Pill (LINKED vs MISSING/OFFLINE - strictly no emojis)
            status_pill = QLabel(card)
            status_pill.setFixedHeight(20)
            status_pill.setFixedWidth(64)
            status_pill.setAlignment(Qt.AlignCenter)
            if is_online:
                status_pill.setText("LINKED")
                status_pill.setStyleSheet("""
                    QLabel {
                        background-color: rgba(16, 185, 129, 0.16);
                        color: #34d399;
                        border: 1px solid rgba(16, 185, 129, 0.38);
                        border-radius: 4px;
                        font-size: 10px;
                        font-weight: 700;
                    }
                """)
            else:
                pill_txt = "OFFLINE" if item.matched_path else "MISSING"
                status_pill.setText(pill_txt)
                status_pill.setStyleSheet("""
                    QLabel {
                        background-color: rgba(245, 158, 11, 0.16);
                        color: #fbbf24;
                        border: 1px solid rgba(245, 158, 11, 0.38);
                        border-radius: 4px;
                        font-size: 10px;
                        font-weight: 700;
                    }
                """)
            c_layout.addWidget(status_pill, 0, Qt.AlignVCenter)

            if is_online and item.is_vfr:
                vfr_pill = QLabel("VFR", card)
                vfr_pill.setFixedHeight(20)
                vfr_pill.setAlignment(Qt.AlignCenter)
                vfr_pill.setToolTip(
                    f"Variable Frame Rate ({item.frame_rate:.2f} FPS avg)\n"
                    "Audio/video timecode will drift in Premiere Pro without CFR transcode or PTS compensation."
                )
                vfr_pill.setStyleSheet("""
                    QLabel {
                        background-color: rgba(245, 158, 11, 0.16);
                        color: #fbbf24;
                        border: 1px solid rgba(245, 158, 11, 0.42);
                        border-radius: 4px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 0px 6px;
                    }
                """)
                c_layout.addWidget(vfr_pill, 0, Qt.AlignVCenter)

            if is_online and getattr(item, "is_interlaced", False):
                int_pill = QLabel("INTERLACED", card)
                int_pill.setFixedHeight(20)
                int_pill.setAlignment(Qt.AlignCenter)
                int_pill.setToolTip(
                    f"Interlaced Scan ({getattr(item, 'field_order', 'interlaced').upper()})\n"
                    "Re-encode with bwdif decomb filter to remove horizontal combing artifacts."
                )
                int_pill.setStyleSheet("""
                    QLabel {
                        background-color: rgba(245, 158, 11, 0.16);
                        color: #fbbf24;
                        border: 1px solid rgba(245, 158, 11, 0.42);
                        border-radius: 4px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 0px 6px;
                    }
                """)
                c_layout.addWidget(int_pill, 0, Qt.AlignVCenter)

            # 2. Middle Text Details (Zero Duplicate Filename!)
            # Line 1: Filename (if linked) or Script Title (if missing)
            # Line 2: Elided parent folder path (plus "Script: <title>" ONLY when title != filename)
            mid_layout = QVBoxLayout()
            mid_layout.setContentsMargins(0, 0, 0, 0)
            mid_layout.setSpacing(1)

            if is_online and item.matched_path:
                matched_filename = os.path.basename(item.matched_path)
                primary_text = matched_filename
                parent_folder = os.path.dirname(item.matched_path)
                elided_folder = _elide_middle_path(parent_folder, 58)
                script_title_clean = (item.title or "").strip()
                if script_title_clean and script_title_clean.lower() != matched_filename.lower():
                    secondary_text = f"{elided_folder}  •  Script: {script_title_clean}"
                else:
                    secondary_text = elided_folder
                secondary_tooltip = f"Full Local Path:\n{item.matched_path}\nScript Title: {item.title}"
            else:
                primary_text = item.title or os.path.basename(item.url)
                if item.matched_path:
                    secondary_text = f"Offline • File moved or deleted ({_elide_middle_path(item.matched_path, 48)})"
                else:
                    secondary_text = "Offline • Missing in footage directory (Double-click to locate)"
                secondary_tooltip = f"Source URL:\n{item.url}"

            title_lbl = QLabel(primary_text, card)
            title_lbl.setStyleSheet(f"color: {text_col}; font-weight: 600; font-size: 12px;")
            title_lbl.setToolTip(primary_text)
            title_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            mid_layout.addWidget(title_lbl)

            sub_lbl = QLabel(secondary_text, card)
            sub_lbl.setToolTip(secondary_tooltip)
            sub_style = f"color: {muted_col}; font-size: 11px;"
            if not is_online:
                sub_style += " font-style: italic;"
            sub_lbl.setStyleSheet(sub_style)
            sub_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            mid_layout.addWidget(sub_lbl)

            c_layout.addLayout(mid_layout, 1)

            # 3. Size / Duration / FPS Badge
            sz_parts = []
            if item.duration_display:
                sz_parts.append(item.duration_display)
            if item.file_size_display:
                sz_parts.append(item.file_size_display)
            if item.frame_rate and item.frame_rate > 0:
                fps_txt = f"{item.frame_rate:.2f} FPS" if item.is_vfr else f"{item.frame_rate:.3f}".rstrip("0").rstrip(".") + " FPS"
                sz_parts.append(fps_txt)
            sz_text = " • ".join(sz_parts) if sz_parts else "—"

            sz_badge = QLabel(sz_text, card)
            sz_badge.setFixedWidth(170)
            sz_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sz_badge.setStyleSheet(f"color: {muted_col}; font-size: 11px; font-weight: 600;")
            c_layout.addWidget(sz_badge, 0, Qt.AlignVCenter)

            card.mouseDoubleClickEvent = lambda e, it=item: self._on_locate_file(it)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, it=item, c=card: self._show_card_context_menu(c.mapToGlobal(pos), it)
            )

            card.setProperty("match_item", item)
            card.setProperty("is_matched", is_online)
            card.setStyleSheet(f"""
                QFrame#RelinkItemCard {{
                    background-color: {surface};
                    border: 1px solid {border};
                    border-radius: 6px;
                }}
                QFrame#RelinkItemCard:hover {{
                    border-color: rgba(56, 189, 248, 0.4);
                }}
            """)

            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.card_widgets.append(card)

    # -------------------------------------------------------------------------
    # Context Menus, Locate, Unlink, Transcode Queue Integration
    # -------------------------------------------------------------------------
    def _show_card_context_menu(self, global_pos, item: FootageMatchItem):
        menu = RoundMenu(parent=self)

        loc_act = Action(FluentIcon.FOLDER_ADD, "Locate File Manually...", menu)
        loc_act.triggered.connect(lambda: self._on_locate_file(item))
        menu.addAction(loc_act)

        if item.is_matched and item.matched_path:
            rev_act = Action(FluentIcon.FOLDER, "Reveal in File Explorer", menu)
            rev_act.triggered.connect(lambda: self._reveal_in_explorer(item.matched_path))
            menu.addAction(rev_act)

            cp_act = Action(FluentIcon.COPY, "Copy Full Local Path", menu)
            cp_act.triggered.connect(lambda: safe_copy_to_clipboard(item.matched_path))
            menu.addAction(cp_act)

            un_act = Action(FluentIcon.DELETE, "Unlink File", menu)
            un_act.triggered.connect(lambda: self._unlink_file(item))
            menu.addAction(un_act)

        if item.is_matched and item.matched_path and os.path.isfile(item.matched_path):
            menu.addSeparator()
            preset = "cfr" if item.is_vfr else "custom"
            tc_act = Action(FluentIcon.MOVIE, "Re-encode Clip...", menu)
            tc_act.triggered.connect(lambda: self._launch_transcode(item, preset_mode=preset))
            menu.addAction(tc_act)

        menu.addSeparator()

        open_act = Action(FluentIcon.SHARE, "Open Source Link in Browser", menu)
        open_act.triggered.connect(lambda: webbrowser.open(item.url))
        menu.addAction(open_act)

        prop_act = Action(FluentIcon.INFO, "Properties & Details...", menu)
        prop_act.triggered.connect(lambda: self._show_properties_dialog(item))
        menu.addAction(prop_act)

        menu.exec(global_pos, ani=False)

    def _reveal_in_explorer(self, file_path: str):
        """
        Reveals and highlights the exact target file in Windows File Explorer.
        Wait why did explorer open Documents before? Because subprocess.run(["explorer", "/select,path"])
        uses Python's list2cmdline which escapes paths with spaces as `/select,"C:\\path with spaces\\file.mp4"`,
        which explorer.exe fails to parse and falls back to the user's Documents folder!
        Using Win32 Shell32 SHOpenFolderAndSelectItems (with raw command-line fallback) fixes this 100%.
        """
        if not file_path:
            return
        abs_path = os.path.normpath(os.path.abspath(file_path))
        if not os.path.exists(abs_path):
            return

        if os.name == "nt":
            try:
                import ctypes
                shell32 = ctypes.windll.shell32
                ole32 = ctypes.windll.ole32
                ole32.CoInitialize(None)
                ILCreateFromPathW = shell32.ILCreateFromPathW
                ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
                ILCreateFromPathW.restype = ctypes.c_void_p
                SHOpenFolderAndSelectItems = shell32.SHOpenFolderAndSelectItems
                SHOpenFolderAndSelectItems.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong
                ]
                SHOpenFolderAndSelectItems.restype = ctypes.c_long
                ILFree = shell32.ILFree
                ILFree.argtypes = [ctypes.c_void_p]

                pidl = ILCreateFromPathW(abs_path)
                if pidl:
                    try:
                        res = SHOpenFolderAndSelectItems(pidl, 0, None, 0)
                        if res == 0:
                            return
                    finally:
                        ILFree(pidl)
            except Exception:
                pass

            import subprocess
            subprocess.Popen(f'explorer.exe /select,"{abs_path}"')
        else:
            parent_dir = os.path.dirname(abs_path)
            webbrowser.open(parent_dir)

    def _unlink_file(self, item: FootageMatchItem):
        self._unlink_multiple_files([item])

    def _unlink_multiple_files(self, items: List[FootageMatchItem]):
        for item in items:
            item.matched_path = None
            item.match_tier = "MISSING"
            item.is_matched = False
            self.manual_overrides.pop(item.norm_url, None)
            self.manual_overrides.pop(item.url, None)
        self._rebuild_cards()
        self.library_stage.set_items(self.match_items)
        self._update_stats()
        self._apply_filter()

    def _launch_transcode(self, item: FootageMatchItem, preset_mode: str = "custom"):
        if not item or not item.matched_path or not os.path.isfile(item.matched_path):
            return
        self._launch_batch_transcode([item], preset_mode=preset_mode)

    def _launch_batch_transcode(self, items: List[FootageMatchItem], preset_mode: str = "custom"):
        paths = [
            it.matched_path for it in items
            if it and it.matched_path and os.path.isfile(it.matched_path)
        ]
        if not paths:
            return
        from .transcode_dialog import TranscodeDialog
        dlg = TranscodeDialog(
            source_path=paths[0],
            source_paths=paths,
            preset_mode=preset_mode,
            parent=self,
        )
        dlg.jobs_queued.connect(self._on_jobs_queued)
        dlg.exec()

    def _on_jobs_queued(self, added_jobs: list):
        if not added_jobs:
            return
        self.queue_strip.set_expanded(True)
        self.queue_strip.refresh()
        self.library_stage.model.refresh_rows()
        InfoBar.success(
            title="Added to Render Queue",
            content=f"Queued {len(added_jobs)} clip{'s' if len(added_jobs) != 1 else ''} for background encoding.",
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
        )

    def _on_queue_job_finished(self, job: RenderJob, success: bool, final_path_or_err: str):
        if not success:
            return
        norm_src = os.path.normcase(os.path.abspath(job.source_path))
        for it in self.match_items:
            if it.matched_path and os.path.normcase(os.path.abspath(it.matched_path)) == norm_src:
                new_path = final_path_or_err if os.path.isfile(final_path_or_err) else job.source_path
                self._on_item_transcode_finished(it, new_path)
                break

    def _on_item_transcode_finished(self, item: FootageMatchItem, new_path: str):
        from ...core.media_probe import probe_media_file
        probed = probe_media_file(new_path, use_cache=False)
        if probed:
            item.matched_path = new_path
            item.file_size_bytes = probed.file_size
            item.file_size_display = format_bytes_display(probed.file_size)
            item.duration_seconds = probed.duration_seconds
            item.duration_display = probed.duration_display
            item.resolution = probed.resolution
            item.frame_rate = probed.frame_rate
            item.is_vfr = probed.is_vfr
            item.is_interlaced = getattr(probed, "is_interlaced", False)
            item.field_order = getattr(probed, "field_order", "progressive")
            item.video_codec = probed.video_codec or ""
            item.audio_codec = probed.audio_codec or ""
        self._rebuild_cards()
        self.library_stage.set_items(self.match_items)
        self._update_stats()
        self._apply_filter()

    def _show_properties_dialog(self, item: FootageMatchItem):
        status_text = "Linked (Active)" if (item.is_matched and item.matched_path and os.path.isfile(item.matched_path)) else "Missing / Offline"
        path_text = item.matched_path or "Not Linked"
        size_info = item.file_size_display or "Unknown"
        if item.file_size_bytes:
            size_info += f" ({item.file_size_bytes:,} bytes)"
        tier_text = item.match_tier
        dur_text = item.duration_display or "Unknown"
        res_text = item.resolution or "Unknown"
        fps_text = f"{item.frame_rate:.2f} fps ({'VFR' if item.is_vfr else 'CFR'})" if item.frame_rate else "Unknown"
        codec_text = (item.video_codec or "Unknown").upper()

        content = (
            f"Asset Title: {item.title}\n"
            f"Status: {status_text}\n"
            f"Match Tier: {tier_text}\n"
            f"Codec: {codec_text}\n"
            f"Duration: {dur_text}\n"
            f"Resolution: {res_text}\n"
            f"Frame Rate: {fps_text}\n"
            f"Local File: {path_text}\n"
            f"File Size: {size_info}\n"
            f"Source URL: {item.url}"
        )
        box = MessageBox("Media Properties", content, self)
        box.cancelButton.hide()
        box.yesButton.setText("Close")
        box.exec()

    def _on_locate_file(self, item: FootageMatchItem):
        initial_dir = self.footage_dir if (self.footage_dir and os.path.isdir(self.footage_dir)) else os.path.expanduser("~")
        chosen_file, _ = QFileDialog.getOpenFileName(
            self,
            f"Locate file for: {item.title}",
            initial_dir,
            "Media Files (*.*)"
        )
        if not chosen_file or not os.path.isfile(chosen_file):
            return

        norm_chosen = os.path.normpath(chosen_file)
        item.matched_path = norm_chosen
        item.match_tier = "MANUAL"
        item.is_matched = True
        self.manual_overrides[item.norm_url] = norm_chosen

        try:
            from ...core.media_probe import probe_media_file
            probed = probe_media_file(norm_chosen, use_cache=True)
            if probed:
                item.duration_seconds = probed.duration_seconds
                item.duration_display = probed.duration_display
                item.resolution = probed.resolution
                item.frame_rate = probed.frame_rate
                item.is_vfr = probed.is_vfr
                item.video_codec = probed.video_codec or ""
                item.audio_codec = probed.audio_codec or ""
                item.file_size_bytes = probed.file_size
                item.file_size_display = format_bytes_display(probed.file_size)
        except Exception:
            pass

        if self.auto_relink_cb.isChecked():
            parent_dir = os.path.dirname(norm_chosen)
            self._auto_relink_siblings(parent_dir)

        self._rebuild_cards()
        self.library_stage.set_items(self.match_items)
        self._update_stats()
        self._apply_filter()

    def _auto_relink_siblings(self, sibling_dir: str):
        """Scans the sibling directory to automatically link any other missing assets."""
        matcher = FootageMatcher(sibling_dir)
        matcher.scan_directory()
        missing_items = [it for it in self.match_items if not it.is_matched]
        for it in missing_items:
            res = matcher.match_links([it.url])
            if res and res[0].is_matched and res[0].matched_path:
                it.matched_path = res[0].matched_path
                it.match_tier = res[0].match_tier
                it.is_matched = True
                it.duration_seconds = res[0].duration_seconds
                it.duration_display = res[0].duration_display
                it.resolution = res[0].resolution
                it.frame_rate = res[0].frame_rate
                it.is_vfr = res[0].is_vfr
                it.video_codec = res[0].video_codec
                it.audio_codec = res[0].audio_codec
                it.file_size_bytes = res[0].file_size_bytes
                it.file_size_display = res[0].file_size_display
                self.manual_overrides[it.norm_url] = res[0].matched_path

    def _on_copy_missing_urls(self):
        missing_urls = [it.url for it in self.match_items if not it.is_matched]
        if missing_urls:
            text = "\n".join(missing_urls)
            safe_copy_to_clipboard(text)
            self.copy_missing_btn.setText("Copied!")
            InfoBar.success(
                title="Links Copied",
                content=f"Copied {len(missing_urls)} missing URLs to clipboard.",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500
            )

    def _apply_filter(self):
        filter_text = self.filter_combo.currentText()
        query = self.search_edit.text().strip().lower()

        for card in self.card_widgets:
            item: FootageMatchItem = card.property("match_item")
            is_matched: bool = card.property("is_matched")

            if "Linked" in filter_text and not is_matched:
                card.hide()
                continue
            if "Missing" in filter_text and is_matched:
                card.hide()
                continue
            if "VFR" in filter_text and not (is_matched and item.is_vfr):
                card.hide()
                continue

            if query:
                title_match = query in (item.title or "").lower()
                path_match = query in (item.matched_path or "").lower()
                url_match = query in (item.url or "").lower()
                if not (title_match or path_match or url_match):
                    card.hide()
                    continue

            card.show()

    def _on_apply_save(self):
        linked_map = {}
        for it in self.match_items:
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path):
                linked_map[it.norm_url] = it.matched_path

        # If all links are bound on Apply & Save, mark project relink confirmed
        if self.match_items and len(linked_map) == len(self.match_items):
            self.set_project_relink_confirmed(True)

        self.footage_linked.emit(self.footage_dir, linked_map)
        if self.vo_file_path:
            self.vo_linked.emit(self.vo_file_path, self.vo_segments)
        if self.vo_transcript_path or self.vo_files:
            self.vo_transcript_ready.emit(self.vo_transcript_path, self.vo_files, self.vo_segments)
        self.accept()

    def showEvent(self, e):
        self.setGraphicsEffect(None)
        p = self.parentWidget() or self.window()
        if p and p.width() > 0 and p.height() > 0:
            self.resize(p.size())
        QDialog.showEvent(self, e)
        if hasattr(self, "apply_theme"):
            self.apply_theme()

    def cleanup(self):
        """Halts active transcription workers and forces VRAM/resource deallocation."""
        if hasattr(self, "transcribe_stage") and self.transcribe_stage:
            try:
                self.transcribe_stage.cleanup()
            except Exception:
                pass
        import gc
        gc.collect()
        gc.collect()

    def done(self, code):
        self.cleanup()
        self.setGraphicsEffect(None)
        if hasattr(self, "widget") and self.widget:
            self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)

    def reject(self):
        self.cleanup()
        super().reject()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        border = pal.get("border", "#27272a")

        self.header_title.setStyleSheet(f"""
            QLabel#RelinkDialogTitle {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
        """)
        self.header_desc.setStyleSheet(f"""
            QLabel#RelinkDialogSubtitle {{
                color: {muted};
                font-size: 12px;
            }}
        """)
        self.header_div1.setStyleSheet(f"color: {border};")
        self.header_div2.setStyleSheet(f"color: {border};")
        if hasattr(self, "header_div3"):
            self.header_div3.setStyleSheet(f"color: {border};")
        self.size_stat_lbl.setStyleSheet(f"""
            QLabel#RelinkHeaderSize {{
                color: {muted};
                font-size: 11px;
                font-weight: 600;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            }}
        """)
        self.confirm_banner.setStyleSheet("""
            QFrame#RelinkConfirmBanner {
                background-color: rgba(16, 185, 129, 0.12);
                border: 1px solid rgba(16, 185, 129, 0.36);
                border-radius: 6px;
            }
        """)
        self.confirm_banner_lbl.setStyleSheet("color: #34d399; font-size: 11.5px; font-weight: 700; border: none; background: transparent;")
        self._refresh_vo_header_status()
        self._update_stats()
        if hasattr(self, "library_stage"):
            self.library_stage.apply_theme()
        if hasattr(self, "queue_strip"):
            self.queue_strip.apply_theme()
