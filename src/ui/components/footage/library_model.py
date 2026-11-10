import os
from typing import List, Set, Optional, Dict, Tuple
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QColor, QFont

from ...theme import ThemeManager
from ....core.footage_matcher import FootageMatchItem
from ....core.render_queue import render_queue


# Pre-allocated static colors & fonts so data() does ZERO allocations on paint
_COL_GREEN = QColor("#34d399")
_COL_AMBER = QColor("#fbbf24")
_COL_BLUE = QColor("#38bdf8")
_COL_RED = QColor("#f87171")

_FONT_DEMI = QFont()
_FONT_DEMI.setWeight(QFont.DemiBold)
_FONT_BOLD = QFont()
_FONT_BOLD.setWeight(QFont.Bold)


class FootageLibraryModel(QAbstractTableModel):
    """
    Zero-I/O, zero-allocation table model for the Footage Library & Media Audit stage.
    All row strings, online states, and colors are precomputed into flat tuples on rebuild/refresh
    so Qt's paint loop executes in <0.05ms per frame at 240Hz+.
    Columns:
      0: ☐ (CheckState)
      1: Status (ACTIVE / OFFLINE / MISSING)
      2: Name (Filename or Title)
      3: Folder (Parent folder name)
      4: Codec (H264 / HEVC / etc.)
      5: Res (1920x1080)
      6: FPS (29.97 / 23.976)
      7: Frame Mode (VFR WARN / CFR)
      8: Duration (01:24)
      9: Size (142.5 MB)
      10: Job State (Idle / Queued / 43% / Done / Failed)
    """
    selection_changed = Signal()

    HEADERS = [
        "",
        "Status",
        "Name",
        "Folder",
        "Codec",
        "Res",
        "FPS",
        "VFR",
        "Duration",
        "Size",
        "Job State",
    ]

    ITEM_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_items: List[FootageMatchItem] = []
        self._visible_items: List[FootageMatchItem] = []
        self._checked_urls: Set[str] = set()
        self._filter_mode: str = "ALL"  # ALL | VFR | OFFLINE | LARGEST
        self._search_query: str = ""
        self._group_by_folder: bool = False

        # Cached disk existence map: norm_url -> bool (only updated on set_items / health check)
        self._online_cache: Dict[str, bool] = {}
        # Precomputed row display tuples & tooltips for instant O(1) paint
        self._row_display_cache: List[Tuple[str, ...]] = []
        self._row_tooltip_cache: List[str] = []
        self._row_job_status_cache: List[str] = []

        self._text_color = QColor("#ffffff")
        self._muted_color = QColor("#94a3b8")
        self.update_palette_cache()

    def update_palette_cache(self):
        pal = ThemeManager.get_palette()
        self._text_color = QColor(pal.get("text", "#ffffff"))
        self._muted_color = QColor(pal.get("muted", "#94a3b8"))

    def set_items(self, items: List[FootageMatchItem]):
        self.beginResetModel()
        self._all_items = list(items)
        valid_urls = {it.norm_url for it in self._all_items}
        self._checked_urls.intersection_update(valid_urls)
        self._refresh_online_cache()
        self._rebuild_visible()
        self.endResetModel()
        self.selection_changed.emit()

    def _refresh_online_cache(self):
        self._online_cache.clear()
        for it in self._all_items:
            self._online_cache[it.norm_url] = bool(
                it.is_matched and it.matched_path and os.path.isfile(it.matched_path)
            )

    def set_filter(self, mode: str, search_query: str = "", group_by_folder: Optional[bool] = None):
        self.beginResetModel()
        self._filter_mode = mode
        self._search_query = (search_query or "").strip().lower()
        if group_by_folder is not None:
            self._group_by_folder = group_by_folder
        self._rebuild_visible()
        self.endResetModel()

    def _is_item_online(self, it: FootageMatchItem) -> bool:
        return self._online_cache.get(it.norm_url, bool(it.is_matched and it.matched_path))

    def _rebuild_visible(self):
        filtered: List[FootageMatchItem] = []
        q = self._search_query
        for it in self._all_items:
            online = self._is_item_online(it)
            if self._filter_mode == "VFR" and not (online and it.is_vfr):
                continue
            if self._filter_mode == "INTERLACED" and not (online and getattr(it, "is_interlaced", False)):
                continue
            if self._filter_mode == "OFFLINE" and online:
                continue

            if q:
                fn = os.path.basename(it.matched_path) if it.matched_path else ""
                folder = os.path.dirname(it.matched_path) if it.matched_path else ""
                haystack = f"{it.title} {fn} {folder} {it.url}".lower()
                if q not in haystack:
                    continue

            filtered.append(it)

        if self._filter_mode == "LARGEST":
            filtered.sort(key=lambda x: int(x.file_size_bytes or 0), reverse=True)
        elif self._group_by_folder:
            filtered.sort(
                key=lambda x: (
                    os.path.dirname(x.matched_path).lower() if x.matched_path else "zzzz",
                    (os.path.basename(x.matched_path) if x.matched_path else x.title).lower(),
                )
            )
        else:
            filtered.sort(
                key=lambda x: (
                    0 if not self._is_item_online(x) else 1,
                    (os.path.basename(x.matched_path) if x.matched_path else x.title).lower(),
                )
            )

        self._visible_items = filtered
        self._rebuild_row_caches()

    def _rebuild_row_caches(self):
        self.update_palette_cache()
        self._row_display_cache.clear()
        self._row_tooltip_cache.clear()
        self._row_job_status_cache.clear()

        for it in self._visible_items:
            online = self._is_item_online(it)
            status_str = "ACTIVE" if online else ("OFFLINE" if it.matched_path else "MISSING")
            name_str = os.path.basename(it.matched_path) if it.matched_path else (it.title or os.path.basename(it.url))
            if it.matched_path:
                parent_dir = os.path.dirname(it.matched_path)
                folder_str = os.path.basename(parent_dir) or parent_dir
            else:
                folder_str = "—"
            codec_str = (it.video_codec or "—").upper()
            res_str = it.resolution or "—"
            if it.frame_rate and it.frame_rate > 0:
                fps_str = f"{it.frame_rate:.2f}" if it.is_vfr else f"{it.frame_rate:.3f}".rstrip("0").rstrip(".")
            else:
                fps_str = "—"
            is_interlaced = bool(getattr(it, "is_interlaced", False))
            if not online:
                vfr_str = "—"
            elif it.is_vfr and is_interlaced:
                vfr_str = "VFR + INT"
            elif it.is_vfr:
                vfr_str = "VFR WARN"
            elif is_interlaced:
                vfr_str = "INTERLACED"
            else:
                vfr_str = "CFR"
            dur_str = it.duration_display or "—"
            size_str = it.file_size_display or "—"

            job_status = ""
            if not it.matched_path:
                job_str = "—"
            else:
                job = render_queue.get_job_for_path(it.matched_path)
                if not job:
                    job_str = "Idle"
                elif job.status == "RUNNING":
                    job_str = f"{job.progress:.0f}% ({job.speed:.1f}x)"
                    job_status = "RUNNING"
                else:
                    job_str = job.status.title()
                    job_status = job.status

            self._row_display_cache.append((
                "",
                status_str,
                name_str,
                folder_str,
                codec_str,
                res_str,
                fps_str,
                vfr_str,
                dur_str,
                size_str,
                job_str,
            ))
            self._row_job_status_cache.append(job_status)

            tip_lines = [
                f"Script Asset: {it.title}",
                f"Local File: {it.matched_path or 'Missing'}",
                f"Source URL: {it.url}",
            ]
            if online and it.is_vfr and it.frame_rate:
                tip_lines.append(f"VFR Warning: Variable Frame Rate ({it.frame_rate:.2f} FPS avg)")
            if online and getattr(it, "is_interlaced", False):
                fo = getattr(it, "field_order", "interlaced")
                tip_lines.append(f"Interlaced Scan ({fo.upper()}): Use Re-encode with bwdif decomb filter")
            self._row_tooltip_cache.append("\n".join(tip_lines))

    def refresh_rows(self):
        if not self._visible_items:
            return
        self._rebuild_row_caches()
        top = self.index(0, 0)
        bot = self.index(len(self._visible_items) - 1, self.columnCount() - 1)
        self.dataChanged.emit(top, bot)

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_items)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        row = index.row()
        if not index.isValid() or not (0 <= row < len(self._visible_items)):
            return None

        col = index.column()
        it = self._visible_items[row]

        if role == Qt.DisplayRole:
            return self._row_display_cache[row][col] if col > 0 else None

        if role == Qt.CheckStateRole:
            if col == 0:
                return Qt.Checked if it.norm_url in self._checked_urls else Qt.Unchecked
            return None

        if role == Qt.ForegroundRole:
            online = self._online_cache.get(it.norm_url, False)
            if col == 1:
                return _COL_GREEN if online else _COL_AMBER
            if col == 2:
                return self._text_color if online else _COL_AMBER
            if col == 7:
                if not online:
                    return self._muted_color
                return _COL_AMBER if (it.is_vfr or getattr(it, "is_interlaced", False)) else _COL_GREEN
            if col == 10:
                jst = self._row_job_status_cache[row]
                if jst == "RUNNING":
                    return _COL_BLUE
                if jst == "DONE":
                    return _COL_GREEN
                if jst in ("FAILED", "INTERRUPTED"):
                    return _COL_RED
                return self._muted_color
            if col in (3, 4, 5, 6, 8, 9):
                return self._muted_color
            return self._text_color

        if role == Qt.FontRole:
            if col == 2:
                return _FONT_DEMI
            if col in (1, 7, 10):
                return _FONT_BOLD
            return None

        if role == Qt.TextAlignmentRole:
            if col in (1, 4, 5, 6, 7, 8, 10):
                return int(Qt.AlignCenter)
            if col == 9:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ToolTipRole:
            return self._row_tooltip_cache[row]

        if role == self.ITEM_ROLE:
            return it

        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or index.column() != 0 or role != Qt.CheckStateRole:
            return False
        it = self._visible_items[index.row()]
        checked = (value == Qt.Checked.value or value == Qt.Checked or bool(value))
        if checked:
            self._checked_urls.add(it.norm_url)
        else:
            self._checked_urls.discard(it.norm_url)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.selection_changed.emit()
        return True

    # Selection Helpers
    def get_checked_items(self) -> List[FootageMatchItem]:
        return [it for it in self._all_items if it.norm_url in self._checked_urls]

    def set_checked_items(self, items: List[FootageMatchItem]):
        self._checked_urls = {it.norm_url for it in items}
        if self._visible_items:
            top = self.index(0, 0)
            bot = self.index(len(self._visible_items) - 1, 0)
            self.dataChanged.emit(top, bot, [Qt.CheckStateRole])
        self.selection_changed.emit()

    def select_all_visible(self, checked: bool):
        for it in self._visible_items:
            if checked:
                self._checked_urls.add(it.norm_url)
            else:
                self._checked_urls.discard(it.norm_url)
        if self._visible_items:
            top = self.index(0, 0)
            bot = self.index(len(self._visible_items) - 1, 0)
            self.dataChanged.emit(top, bot, [Qt.CheckStateRole])
        self.selection_changed.emit()

    def select_vfr_items(self):
        self._checked_urls = {
            it.norm_url for it in self._all_items
            if self._is_item_online(it) and (it.is_vfr or getattr(it, "is_interlaced", False))
        }
        if self._visible_items:
            top = self.index(0, 0)
            bot = self.index(len(self._visible_items) - 1, 0)
            self.dataChanged.emit(top, bot, [Qt.CheckStateRole])
        self.selection_changed.emit()

    def clear_selection(self):
        if not self._checked_urls:
            return
        self._checked_urls.clear()
        if self._visible_items:
            top = self.index(0, 0)
            bot = self.index(len(self._visible_items) - 1, 0)
            self.dataChanged.emit(top, bot, [Qt.CheckStateRole])
        self.selection_changed.emit()

    def item_at(self, row: int) -> Optional[FootageMatchItem]:
        if 0 <= row < len(self._visible_items):
            return self._visible_items[row]
        return None
