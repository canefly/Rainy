import os
from PySide6.QtCore import Qt, Signal, QMimeData, QUrl, QTimer, QElapsedTimer
from PySide6.QtGui import QDrag, QPixmap, QCursor, QColor, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QTextBrowser, QTextEdit, QAbstractScrollArea, QMenu, QApplication
)
from qfluentwidgets import Slider
from ..theme import ThemeManager
from .widgets import GpuSmoothScroller, MiddleClickAutoScroller, get_display_frame_interval_ms

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
    """High-performance text browser with fine-grained reflow decoupling during window resize."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_reflow_held = False
        self._held_width = -1

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
                    self.document().setTextWidth(target_w)
                    self.viewport().update()

    def resizeEvent(self, event):
        if self._is_reflow_held and self._held_width > 0:
            # Allow viewport and scrollbars to resize live with the window,
            # but lock document text width to prevent expensive word-wrap loops!
            QAbstractScrollArea.resizeEvent(self, event)
            return
        super().resizeEvent(event)


class ScriptViewer(QFrame):
    """Left Pane: Clean NLE-style Source Script viewer with Aa zoom slider and draggable assets."""
    timestamp_clicked = Signal(str)
    add_note_requested = Signal(str)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("ScriptViewerPane")
        self.current_font_size = 14
        self._raw_html = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header: File Name (or SCRIPT fallback) + Font Zoom Slider (Aa)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        self.title_label = QLabel("SCRIPT")
        self.title_label.setObjectName("PaneTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        zoom_lbl = QLabel("Aa")
        zoom_lbl.setStyleSheet("font-size: 11px; font-weight: 700; margin-right: 4px;")
        header.addWidget(zoom_lbl)

        self.zoom_slider = Slider(Qt.Horizontal, self)
        self.zoom_slider.setRange(11, 24)
        self.zoom_slider.setValue(14)
        self.zoom_slider.setFixedWidth(80)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        header.addWidget(self.zoom_slider)

        layout.addLayout(header)

        # Rich Text Browser
        self.browser = ScriptTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setAutoFillBackground(False)
        if self.browser.viewport():
            self.browser.viewport().setAutoFillBackground(False)
        self.browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
            }
        """)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        self.browser.setContextMenuPolicy(Qt.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._show_context_menu)
        self.smooth_scroller = GpuSmoothScroller(self.browser, self)
        self.middle_scroller = MiddleClickAutoScroller(self.browser, self)
        self._pulse_clock = QElapsedTimer()
        self._pulse_duration_ms = 1050.0
        layout.addWidget(self.browser)

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

    def set_content(self, html_content: str):
        self._raw_html = html_content
        self._update_display()

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

    def _smooth_scroll_to_block(self, target_block):
        if not target_block or not target_block.isValid():
            return
        doc = self.browser.document()
        clean_cursor = QTextCursor(doc)
        clean_cursor.setPosition(target_block.position())
        clean_cursor.setPosition(target_block.position() + max(0, target_block.length() - 1), QTextCursor.KeepAnchor)

        # Measure block position via a non-selecting cursor so QTextBrowser does NOT teleport
        pos_cursor = QTextCursor(target_block)
        cursor_rect = self.browser.cursorRect(pos_cursor)
        sb = self.browser.verticalScrollBar()
        viewport_h = self.browser.viewport().height()
        center_y = sb.value() + cursor_rect.center().y() - (viewport_h // 2)
        target_scroll = max(0, min(sb.maximum(), center_y))

        self.smooth_scroller.scroll_to(target_scroll)
        self._pulse_highlight(clean_cursor)

    def scroll_to_sync(self, sync_id: int, meta: str = ""):
        doc = self.browser.document()
        target_block = None

        # 1. Search directly for timestamp or text in the document (highest precision)
        if meta:
            clean_search = meta.replace("(", "").replace(")", "").strip()
            found = doc.find(clean_search)
            if not found.isNull():
                target_block = found.block()

        # 2. Fallback to named anchor without instant viewport teleport
        if target_block is None or not target_block.isValid():
            target_block = self._find_block_by_anchor(f"block-{sync_id}")

        # 3. Fallback to block index if within range
        if (target_block is None or not target_block.isValid()) and 0 <= sync_id < doc.blockCount():
            block = doc.findBlockByNumber(sync_id)
            if block.isValid():
                target_block = block

        if target_block and target_block.isValid():
            self._smooth_scroll_to_block(target_block)

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
        self._pulse_clock.restart()
        if not hasattr(self, '_pulse_timer') or self._pulse_timer is None:
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setTimerType(Qt.PreciseTimer)
            self._pulse_timer.timeout.connect(self._step_pulse)
        self._pulse_timer.stop()
        self._apply_pulse_alpha(150)
        interval = get_display_frame_interval_ms(self)
        self._pulse_timer.start(interval)

    def _apply_pulse_alpha(self, alpha: int):
        if not hasattr(self, '_highlight_cursor') or self._highlight_cursor is None:
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(251, 191, 36, alpha))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self._highlight_cursor
        self.browser.setExtraSelections([sel])

    def _step_pulse(self):
        elapsed = self._pulse_clock.nsecsElapsed() / 1_000_000.0
        if elapsed >= self._pulse_duration_ms:
            self._pulse_timer.stop()
            self.browser.setExtraSelections([])
            return
        # Hold peak brightness for first 18%, then cubic ease-out fade
        progress = elapsed / self._pulse_duration_ms
        if progress <= 0.18:
            alpha = 150
        else:
            fade_t = (progress - 0.18) / 0.82
            inv = 1.0 - fade_t
            alpha = max(0, int(round(150 * (inv * inv * inv))))
        self._apply_pulse_alpha(alpha)

    def _on_zoom_changed(self, size: int):
        self.current_font_size = size
        scroll_pos = self.browser.verticalScrollBar().value()
        font = self.browser.font()
        font.setPointSize(size)
        self.browser.setFont(font)
        self.browser.document().setDefaultFont(font)
        self._update_display()
        self.browser.verticalScrollBar().setValue(scroll_pos)

    def _update_display(self):
        if not self._raw_html:
            return
        from ..theme import ThemeManager
        pal = ThemeManager.get_palette()
        body_color = pal["text"]
        scroll_pos = self.browser.verticalScrollBar().value()
        wrapped = f"""
        <html>
        <head>
        <style>
            .active-sync {{ background-color: rgba(251, 191, 36, 0.25); border-left: 4px solid #f59e0b; }}
            .notes-badge {{ background-color: #fbbf24; color: #000; border-radius: 99px; }}
        </style>
        </head>
        <body style='font-family: \"Segoe UI Variable Text\", \"Segoe UI\", sans-serif; font-size: {self.current_font_size}pt; line-height: 1.6; color: {body_color};'>
            {self._raw_html}
        </body>
        </html>
        """
        self.browser.setHtml(wrapped)
        self.browser.verticalScrollBar().setValue(scroll_pos)

    def _on_anchor_clicked(self, url: QUrl):
        raw_url = url.toString()
        if raw_url.startswith("time:"):
            tc_str = raw_url.replace("time:", "")
            QApplication.clipboard().setText(tc_str)
            self.copy_feedback_requested.emit(tc_str)
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
        action_note = None
        if selected_text:
            action_note = menu.addAction(f"Add Note: '{selected_text[:22]}...'")

        res = menu.exec(self.browser.mapToGlobal(pos))
        if res == action_copy:
            self.browser.copy()
            self.copy_feedback_requested.emit("Copied!")
        elif action_note and res == action_note:
            self.add_note_requested.emit(selected_text)
