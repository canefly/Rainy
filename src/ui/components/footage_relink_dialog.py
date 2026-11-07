import os
import webbrowser
from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal, QThreadPool, QRect
from PySide6.QtGui import QCursor, QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QFileDialog, QSizePolicy, QApplication, QDialog
)
from qfluentwidgets import (
    MessageBoxBase, PushButton, PrimaryPushButton, ToolButton,
    FluentIcon, ComboBox, LineEdit, SearchLineEdit, CheckBox,
    SmoothScrollArea, InfoBar, InfoBarPosition, RoundMenu, Action, MessageBox
)

from ..theme import ThemeManager
from ...core.parser import normalize_link
from ...core.footage_matcher import FootageMatcher, FootageMatchItem, FootageScanWorker
from ...core.unfurl_engine import format_bytes_display
from .widgets import safe_copy_to_clipboard, StatusDot


class FootageRelinkDialog(MessageBoxBase):
    """
    Premiere Pro-style Footage Relink & Media Audit Dialog.
    Searches a local folder for script assets via Exact Filename, Exact Byte Size,
    and Fuzzy Stems, displaying a complete status report of found vs missing media.
    Strictly NO emojis are used in icons, labels, or badges.
    """

    footage_linked = Signal(str, dict)  # (footage_folder, linked_footage_map)

    def __init__(
        self,
        links: List[str],
        current_footage_dir: str = "",
        existing_linked: Optional[Dict[str, str]] = None,
        parent: Optional[QWidget] = None,
    ):
        self._fallback_parent = None
        target_parent = parent or QApplication.activeWindow()
        if target_parent is None:
            self._fallback_parent = QWidget()
            self._fallback_parent.resize(800, 600)
            target_parent = self._fallback_parent

        super().__init__(parent=target_parent)

        self.links = list(dict.fromkeys(links))  # deduplicate preserving order
        self.footage_dir = current_footage_dir or ""
        self.manual_overrides: Dict[str, str] = (existing_linked or {}).copy()
        self.match_items: List[FootageMatchItem] = []
        self.card_widgets: List[QFrame] = []

        # Dialog Sizing: Wide landscape format for clear side-by-side file auditing
        parent_w = target_parent.width() if target_parent else 900
        self.widget.setFixedWidth(min(840, max(680, parent_w - 40)))

        self.viewLayout.setSpacing(10)
        self.viewLayout.setContentsMargins(20, 20, 20, 16)

        # 1. Dialog Header: Title & Inline Contextual Readout (Top-Nav Style)
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.setAlignment(Qt.AlignVCenter)

        self.header_title = QLabel("Footage Relink & Media Audit", self.widget)
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

        # Status dot (green if all linked, amber if any missing)
        self.status_dot = StatusDot("#34d399", "rgba(52, 211, 153, 0.35)", self.header_stats)
        hs_layout.addWidget(self.status_dot)

        # Linked count acts as the checker (Missing removed per Cane's instruction)
        self.linked_stat_lbl = QLabel("0 / 0 Linked", self.header_stats)
        self.linked_stat_lbl.setObjectName("RelinkHeaderLinked")
        hs_layout.addWidget(self.linked_stat_lbl)

        self.header_div2 = QFrame(self.header_stats)
        self.header_div2.setFrameShape(QFrame.VLine)
        self.header_div2.setFixedHeight(12)
        hs_layout.addWidget(self.header_div2)

        # Local size readout
        self.size_stat_lbl = QLabel("0 B Local", self.header_stats)
        self.size_stat_lbl.setObjectName("RelinkHeaderSize")
        hs_layout.addWidget(self.size_stat_lbl)

        title_row.addWidget(self.header_stats)
        title_row.addStretch(1)

        header_layout.addLayout(title_row)

        self.header_desc = QLabel(
            "Verifies local footage against script source links based on filenames and exact probed byte sizes.",
            self.widget
        )
        self.header_desc.setObjectName("RelinkDialogSubtitle")
        self.header_desc.setWordWrap(True)
        header_layout.addWidget(self.header_desc)

        self.viewLayout.addLayout(header_layout)

        # 2. Directory Selector Row
        dir_layout = QHBoxLayout()
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(8)

        dir_lbl = QLabel("Footage Folder:", self.widget)
        dir_lbl.setObjectName("RelinkFieldLabel")
        dir_layout.addWidget(dir_lbl, 0, Qt.AlignVCenter)

        self.dir_edit = LineEdit(self.widget)
        self.dir_edit.setText(self.footage_dir)
        self.dir_edit.setPlaceholderText("Select directory containing downloaded footage...")
        self.dir_edit.setReadOnly(True)
        dir_layout.addWidget(self.dir_edit, 1, Qt.AlignVCenter)

        self.browse_btn = PushButton("Browse...", self.widget)
        self.browse_btn.setIcon(FluentIcon.FOLDER)
        self.browse_btn.setFixedHeight(32)
        self.browse_btn.clicked.connect(self._on_browse_directory)
        dir_layout.addWidget(self.browse_btn, 0, Qt.AlignVCenter)

        self.rescan_btn = ToolButton(FluentIcon.SYNC, self.widget)
        self.rescan_btn.setFixedSize(32, 32)
        self.rescan_btn.setToolTip("Rescan Directory")
        self.rescan_btn.clicked.connect(self._run_scan)
        dir_layout.addWidget(self.rescan_btn, 0, Qt.AlignVCenter)

        self.viewLayout.addLayout(dir_layout)

        # 3. Filter & Search Toolbar
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)

        self.filter_combo = ComboBox(self.widget)
        self.filter_combo.addItems(["All Files", "Linked Only", "Missing Only"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(130)
        self.filter_combo.setFixedHeight(30)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_combo)

        self.search_edit = SearchLineEdit(self.widget)
        self.search_edit.setPlaceholderText("Filter files by name or path...")
        self.search_edit.setFixedHeight(30)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.search_edit, 1)

        self.viewLayout.addLayout(filter_layout)

        # 4. Media Items Smooth Scroll Area
        self.scroll = SmoothScrollArea(self.widget)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(320)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_container = QWidget(self.scroll)
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_container)
        self.viewLayout.addWidget(self.scroll)

        # 6. Bottom Options Row
        bottom_options = QHBoxLayout()
        bottom_options.setContentsMargins(0, 0, 0, 0)

        self.auto_relink_cb = CheckBox("Automatically relink remaining missing files in same folder", self.widget)
        self.auto_relink_cb.setChecked(True)
        bottom_options.addWidget(self.auto_relink_cb, 1, Qt.AlignVCenter)

        self.copy_missing_btn = PushButton("Copy Missing Links", self.widget)
        self.copy_missing_btn.setIcon(FluentIcon.COPY)
        self.copy_missing_btn.setFixedHeight(28)
        self.copy_missing_btn.setToolTip("Copy all missing asset URLs to clipboard")
        self.copy_missing_btn.clicked.connect(self._on_copy_missing_urls)
        bottom_options.addWidget(self.copy_missing_btn, 0, Qt.AlignVCenter)

        self.viewLayout.addLayout(bottom_options)

        # 7. Action Buttons in Button Group
        self.yesButton.setText("Apply & Save")
        self.yesButton.setIcon(FluentIcon.ACCEPT)
        self.cancelButton.setText("Cancel")
        self.yesButton.clicked.connect(self._on_apply_save)

        self.apply_theme()

        # Initial scan if directory is already selected
        if self.footage_dir and os.path.isdir(self.footage_dir):
            self._run_scan()
        else:
            self._populate_unscanned_list()

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
        self.header_desc.setText(
            "Verifies local footage against script source links based on filenames and exact probed byte sizes."
        )
        self._rebuild_cards()
        self._update_stats()
        self._apply_filter()

    def _on_scan_error(self, err_msg: str):
        self.rescan_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.header_desc.setText(f"Scan failed: {err_msg}")

    def _populate_unscanned_list(self):
        matcher = FootageMatcher("")
        self.match_items = matcher.match_links(self.links, self.manual_overrides)
        self._rebuild_cards()
        self._update_stats()
        self._apply_filter()

    def _update_stats(self):
        total = len(self.match_items)
        linked = sum(1 for item in self.match_items if item.is_matched)
        missing = total - linked
        pct = int((linked / total) * 100) if total > 0 else 0

        # Calculate total matched bytes on disk
        local_bytes = 0
        for item in self.match_items:
            if item.is_matched and item.matched_path and os.path.isfile(item.matched_path):
                try:
                    local_bytes += os.path.getsize(item.matched_path)
                except Exception:
                    pass

        # Update Top-Nav style header stats (Missing removed, Linked acts as the checker)
        size_display = format_bytes_display(local_bytes) or "0 B"
        self.size_stat_lbl.setText(f"{size_display} Local")

        if total > 0 and missing == 0:
            # All linked: vibrant green
            self.status_dot._dot_color = QColor("#34d399")
            self.status_dot._halo_color = QColor("rgba(52, 211, 153, 0.35)")
            self.linked_stat_lbl.setText(f"{linked} / {total} Linked ({pct}%)")
            self.linked_stat_lbl.setStyleSheet("color: #34d399; font-weight: 700; font-size: 11px;")
        elif total > 0:
            # Incomplete / missing items: warning amber
            self.status_dot._dot_color = QColor("#fbbf24")
            self.status_dot._halo_color = QColor("rgba(245, 158, 11, 0.35)")
            self.linked_stat_lbl.setText(f"{linked} / {total} Linked ({pct}%)")
            self.linked_stat_lbl.setStyleSheet("color: #fbbf24; font-weight: 700; font-size: 11px;")
        else:
            self.status_dot._dot_color = QColor("#94a3b8")
            self.status_dot._halo_color = QColor("rgba(148, 163, 184, 0.25)")
            self.linked_stat_lbl.setText("0 / 0 Linked")
            self.linked_stat_lbl.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 11px;")

        self.status_dot.update()

        self.filter_combo.setItemText(0, f"All Files ({total})")
        self.filter_combo.setItemText(1, f"Linked ({linked})")
        self.filter_combo.setItemText(2, f"Missing ({missing})")

        self.copy_missing_btn.setEnabled(missing > 0)
        self.copy_missing_btn.setText(f"Copy Missing Links ({missing})")

    def _rebuild_cards(self):
        # Clear existing cards
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

        for idx, item in enumerate(self.match_items):
            card = QFrame(self.list_container)
            card.setObjectName("RelinkItemCard")
            card.setCursor(QCursor(Qt.PointingHandCursor))
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(12, 6, 12, 6)
            c_layout.setSpacing(10)

            # 1. Status Pill (LINKED vs MISSING - strictly no emojis)
            status_pill = QLabel(card)
            status_pill.setFixedHeight(22)
            status_pill.setFixedWidth(64)
            status_pill.setAlignment(Qt.AlignCenter)
            if item.is_matched:
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
                status_pill.setText("MISSING")
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

            # 2. Middle Text Details (Clamped, Never expands horizontally)
            mid_layout = QVBoxLayout()
            mid_layout.setContentsMargins(0, 0, 0, 0)
            mid_layout.setSpacing(2)

            title_lbl = QLabel(item.title, card)
            title_lbl.setStyleSheet(f"color: {text_col}; font-weight: 600; font-size: 12px;")
            title_lbl.setToolTip(item.title)
            title_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            mid_layout.addWidget(title_lbl)

            detail_row = QHBoxLayout()
            detail_row.setContentsMargins(0, 0, 0, 0)
            detail_row.setSpacing(6)

            if item.is_matched and item.matched_path:
                matched_filename = os.path.basename(item.matched_path)
                path_lbl = QLabel(matched_filename, card)
                path_lbl.setToolTip(f"Full Local Path:\n{item.matched_path}")
                path_lbl.setStyleSheet(f"color: {muted_col}; font-size: 11px;")
                path_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                detail_row.addWidget(path_lbl, 1, Qt.AlignVCenter)
            else:
                unmatched_lbl = QLabel("Offline • Missing in footage directory", card)
                unmatched_lbl.setStyleSheet(f"color: {muted_col}; font-size: 11px; font-style: italic;")
                unmatched_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                detail_row.addWidget(unmatched_lbl, 1, Qt.AlignVCenter)

            mid_layout.addLayout(detail_row)
            c_layout.addLayout(mid_layout, 1)

            # 3. Size / Duration Badge
            sz_parts = []
            if item.duration_display:
                sz_parts.append(item.duration_display)
            if item.file_size_display:
                sz_parts.append(item.file_size_display)
            sz_text = " • ".join(sz_parts) if sz_parts else "—"

            sz_badge = QLabel(sz_text, card)
            sz_badge.setFixedWidth(130)
            sz_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sz_badge.setStyleSheet(f"color: {muted_col}; font-size: 11px; font-weight: 600;")
            c_layout.addWidget(sz_badge, 0, Qt.AlignVCenter)

            # Double-click to locate on disk, right-click for instant context menu
            card.mouseDoubleClickEvent = lambda e, it=item: self._on_locate_file(it)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda pos, it=item, c=card: self._show_card_context_menu(c.mapToGlobal(pos), it))

            card.setProperty("match_item", item)
            card.setProperty("is_matched", item.is_matched)
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

        menu.addSeparator()

        open_act = Action(FluentIcon.SHARE, "Open Source Link in Browser", menu)
        open_act.triggered.connect(lambda: webbrowser.open(item.url))
        menu.addAction(open_act)

        prop_act = Action(FluentIcon.INFO, "Properties & Details...", menu)
        prop_act.triggered.connect(lambda: self._show_properties_dialog(item))
        menu.addAction(prop_act)

        menu.exec(global_pos, ani=False)

    def _reveal_in_explorer(self, file_path: str):
        if not file_path or not os.path.exists(file_path):
            return
        if os.name == 'nt':
            import subprocess
            subprocess.run(["explorer", f"/select,{os.path.normpath(file_path)}"])
        else:
            parent_dir = os.path.dirname(file_path)
            webbrowser.open(parent_dir)

    def _unlink_file(self, item: FootageMatchItem):
        item.matched_path = None
        item.match_tier = "MISSING"
        item.is_matched = False
        self.manual_overrides.pop(item.norm_url, None)
        self.manual_overrides.pop(item.url, None)
        self._rebuild_cards()
        self._update_stats()
        self._apply_filter()

    def _show_properties_dialog(self, item: FootageMatchItem):
        status_text = "Linked" if item.is_matched else "Missing / Offline"
        path_text = item.matched_path or "Not Linked"
        size_info = item.file_size_display or "Unknown"
        if item.file_size_bytes:
            size_info += f" ({item.file_size_bytes:,} bytes)"
        tier_text = item.match_tier
        dur_text = item.duration_display or "Unknown"
        res_text = item.resolution or "Unknown"
        fps_text = f"{item.frame_rate:.2f} fps" if item.frame_rate else "Unknown"

        content = (
            f"Asset Title: {item.title}\n"
            f"Status: {status_text}\n"
            f"Match Tier: {tier_text}\n"
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

        # Probe located file for duration, resolution, and fps
        try:
            from ...core.media_probe import probe_media_file
            probed = probe_media_file(norm_chosen, use_cache=True)
            if probed:
                item.duration_seconds = probed.duration_seconds
                item.duration_display = probed.duration_display
                item.resolution = probed.resolution
                item.frame_rate = probed.frame_rate
                item.file_size_bytes = probed.file_size
                item.file_size_display = format_bytes_display(probed.file_size)
        except Exception:
            pass

        # Premiere Pro-style: Automatically relink remaining missing files in same folder
        if self.auto_relink_cb.isChecked():
            parent_dir = os.path.dirname(norm_chosen)
            self._auto_relink_siblings(parent_dir)

        self._rebuild_cards()
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

            # 1. State Filter
            if "Linked" in filter_text and not is_matched:
                card.hide()
                continue
            if "Missing" in filter_text and is_matched:
                card.hide()
                continue

            # 2. Text Query Filter
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

        self.footage_linked.emit(self.footage_dir, linked_map)
        self.accept()

    def showEvent(self, e):
        self.setGraphicsEffect(None)
        p = self.parentWidget() or self.window()
        if p and p.width() > 0 and p.height() > 0:
            self.resize(p.size())
        QDialog.showEvent(self, e)
        if hasattr(self, "apply_theme"):
            self.apply_theme()

    def done(self, code):
        self.setGraphicsEffect(None)
        if hasattr(self, "widget") and self.widget:
            self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
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
        self.size_stat_lbl.setStyleSheet(f"""
            QLabel#RelinkHeaderSize {{
                color: {muted};
                font-size: 11px;
                font-weight: 600;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            }}
        """)
        self._update_stats()
