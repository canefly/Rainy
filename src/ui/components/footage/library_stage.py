import os
import webbrowser
from typing import List, Optional
from PySide6.QtCore import Qt, Signal, QUrl, QSize, QEvent, QItemSelection, QItemSelectionModel
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableView,
    QHeaderView, QAbstractItemView, QSizePolicy
)
from qfluentwidgets import (
    PushButton, ToolButton, CheckBox, ComboBox, SearchLineEdit,
    FluentIcon, RoundMenu, Action, SmoothScrollDelegate, HyperlinkButton, TransparentToolButton
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
        layout.setSpacing(12)

        # 1. Toolbar: filter, search, group-by, select all (same shape as the Relink toolbar)
        self.toolbar_widget = QWidget(self)
        self.toolbar_widget.setFixedHeight(32)
        self.toolbar_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar = QHBoxLayout(self.toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.filter_combo = ComboBox(self.toolbar_widget)
        self.filter_combo.addItems([
            "All Clips",
            "VFR Warning Only",
            "Interlaced Only",
            "Missing / Offline",
            "Largest First",
        ])
        self.filter_combo.setFixedWidth(160)
        self.filter_combo.setFixedHeight(32)
        self.filter_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.filter_combo, 0, Qt.AlignVCenter)

        self.search_edit = SearchLineEdit(self.toolbar_widget)
        self.search_edit.setPlaceholderText("Search filename, folder or script title")
        self.search_edit.setFixedHeight(32)
        self.search_edit.textChanged.connect(self._apply_filters)
        toolbar.addWidget(self.search_edit, 1, Qt.AlignVCenter)

        self.group_folder_cb = CheckBox("Group by folder", self.toolbar_widget)
        self.group_folder_cb.stateChanged.connect(self._apply_filters)
        toolbar.addWidget(self.group_folder_cb, 0, Qt.AlignVCenter)

        self.select_all_cb = CheckBox("Select all", self.toolbar_widget)
        self.select_all_cb.stateChanged.connect(self._on_select_all_toggled)
        toolbar.addWidget(self.select_all_cb, 0, Qt.AlignVCenter)

        layout.addWidget(self.toolbar_widget, 0)

        # 2. Summary line: clip count + size on disk (details live inside the tab, not on it)
        self.summary_row = QWidget(self)
        self.summary_row.setFixedHeight(20)
        sr = QHBoxLayout(self.summary_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(8)
        self.summary_dot = QLabel(self.summary_row)
        self.summary_dot.setFixedSize(7, 7)
        sr.addWidget(self.summary_dot, 0, Qt.AlignVCenter)
        self.summary_lbl = QLabel("No clips linked yet", self.summary_row)
        sr.addWidget(self.summary_lbl, 1, Qt.AlignVCenter)
        layout.addWidget(self.summary_row, 0)

        # Dismissible VFR / interlaced notice (Fixed height so it never stretches vertically)
        self.vfr_banner = QFrame(self)
        self.vfr_banner.setObjectName("LibraryVfrBanner")
        self.vfr_banner.setFixedHeight(32)
        self.vfr_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        vb_layout = QHBoxLayout(self.vfr_banner)
        vb_layout.setContentsMargins(12, 0, 4, 0)
        vb_layout.setSpacing(8)

        self.vfr_banner_lbl = QLabel("0 clips are VFR", self.vfr_banner)
        vb_layout.addWidget(self.vfr_banner_lbl, 1, Qt.AlignVCenter)

        self.select_vfr_btn = HyperlinkButton("", "Select flagged clips", self.vfr_banner)
        self.select_vfr_btn.setFixedHeight(26)
        self.select_vfr_btn.clicked.connect(self._on_select_vfr_clicked)
        vb_layout.addWidget(self.select_vfr_btn, 0, Qt.AlignVCenter)

        self.dismiss_vfr_btn = TransparentToolButton(FluentIcon.CLOSE, self.vfr_banner)
        self.dismiss_vfr_btn.setFixedSize(24, 24)
        self.dismiss_vfr_btn.setIconSize(QSize(10, 10))
        self.dismiss_vfr_btn.setToolTip("Dismiss")
        self.dismiss_vfr_btn.clicked.connect(self._on_dismiss_vfr_hint)
        vb_layout.addWidget(self.dismiss_vfr_btn, 0, Qt.AlignVCenter)

        self.vfr_banner.hide()
        layout.addWidget(self.vfr_banner, 0)

        # Offline / missing notice (Fixed height so it never stretches vertically)
        self.offline_banner = QFrame(self)
        self.offline_banner.setObjectName("LibraryOfflineBanner")
        self.offline_banner.setFixedHeight(32)
        self.offline_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        ob_layout = QHBoxLayout(self.offline_banner)
        ob_layout.setContentsMargins(12, 0, 4, 0)
        ob_layout.setSpacing(8)

        self.offline_banner_lbl = QLabel("All media files are active and verified on disk.", self.offline_banner)
        ob_layout.addWidget(self.offline_banner_lbl, 1, Qt.AlignVCenter)

        self.back_relink_btn = HyperlinkButton("", "Fix in Relink", self.offline_banner)
        self.back_relink_btn.setFixedHeight(26)
        self.back_relink_btn.clicked.connect(self.switch_to_relink_requested.emit)
        ob_layout.addWidget(self.back_relink_btn, 0, Qt.AlignVCenter)

        self.offline_banner.hide()
        layout.addWidget(self.offline_banner, 0)


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
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.setMinimumHeight(160)
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
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setFixedHeight(30)
        M = FootageLibraryModel
        widths = {
            M.COL_CHECK: 34, M.COL_FOLDER: 100, M.COL_FORMAT: 188,
            M.COL_DURATION: 76, M.COL_SIZE: 84, M.COL_FLAGS: 96,
        }
        for col, w in widths.items():
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, w)
        hdr.setSectionResizeMode(M.COL_NAME, QHeaderView.Stretch)

        # Row selection is the single source of truth; checkboxes mirror it.
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)
        self.table.viewport().installEventFilter(self)
        self.model.selection_changed.connect(self._sync_selection_state)

        layout.addWidget(self.table, 1)

        # Live refresh on render queue updates
        render_queue.queue_changed.connect(self.model.refresh_rows)
        render_queue.job_progress.connect(lambda *_: self.model.refresh_rows())

        self.apply_theme()

    def set_items(self, items: List[FootageMatchItem]):
        keep = self.model.get_checked_items()
        self.model.set_items(items)
        self._select_items(keep)
        self.update_banners()

    def set_summary(self, clip_count: int, size_display: str):
        if clip_count <= 0:
            self.summary_lbl.setText("No clips linked yet. Link footage in Relink first.")
            tone = "#6f737b"
        else:
            self.summary_lbl.setText(f"{clip_count} clip{'s' if clip_count != 1 else ''}  ·  {size_display} on disk")
            tone = "#34d399"
        self.summary_dot.setStyleSheet(f"background-color: {tone}; border-radius: 3px; border: none;")

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
                f"{summary} clip{'s' if (vfr_count + interlaced_count) != 1 else ''} may drift or comb in Premiere. "
                "Re-encode before cutting."
            )
            self.vfr_banner.show()
        else:
            self.vfr_banner.hide()

        if offline_count > 0:
            self.offline_banner_lbl.setText(
                f"{offline_count} file{'s are' if offline_count != 1 else ' is'} missing or offline on disk."
            )
            self.offline_banner.show()
        else:
            self.offline_banner.hide()

    def _on_select_vfr_clicked(self):
        self._select_items([
            it for it in self.model._all_items
            if self.model._is_item_online(it) and (it.is_vfr or getattr(it, "is_interlaced", False))
        ])

    # ------------------------------------------------------------------
    # Selection <-> checkbox sync
    # ------------------------------------------------------------------
    def _on_table_selection_changed(self, *_):
        sm = self.table.selectionModel()
        items = [self.model.item_at(i.row()) for i in sm.selectedRows()] if sm else []
        self.model.set_checked_items([it for it in items if it])  # emits selection_changed -> action bar
        all_visible = self.model.rowCount() > 0 and len(items) == self.model.rowCount()
        self.select_all_cb.blockSignals(True)
        self.select_all_cb.setChecked(all_visible)
        self.select_all_cb.blockSignals(False)

    def _select_items(self, items: List[FootageMatchItem]):
        """Selects the visible rows for `items` (checkboxes follow via the mirror)."""
        sm = self.table.selectionModel()
        if not sm:
            return
        wanted = {it.norm_url for it in items}
        sel = QItemSelection()
        last_col = self.model.columnCount() - 1
        for row in range(self.model.rowCount()):
            it = self.model.item_at(row)
            if it and it.norm_url in wanted:
                sel.select(self.model.index(row, 0), self.model.index(row, last_col))
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if not wanted:
            self._on_table_selection_changed()

    def eventFilter(self, obj, e):
        # Clicking the checkbox column toggles just that row (like Ctrl+click) instead of
        # replacing the selection; swallow the rest so the delegate doesn't toggle it twice.
        if obj is self.table.viewport() and e.type() in (
            QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick
        ):
            if e.button() == Qt.LeftButton:
                idx = self.table.indexAt(e.position().toPoint())
                if idx.isValid() and idx.column() == FootageLibraryModel.COL_CHECK:
                    if e.type() == QEvent.MouseButtonPress:
                        self.table.selectionModel().select(
                            idx, QItemSelectionModel.Toggle | QItemSelectionModel.Rows
                        )
                    return True
        return super().eventFilter(obj, e)

    def _on_dismiss_vfr_hint(self):
        self._vfr_hint_dismissed = True
        self.vfr_banner.hide()

    def _on_select_all_toggled(self, state: int):
        checked = (state == Qt.Checked.value or state == True)
        if checked:
            self.table.selectAll()
        else:
            self.table.clearSelection()

    def _apply_filters(self, *_):
        idx = self.filter_combo.currentIndex()
        modes = {0: "ALL", 1: "VFR", 2: "INTERLACED", 3: "OFFLINE", 4: "LARGEST"}
        mode = modes.get(idx, "ALL")
        keep = self.model.get_checked_items()
        self.model.set_filter(
            mode=mode,
            search_query=self.search_edit.text(),
            group_by_folder=self.group_folder_cb.isChecked(),
        )
        self._select_items(keep)  # model reset drops the view selection; restore visible picks

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
        # Checkbox-column clicks are handled in eventFilter; row clicks via selection sync.
        return

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
        text_col = pal.get("text", "#ffffff")
        muted_col = pal.get("muted", "#94a3b8")
        is_dark = pal.get("is_dark", True)
        line_col = "rgba(255, 255, 255, 0.07)" if is_dark else "rgba(0, 0, 0, 0.08)"
        row_line = "rgba(255, 255, 255, 0.05)" if is_dark else "rgba(0, 0, 0, 0.06)"
        hover_col = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.04)"

        self.summary_lbl.setStyleSheet(f"color: {muted_col}; font-size: 12px; border: none; background: transparent;")
        if not self.summary_dot.styleSheet():
            self.summary_dot.setStyleSheet("background-color: #6f737b; border-radius: 3px; border: none;")

        # Notices: soft tint, no outline, same height as a toolbar row
        self.vfr_banner.setStyleSheet("""
            QFrame#LibraryVfrBanner {
                background-color: rgba(251, 191, 36, 0.08);
                border: none;
                border-radius: 6px;
            }
        """)
        self.vfr_banner_lbl.setStyleSheet("color: #fbbf24; font-size: 12px; border: none; background: transparent;")
        self.offline_banner.setStyleSheet("""
            QFrame#LibraryOfflineBanner {
                background-color: rgba(248, 113, 113, 0.08);
                border: none;
                border-radius: 6px;
            }
        """)
        self.offline_banner_lbl.setStyleSheet("color: #f87171; font-size: 12px; border: none; background: transparent;")

        # Flat list: hairlines between rows, no outer box
        self.table.setStyleSheet(f"""
            QTableView#FootageLibraryTable {{
                background-color: transparent;
                border: none;
                color: {text_col};
                font-size: 12px;
                selection-background-color: rgba(96, 165, 250, 0.14);
                selection-color: {text_col};
                outline: none;
            }}
            QTableView#FootageLibraryTable::item {{
                padding: 0px 8px;
                border: none;
                border-bottom: 1px solid {row_line};
            }}
            QTableView#FootageLibraryTable::item:hover {{
                background-color: {hover_col};
            }}
            QHeaderView {{ background-color: transparent; border: none; }}
            QHeaderView::section {{
                background-color: transparent;
                color: {muted_col};
                font-size: 10.5px;
                font-weight: 600;
                border: none;
                border-top: 1px solid {line_col};
                border-bottom: 1px solid {line_col};
                padding: 0px 8px;
            }}
        """)
        self.model.update_palette_cache()
        self.action_bar.apply_theme()
