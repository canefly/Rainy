import os
import webbrowser
from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal, QThreadPool, QEvent, QSize, QRectF, QPoint
from PySide6.QtGui import QCursor, QColor, QFontMetrics, QPainter, QPainterPath, QPixmap, QImage, QBrush, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QFileDialog, QSizePolicy, QApplication, QDialog, QProgressBar, QStackedWidget,
    QGraphicsScene, QGraphicsPathItem, QGraphicsDropShadowEffect
)
from qfluentwidgets import (
    MessageBoxBase, PushButton, PrimaryPushButton, ToolButton,
    FluentIcon, ComboBox, LineEdit, SearchLineEdit, CheckBox,
    SmoothScrollArea, InfoBar, InfoBarPosition, RoundMenu, Action, MessageBox,
    SegmentedWidget, Pivot, HyperlinkButton, TransparentToolButton
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


class _CachedCardShadow(QWidget):
    """
    Drop-in replacement for the card's live QGraphicsDropShadowEffect.

    A live effect forces Qt to re-render and re-blur the ENTIRE card offscreen on every
    scroll frame (and disables viewport scroll blitting). This renders the identical shadow
    (same QGraphicsDropShadowEffect, same blur/offset/color, same rounded card shape) ONCE
    per card size into a pixmap and paints that from a layer between the dim mask and the card.
    """

    def __init__(self, card: QWidget, parent: QWidget, blur: float = 60, offset=(0, 10),
                 color: QColor = QColor(0, 0, 0, 50), radius: float = 10.0,
                 edge_color: QColor = QColor(58, 58, 58)):
        super().__init__(parent)
        self._card = card
        self._blur, self._offset, self._color = blur, offset, QColor(color)
        self._radius, self._edge = radius, QColor(edge_color)
        self._pix: Optional[QPixmap] = None
        self._pix_key = None
        self._origin = QPoint(0, 0)  # pixmap top-left relative to the card's top-left
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # mask clicks still close
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        card.installEventFilter(self)
        self._sync()

    def _render(self):
        w, h = self._card.width(), self._card.height()
        dpr = self.devicePixelRatioF() or 1.0
        key = (w, h, dpr)
        if self._pix_key == key or w <= 0 or h <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
        item = QGraphicsPathItem(path)
        item.setPen(QPen(Qt.NoPen))
        # Card covers this fill; edge color matches the card border so AA edges blend the same
        item.setBrush(QBrush(self._edge))
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(self._blur)
        eff.setOffset(*self._offset)
        eff.setColor(self._color)
        item.setGraphicsEffect(eff)
        scene = QGraphicsScene()
        scene.addItem(item)
        src = eff.boundingRect().toAlignedRect()
        img = QImage(int(src.width() * dpr), int(src.height() * dpr), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        scene.render(p, QRectF(0, 0, src.width(), src.height()), QRectF(src))
        p.end()
        self._pix = QPixmap.fromImage(img)
        self._pix_key = key
        self._origin = src.topLeft()

    def _sync(self):
        self._render()
        if self._pix is None:
            return
        g = self._card.geometry()
        dpr = self._pix.devicePixelRatio() or 1.0
        self.setGeometry(g.x() + self._origin.x(), g.y() + self._origin.y(),
                         int(self._pix.width() / dpr), int(self._pix.height() / dpr))
        self.setVisible(self._card.isVisible())
        self.stackUnder(self._card)
        self.update()

    def eventFilter(self, obj, e):
        if obj is self._card and e.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide):
            self._sync()
        return super().eventFilter(obj, e)

    def paintEvent(self, e):
        if self._pix is not None:
            QPainter(self).drawPixmap(0, 0, self._pix)


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
        # Swap the live card shadow (re-blurred every scroll frame) for a cached identical one
        self.widget.setGraphicsEffect(None)
        self._card_shadow = _CachedCardShadow(self.widget, self)

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

        # Snapshot of what the workspace already has, so closing only emits real changes
        self._initial_footage_dir = self.footage_dir
        self._initial_linked = dict(self.manual_overrides)
        self._committed = False

        # Dialog Sizing: one fixed size for every tab so switching never makes the window jump
        parent_w = target_parent.width() if target_parent else 980
        parent_h = target_parent.height() if target_parent else 720
        self.widget.setFixedWidth(min(920, max(740, parent_w - 40)))
        self.widget.setFixedHeight(min(640, max(480, parent_h - 60)))

        # Changes save as they happen; the X / Esc closes. No Apply & Save / Close footer.
        self.buttonGroup.hide()
        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        # Inset content area. Outer margin is 16px; title, rule and pages add 8px more (24px gutter)
        # so the tab labels, whose items carry 8px padding, line up exactly with the title.
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(16, 12, 12, 12)
        self.content_layout.setSpacing(12)
        self.viewLayout.addLayout(self.content_layout, 1)

        # =====================================================================
        # 1. Compact header: title + close, underline tabs tight beneath
        # =====================================================================
        header_block = QVBoxLayout()
        header_block.setContentsMargins(0, 0, 0, 0)
        header_block.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(8, 0, 0, 0)
        title_row.setSpacing(8)

        self.header_title = QLabel("Footage & Media", self.widget)
        self.header_title.setObjectName("RelinkDialogTitle")
        title_row.addWidget(self.header_title, 1, Qt.AlignVCenter)

        self.close_btn = TransparentToolButton(FluentIcon.CLOSE, self.widget)
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setIconSize(QSize(10, 10))
        self.close_btn.setToolTip("Close (Esc)")
        self.close_btn.clicked.connect(self.accept)
        # Clicking the dimmed background closes too (reject -> done -> _commit_changes saves)
        self.setClosableOnMaskClicked(True)
        title_row.addWidget(self.close_btn, 0, Qt.AlignVCenter)
        header_block.addLayout(title_row)

        # Underline tabs. Labels stay plain; Relink only gains a count when files are missing.
        self.stage_tabs = Pivot(self.widget)
        # Disable sliding transition animation for instant responsive tab switching
        def _instant_stage_tab_slide(endValue, startValue=None):
            self.stage_tabs.slideAni.stop()
            self.stage_tabs.slideAni.setValue(endValue)
            self.stage_tabs.update()
        self.stage_tabs.slideAni.startAnimation = _instant_stage_tab_slide
        for key, text in (("relink", "Relink"), ("library", "Library"), ("transcribe", "Transcribe")):
            # PivotItem.itemClicked emits a bool; swallow it so it can't overwrite the route key
            self.stage_tabs.addItem(
                routeKey=key, text=text, onClick=lambda *_, k=key: self.switch_stage(k)
            )
            # Smaller, tighter items than the Pivot default (18px font, 10px/12px padding)
            self.stage_tabs.items[key].setStyleSheet("PivotItem { padding: 6px 8px; }")
        self.stage_tabs.setItemFontSize(13)  # must run after addItem or items keep 18px
        self.stage_tabs.setCurrentItem("relink")

        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.addWidget(self.stage_tabs, 0, Qt.AlignLeft | Qt.AlignBottom)
        tabs_row.addStretch(1)
        header_block.addLayout(tabs_row)

        self.tabs_rule = QFrame(self.widget)
        self.tabs_rule.setObjectName("FootageTabsRule")
        self.tabs_rule.setFixedHeight(1)
        rule_row = QHBoxLayout()
        rule_row.setContentsMargins(8, 0, 8, 0)
        rule_row.addWidget(self.tabs_rule)
        tabs_block = QVBoxLayout()
        tabs_block.setContentsMargins(0, 0, 0, 0)
        tabs_block.setSpacing(0)
        tabs_block.addLayout(header_block)
        tabs_block.addLayout(rule_row)
        self.content_layout.addLayout(tabs_block)

        # =====================================================================
        # 2. Central Stage Stack (Stage A: Relink | Stage B: Library)
        # =====================================================================
        self.stage_stack = QStackedWidget(self.widget)
        self.stage_stack.setContentsMargins(0, 0, 0, 0)

        stage_wrap = QHBoxLayout()
        stage_wrap.setContentsMargins(8, 0, 12, 0)  # 24px gutters on both sides
        stage_wrap.addWidget(self.stage_stack)

        # --- Stage A: Relink Container ---
        self.relink_page = QWidget(self.stage_stack)
        relink_layout = QVBoxLayout(self.relink_page)
        relink_layout.setContentsMargins(0, 0, 0, 0)
        relink_layout.setSpacing(12)

        # 2a. Folder row: left-elided path (folder name always visible), change, rescan
        dir_layout = QHBoxLayout()
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(8)

        self.dir_edit = LineEdit(self.relink_page)
        self.dir_edit.setPlaceholderText("Choose the folder that holds the downloaded footage")
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setFixedHeight(32)
        self.dir_edit.installEventFilter(self)
        dir_layout.addWidget(self.dir_edit, 1, Qt.AlignVCenter)

        self.browse_btn = PushButton("Change folder…", self.relink_page)
        self.browse_btn.setIcon(FluentIcon.FOLDER)
        self.browse_btn.setFixedHeight(32)
        self.browse_btn.clicked.connect(self._on_browse_directory)
        dir_layout.addWidget(self.browse_btn, 0, Qt.AlignVCenter)

        self.rescan_btn = TransparentToolButton(FluentIcon.SYNC, self.relink_page)
        self.rescan_btn.setFixedSize(32, 32)
        self.rescan_btn.setToolTip("Rescan folder")
        self.rescan_btn.clicked.connect(self._run_scan)
        dir_layout.addWidget(self.rescan_btn, 0, Qt.AlignVCenter)

        relink_layout.addLayout(dir_layout)
        self._refresh_dir_display()

        # 2b. Status line: quiet inline summary (no banner box) + auto-relink option
        self.confirm_banner = QWidget(self.relink_page)
        self.confirm_banner.setObjectName("RelinkStatusLine")
        self.confirm_banner.setFixedHeight(28)
        cb_layout = QHBoxLayout(self.confirm_banner)
        cb_layout.setContentsMargins(0, 0, 0, 0)
        cb_layout.setSpacing(8)

        self.relink_status_dot = StatusDot("#34d399", "#0034d399", self.confirm_banner)
        cb_layout.addWidget(self.relink_status_dot, 0, Qt.AlignVCenter)

        self.confirm_banner_lbl = QLabel("Scanning folder…", self.confirm_banner)
        cb_layout.addWidget(self.confirm_banner_lbl, 0, Qt.AlignVCenter)

        self.continue_lib_btn = HyperlinkButton("", "Open Library →", self.confirm_banner)
        self.continue_lib_btn.setFixedHeight(26)
        self.continue_lib_btn.clicked.connect(self._on_confirm_and_open_library)
        cb_layout.addWidget(self.continue_lib_btn, 0, Qt.AlignVCenter)

        self.copy_missing_btn = HyperlinkButton("", "Copy missing links", self.confirm_banner)
        self.copy_missing_btn.setFixedHeight(26)
        self.copy_missing_btn.setToolTip("Copy the source URLs of missing files to the clipboard")
        self.copy_missing_btn.clicked.connect(self._on_copy_missing_urls)
        cb_layout.addWidget(self.copy_missing_btn, 0, Qt.AlignVCenter)

        cb_layout.addStretch(1)

        self.auto_relink_cb = CheckBox("Auto-relink others in same folder", self.confirm_banner)
        self.auto_relink_cb.setChecked(True)
        self.auto_relink_cb.setToolTip("When you locate one missing file, link any other missing files found next to it")
        cb_layout.addWidget(self.auto_relink_cb, 0, Qt.AlignVCenter)

        relink_layout.addWidget(self.confirm_banner, 0)

        # 2c. Filter & Search Toolbar (Stage A)
        self.relink_toolbar = QWidget(self.relink_page)
        self.relink_toolbar.setFixedHeight(32)
        self.relink_toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        filter_layout = QHBoxLayout(self.relink_toolbar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)

        self.filter_combo = ComboBox(self.relink_toolbar)
        self.filter_combo.addItems(["All Files", "Linked Only", "Missing Only", "VFR"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(150)
        self.filter_combo.setFixedHeight(32)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_combo)

        self.search_edit = SearchLineEdit(self.relink_toolbar)
        self.search_edit.setPlaceholderText("Filter by name or folder")
        self.search_edit.setFixedHeight(32)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.search_edit, 1)

        relink_layout.addWidget(self.relink_toolbar, 0)

        # 2d. Flat list: column header + hairline-separated rows (no card per row)
        list_block = QVBoxLayout()
        list_block.setContentsMargins(0, 0, 0, 0)
        list_block.setSpacing(0)
        self.relink_header = self._build_relink_row_header(self.relink_page)
        list_block.addWidget(self.relink_header, 0)

        self.scroll = SmoothScrollArea(self.relink_page)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(160)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_container = QWidget(self.scroll)
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
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
        list_block.addWidget(self.scroll, 1)
        relink_layout.addLayout(list_block, 1)

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

        self.content_layout.addLayout(stage_wrap, 1)

        # =====================================================================
        # 3. Shared activity dock: identical on every tab (render / voiceover / footage)
        # =====================================================================
        self.queue_strip = QueueStrip(self.widget)
        self.queue_strip.open_transcribe_requested.connect(lambda: self.switch_stage("transcribe"))
        self.viewLayout.addWidget(self.queue_strip)  # edge to edge, below the inset content
        self.vo_header_stat_lbl = self.queue_strip.vo_lbl
        self._refresh_vo_header_status()

        # Connect global render queue completion to auto-reprobe items in place
        render_queue.job_finished.connect(self._on_queue_job_finished)

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
        # The activity dock stays visible on every tab; only the page changes.
        if stage_key == "library":
            self.run_health_check()
            self.library_stage.set_items(self.match_items)
            self.stage_stack.setCurrentWidget(self.library_stage)
            self.stage_tabs.setCurrentItem("library")
        elif stage_key == "transcribe":
            self.stage_stack.setCurrentWidget(self.transcribe_stage)
            self.stage_tabs.setCurrentItem("transcribe")
        else:
            self.stage_stack.setCurrentWidget(self.relink_page)
            self.stage_tabs.setCurrentItem("relink")

    def _on_vo_stage_status_changed(self, status_text: str):
        # TranscribeStage emits "VO: 42%", "VO: Ready (...)", or "VO: Error"
        if "%" in status_text:
            pct = status_text.split(":", 1)[-1].strip()
            self.queue_strip.set_vo_status("Voiceover", f"transcribing {pct}", "busy")
        elif "Ready" in status_text:
            self.queue_strip.set_vo_status("Voiceover", "ready", "ok")
        elif "Error" in status_text:
            self.queue_strip.set_vo_status("Voiceover", "failed", "bad")

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
            self._refresh_dir_display()
            self._run_scan()

    def _refresh_dir_display(self):
        """Show the footage folder elided from the LEFT so the folder name itself stays visible."""
        if not hasattr(self, "dir_edit"):
            return
        full = self.footage_dir or ""
        self.dir_edit.setToolTip(full)
        if not full:
            self.dir_edit.setText("")
            return
        avail = max(60, self.dir_edit.width() - 28)
        elided = QFontMetrics(self.dir_edit.font()).elidedText(full, Qt.ElideLeft, avail)
        self.dir_edit.setText(elided)
        self.dir_edit.setCursorPosition(0)

    def eventFilter(self, obj, e):
        if obj is getattr(self, "dir_edit", None) and e.type() == QEvent.Resize:
            self._refresh_dir_display()
        return super().eventFilter(obj, e)

    def _refresh_vo_header_status(self):
        if not hasattr(self, "queue_strip"):
            return
        if self.vo_file_path and os.path.isfile(self.vo_file_path):
            if self.vo_segments:
                self.queue_strip.set_vo_status("Voiceover", "ready", "ok")
            else:
                self.queue_strip.set_vo_status("Voiceover", "not transcribed", "warn")
        else:
            self.queue_strip.set_vo_status("Voiceover", "not set", "idle")

    # -------------------------------------------------------------------------
    # Directory Scan & Matching
    # -------------------------------------------------------------------------
    def _run_scan(self):
        if not self.footage_dir or not os.path.isdir(self.footage_dir):
            self._populate_unscanned_list()
            return

        self.rescan_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.confirm_banner_lbl.setText("Scanning folder and matching byte sizes…")
        self.continue_lib_btn.hide()
        self.copy_missing_btn.hide()

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
        self.relink_status_dot.set_color("#f87171", "#00f87171")
        self.confirm_banner_lbl.setText(f"Scan failed: {err_msg}. Check the folder and rescan.")

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
    # Stats (status line, dock, tab label) and flat relink rows
    # -------------------------------------------------------------------------
    # Column widths shared by the relink header and every row so they line up
    _REL_COL_FOLDER = 120
    _REL_COL_DUR = 72
    _REL_COL_SIZE = 78
    _REL_COL_FPS = 52

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

        local_bytes = 0
        for item in self.match_items:
            if item.is_matched and item.matched_path and os.path.isfile(item.matched_path):
                try:
                    local_bytes += os.path.getsize(item.matched_path)
                except Exception:
                    pass
        size_display = format_bytes_display(local_bytes) or "0 B"

        # Tabs stay plain names; Relink only shows a count when something is missing
        try:
            if "relink" in self.stage_tabs.items:
                self.stage_tabs.items["relink"].setText(f"Relink ({missing})" if missing > 0 else "Relink")
            if "library" in self.stage_tabs.items:
                self.stage_tabs.items["library"].setText("Library")
        except Exception:
            pass

        # Relink status line + dock footage stat
        if total > 0 and missing == 0:
            self.relink_status_dot.set_color("#34d399", "#0034d399")
            self.confirm_banner_lbl.setText(f"All {total} files linked by name and byte size.")
            self.continue_lib_btn.show()
            self.copy_missing_btn.hide()
            self.queue_strip.set_footage_status("Footage", "all linked", "ok")
        elif total > 0:
            self.relink_status_dot.set_color("#fbbf24", "#00fbbf24")
            self.confirm_banner_lbl.setText(
                f"{missing} of {total} file{'s' if total != 1 else ''} missing. Double-click a row to locate it."
            )
            self.continue_lib_btn.hide()
            self.copy_missing_btn.show()
            self.queue_strip.set_footage_status("Footage", f"{missing} missing", "warn")
        else:
            self.relink_status_dot.set_color("#94a3b8", "#0094a3b8")
            self.confirm_banner_lbl.setText("No footage links found in this script.")
            self.continue_lib_btn.hide()
            self.copy_missing_btn.hide()
            self.queue_strip.set_footage_status("Footage", "none in script", "idle")
        self.relink_status_dot.update()

        self.library_stage.set_summary(linked, size_display)

        self.filter_combo.setItemText(0, f"All Files ({total})")
        self.filter_combo.setItemText(1, f"Linked ({linked})")
        self.filter_combo.setItemText(2, f"Missing ({missing})")
        self.filter_combo.setItemText(3, f"VFR ({vfr_count})")

        self.copy_missing_btn.setEnabled(missing > 0)
        self.copy_missing_btn.setText(f"Copy missing links ({missing})")

    def _build_relink_row_header(self, parent: QWidget) -> QWidget:
        hdr = QWidget(parent)
        hdr.setObjectName("RelinkListHeader")
        hdr.setFixedHeight(30)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(12)
        self._relink_header_labels = []

        def _h(text, width=None, align=Qt.AlignLeft):
            lbl = QLabel(text.upper(), hdr)
            lbl.setAlignment(align | Qt.AlignVCenter)
            if width:
                lbl.setFixedWidth(width)
            self._relink_header_labels.append(lbl)
            return lbl

        spacer = QWidget(hdr)
        spacer.setFixedWidth(8)
        hl.addWidget(spacer)
        hl.addWidget(_h("File"), 1)
        hl.addWidget(_h("Folder", self._REL_COL_FOLDER))
        hl.addWidget(_h("Duration", self._REL_COL_DUR, Qt.AlignRight))
        hl.addWidget(_h("Size", self._REL_COL_SIZE, Qt.AlignRight))
        hl.addWidget(_h("FPS", self._REL_COL_FPS, Qt.AlignRight))
        return hdr

    @staticmethod
    def _chip(text: str, parent: QWidget, tone: str = "warn", tooltip: str = "") -> QLabel:
        colors = {
            "warn": ("rgba(251, 191, 36, 0.13)", "#fbbf24"),
            "bad": ("rgba(248, 113, 113, 0.14)", "#f87171"),
        }
        bg, fg = colors.get(tone, colors["warn"])
        chip = QLabel(text, parent)
        chip.setFixedHeight(18)
        chip.setAlignment(Qt.AlignCenter)
        chip.setToolTip(tooltip)
        chip.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; border: none; border-radius: 4px;"
            f" font-size: 10px; font-weight: 700; padding: 0px 6px; }}"
        )
        return chip

    def _rebuild_cards(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.card_widgets.clear()

        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        line_col = "rgba(255, 255, 255, 0.06)" if pal.get("is_dark", True) else "rgba(0, 0, 0, 0.07)"
        hover_col = "rgba(255, 255, 255, 0.04)" if pal.get("is_dark", True) else "rgba(0, 0, 0, 0.04)"

        footage_root = os.path.normcase(os.path.normpath(self.footage_dir)) if self.footage_dir else ""

        # Sort MISSING/OFFLINE first while preserving relative script order within each group
        sorted_items = sorted(
            self.match_items,
            key=lambda it: 1 if (it.is_matched and it.matched_path and os.path.isfile(it.matched_path)) else 0
        )

        for item in sorted_items:
            card = QFrame(self.list_container)
            card.setObjectName("RelinkItemCard")
            card.setFixedHeight(34)
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(8, 0, 8, 0)
            c_layout.setSpacing(12)

            is_online = bool(item.is_matched and item.matched_path and os.path.isfile(item.matched_path))

            # 1. Status dot (green linked / amber missing)
            dot = QLabel(card)
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(
                f"background-color: {'#34d399' if is_online else '#fbbf24'}; border-radius: 4px; border: none;"
            )
            dot.setToolTip("Linked" if is_online else ("Offline" if item.matched_path else "Missing"))
            c_layout.addWidget(dot, 0, Qt.AlignVCenter)

            # 2. Name + exception chips only
            name_box = QHBoxLayout()
            name_box.setContentsMargins(0, 0, 0, 0)
            name_box.setSpacing(8)

            if is_online:
                primary_text = os.path.basename(item.matched_path)
                tip = f"{item.matched_path}"
                script_title_clean = (item.title or "").strip()
                if script_title_clean and script_title_clean.lower() != primary_text.lower():
                    tip += f"\nScript title: {script_title_clean}"
            else:
                primary_text = item.title or os.path.basename(item.url)
                if item.matched_path:
                    tip = f"Moved or deleted: {item.matched_path}\nDouble-click to locate"
                else:
                    tip = f"Not found in the footage folder. Double-click to locate.\nSource: {item.url}"

            title_lbl = QLabel(primary_text, card)
            title_lbl.setToolTip(tip)
            title_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            title_lbl.setMinimumWidth(60)
            title_lbl.setStyleSheet(
                f"color: {text_col if is_online else muted_col}; font-size: 12px; border: none; background: transparent;"
            )
            name_box.addWidget(title_lbl, 1)

            if not is_online:
                name_box.addWidget(self._chip("OFFLINE" if item.matched_path else "MISSING", card, "warn", tip))
            if is_online and item.is_vfr:
                name_box.addWidget(self._chip(
                    "VFR", card, "warn",
                    f"Variable frame rate ({item.frame_rate:.2f} fps avg). Timecode will drift in Premiere "
                    "without a CFR transcode."
                ))
            if is_online and getattr(item, "is_interlaced", False):
                name_box.addWidget(self._chip(
                    "INTERLACED", card, "warn",
                    f"Interlaced ({getattr(item, 'field_order', 'interlaced').upper()}). "
                    "Re-encode with the bwdif decomb filter to remove combing."
                ))
            c_layout.addLayout(name_box, 1)

            # 3. Folder, relative to the footage folder instead of the full absolute path
            folder_txt = "—"
            if item.matched_path:
                parent_dir = os.path.dirname(os.path.normpath(item.matched_path))
                norm_parent = os.path.normcase(parent_dir)
                if footage_root and norm_parent == footage_root:
                    folder_txt = "."
                elif footage_root and norm_parent.startswith(footage_root + os.sep):
                    folder_txt = parent_dir[len(footage_root) + 1:]
                else:
                    folder_txt = os.path.basename(parent_dir) or parent_dir

            def _cell(text, width, align=Qt.AlignLeft, color=muted_col):
                lbl = QLabel(text, card)
                lbl.setFixedWidth(width)
                lbl.setAlignment(align | Qt.AlignVCenter)
                lbl.setStyleSheet(f"color: {color}; font-size: 12px; border: none; background: transparent;")
                return lbl

            folder_lbl = _cell(folder_txt, self._REL_COL_FOLDER)
            folder_lbl.setToolTip(os.path.dirname(item.matched_path) if item.matched_path else "")
            c_layout.addWidget(folder_lbl)
            c_layout.addWidget(_cell(item.duration_display or "—", self._REL_COL_DUR, Qt.AlignRight))
            c_layout.addWidget(_cell(item.file_size_display or "—", self._REL_COL_SIZE, Qt.AlignRight, text_col))
            if item.frame_rate and item.frame_rate > 0:
                fps_txt = f"{item.frame_rate:.2f}" if item.is_vfr else f"{item.frame_rate:.3f}".rstrip("0").rstrip(".")
            else:
                fps_txt = "—"
            c_layout.addWidget(_cell(fps_txt, self._REL_COL_FPS, Qt.AlignRight))

            card.mouseDoubleClickEvent = lambda e, it=item: self._on_locate_file(it)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, it=item, c=card: self._show_card_context_menu(c.mapToGlobal(pos), it)
            )

            card.setProperty("match_item", item)
            card.setProperty("is_matched", is_online)
            card.setStyleSheet(f"""
                QFrame#RelinkItemCard {{
                    background-color: transparent;
                    border: none;
                    border-bottom: 1px solid {line_col};
                }}
                QFrame#RelinkItemCard:hover {{
                    background-color: {hover_col};
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

    def _current_linked_map(self) -> Dict[str, str]:
        return {
            it.norm_url: it.matched_path
            for it in self.match_items
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        }

    def _commit_changes(self):
        """
        Saves relink results when the window closes (X, Esc, or click-away).
        There is no Apply button: changes count as soon as they are made. Only emits
        footage_linked when the folder or links actually changed, so closing an
        untouched window does not re-announce an audit. VO results are already
        emitted live when transcription finishes.
        """
        if self._committed:
            return
        self._committed = True

        linked_map = self._current_linked_map()
        if self.match_items and len(linked_map) == len(self.match_items):
            self.set_project_relink_confirmed(True)

        folder_changed = os.path.normcase(self.footage_dir or "") != os.path.normcase(self._initial_footage_dir or "")
        links_changed = linked_map != self._initial_linked
        if self.footage_dir and (folder_changed or links_changed):
            self.footage_linked.emit(self.footage_dir, linked_map)

    def _on_apply_save(self):
        """Kept for callers/tests that still close via the old Apply path."""
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
        try:
            self._commit_changes()
        except Exception:
            pass
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
                font-size: 14px;
                font-weight: 600;
            }}
        """)
        line_col = "rgba(255, 255, 255, 0.07)" if pal.get("is_dark", True) else "rgba(0, 0, 0, 0.08)"
        self.tabs_rule.setStyleSheet(f"QFrame#FootageTabsRule {{ background-color: {line_col}; border: none; }}")
        self.relink_header.setStyleSheet(
            f"QWidget#RelinkListHeader {{ border-top: 1px solid {line_col}; border-bottom: 1px solid {line_col}; }}"
        )
        for lbl in getattr(self, "_relink_header_labels", []):
            lbl.setStyleSheet(
                f"color: {muted}; font-size: 10.5px; font-weight: 600; letter-spacing: 0.5px;"
                " border: none; background: transparent;"
            )
        self.confirm_banner_lbl.setStyleSheet(f"color: {muted}; font-size: 12px; border: none; background: transparent;")
        self._refresh_dir_display()
        self._refresh_vo_header_status()
        self._update_stats()
        self._rebuild_cards()
        self._apply_filter()
        if hasattr(self, "library_stage"):
            self.library_stage.apply_theme()
        if hasattr(self, "queue_strip"):
            self.queue_strip.apply_theme()
