import os
import re
import copy
from typing import List, Dict, Any, Optional
from PySide6.QtCore import Qt, Signal, QMimeData, QUrl, QTimer, QElapsedTimer, QRectF, QSizeF, QPointF
from PySide6.QtGui import (
    QDrag, QPixmap, QCursor, QColor, QPainter, QPen,
    QTextCursor, QTextFormat, QTextCharFormat, QTextBlockFormat,
    QTextBlockUserData, QFont, QKeySequence, QShortcut, QPalette,
    QAbstractTextDocumentLayout
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTextBrowser, QTextEdit, QAbstractScrollArea, QMenu, QApplication, QColorDialog
)
from qfluentwidgets import Slider, ComboBox
from ..theme import ThemeManager
from .widgets import GpuSmoothScroller, MiddleClickAutoScroller, get_display_frame_interval_ms, safe_copy_to_clipboard
from ...core.parser import build_raw_html


class ScriptBlockUserData(QTextBlockUserData):
    """Stores original .docx paragraph metadata on each QTextBlock for lossless 2-way editing."""
    def __init__(self, block_dict: Dict[str, Any]):
        super().__init__()
        self.block_id: Optional[int] = block_dict.get("id")
        self.is_header: bool = bool(block_dict.get("isHeader", False))
        self.header_level: Optional[int] = block_dict.get("headerLevel")
        self.images: List[str] = list(block_dict.get("images", []))
        self.orig_text: str = block_dict.get("text", "")
        self.orig_spans: List[Dict[str, Any]] = copy.deepcopy(block_dict.get("spans", []))
        self.formatting_modified: bool = False


class DraggableImageLabel(QLabel):
    """An image label that allows dragging local image files directly into Premiere Pro / DaVinci Resolve."""
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self.setToolTip("Drag me to your timeline!")

        if os.path.exists(image_path):
            pix = QPixmap(image_path)
            scaled = pix.scaled(320, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(scaled)
            self.setStyleSheet("""
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 4px;
                background-color: rgba(0, 0, 0, 0.2);
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_position = event.pos()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if not hasattr(self, 'drag_start_position'):
            return
        if (event.pos() - self.drag_start_position).manhattanLength() < 10:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        url = QUrl.fromLocalFile(os.path.abspath(self.image_path))
        mime_data.setUrls([url])
        mime_data.setText(os.path.abspath(self.image_path))
        
        drag.setMimeData(mime_data)
        if self.pixmap():
            drag.setPixmap(self.pixmap().scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        
        drag.exec(Qt.CopyAction)


class ScriptTextBrowser(QTextBrowser):
    """High-performance text browser supporting fluid Reading Mode, MS Word Print Layout Mode, and Direct Editing."""
    page_info_changed = Signal(int, int)       # (current_page, total_pages)
    zoom_step_requested = Signal(int)          # +10 or -10 from Ctrl+Wheel in Document Mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewport().setAttribute(Qt.WA_Hover, True)
        self.viewport().setMouseTracking(True)
        self._is_reflow_held = False
        self._held_width = -1
        self._view_mode = "reading"   # "reading" | "document"
        self._paper_theme = "dark"    # "dark" | "light"
        self._font_size = 14
        self._doc_zoom_pct = 100
        self._is_paginating = False
        self._page_count = 1
        self._current_page = 1
        self._page_gap = 24.0

        # Authentic 1:1 MS Word Geometry at 96 DPI:
        # US Letter: 8.5 x 11 in = 816 x 1056 px
        # Margins: 1.0 in left/right = 96 px -> Printable Width = 624 px
        # Margins: 1.0 in top/bottom = 96 px -> Printable Height = 704 px (Word body text)
        self._sheet_w = 816.0
        self._sheet_h = 1056.0
        self._margin_left = 96.0
        self._margin_right = 96.0
        self._margin_top = 96.0
        self._margin_bottom = 96.0
        self._printable_w = 624.0
        self._printable_h = 864.0
        self._page_geometry = {
            "width_pt": 612.0,
            "height_pt": 792.0,
            "margin_top_pt": 72.0,
            "margin_bottom_pt": 72.0,
            "margin_left_pt": 72.0,
            "margin_right_pt": 72.0,
        }
        self._hovered_anchor = None
        if self.verticalScrollBar():
            self.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)

    def set_page_layout(
        self,
        view_mode: str,
        paper_theme: str,
        page_geometry: Dict[str, float],
        font_size: int,
        doc_zoom_pct: int = 100
    ):
        self._view_mode = view_mode
        self._paper_theme = paper_theme
        if page_geometry:
            self._page_geometry = dict(page_geometry)
        self._font_size = font_size
        self._doc_zoom_pct = max(40, min(200, int(doc_zoom_pct)))
        self._apply_document_margins()
        if self.viewport():
            self.viewport().update()

    def set_zoom(self, pct: int):
        """Zero-reflow camera zoom: scales paper sheets graphically and anchors to current page."""
        pct = max(40, min(200, int(pct)))
        if pct == self._doc_zoom_pct:
            return

        # 1. Capture current focal point before changing zoom
        scroll_y = self.verticalScrollBar().value() if self.verticalScrollBar() else 0
        vp_h = self.viewport().height() if self.viewport() else 600
        old_scale = self._doc_zoom_pct / 100.0
        old_stride = (self._sheet_h + self._page_gap) * old_scale
        old_center = scroll_y + (vp_h * 0.4)
        focal_page = max(0, int(old_center // max(1.0, old_stride)))
        focal_ratio = (old_center % max(1.0, old_stride)) / max(1.0, old_stride)

        # 2. Update zoom percentage (ZERO REFLOW: document layout is untouched!)
        self._doc_zoom_pct = pct
        self._update_scroll_ranges()

        # 3. Restore focal point to keep the viewed page anchored
        new_scale = pct / 100.0
        new_stride = (self._sheet_h + self._page_gap) * new_scale
        new_center = focal_page * new_stride + focal_ratio * new_stride
        new_scroll_y = int(new_center - (vp_h * 0.4))
        if self.verticalScrollBar():
            self.verticalScrollBar().setValue(max(0, min(self.verticalScrollBar().maximum(), new_scroll_y)))

        self._update_current_page_from_scroll()
        if self.viewport():
            self.viewport().update()

    def _apply_document_margins(self):
        doc = self.document()
        if not doc or not doc.rootFrame() or self._is_paginating:
            return
        vp_w = self.viewport().width() if self.viewport() else self.width()
        if vp_w <= 0:
            vp_w = 560

        self._is_paginating = True
        was_undo = doc.isUndoRedoEnabled()
        doc.setUndoRedoEnabled(False)
        try:
            root_fmt = doc.rootFrame().frameFormat()
            if self._view_mode != "document":
                self.setLineWrapMode(QTextEdit.WidgetWidth)
                root_fmt.setLeftMargin(0)
                root_fmt.setRightMargin(0)
                root_fmt.setTopMargin(0)
                root_fmt.setBottomMargin(0)
                doc.rootFrame().setFrameFormat(root_fmt)
                doc.setPageSize(QSizeF(vp_w, -1))
                self._page_count = 1
                self._current_page = 1
                return

            self.setLineWrapMode(QTextEdit.FixedPixelWidth)

            # Convert DOCX point geometry to 96 DPI pixel geometry
            dpi_factor = 96.0 / 72.0
            w_pt = float(self._page_geometry.get("width_pt", 612.0))
            h_pt = float(self._page_geometry.get("height_pt", 792.0))
            ml_pt = float(self._page_geometry.get("margin_left_pt", 72.0))
            mr_pt = float(self._page_geometry.get("margin_right_pt", 72.0))
            mt_pt = float(self._page_geometry.get("margin_top_pt", 72.0))
            mb_pt = float(self._page_geometry.get("margin_bottom_pt", 72.0))

            self._sheet_w = w_pt * dpi_factor
            self._sheet_h = h_pt * dpi_factor
            self._margin_left = ml_pt * dpi_factor
            self._margin_right = mr_pt * dpi_factor
            self._margin_top = mt_pt * dpi_factor
            self._margin_bottom = mb_pt * dpi_factor
            self._printable_w = max(200.0, (w_pt - ml_pt - mr_pt) * dpi_factor)
            self._printable_h = max(300.0, (h_pt - mt_pt - mb_pt) * dpi_factor)
            self._page_gap = 24.0

            # Document Mode layout is FIXED at 11.0 pt Arial (authentic MS Word base font)
            doc_font = doc.defaultFont()
            doc_font.setFamily("Arial")
            doc_font.setPointSizeF(11.0)
            doc.setDefaultFont(doc_font)

            doc.setDocumentMargin(0)
            root_fmt.setLeftMargin(0)
            root_fmt.setRightMargin(0)
            root_fmt.setTopMargin(0)
            root_fmt.setBottomMargin(0)
            doc.rootFrame().setFrameFormat(root_fmt)

            self.setLineWrapColumnOrWidth(int(self._printable_w))
            doc.setPageSize(QSizeF(self._printable_w, self._printable_h))
            self._page_count = max(1, doc.pageCount())

            self._update_scroll_ranges()
            self._update_current_page_from_scroll()
        finally:
            doc.setUndoRedoEnabled(was_undo)
            self._is_paginating = False

    def _update_scroll_ranges(self):
        if self._view_mode != "document":
            return
        scale = self._doc_zoom_pct / 100.0
        scaled_w = self._sheet_w * scale
        scaled_h = self._sheet_h * scale
        gap = self._page_gap * scale
        total_h = self._page_count * (scaled_h + gap) + gap

        vp_w = self.viewport().width() if self.viewport() else self.width()
        vp_h = self.viewport().height() if self.viewport() else self.height()

        if self.verticalScrollBar():
            self.verticalScrollBar().setRange(0, max(0, int(total_h - vp_h)))
            self.verticalScrollBar().setPageStep(max(10, vp_h))

        if self.horizontalScrollBar():
            h_excess = max(0, int((scaled_w + 48) - vp_w))
            self.horizontalScrollBar().setRange(0, h_excess)
            self.horizontalScrollBar().setPageStep(max(10, vp_w))

    def _on_scroll_value_changed(self, val):
        self._update_current_page_from_scroll()
        if self._view_mode == "document" and self.viewport():
            self.viewport().update()

    def _update_current_page_from_scroll(self):
        if self._view_mode != "document":
            return
        scale = self._doc_zoom_pct / 100.0
        stride = (self._sheet_h + self._page_gap) * scale
        scroll_y = self.verticalScrollBar().value() if self.verticalScrollBar() else 0
        vp_h = self.viewport().height() if self.viewport() else self.height()
        focal_y = scroll_y + (vp_h * 0.38)
        cur_page = int(focal_y // max(1.0, stride)) + 1
        cur_page = max(1, min(self._page_count, cur_page))
        cur_info = (cur_page, self._page_count)
        if getattr(self, "_last_emitted_page_info", None) != cur_info:
            self._current_page = cur_page
            self._last_emitted_page_info = cur_info
            self.page_info_changed.emit(self._current_page, self._page_count)

    def set_reflow_held(self, hold: bool):
        if self._is_reflow_held == hold:
            return
        self._is_reflow_held = hold
        if hold:
            vp_w = self.viewport().width() if self.viewport() else self.width()
            self._held_width = max(100, vp_w)
        else:
            self._held_width = -1
            if self.viewport():
                target_w = self.viewport().width()
                if target_w > 0:
                    self._apply_document_margins()
                    self.document().setTextWidth(target_w)
                    self.viewport().update()

    def resizeEvent(self, event):
        if self._is_reflow_held and self._held_width > 0:
            QAbstractScrollArea.resizeEvent(self, event)
            return
        super().resizeEvent(event)
        if not getattr(self.parent(), "_updating_programmatically", False):
            if self._view_mode == "document":
                self._update_scroll_ranges()
                self._update_current_page_from_scroll()
            else:
                self._apply_document_margins()

    def wheelEvent(self, event):
        if self._view_mode == "document" and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta != 0:
                self.zoom_step_requested.emit(10 if delta > 0 else -10)
                event.accept()
                return
        super().wheelEvent(event)

    def _map_viewport_to_doc_coords(self, vp_pos):
        """Maps viewport mouse position to 1:1 document coordinates for link clicking in Document Mode."""
        scale = self._doc_zoom_pct / 100.0
        scaled_w = self._sheet_w * scale
        scaled_h = self._sheet_h * scale
        gap = self._page_gap * scale
        stride = scaled_h + gap

        scroll_y = self.verticalScrollBar().value() if self.verticalScrollBar() else 0
        scroll_x = self.horizontalScrollBar().value() if self.horizontalScrollBar() else 0
        vp_w = self.viewport().width() if self.viewport() else self.width()
        sheet_x = max(24.0, (vp_w - scaled_w) / 2.0) - scroll_x

        pos_x = vp_pos.x()
        pos_y = vp_pos.y()

        canvas_y = pos_y + scroll_y
        p_idx = int(canvas_y // max(1.0, stride))
        if 0 <= p_idx < self._page_count:
            page_top = gap + p_idx * stride
            if page_top <= canvas_y <= page_top + scaled_h:
                if sheet_x <= pos_x <= sheet_x + scaled_w:
                    text_left = sheet_x + self._margin_left * scale
                    text_top = page_top - scroll_y + self._margin_top * scale
                    doc_x = (pos_x - text_left) / scale
                    doc_y = (pos_y - text_top) / scale + (p_idx * self._printable_h)
                    return p_idx, QPointF(doc_x, doc_y)
        return None, None

    def mouseMoveEvent(self, event):
        if self._view_mode == "document":
            p = event.position() if hasattr(event, "position") else event.pos()
            p_idx, doc_pt = self._map_viewport_to_doc_coords(p)
            if doc_pt is not None:
                anchor = self.document().documentLayout().anchorAt(doc_pt)
                if anchor:
                    if anchor != self._hovered_anchor:
                        self._hovered_anchor = anchor
                        self.viewport().setCursor(QCursor(Qt.PointingHandCursor))
                        self.setToolTip(anchor)
                    return
            if self._hovered_anchor is not None:
                self._hovered_anchor = None
                self.viewport().setCursor(QCursor(Qt.ArrowCursor))
                self.setToolTip("")
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._view_mode == "document" and event.button() == Qt.LeftButton:
            p = event.position() if hasattr(event, "position") else event.pos()
            p_idx, doc_pt = self._map_viewport_to_doc_coords(p)
            if doc_pt is not None:
                anchor = self.document().documentLayout().anchorAt(doc_pt)
                if anchor:
                    self.anchorClicked.emit(QUrl(anchor))
                    event.accept()
                    return
        elif not self.isReadOnly() and event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            href = self.anchorAt(event.pos())
            if href:
                self.anchorClicked.emit(QUrl(href))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.isReadOnly():
            self.clearFocus()

    def paintEvent(self, event):
        if self._view_mode != "document":
            super().paintEvent(event)
            return

        if not self.viewport():
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        vp_rect = self.viewport().rect()
        vp_w = vp_rect.width()
        vp_h = vp_rect.height()

        pal = ThemeManager.get_palette()

        # Canvas backdrop behind pages (match outer card surface grey)
        canvas_bg = QColor(pal["surface"])
        if self._paper_theme == "light":
            paper_bg = QColor("#ffffff")
            paper_border = QColor(148, 163, 184, 180)
            shadow_col = QColor(0, 0, 0, 75)
            crop_col = QColor(148, 163, 184, 140)
        else:
            paper_bg = QColor("#141720")
            paper_border = QColor(255, 255, 255, 35)
            shadow_col = QColor(0, 0, 0, 95)
            crop_col = QColor(255, 255, 255, 45)

        painter.fillRect(vp_rect, canvas_bg)

        scale = self._doc_zoom_pct / 100.0
        scaled_w = self._sheet_w * scale
        scaled_h = self._sheet_h * scale
        gap = self._page_gap * scale
        stride = scaled_h + gap

        scroll_y = self.verticalScrollBar().value() if self.verticalScrollBar() else 0
        scroll_x = self.horizontalScrollBar().value() if self.horizontalScrollBar() else 0
        sheet_x = max(24.0, (vp_w - scaled_w) / 2.0) - scroll_x

        crop_len = max(6.0, 9.0 * scale)

        for p_idx in range(self._page_count):
            page_top = gap + p_idx * stride - scroll_y
            if page_top + scaled_h < -20 or page_top > vp_h + 20:
                continue

            sheet_rect = QRectF(sheet_x, page_top, scaled_w, scaled_h)

            # 1. Page Drop Shadow
            shadow_rect = sheet_rect.adjusted(2, 4, 4, 6)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shadow_col)
            painter.drawRoundedRect(shadow_rect, 3, 3)

            # 2. Page Sheet Surface
            painter.setPen(QPen(paper_border, 1.0))
            painter.setBrush(paper_bg)
            painter.drawRoundedRect(sheet_rect, 2, 2)

            # 3. Four-Corner Margin Crop Marks
            tl_x = sheet_x + self._margin_left * scale
            tr_x = sheet_x + scaled_w - self._margin_right * scale
            top_y = page_top + self._margin_top * scale
            bot_y = page_top + self._margin_top * scale + self._printable_h * scale

            painter.setPen(QPen(crop_col, 1.0))
            painter.drawLine(tl_x - crop_len, top_y, tl_x, top_y)
            painter.drawLine(tl_x, top_y - crop_len, tl_x, top_y)
            painter.drawLine(tr_x, top_y, tr_x + crop_len, top_y)
            painter.drawLine(tr_x, top_y - crop_len, tr_x, top_y)
            painter.drawLine(tl_x - crop_len, bot_y, tl_x, bot_y)
            painter.drawLine(tl_x, bot_y, tl_x, bot_y + crop_len)
            painter.drawLine(tr_x, bot_y, tr_x + crop_len, bot_y)
            painter.drawLine(tr_x, bot_y, tr_x, bot_y + crop_len)

            # 4. Text Rendering with C++ Painter Scaling (ZERO REFLOW)
            text_x = sheet_x + self._margin_left * scale
            text_y = page_top + self._margin_top * scale

            painter.save()
            painter.setClipRect(QRectF(text_x, text_y, self._printable_w * scale, self._printable_h * scale))
            painter.translate(text_x, text_y)
            painter.scale(scale, scale)

            clip_rect = QRectF(0, p_idx * self._printable_h, self._printable_w, self._printable_h)
            painter.translate(0, -p_idx * self._printable_h)

            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.clip = clip_rect
            text_col = QColor("#0f172a") if self._paper_theme == "light" else QColor("#f1f5f9")
            p_pal = self.palette()
            p_pal.setColor(QPalette.Text, text_col)
            ctx.palette = p_pal
            selections = []
            for es in self.extraSelections():
                s = QAbstractTextDocumentLayout.Selection()
                s.cursor = es.cursor
                s.format = es.format
                selections.append(s)
            ctx.selections = selections

            self.document().documentLayout().draw(painter, ctx)
            painter.restore()

        painter.end()


class ScriptViewer(QFrame):
    """Left Pane: Source Script viewer with Reading/Document Print Layout modes, Direct Edit Mode, and Word status bar."""
    timestamp_clicked = Signal(str)
    timestamp_sync_clicked = Signal(str, int)
    add_note_requested = Signal(str)
    copy_feedback_requested = Signal(str)
    save_docx_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("ScriptViewerPane")
        self.current_font_size = 14
        self.current_doc_zoom = 100
        self._raw_html = ""
        self._blocks: List[Dict[str, Any]] = []
        self._link_colors: Dict[str, str] = {}
        self._page_geometry: Dict[str, float] = {
            "width_pt": 612.0,
            "height_pt": 792.0,
            "margin_top_pt": 72.0,
            "margin_bottom_pt": 72.0,
            "margin_left_pt": 72.0,
            "margin_right_pt": 72.0,
        }
        self.view_mode = "reading"      # "reading" | "document"
        self.paper_theme = "dark"       # "dark" | "light"
        self.is_edit_mode = False
        self.is_dirty = False
        self._updating_programmatically = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(6)

        # Header Row: File Name + View Mode Toggle + Paper Toggle + Edit/Save Controls + Reading Font Slider
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        header.setSpacing(6)

        self.title_label = QLabel("SCRIPT")
        self.title_label.setObjectName("PaneTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        # 1. Segmented View Mode Pills: Reading | Document
        self.reading_mode_btn = QPushButton("Reading", self)
        self.reading_mode_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.reading_mode_btn.setFixedHeight(24)
        self.reading_mode_btn.setToolTip("Fluid NLE reading layout")
        self.reading_mode_btn.clicked.connect(lambda: self.set_view_mode("reading"))
        header.addWidget(self.reading_mode_btn)

        self.document_mode_btn = QPushButton("Document", self)
        self.document_mode_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.document_mode_btn.setFixedHeight(24)
        self.document_mode_btn.setToolTip("MS Word Print Layout with discrete pages & real margins")
        self.document_mode_btn.clicked.connect(lambda: self.set_view_mode("document"))
        header.addWidget(self.document_mode_btn)

        # 2. Paper Theme Toggle (Only shown in Document Mode)
        self.paper_theme_btn = QPushButton("White Page", self)
        self.paper_theme_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.paper_theme_btn.setFixedHeight(24)
        self.paper_theme_btn.setToolTip("Toggle between Dark Paper and Classic White Paper")
        self.paper_theme_btn.clicked.connect(self.toggle_paper_theme)
        self.paper_theme_btn.hide()
        header.addWidget(self.paper_theme_btn)

        # 3. Direct Edit Mode Toggle + Save / Cancel
        self.edit_btn = QPushButton("Edit", self)
        self.edit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.edit_btn.setFixedHeight(24)
        self.edit_btn.setToolTip("Toggle Direct Edit Mode (Ctrl+E)")
        self.edit_btn.clicked.connect(self.toggle_edit_mode)
        header.addWidget(self.edit_btn)

        self.save_btn = QPushButton("Save .docx", self)
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.save_btn.setFixedHeight(24)
        self.save_btn.setToolTip("Save edits to .docx file and refresh timeline (Ctrl+S)")
        self.save_btn.clicked.connect(self.save_edits)
        self.save_btn.hide()
        header.addWidget(self.save_btn)

        self.cancel_edit_btn = QPushButton("Cancel", self)
        self.cancel_edit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cancel_edit_btn.setFixedHeight(24)
        self.cancel_edit_btn.setToolTip("Discard unsaved script edits")
        self.cancel_edit_btn.clicked.connect(self.cancel_edit_mode)
        self.cancel_edit_btn.hide()
        header.addWidget(self.cancel_edit_btn)

        # 4. Reading Mode Font Size Slider (Hidden in Document Mode!)
        self.zoom_lbl = QLabel("Aa", self)
        self.zoom_lbl.setStyleSheet("font-size: 11px; font-weight: 700; margin-left: 4px; margin-right: 2px;")
        header.addWidget(self.zoom_lbl)

        self.zoom_slider = Slider(Qt.Horizontal, self)
        self.zoom_slider.setRange(11, 24)
        self.zoom_slider.setValue(14)
        self.zoom_slider.setFixedWidth(74)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        header.addWidget(self.zoom_slider)

        layout.addLayout(header)

        # Collapsible Direct Edit Formatting Bar
        self.edit_toolbar = QFrame(self)
        self.edit_toolbar.setObjectName("ScriptEditToolbar")
        self.edit_toolbar.setFixedHeight(34)
        tb_layout = QHBoxLayout(self.edit_toolbar)
        tb_layout.setContentsMargins(8, 3, 8, 3)
        tb_layout.setSpacing(6)

        self.style_combo = ComboBox(self.edit_toolbar)
        self.style_combo.addItems(["Normal", "Heading 1", "Heading 2", "Heading 3"])
        self.style_combo.setFixedWidth(108)
        self.style_combo.setFixedHeight(26)
        self.style_combo.currentTextChanged.connect(self._on_heading_style_selected)
        tb_layout.addWidget(self.style_combo)

        self.bold_btn = QPushButton("B", self.edit_toolbar)
        self.bold_btn.setFixedSize(26, 26)
        self.bold_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.bold_btn.setToolTip("Bold (Ctrl+B)")
        self.bold_btn.clicked.connect(self.format_bold)
        tb_layout.addWidget(self.bold_btn)

        self.italic_btn = QPushButton("I", self.edit_toolbar)
        self.italic_btn.setFixedSize(26, 26)
        self.italic_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.italic_btn.setToolTip("Italic (Ctrl+I)")
        self.italic_btn.clicked.connect(self.format_italic)
        tb_layout.addWidget(self.italic_btn)

        self.color_btn = QPushButton("Color", self.edit_toolbar)
        self.color_btn.setFixedHeight(26)
        self.color_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.color_btn.setToolTip("Set text color")
        self.color_btn.clicked.connect(self._show_color_menu)
        tb_layout.addWidget(self.color_btn)

        self.insert_editor_btn = QPushButton("+ @editor", self.edit_toolbar)
        self.insert_editor_btn.setFixedHeight(26)
        self.insert_editor_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.insert_editor_btn.setToolTip("Insert @editor command tag at cursor")
        self.insert_editor_btn.clicked.connect(self.insert_editor_tag)
        tb_layout.addWidget(self.insert_editor_btn)

        self.insert_ts_btn = QPushButton("+ 00:00", self.edit_toolbar)
        self.insert_ts_btn.setFixedHeight(26)
        self.insert_ts_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.insert_ts_btn.setToolTip("Insert timestamp span at cursor")
        self.insert_ts_btn.clicked.connect(self.insert_timestamp_template)
        tb_layout.addWidget(self.insert_ts_btn)

        tb_layout.addStretch(1)
        self.edit_hint_lbl = QLabel("Ctrl+Click links • Ctrl+S to Save", self.edit_toolbar)
        self.edit_hint_lbl.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: 600;")
        tb_layout.addWidget(self.edit_hint_lbl)

        self.edit_toolbar.hide()
        layout.addWidget(self.edit_toolbar)

        # Rich Text Browser / Editor
        self.browser = ScriptTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setReadOnly(True)
        self.browser.setFocusPolicy(Qt.NoFocus)
        self.browser.setAutoFillBackground(False)
        if self.browser.viewport():
            self.browser.viewport().setAutoFillBackground(False)
        self.browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                outline: none;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
            }
            QTextBrowser:focus {
                outline: none;
                border: none;
            }
        """)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        self.browser.setContextMenuPolicy(Qt.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._show_context_menu)
        self.browser.document().contentsChanged.connect(self._on_document_contents_changed)
        self.browser.page_info_changed.connect(self._on_page_info_changed)
        self.browser.zoom_step_requested.connect(self._step_doc_zoom)
        self.smooth_scroller = GpuSmoothScroller(self.browser, self)
        self.middle_scroller = MiddleClickAutoScroller(self.browser, self)
        self._pulse_clock = QElapsedTimer()
        self._pulse_duration_ms = 1050.0
        layout.addWidget(self.browser, 1)

        # Bottom MS Word-style Status Bar (Visible in Document Mode: Page X of Y on left, - [slider] + 100% on right)
        self.doc_status_bar = QFrame(self)
        self.doc_status_bar.setObjectName("DocStatusBar")
        self.doc_status_bar.setFixedHeight(26)
        status_layout = QHBoxLayout(self.doc_status_bar)
        status_layout.setContentsMargins(8, 0, 8, 0)
        status_layout.setSpacing(6)

        self.page_indicator_lbl = QLabel("Page 1 of 1", self.doc_status_bar)
        self.page_indicator_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
        status_layout.addWidget(self.page_indicator_lbl)

        status_layout.addStretch(1)

        self.doc_zoom_out_btn = QPushButton("−", self.doc_status_bar)
        self.doc_zoom_out_btn.setFixedSize(20, 20)
        self.doc_zoom_out_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.doc_zoom_out_btn.setToolTip("Zoom Out (Ctrl + Wheel Down)")
        self.doc_zoom_out_btn.clicked.connect(lambda: self._step_doc_zoom(-10))
        status_layout.addWidget(self.doc_zoom_out_btn)

        self.doc_zoom_slider = Slider(Qt.Horizontal, self.doc_status_bar)
        self.doc_zoom_slider.setRange(40, 200)
        self.doc_zoom_slider.setValue(100)
        self.doc_zoom_slider.setFixedWidth(110)
        self.doc_zoom_slider.setToolTip("Document Page Zoom (40% – 200%)")
        self.doc_zoom_slider.valueChanged.connect(self._on_doc_zoom_changed)
        status_layout.addWidget(self.doc_zoom_slider)

        self.doc_zoom_in_btn = QPushButton("+", self.doc_status_bar)
        self.doc_zoom_in_btn.setFixedSize(20, 20)
        self.doc_zoom_in_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.doc_zoom_in_btn.setToolTip("Zoom In (Ctrl + Wheel Up)")
        self.doc_zoom_in_btn.clicked.connect(lambda: self._step_doc_zoom(10))
        status_layout.addWidget(self.doc_zoom_in_btn)

        self.doc_zoom_pct_btn = QPushButton("100%", self.doc_status_bar)
        self.doc_zoom_pct_btn.setFixedWidth(44)
        self.doc_zoom_pct_btn.setFixedHeight(20)
        self.doc_zoom_pct_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.doc_zoom_pct_btn.setToolTip("Click to reset zoom to 100%")
        self.doc_zoom_pct_btn.clicked.connect(lambda: self.doc_zoom_slider.setValue(100))
        status_layout.addWidget(self.doc_zoom_pct_btn)

        self.doc_status_bar.hide()
        layout.addWidget(self.doc_status_bar)

        # Keyboard Shortcuts
        self._shortcut_edit = QShortcut(QKeySequence("Ctrl+E"), self)
        self._shortcut_edit.activated.connect(self.toggle_edit_mode)
        self._shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_save.activated.connect(self._on_ctrl_s)
        self._shortcut_bold = QShortcut(QKeySequence("Ctrl+B"), self.browser)
        self._shortcut_bold.activated.connect(lambda: self.format_bold() if self.is_edit_mode else None)
        self._shortcut_italic = QShortcut(QKeySequence("Ctrl+I"), self.browser)
        self._shortcut_italic.activated.connect(lambda: self.format_italic() if self.is_edit_mode else None)

        self._style_header_controls()

    def _style_header_controls(self):
        pal = ThemeManager.get_palette()
        is_dark = pal["is_dark"]
        accent = pal["accent"]
        text_col = pal["text"]
        muted_col = pal["muted"]
        muted_bg = "rgba(255, 255, 255, 0.06)" if is_dark else "rgba(0, 0, 0, 0.06)"
        border_col = "rgba(255, 255, 255, 0.12)" if is_dark else "rgba(0, 0, 0, 0.12)"

        def pill_qss(active: bool, primary: bool = False) -> str:
            if primary:
                return f"""
                    QPushButton {{
                        background-color: #22c55e;
                        color: #0b0f1a;
                        border: none;
                        border-radius: 5px;
                        padding: 0px 10px;
                        font-size: 11px;
                        font-weight: 700;
                    }}
                    QPushButton:hover {{
                        background-color: #4ade80;
                    }}
                """
            if active:
                return f"""
                    QPushButton {{
                        background-color: {accent};
                        color: #0b0f1a;
                        border: none;
                        border-radius: 5px;
                        padding: 0px 9px;
                        font-size: 11px;
                        font-weight: 700;
                    }}
                """
            return f"""
                QPushButton {{
                    background-color: {muted_bg};
                    color: {text_col};
                    border: 1px solid {border_col};
                    border-radius: 5px;
                    padding: 0px 9px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    border: 1px solid {accent};
                }}
            """

        self.reading_mode_btn.setStyleSheet(pill_qss(self.view_mode == "reading"))
        self.document_mode_btn.setStyleSheet(pill_qss(self.view_mode == "document"))
        self.paper_theme_btn.setStyleSheet(pill_qss(self.paper_theme == "light"))
        self.edit_btn.setStyleSheet(pill_qss(self.is_edit_mode))
        self.save_btn.setStyleSheet(pill_qss(False, primary=True))
        self.cancel_edit_btn.setStyleSheet(pill_qss(False))

        tb_bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.04)"
        self.edit_toolbar.setStyleSheet(f"""
            QFrame#ScriptEditToolbar {{
                background-color: {tb_bg};
                border: 1px solid {border_col};
                border-radius: 6px;
            }}
        """)
        self.bold_btn.setStyleSheet(pill_qss(False) + "QPushButton { font-weight: 800; padding: 0px; }")
        self.italic_btn.setStyleSheet(pill_qss(False) + "QPushButton { font-style: italic; font-weight: 700; padding: 0px; }")
        self.color_btn.setStyleSheet(pill_qss(False))
        self.insert_editor_btn.setStyleSheet(pill_qss(False) + "QPushButton { color: #facc15; }")
        self.insert_ts_btn.setStyleSheet(pill_qss(False) + "QPushButton { color: #38bdf8; }")

        # Word-style Bottom Status Bar styling
        status_bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.04)"
        self.doc_status_bar.setStyleSheet(f"""
            QFrame#DocStatusBar {{
                background-color: {status_bg};
                border-top: 1px solid {border_col};
                border-radius: 5px;
            }}
        """)
        self.page_indicator_lbl.setStyleSheet(f"color: {muted_col}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        zoom_btn_qss = f"""
            QPushButton {{
                background: transparent;
                color: {text_col};
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: 700;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {muted_bg};
                color: {accent};
            }}
        """
        self.doc_zoom_out_btn.setStyleSheet(zoom_btn_qss)
        self.doc_zoom_in_btn.setStyleSheet(zoom_btn_qss)
        self.doc_zoom_pct_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {muted_col};
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                padding: 0px 2px;
            }}
            QPushButton:hover {{
                color: {accent};
            }}
        """)

    def set_view_mode(self, mode: str):
        if mode not in ("reading", "document"):
            return
        self.view_mode = mode
        is_doc = (mode == "document")
        self.paper_theme_btn.setVisible(is_doc)
        # In Document Mode: hide top Aa font slider and show bottom MS Word Status Bar
        self.zoom_lbl.setVisible(not is_doc)
        self.zoom_slider.setVisible(not is_doc)
        self.doc_status_bar.setVisible(is_doc)
        self._style_header_controls()
        self._update_display()

    def _on_page_info_changed(self, current_page: int, total_pages: int):
        self.page_indicator_lbl.setText(f"Page {current_page} of {total_pages}")

    def _step_doc_zoom(self, delta_pct: int):
        new_val = max(40, min(200, self.current_doc_zoom + delta_pct))
        self.doc_zoom_slider.setValue(new_val)

    def _on_doc_zoom_changed(self, pct: int):
        self.current_doc_zoom = pct
        self.doc_zoom_pct_btn.setText(f"{pct}%")
        if self.view_mode == "document":
            self.browser.set_zoom(pct)

    def toggle_paper_theme(self):
        self.paper_theme = "light" if self.paper_theme == "dark" else "dark"
        self.paper_theme_btn.setText("Dark Page" if self.paper_theme == "light" else "White Page")
        self._style_header_controls()
        self._update_display()

    def toggle_edit_mode(self):
        self.set_edit_mode(not self.is_edit_mode)

    def set_edit_mode(self, enabled: bool):
        if self.is_edit_mode == enabled:
            return
        self._updating_programmatically = True
        try:
            self.is_edit_mode = enabled
            self.browser.setReadOnly(not enabled)
            self.browser.setFocusPolicy(Qt.StrongFocus if enabled else Qt.NoFocus)
            self.browser.setTextInteractionFlags(
                Qt.TextEditorInteraction if enabled else Qt.TextBrowserInteraction
            )
            self.edit_toolbar.setVisible(enabled)
            self.save_btn.setVisible(enabled)
            self.cancel_edit_btn.setVisible(enabled)
            self.edit_btn.setText("Editing" if enabled else "Edit")
            self.is_dirty = False
            self.save_btn.setText("Save .docx")
            if enabled:
                self.browser.setFocus()
            else:
                self.browser.clearFocus()
            self._style_header_controls()
        finally:
            self._updating_programmatically = False

    def cancel_edit_mode(self):
        self.is_dirty = False
        self.set_edit_mode(False)
        self._update_display()

    def _on_ctrl_s(self):
        if self.is_edit_mode:
            self.save_edits()

    def _on_document_contents_changed(self):
        if self._updating_programmatically or getattr(self.browser, "_is_paginating", False) or not self.is_edit_mode:
            return
        self.is_dirty = True
        self.save_btn.setText("Save .docx *")
        if self.view_mode == "document":
            self.browser._apply_document_margins()
            if self.browser.viewport():
                self.browser.viewport().update()

    def _mark_current_block_formatting_modified(self):
        block = self.browser.textCursor().block()
        if block.isValid() and isinstance(block.userData(), ScriptBlockUserData):
            block.userData().formatting_modified = True

    def format_bold(self):
        if not self.is_edit_mode:
            return
        cursor = self.browser.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        fmt = QTextCharFormat()
        is_bold = cursor.charFormat().fontWeight() >= QFont.Bold
        fmt.setFontWeight(QFont.Normal if is_bold else QFont.Bold)
        cursor.mergeCharFormat(fmt)
        self.browser.mergeCurrentCharFormat(fmt)
        self._mark_current_block_formatting_modified()

    def format_italic(self):
        if not self.is_edit_mode:
            return
        cursor = self.browser.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        fmt = QTextCharFormat()
        fmt.setFontItalic(not cursor.charFormat().fontItalic())
        cursor.mergeCharFormat(fmt)
        self.browser.mergeCurrentCharFormat(fmt)
        self._mark_current_block_formatting_modified()

    def format_color(self, hex_color: Optional[str]):
        if not self.is_edit_mode:
            return
        cursor = self.browser.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        fmt = QTextCharFormat()
        if hex_color:
            fmt.setForeground(QColor(hex_color))
        else:
            fmt.clearForeground()
        cursor.mergeCharFormat(fmt)
        self.browser.mergeCurrentCharFormat(fmt)
        self._mark_current_block_formatting_modified()

    def _show_color_menu(self):
        menu = QMenu(self)
        swatches = [
            ("Default Text", None),
            ("Editor Yellow (#facc15)", "#facc15"),
            ("Sky Blue (#38bdf8)", "#38bdf8"),
            ("Emerald Green (#4ade80)", "#4ade80"),
            ("Rose Pink (#fb7185)", "#fb7185"),
            ("Amber Orange (#fb923c)", "#fb923c"),
            ("Crimson Red (#f87171)", "#f87171"),
        ]
        actions = {}
        for label, col in swatches:
            act = menu.addAction(label)
            actions[act] = col
        menu.addSeparator()
        custom_act = menu.addAction("Custom Color...")

        chosen = menu.exec(self.color_btn.mapToGlobal(self.color_btn.rect().bottomLeft()))
        if chosen in actions:
            self.format_color(actions[chosen])
        elif chosen == custom_act:
            col = QColorDialog.getColor(Qt.white, self, "Select Script Text Color")
            if col.isValid():
                self.format_color(col.name())

    def _on_heading_style_selected(self, style_text: str):
        if not self.is_edit_mode or self._updating_programmatically:
            return
        cursor = self.browser.textCursor()
        block = cursor.block()
        if not block.isValid():
            return

        user_data = block.userData()
        if not isinstance(user_data, ScriptBlockUserData):
            user_data = ScriptBlockUserData({"id": None, "text": block.text()})
            block.setUserData(user_data)
        user_data.formatting_modified = True

        base_pt = self._effective_font_pt()
        char_fmt = QTextCharFormat()
        if style_text.startswith("Heading"):
            lvl = int(style_text.split()[-1])
            user_data.is_header = True
            user_data.header_level = lvl
            scale_map = {1: 1.5, 2: 1.3, 3: 1.15}
            char_fmt.setFontPointSize(round(base_pt * scale_map.get(lvl, 1.2), 1))
            char_fmt.setFontWeight(QFont.Bold)
            char_fmt.setForeground(QColor("#15803d" if (self.view_mode == "document" and self.paper_theme == "light") else "#4ade80"))
        else:
            user_data.is_header = False
            user_data.header_level = None
            char_fmt.setFontPointSize(base_pt)
            char_fmt.setFontWeight(QFont.Normal)
            pal = ThemeManager.get_palette()
            body_col = "#0f172a" if (self.view_mode == "document" and self.paper_theme == "light") else pal["text"]
            char_fmt.setForeground(QColor(body_col))

        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.mergeCharFormat(char_fmt)
        self.is_dirty = True
        self.save_btn.setText("Save .docx *")

    def insert_editor_tag(self):
        if not self.is_edit_mode:
            self.set_edit_mode(True)
        cursor = self.browser.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#facc15"))
        fmt.setFontWeight(QFont.Bold)
        cursor.insertText("@editor: play ", fmt)
        normal_fmt = QTextCharFormat()
        pal = ThemeManager.get_palette()
        body_col = "#0f172a" if (self.view_mode == "document" and self.paper_theme == "light") else (
            "#f1f5f9" if self.view_mode == "document" else pal["text"]
        )
        normal_fmt.setForeground(QColor(body_col))
        normal_fmt.setFontWeight(QFont.Normal)
        self.browser.setCurrentCharFormat(normal_fmt)
        self.browser.setFocus()

    def insert_timestamp_template(self):
        if not self.is_edit_mode:
            self.set_edit_mode(True)
        cursor = self.browser.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#38bdf8"))
        fmt.setFontWeight(QFont.Bold)
        fmt.setFontUnderline(True)
        cursor.insertText("00:00 - 00:00", fmt)
        self.browser.setFocus()

    def extract_edited_blocks(self) -> List[Dict[str, Any]]:
        """Extracts all blocks and rich text spans from QTextDocument for saving back to .docx."""
        doc = self.browser.document()
        edited_blocks: List[Dict[str, Any]] = []
        pal = ThemeManager.get_palette()
        default_body_colors = {
            pal["text"].lower(),
            "#ffffff",
            "#f8fafc",
            "#f1f5f9",
            "#e2e8f0",
            "#0f172a",
            "#1e1318",
            "#000000",
        }
        synthetic_highlight_colors = {"#facc15", "#b45309", "#38bdf8", "#0284c7", "#4ade80", "#15803d"}

        block = doc.begin()
        while block.isValid():
            text = block.text().strip()
            user_data = block.userData()
            orig_id = user_data.block_id if isinstance(user_data, ScriptBlockUserData) else None
            is_header = user_data.is_header if isinstance(user_data, ScriptBlockUserData) else False
            header_level = user_data.header_level if isinstance(user_data, ScriptBlockUserData) else None
            images = user_data.images if isinstance(user_data, ScriptBlockUserData) else []

            if not text and not images and orig_id is None:
                block = block.next()
                continue

            norm_curr = re.sub(r'\s+', ' ', text)
            norm_orig = re.sub(r'\s+', ' ', (user_data.orig_text or "").strip()) if isinstance(user_data, ScriptBlockUserData) else ""

            # If text (ignoring HTML whitespace collapse) and formatting were NOT modified, keep original spans & text verbatim!
            if (isinstance(user_data, ScriptBlockUserData) and
                norm_curr == norm_orig and
                not user_data.formatting_modified and
                is_header == bool(user_data.is_header) and
                header_level == user_data.header_level and
                user_data.orig_spans):
                spans = copy.deepcopy(user_data.orig_spans)
                text = user_data.orig_text
            else:
                orig_explicit_colors = set()
                if isinstance(user_data, ScriptBlockUserData):
                    for os_span in user_data.orig_spans:
                        if os_span.get("color"):
                            orig_explicit_colors.add(os_span["color"].lower())

                raw_spans = []
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    if frag.isValid():
                        frag_text = frag.text()
                        if frag_text:
                            fmt = frag.charFormat()
                            bold = fmt.fontWeight() >= QFont.Bold
                            italic = fmt.fontItalic()
                            href = fmt.anchorHref()
                            link = href if (href and not href.startswith("time:") and not href.startswith("#")) else None

                            is_time_anchor = bool(href and href.startswith("time:"))
                            is_editor_token = bool(re.fullmatch(r'@?editor\s*[:;]?', frag_text.strip(), re.IGNORECASE))

                            col_hex = None
                            if fmt.hasProperty(QTextFormat.ForegroundBrush):
                                c_name = fmt.foreground().color().name().lower()
                                if c_name not in default_body_colors:
                                    if c_name in orig_explicit_colors or (c_name not in synthetic_highlight_colors and not link):
                                        col_hex = c_name

                            if is_time_anchor or is_editor_token:
                                bold = False

                            raw_spans.append({
                                "text": frag_text,
                                "color": col_hex,
                                "bold": bool(bold and not is_header),
                                "italic": bool(italic),
                                "size": None,
                                "link": link
                            })
                    it += 1

                # Merge adjacent spans with identical properties
                spans = []
                for sp in raw_spans:
                    if not spans:
                        spans.append(sp)
                    else:
                        prev = spans[-1]
                        if (prev["color"] == sp["color"] and
                            prev["bold"] == sp["bold"] and
                            prev["italic"] == sp["italic"] and
                            prev["size"] == sp["size"] and
                            prev["link"] == sp["link"]):
                            prev["text"] += sp["text"]
                        else:
                            spans.append(sp)

                if not spans and text:
                    spans = [{"text": text, "color": None, "bold": False, "italic": False, "size": None, "link": None}]

            edited_blocks.append({
                "id": orig_id,
                "text": text,
                "spans": spans,
                "images": images,
                "isEditorLine": bool(re.search(r'@?editor', text, re.IGNORECASE)),
                "isHeader": is_header,
                "headerLevel": header_level
            })
            block = block.next()

        return edited_blocks

    def save_edits(self):
        edited_blocks = self.extract_edited_blocks()
        self.is_dirty = False
        self.set_edit_mode(False)
        self.save_docx_requested.emit(edited_blocks)

    def set_hold_reflow(self, hold: bool):
        if hasattr(self, 'browser') and hasattr(self.browser, 'set_reflow_held'):
            self.browser.set_reflow_held(hold)

    def set_file_name(self, name: str):
        if name:
            self.title_label.setText(name)
            self.title_label.setToolTip(name)
        else:
            self.title_label.setText("SCRIPT")
            self.title_label.setToolTip("")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = ThemeManager.get_palette()
        painter.setPen(QColor(pal["border"]))
        painter.setBrush(QColor(pal["surface"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

    def load_document_state(self, doc_state):
        """Loads structured blocks, page geometry, link colors, and HTML from DocumentState."""
        self._blocks = copy.deepcopy(getattr(doc_state, "blocks", []) or [])
        self._link_colors = dict(getattr(doc_state, "link_colors", {}) or {})
        geom = getattr(doc_state, "page_geometry", None)
        if geom:
            self._page_geometry = dict(geom)
        self._raw_html = getattr(doc_state, "raw_html", "")
        self._update_display()

    def set_content(self, html_content: str):
        self._raw_html = html_content
        self._update_display()

    def _attach_block_user_data(self):
        if not self._blocks:
            return
        doc = self.browser.document()
        block = doc.begin()
        idx = 0
        while block.isValid() and idx < len(self._blocks):
            block.setUserData(ScriptBlockUserData(self._blocks[idx]))
            block = block.next()
            idx += 1

    def _find_block_by_anchor(self, anchor_name: str):
        doc = self.browser.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if anchor_name in fmt.anchorNames() or fmt.anchorHref() == f"#{anchor_name}":
                        return block
                it += 1
            block = block.next()
        found = doc.find(anchor_name)
        if not found.isNull():
            return found.block()
        return None

    def _smooth_scroll_to_block(self, target_block, highlight_cursor: Optional[QTextCursor] = None):
        if not target_block or not target_block.isValid():
            return
        doc = self.browser.document()
        if highlight_cursor is not None and not highlight_cursor.isNull():
            clean_cursor = highlight_cursor
        else:
            clean_cursor = QTextCursor(doc)
            clean_cursor.setPosition(target_block.position())
            clean_cursor.setPosition(target_block.position() + max(0, target_block.length() - 1), QTextCursor.KeepAnchor)

        sb = self.browser.verticalScrollBar()
        viewport_h = self.browser.viewport().height()

        if self.view_mode == "document":
            brect = doc.documentLayout().blockBoundingRect(target_block)
            doc_y = brect.y()
            printable_h = self.browser._printable_h
            p_idx = int(doc_y // max(1.0, printable_h))
            in_page_y = doc_y % max(1.0, printable_h)

            scale = self.browser._doc_zoom_pct / 100.0
            scaled_h = self.browser._sheet_h * scale
            gap = self.browser._page_gap * scale
            stride = scaled_h + gap

            canvas_y = gap + p_idx * stride + self.browser._margin_top * scale + in_page_y * scale
            center_y = canvas_y - (viewport_h // 2)
            target_scroll = max(0, min(sb.maximum(), int(center_y)))
        else:
            # Measure block position via a non-selecting cursor so QTextBrowser does NOT teleport
            pos_cursor = QTextCursor(target_block)
            cursor_rect = self.browser.cursorRect(pos_cursor)
            center_y = sb.value() + cursor_rect.center().y() - (viewport_h // 2)
            target_scroll = max(0, min(sb.maximum(), center_y))

        self.smooth_scroller.scroll_to(target_scroll)
        self._pulse_highlight(clean_cursor)

    def scroll_to_sync(self, sync_id: int, meta: str = ""):
        doc = self.browser.document()
        target_block = None
        target_cursor = None

        # 1. Search directly for timestamp or text variants in the document (highest precision)
        if meta:
            candidates = [
                meta.strip(),
                meta.replace("(", "").replace(")", "").strip(),
                re.sub(r'[\s()]+', '', meta),
                re.sub(r'\s*-\s*', ' - ', meta),
                re.sub(r'\s*-\s*', '-', meta),
            ]
            for cand in candidates:
                if not cand:
                    continue
                found = doc.find(cand)
                if not found.isNull():
                    target_cursor = found
                    target_block = found.block()
                    break

        # 2. Fallback to named anchor without instant viewport teleport
        if target_block is None or not target_block.isValid():
            target_block = self._find_block_by_anchor(f"block-{sync_id}")

        # 3. Fallback to block index if within range
        if (target_block is None or not target_block.isValid()) and 0 <= sync_id < doc.blockCount():
            block = doc.findBlockByNumber(sync_id)
            if block.isValid():
                target_block = block

        # 4. If target_block was located but target_cursor wasn't, find timestamp inside block text
        if target_block and target_block.isValid() and (target_cursor is None or target_cursor.isNull()):
            b_txt = target_block.text()
            m = re.search(r'\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?(?:\s*(?:[-–—]|to)\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)?', b_txt)
            if m and (m.group(0).strip()):
                target_cursor = QTextCursor(doc)
                target_cursor.setPosition(target_block.position() + m.start())
                target_cursor.setPosition(target_block.position() + m.end(), QTextCursor.KeepAnchor)

        if target_block and target_block.isValid():
            self._smooth_scroll_to_block(target_block, highlight_cursor=target_cursor)

    def scroll_to_block(self, block_id: int):
        doc = self.browser.document()
        target_block = None
        if 0 <= block_id < doc.blockCount():
            block = doc.findBlockByNumber(block_id)
            if block.isValid():
                target_block = block

        if target_block is None or not target_block.isValid():
            target_block = self._find_block_by_anchor(f"block-{block_id}")
            if (target_block is None or not target_block.isValid()) and f"heading-{block_id}" in self._raw_html:
                target_block = self._find_block_by_anchor(f"heading-{block_id}")

        if target_block and target_block.isValid():
            self._smooth_scroll_to_block(target_block)

    def scroll_to_anchor(self, anchor_name: str):
        clean_anchor = anchor_name.lstrip("#")
        target_block = self._find_block_by_anchor(clean_anchor)
        if target_block and target_block.isValid():
            self._smooth_scroll_to_block(target_block)
            return

        # Fallback: resolve native anchor offset invisibly and animate smoothly
        sb = self.browser.verticalScrollBar()
        old_val = sb.value()
        self.browser.setUpdatesEnabled(False)
        self.browser.scrollToAnchor(clean_anchor)
        target_val = sb.value()
        sb.setValue(old_val)
        self.browser.setUpdatesEnabled(True)
        self.smooth_scroller.scroll_to(target_val)

    def _pulse_highlight(self, cursor: QTextCursor):
        self._highlight_cursor = cursor
        self._pulse_duration_ms = 1400.0
        self._pulse_clock.restart()
        if not hasattr(self, '_pulse_timer') or self._pulse_timer is None:
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setTimerType(Qt.PreciseTimer)
            self._pulse_timer.timeout.connect(self._step_pulse)
        self._pulse_timer.stop()
        self._last_pulse_alpha = -1
        self._apply_pulse_alpha(230)
        interval = max(16, get_display_frame_interval_ms(self))
        self._pulse_timer.start(interval)

    def _apply_pulse_alpha(self, alpha: int):
        if not hasattr(self, '_highlight_cursor') or self._highlight_cursor is None:
            return
        if getattr(self, '_last_pulse_alpha', None) == alpha:
            return
        self._last_pulse_alpha = alpha
        sel = QTextEdit.ExtraSelection()
        is_exact = self._highlight_cursor.hasSelection()
        is_light = (self.view_mode == "document" and self.paper_theme == "light")
        bg_col = QColor(245, 158, 11, alpha) if is_light else QColor(251, 191, 36, alpha)
        sel.format.setBackground(bg_col)
        if is_exact and alpha > 70:
            sel.format.setForeground(QColor("#000000"))
        sel.format.setProperty(QTextFormat.FullWidthSelection, not is_exact)
        sel.cursor = self._highlight_cursor
        self.browser.setExtraSelections([sel])
        if self.browser.viewport():
            self.browser.viewport().update()

    def _step_pulse(self):
        elapsed = self._pulse_clock.nsecsElapsed() / 1_000_000.0
        if elapsed >= self._pulse_duration_ms:
            self._pulse_timer.stop()
            self._last_pulse_alpha = -1
            self.browser.setExtraSelections([])
            if self.browser.viewport():
                self.browser.viewport().update()
            return
        # Hold peak brightness during smooth scroll glide (first 450ms), then quadratic fade
        progress = elapsed / self._pulse_duration_ms
        hold_ratio = 450.0 / self._pulse_duration_ms
        if progress <= hold_ratio:
            alpha = 230
        else:
            fade_t = (progress - hold_ratio) / (1.0 - hold_ratio)
            inv = max(0.0, 1.0 - fade_t)
            alpha = max(0, int(round(230 * (inv * inv))))
        self._apply_pulse_alpha(alpha)

    def _effective_font_pt(self) -> float:
        if self.view_mode == "document":
            return 11.0
        return max(5.0, float(self.current_font_size))

    def _on_zoom_changed(self, size: int):
        self.current_font_size = size
        scroll_pos = self.browser.verticalScrollBar().value()
        self._update_display()
        self.browser.verticalScrollBar().setValue(scroll_pos)

    def _update_display(self):
        if not self._raw_html and not self._blocks:
            return
        self._updating_programmatically = True
        try:
            self._style_header_controls()
            pal = ThemeManager.get_palette()
            if self.view_mode == "document":
                use_light_surface = (self.paper_theme == "light")
                body_color = "#0f172a" if use_light_surface else "#f1f5f9"
                font_family = "'Arial', 'Segoe UI Variable Text', 'Segoe UI', sans-serif"
            else:
                use_light_surface = not pal["is_dark"]
                body_color = pal["text"]
                font_family = "'Segoe UI Variable Text', 'Segoe UI', sans-serif"

            if self._blocks:
                inner_html = build_raw_html(
                    self._blocks,
                    self._link_colors,
                    light_paper=use_light_surface,
                    view_mode=self.view_mode
                )
            else:
                inner_html = self._raw_html

            effective_pt = self._effective_font_pt()
            doc_font = self.browser.font()
            if effective_pt > 0:
                doc_font.setPointSizeF(effective_pt)
            self.browser.setFont(doc_font)
            self.browser.document().setDefaultFont(doc_font)

            b_pal = self.browser.palette()
            b_pal.setColor(QPalette.Text, QColor(body_color))
            self.browser.setPalette(b_pal)

            scroll_pos = self.browser.verticalScrollBar().value()
            body_lh = "1.15" if self.view_mode == "document" else "1.4"
            self.browser.document().setDefaultStyleSheet(f"body, p, span {{ color: {body_color}; }}")
            wrapped = f"""
            <html>
            <head>
            <style>
                body, p, span {{ color: {body_color}; }}
                .active-sync {{ background-color: rgba(251, 191, 36, 0.25); border-left: 4px solid #f59e0b; }}
                .notes-badge {{ background-color: #fbbf24; color: #000; border-radius: 99px; }}
            </style>
            </head>
            <body style='font-family: {font_family}; font-size: {effective_pt}pt; line-height: {body_lh}; color: {body_color};'>
                {inner_html}
            </body>
            </html>
            """
            self.browser.setHtml(wrapped)
            self._attach_block_user_data()
            self.browser.set_page_layout(
                self.view_mode,
                self.paper_theme,
                self._page_geometry,
                self.current_font_size,
                self.current_doc_zoom
            )
            self.browser.verticalScrollBar().setValue(scroll_pos)
            self.browser._update_current_page_from_scroll()
        finally:
            self._updating_programmatically = False

    def _on_anchor_clicked(self, url: QUrl):
        if not self.is_edit_mode:
            self.browser.clearFocus()
        raw_url = url.toString()
        if raw_url.startswith("time:"):
            tc_part = raw_url.replace("time:", "")
            sync_id = None
            if "?sync=" in tc_part:
                parts = tc_part.split("?sync=")
                tc_str = parts[0]
                try:
                    sync_id = int(parts[1])
                except (ValueError, TypeError):
                    sync_id = None
            else:
                tc_str = tc_part
            safe_copy_to_clipboard(tc_str)
            self.copy_feedback_requested.emit(tc_str)
            if sync_id is not None:
                self.timestamp_sync_clicked.emit(tc_str, sync_id)
            self.timestamp_clicked.emit(tc_str)
        elif raw_url.startswith("http://") or raw_url.startswith("https://"):
            import webbrowser
            webbrowser.open(raw_url)
        else:
            self.scroll_to_anchor(raw_url)

    def _show_context_menu(self, pos):
        cursor = self.browser.textCursor()
        selected_text = cursor.selectedText().strip()

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1b1e;
                color: #ffffff;
                border: 1px solid #4c3d4c;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #fb7185;
                color: #ffffff;
                border-radius: 4px;
            }
        """)

        action_copy = menu.addAction("Copy")
        action_edit_toggle = menu.addAction("Exit Edit Mode" if self.is_edit_mode else "Edit Script (Ctrl+E)")
        action_save = menu.addAction("Save to .docx (Ctrl+S)") if self.is_edit_mode else None
        action_note = None
        if selected_text:
            action_note = menu.addAction(f"Add Note: '{selected_text[:22]}...'")

        res = menu.exec(self.browser.mapToGlobal(pos))
        if res == action_copy:
            self.browser.copy()
            self.copy_feedback_requested.emit("Copied!")
        elif res == action_edit_toggle:
            self.toggle_edit_mode()
        elif action_save and res == action_save:
            self.save_edits()
        elif action_note and res == action_note:
            self.add_note_requested.emit(selected_text)
