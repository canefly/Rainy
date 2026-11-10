import os
import webbrowser
from typing import List, Optional
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableView,
    QHeaderView, QAbstractItemView, QSizePolicy
)
from qfluentwidgets import (
    PushButton, ToolButton, CheckBox, ComboBox, SearchLineEdit,
    FluentIcon, RoundMenu, Action, SmoothScrollDelegate
)

from ...theme import ThemeManager
from ....core.footage_matcher import FootageMatchItem
from ....core.render_queue import render_queue
from ..widgets import safe_copy_to_clipboard, GpuSmoothScroller, MiddleClickAutoScroller
from .library_model import FootageLibraryModel
from .action_bar import BulkActionBar


class FootageLibraryStage(QWidget):
    """
    Stage B: Footage Library & Media Audit Explorer.
    Provides a fast QTableView with smooth pixel scrolling, multi-select, quick filters,
    dismissible VFR selection hint, and BulkActionBar for batch re-encoding/explorer operations.
    Strictly no emojis.
    """
    batch_transcode_requested = Signal(list, str)  # (List[FootageMatchItem], preset_mode: "cfr" | "compress" | "custom")
    reveal_requested = Signal(str)                 # file_path
    locate_requested = Signal(object)              # FootageMatchItem
    unlink_requested = Signal(list)                # List[FootageMatchItem]
    properties_requested = Signal(object)          # FootageMatchItem
    switch_to_relink_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._vfr_hint_dismissed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)

        # 1. Dismissible VFR Hint Banner (Fixed height so it never stretches vertically)
        self.vfr_banner = QFrame(self)
        self.vfr_banner.setObjectName("LibraryVfrBanner")
        self.vfr_banner.setFixedHeight(34)
        self.vfr_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        vb_layout = QHBoxLayout(self.vfr_banner)
        vb_layout.setContentsMargins(10, 4, 10, 4)
        vb_layout.setSpacing(8)

        self.vfr_banner_lbl = QLabel("0 clips are VFR", self.vfr_banner)
        vb_layout.addWidget(self.vfr_banner_lbl, 1, Qt.AlignVCenter)

        self.select_vfr_btn = PushButton("Select VFR Clips", self.vfr_banner)
        self.select_vfr_btn.setFixedHeight(24)
        self.select_vfr_btn.clicked.connect(self._on_select_vfr_clicked)
        vb_layout.addWidget(self.select_vfr_btn, 0, Qt.AlignVCenter)

        self.dismiss_vfr_btn = ToolButton(FluentIcon.CLOSE, self.vfr_banner)
        self.dismiss_vfr_btn.setFixedSize(22, 22)
        self.dismiss_vfr_btn.setToolTip("Dismiss VFR Hint")
        self.dismiss_vfr_btn.clicked.connect(self._on_dismiss_vfr_hint)
        vb_layout.addWidget(self.dismiss_vfr_btn, 0, Qt.AlignVCenter)

        self.vfr_banner.hide()
        layout.addWidget(self.vfr_banner, 0)

        # 2. Offline / Missing Audit Alert Banner (Fixed height so it never stretches vertically)
        self.offline_banner = QFrame(self)
        self.offline_banner.setObjectName("LibraryOfflineBanner")
        self.offline_banner.setFixedHeight(34)
        self.offline_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        ob_layout = QHBoxLayout(self.offline_banner)
        ob_layout.setContentsMargins(10, 4, 10, 4)
        ob_layout.setSpacing(8)

        self.offline_banner_lbl = QLabel("All media files are active and verified on disk.", self.offline_banner)
        ob_layout.addWidget(self.offline_banner_lbl, 1, Qt.AlignVCenter)

        self.back_relink_btn = PushButton("Back to Relink", self.offline_banner)
        self.back_relink_btn.setIcon(FluentIcon.SYNC)
        self.back_relink_btn.setFixedHeight(24)
        self.back_relink_btn.clicked.connect(self.switch_to_relink_requested.emit)
        ob_layout.addWidget(self.back_relink_btn, 0, Qt.AlignVCenter)

        self.offline_banner.hide()
        layout.addWidget(self.offline_banner, 0)

        # 3. Filter & Search Toolbar (Wrapped in a Fixed-Height container to eliminate layout gap jumps)
        self.toolbar_widget = QWidget(self)
        self.toolbar_widget.setFixedHeight(32)
        self.toolbar_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar = QHBoxLayout(self.toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.select_all_cb = CheckBox("Select All", self.toolbar_widget)
        self.select_all_cb.stateChanged.connect(self._on_select_all_toggled)
        toolbar.addWidget(self.select_all_cb, 0, Qt.AlignVCenter)

        self.filter_combo = ComboBox(self.toolbar_widget)
        self.filter_combo.addItems([
            "All Clips",
            "VFR Warning Only",
            "Interlaced Only",
            "Missing / Offline",
            "Largest First",
        ])
        self.filter_combo.setFixedWidth(165)
        self.filter_combo.setFixedHeight(30)
        self.filter_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.filter_combo, 0, Qt.AlignVCenter)

        self.group_folder_cb = CheckBox("Group by Folder", self.toolbar_widget)
        self.group_folder_cb.stateChanged.connect(self._apply_filters)
        toolbar.addWidget(self.group_folder_cb, 0, Qt.AlignVCenter)

        self.search_edit = SearchLineEdit(self.toolbar_widget)
        self.search_edit.setPlaceholderText("Search library by filename, folder, or script title...")
        self.search_edit.setFixedHeight(30)
        self.search_edit.textChanged.connect(self._apply_filters)
        toolbar.addWidget(self.search_edit, 1, Qt.AlignVCenter)

        layout.addWidget(self.toolbar_widget, 0)

        # 4. Contextual Bulk Action Bar (Appears when >=1 items are selected)
        self.action_bar = BulkActionBar(self)
        self.action_bar.reencode_requested.connect(self._trigger_reencode)
        self.action_bar.reveal_requested.connect(self._on_bulk_reveal)
        self.action_bar.open_requested.connect(self._on_bulk_open)
        self.action_bar.copy_path_requested.connect(self._on_bulk_copy_path)
        self.action_bar.properties_requested.connect(self._on_bulk_properties)
        self.action_bar.unlink_requested.connect(self._on_bulk_unlink)
        self.action_bar.clear_selection_requested.connect(self._clear_all_selections)
        layout.addWidget(self.action_bar, 0)

        # 5. High-Performance QTableView + FootageLibraryModel + Smooth Pixel Scrolling
        self.model = FootageLibraryModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("FootageLibraryTable")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.setMinimumHeight(230)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.clicked.connect(self._on_table_clicked)

        # Attach Fluent ScrollBar styling but disable its 60fps QTimer step-loop,
        # and drive wheel scrolling with the native VSync GpuSmoothScroller (same as TimelineList)
        self.scroll_delegate = SmoothScrollDelegate(self.table, useAni=True)
        try:
            from qfluentwidgets.common.smooth_scroll import SmoothMode
            self.scroll_delegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        except Exception:
            pass
        if self.table.viewport():
            self.table.viewport().setAttribute(Qt.WA_StaticContents, True)
        if self.table.verticalScrollBar():
            self.table.verticalScrollBar().setSingleStep(16)
        self.smooth_scroller = GpuSmoothScroller(self.table, self)
        self.smooth_scroller.enable_smooth_wheel = True
        self.middle_scroller = MiddleClickAutoScroller(self.table, self)

        hdr = self.table.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 32)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 68)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.setColumnWidth(3, 105)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(4, 54)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 76)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 52)
        hdr.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 74)
        hdr.setSectionResizeMode(8, QHeaderView.Fixed)
        self.table.setColumnWidth(8, 58)
        hdr.setSectionResizeMode(9, QHeaderView.Fixed)
        self.table.setColumnWidth(9, 72)
        hdr.setSectionResizeMode(10, QHeaderView.Fixed)
        self.table.setColumnWidth(10, 88)

        self.table.selectionModel().selectionChanged.connect(self._sync_selection_state)
        self.model.selection_changed.connect(self._sync_selection_state)

        layout.addWidget(self.table, 1)

        # Live refresh on render queue updates
        render_queue.queue_changed.connect(self.model.refresh_rows)
        render_queue.job_progress.connect(lambda *_: self.model.refresh_rows())

        self.apply_theme()

    def set_items(self, items: List[FootageMatchItem]):
        self.model.set_items(items)
        self.update_banners()

    def update_banners(self):
        items = self.model._all_items
        vfr_count = sum(
            1 for it in items
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path) and it.is_vfr
        )
        interlaced_count = sum(
            1 for it in items
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path) and getattr(it, "is_interlaced", False)
        )
        offline_count = sum(
            1 for it in items
            if not (it.is_matched and it.matched_path and os.path.isfile(it.matched_path))
        )

        if (vfr_count > 0 or interlaced_count > 0) and not self._vfr_hint_dismissed:
            parts = []
            if vfr_count > 0:
                parts.append(f"{vfr_count} VFR")
            if interlaced_count > 0:
                parts.append(f"{interlaced_count} Interlaced")
            summary = " and ".join(parts)
            self.vfr_banner_lbl.setText(
                f"Detected {summary} clip{'s' if (vfr_count + interlaced_count) != 1 else ''}. "
                "Select them to re-encode (CFR + bwdif decomb) when ready."
            )
            self.select_vfr_btn.setText("Select Flagged Clips")
            self.vfr_banner.show()
        else:
            self.vfr_banner.hide()

        if offline_count > 0:
            self.offline_banner_lbl.setText(
                f"Media Audit Alert: {offline_count} footage file{'s are' if offline_count != 1 else ' is'} offline or missing on disk."
            )
            self.offline_banner.show()
        else:
            self.offline_banner.hide()

    def _on_select_vfr_clicked(self):
        self.model.select_vfr_items()

    def _on_dismiss_vfr_hint(self):
        self._vfr_hint_dismissed = True
        self.vfr_banner.hide()

    def _on_select_all_toggled(self, state: int):
        checked = (state == Qt.Checked.value or state == True)
        self.model.select_all_visible(checked)

    def _apply_filters(self, *_):
        idx = self.filter_combo.currentIndex()
        modes = {0: "ALL", 1: "VFR", 2: "INTERLACED", 3: "OFFLINE", 4: "LARGEST"}
        mode = modes.get(idx, "ALL")
        self.model.set_filter(
            mode=mode,
            search_query=self.search_edit.text(),
            group_by_folder=self.group_folder_cb.isChecked(),
        )

    def get_effective_selected_items(self) -> List[FootageMatchItem]:
        """Combines checkbox-checked items and Ctrl/Shift highlighted table rows."""
        checked = self.model.get_checked_items()
        seen = {it.norm_url for it in checked}
        result = list(checked)

        if self.table.selectionModel():
            for idx in self.table.selectionModel().selectedRows():
                it = self.model.item_at(idx.row())
                if it and it.norm_url not in seen:
                    seen.add(it.norm_url)
                    result.append(it)
        return result

    def _sync_selection_state(self, *_):
        selected = self.get_effective_selected_items()
        online = [
            it for it in selected
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        ]
        self.action_bar.set_selection_count(len(selected), len(online))

    def _clear_all_selections(self):
        self.select_all_cb.blockSignals(True)
        self.select_all_cb.setChecked(False)
        self.select_all_cb.blockSignals(False)
        if self.table.selectionModel():
            self.table.selectionModel().clearSelection()
        self.model.clear_selection()

    def _on_table_clicked(self, index):
        if index.isValid() and index.column() == 0:
            cur = self.model.data(index, Qt.CheckStateRole)
            new_val = Qt.Unchecked if cur == Qt.Checked else Qt.Checked
            self.model.setData(index, new_val, Qt.CheckStateRole)

    def _on_table_double_clicked(self, index):
        if not index.isValid():
            return
        it = self.model.item_at(index.row())
        if not it:
            return
        if it.is_matched and it.matched_path and os.path.isfile(it.matched_path):
            self._open_file_in_os(it.matched_path)
        else:
            self.locate_requested.emit(it)

    def _open_file_in_os(self, path: str):
        if not path or not os.path.isfile(path):
            return
        abs_p = os.path.abspath(path)
        if os.name == "nt":
            try:
                os.startfile(abs_p)
                return
            except Exception:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(abs_p))

    def _trigger_reencode(self):
        selected = self.get_effective_selected_items()
        online = [
            it for it in selected
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        ]
        if not online:
            return
        # Default to CFR preset if any selected clip is VFR, else custom/global settings
        preset_mode = "cfr" if any(it.is_vfr for it in online) else "custom"
        self.batch_transcode_requested.emit(online, preset_mode)

    def _on_bulk_reveal(self):
        selected = self.get_effective_selected_items()
        online = [
            it for it in selected
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        ]
        if len(online) == 1:
            self.reveal_requested.emit(online[0].matched_path)

    def _on_bulk_open(self):
        selected = self.get_effective_selected_items()
        online = [
            it for it in selected
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        ]
        if len(online) == 1:
            self._open_file_in_os(online[0].matched_path)

    def _on_bulk_copy_path(self):
        selected = self.get_effective_selected_items()
        paths = [os.path.abspath(it.matched_path) for it in selected if it.matched_path]
        if paths:
            safe_copy_to_clipboard("\n".join(paths))

    def _on_bulk_properties(self):
        selected = self.get_effective_selected_items()
        if selected:
            self.properties_requested.emit(selected[0])

    def _on_bulk_unlink(self):
        selected = self.get_effective_selected_items()
        if selected:
            self.unlink_requested.emit(selected)

    def _on_table_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if idx.isValid():
            row_item = self.model.item_at(idx.row())
            selected = self.get_effective_selected_items()
            if row_item and all(it.norm_url != row_item.norm_url for it in selected):
                selected = [row_item]
        else:
            selected = self.get_effective_selected_items()

        if not selected:
            return

        online = [
            it for it in selected
            if it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
        ]
        single = selected[0] if len(selected) == 1 else None
        is_single_online = (len(selected) == 1 and len(online) == 1)

        menu = RoundMenu(parent=self)

        if online:
            re_lbl = f"Re-encode {len(online)} Clips..." if len(online) > 1 else "Re-encode Clip..."
            preset = "cfr" if any(it.is_vfr for it in online) else "custom"
            re_act = Action(FluentIcon.MOVIE, re_lbl, menu)
            re_act.triggered.connect(lambda: self.batch_transcode_requested.emit(online, preset))
            menu.addAction(re_act)

            menu.addSeparator()

            rev_act = Action(FluentIcon.FOLDER, "Show in File Explorer", menu)
            rev_act.setEnabled(is_single_online)
            if is_single_online:
                rev_act.triggered.connect(lambda: self.reveal_requested.emit(online[0].matched_path))
            menu.addAction(rev_act)

            open_act = Action(FluentIcon.PLAY, "Open in Default Player", menu)
            open_act.setEnabled(is_single_online)
            if is_single_online:
                open_act.triggered.connect(lambda: self._open_file_in_os(online[0].matched_path))
            menu.addAction(open_act)

            cp_act = Action(FluentIcon.COPY, "Copy Local Path(s)", menu)
            cp_act.triggered.connect(
                lambda: safe_copy_to_clipboard(
                    "\n".join(os.path.abspath(it.matched_path) for it in online if it.matched_path)
                )
            )
            menu.addAction(cp_act)

        if single:
            menu.addSeparator()
            loc_act = Action(FluentIcon.FOLDER_ADD, "Locate / Relink File Manually...", menu)
            loc_act.triggered.connect(lambda: self.locate_requested.emit(single))
            menu.addAction(loc_act)

            src_act = Action(FluentIcon.SHARE, "Open Source Link in Browser", menu)
            src_act.triggered.connect(lambda: webbrowser.open(single.url))
            menu.addAction(src_act)

            prop_act = Action(FluentIcon.INFO, "Properties & Details...", menu)
            prop_act.triggered.connect(lambda: self.properties_requested.emit(single))
            menu.addAction(prop_act)

        menu.addSeparator()
        un_lbl = f"Unlink {len(selected)} Files" if len(selected) > 1 else "Unlink File"
        un_act = Action(FluentIcon.DELETE, un_lbl, menu)
        un_act.triggered.connect(lambda: self.unlink_requested.emit(selected))
        menu.addAction(un_act)

        menu.exec(self.table.viewport().mapToGlobal(pos), ani=False)

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        surface = pal.get("surface", "#18181b")
        border = pal.get("border", "#27272a")
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")

        self.vfr_banner.setStyleSheet("""
            QFrame#LibraryVfrBanner {
                background-color: rgba(245, 158, 11, 0.12);
                border: 1px solid rgba(245, 158, 11, 0.35);
                border-radius: 6px;
            }
        """)
        self.vfr_banner_lbl.setStyleSheet(
            "color: #fbbf24; font-size: 11.5px; font-weight: 600; border: none; background: transparent;"
        )

        self.offline_banner.setStyleSheet("""
            QFrame#LibraryOfflineBanner {
                background-color: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.38);
                border-radius: 6px;
            }
        """)
        self.offline_banner_lbl.setStyleSheet(
            "color: #f87171; font-size: 11.5px; font-weight: 700; border: none; background: transparent;"
        )

        self.table.setStyleSheet(f"""
            QTableView#FootageLibraryTable {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
                color: {text_col};
                font-size: 11.5px;
                selection-background-color: rgba(56, 189, 248, 0.16);
                selection-color: {text_col};
                outline: none;
            }}
            QTableView#FootageLibraryTable::item {{
                padding: 2px 6px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.03);
                color: {muted_col};
                font-size: 10.5px;
                font-weight: 700;
                border: none;
                border-bottom: 1px solid {border};
                padding: 4px 6px;
            }}
        """)
        self.model.update_palette_cache()
        self.action_bar.apply_theme()
