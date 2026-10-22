from typing import List, Dict, Optional
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget
)
from qfluentwidgets import ComboBox, SmoothScrollArea
from .timeline_card import TimelineCard
from .widgets import GpuSmoothScroller, MiddleClickAutoScroller
from ...core.models import ScriptItem
from ..theme import ThemeManager

class TimelineList(QFrame):
    """Right Pane: Clean NLE-style A-Roll Timeline checklist container."""
    item_checked_changed = Signal(str, bool)
    item_timecode_changed = Signal(str, str)
    sync_script_requested = Signal(int, str)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("TimelineListPane")
        self.setMinimumWidth(300)
        self._is_compact = False
        self._pending_compact = False
        self._compact_timer = QTimer(self)
        self._compact_timer.setSingleShot(True)
        self._compact_timer.timeout.connect(self._apply_pending_compact)
        self.card_widgets: Dict[str, TimelineCard] = {}
        self.card_list: List[TimelineCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header toolbar: Title + Status Dropdown Filter (All | Done | Pending)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        title = QLabel("TIMESTAMPS")
        title.setObjectName("PaneTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.filter_combo = ComboBox(self)
        self.filter_combo.addItems(["All", "Done", "Pending"])
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.setFixedWidth(112)
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        header.addWidget(self.filter_combo)

        layout.addLayout(header)

        # Smooth Kinetic Scroll Area for cards
        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setAutoFillBackground(False)
        self.scroll_area.setStyleSheet("QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { border: none; background: transparent; }")
        if self.scroll_area.viewport():
            self.scroll_area.viewport().setAutoFillBackground(False)
            self.scroll_area.viewport().setStyleSheet("background: transparent;")

        self.container = QWidget(self.scroll_area)
        self.container.setAutoFillBackground(False)
        self.container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.container)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(5)
        self.cards_layout.addStretch(1)

        self.scroll_area.setWidget(self.container)
        self.smooth_scroller = GpuSmoothScroller(self.scroll_area, self)
        self.middle_scroller = MiddleClickAutoScroller(self.scroll_area, self)
        self._active_highlighted_card: Optional[TimelineCard] = None
        layout.addWidget(self.scroll_area)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Hysteresis buffer to eliminate stutter/jitter near the responsive threshold
        w = self.width()
        if not self._is_compact and w < 390:
            self._pending_compact = True
            if not self._compact_timer.isActive():
                self._compact_timer.start(16)
        elif self._is_compact and w >= 415:
            self._pending_compact = False
            if not self._compact_timer.isActive():
                self._compact_timer.start(16)

    def _apply_pending_compact(self):
        self._set_compact_all(self._pending_compact)

    def _set_compact_all(self, compact: bool):
        if self._compact_timer.isActive():
            self._compact_timer.stop()
        if self._is_compact == compact:
            return
        self._is_compact = compact
        self.container.setUpdatesEnabled(False)
        for card in self.card_list:
            card.set_compact_mode(compact)
        self.container.setUpdatesEnabled(True)
        self.container.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = ThemeManager.get_palette()
        painter.setPen(QColor(pal["border"]))
        painter.setBrush(QColor(pal["surface"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

    def load_items(self, items: List[ScriptItem], doc_signature: str = "", link_colors: Dict[str, str] = None):
        if self.width() > 0:
            self._is_compact = self.width() < 400
        for card in self.card_widgets.values():
            card.deleteLater()
        self.card_widgets.clear()
        self.card_list.clear()

        # Clean all items from cards_layout to avoid stale indices or ghost stretches
        while self.cards_layout.count() > 0:
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for item in items:
            card = TimelineCard(item, doc_signature, link_colors=link_colors, parent=self.container)
            if self._is_compact:
                card.set_compact_mode(True)
            card.item_checked_changed.connect(self._on_card_checked_changed)
            card.item_timecode_changed.connect(self.item_timecode_changed.emit)
            card.sync_script_requested.connect(self.sync_script_requested.emit)
            card.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
            
            self.cards_layout.addWidget(card)
            self.card_widgets[item.id] = card
            self.card_list.append(card)

        self.cards_layout.addStretch(1)
        self.apply_filter()

    def _on_filter_changed(self, text: str = ""):
        self.apply_filter()

    def apply_filter(self):
        mode = self.filter_combo.currentText()
        self.container.setUpdatesEnabled(False)
        for card in self.card_list:
            if mode == "Done":
                card.setVisible(card.item.is_checked)
            elif mode in ("Pending", "Not Done"):
                card.setVisible(not card.item.is_checked)
            else:
                card.show()
        self.container.setUpdatesEnabled(True)
        self.container.update()

    def _on_card_checked_changed(self, item_id: str, is_checked: bool):
        self.item_checked_changed.emit(item_id, is_checked)
        mode = self.filter_combo.currentText()
        if mode in ("Done", "Pending", "Not Done"):
            card = self.card_widgets.get(item_id)
            if card:
                card.setVisible(is_checked if mode == "Done" else not is_checked)

    def scroll_to_timestamp(self, tc: str):
        cleaned_search = tc.replace('(', '').replace(')', '').strip()
        for item_id, card in self.card_widgets.items():
            if (cleaned_search in card.item.raw_timestamp or 
                card.item.start_timecode.startswith(cleaned_search) or 
                cleaned_search.startswith(card.item.start_timecode) or
                (card.item.raw_timestamp and card.item.raw_timestamp in cleaned_search)):
                
                if card.isHidden():
                    card.show()
                if self.cards_layout:
                    self.cards_layout.activate()

                # Smoothly glide card to center of the scroll area viewport at monitor refresh rate
                card_y = card.y()
                viewport_h = self.scroll_area.viewport().height()
                sb = self.scroll_area.verticalScrollBar()
                target_scroll = max(0, min(sb.maximum(), card_y - (viewport_h // 2) + (card.height() // 2)))
                self.smooth_scroller.scroll_to(target_scroll)

                if self._active_highlighted_card is not None and self._active_highlighted_card is not card:
                    try:
                        self._active_highlighted_card.set_highlighted(False)
                    except RuntimeError:
                        pass
                self._active_highlighted_card = card
                card.pulse_highlight(1200)
                break
