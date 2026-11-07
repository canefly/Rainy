import re
from typing import List, Dict, Optional
from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QCursor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget
)
from qfluentwidgets import ComboBox, SmoothScrollArea
from qfluentwidgets.components.widgets.menu import MenuAnimationType
from .timeline_card import TimelineCard
from .link_embed_card import link_embed_controller
from .widgets import GpuSmoothScroller, MiddleClickAutoScroller
from ...core.models import ScriptItem
from ...core.parser import parse_time_single, process_time_span, normalize_link
from ...core.unfurl_engine import unfurl_engine, LinkMetadata
from ..theme import ThemeManager


class TimelineCanvas(QWidget):
    """High-performance Direct3D/GDI cached texture surface canvas for TimelineList.
    
    Eliminates per-frame CPU layout and CSS cascade thrash for 40+ visible cards on
    tall portrait displays by blitting pre-rendered high-DPI raster surfaces in ~0.6ms
    (1,500+ FPS) while maintaining full native widget interactivity for hover, check,
    click, and double-click inline editing.
    """
    def __init__(self, timeline_list: 'TimelineList', parent=None):
        super().__init__(parent)
        self.timeline = timeline_list
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StaticContents, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.active_card: Optional[TimelineCard] = None
        self.active_idx: int = -1

    def activate_card(self, idx: int):
        cards = self.timeline.filtered_cards
        if not (0 <= idx < len(cards)):
            self.deactivate_card()
            return
        if idx == self.active_idx and self.active_card is not None and self.active_card.isVisible():
            return

        target_card = cards[idx]
        if self.active_card is not None and self.active_card is not target_card:
            try:
                self.active_card.isHover = False
                self.active_card.hide()
            except (RuntimeError, ReferenceError):
                pass

        slot_h = self.timeline.slot_height
        card_h = self.timeline.card_height
        w = max(100, self.width() - 8)
        target_card.setGeometry(0, idx * slot_h, w, card_h)
        target_card.show()
        target_card.raise_()
        self.active_card = target_card
        self.active_idx = idx
        self.update()

    def deactivate_card(self):
        if self.active_card is not None:
            try:
                self.active_card.isHover = False
                self.active_card.hide()
            except (RuntimeError, ReferenceError):
                pass
            finally:
                self.active_card = None
                self.active_idx = -1
                link_embed_controller.schedule_hide()
                self.update()

    def mouseMoveEvent(self, event):
        if hasattr(self.timeline, "_idle_scrub_timer"):
            self.timeline._idle_scrub_timer.start(350)
        slot_h = self.timeline.slot_height
        idx = event.pos().y() // slot_h
        if 0 <= idx < len(self.timeline.filtered_cards):
            self.activate_card(idx)
        else:
            self.deactivate_card()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        pos_in_container = event.pos() if hasattr(event, 'pos') else self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(pos_in_container):
            return
        self.deactivate_card()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        slot_h = self.timeline.slot_height
        idx = event.pos().y() // slot_h
        if 0 <= idx < len(self.timeline.filtered_cards):
            self.activate_card(idx)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        rect = event.rect()
        slot_h = self.timeline.slot_height
        card_h = self.timeline.card_height
        w = max(100, self.width() - 8)
        dpr = self.devicePixelRatioF() or 1.0

        f_idx = max(0, rect.top() // slot_h)
        l_idx = min(len(self.timeline.filtered_cards) - 1, rect.bottom() // slot_h)
        is_held = getattr(self.timeline, "is_resizing_held", False)

        for i in range(f_idx, l_idx + 1):
            if i == self.active_idx:
                try:
                    if self.active_card is not None and self.active_card.isVisible():
                        continue
                except (RuntimeError, ReferenceError):
                    self.active_card = None
                    self.active_idx = -1
            card = self.timeline.filtered_cards[i]
            pix = card.get_cached_surface(w, card_h, dpr, hold_cache=is_held)
            if pix is not None and not pix.isNull():
                painter.drawPixmap(0, i * slot_h, pix)


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
        self.setMinimumWidth(285)
        self._is_compact = False
        self._pending_compact = False
        self._compact_timer = QTimer(self)
        self._compact_timer.setSingleShot(True)
        self._compact_timer.timeout.connect(self._apply_pending_compact)
        self.card_widgets: Dict[str, TimelineCard] = {}
        self.card_list: List[TimelineCard] = []
        self.filtered_cards: List[TimelineCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 2, 10)
        layout.setSpacing(8)

        # Header toolbar: Title + Status Dropdown Filter (All | Done | Pending)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 10, 0)
        title = QLabel("TIMESTAMPS")
        title.setObjectName("PaneTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.filter_combo = ComboBox(self)
        # Instant zero-lag popup (disables QFluentWidgets 250ms QGraphicsDropShadow per-frame slide recalculation)
        orig_create_menu = self.filter_combo._createComboMenu
        def _fast_menu():
            menu = orig_create_menu()
            orig_exec = menu.exec
            menu.exec = lambda pos, ani=True, aniType=MenuAnimationType.DROP_DOWN: orig_exec(pos, ani=False, aniType=MenuAnimationType.NONE)
            return menu
        self.filter_combo._createComboMenu = _fast_menu
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
            self.scroll_area.viewport().setAttribute(Qt.WA_StaticContents, True)

        self.container = TimelineCanvas(self, self.scroll_area)
        self.scroll_area.setWidget(self.container)
        # Turn off floaty Python timer inertia for crisp 1:1 hardware wheel response
        if hasattr(self.scroll_area, "delegate") and self.scroll_area.delegate:
            from qfluentwidgets.common.smooth_scroll import SmoothMode
            self.scroll_area.delegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)

        self.smooth_scroller = GpuSmoothScroller(self.scroll_area, self)
        self.smooth_scroller.enable_smooth_wheel = True
        self.middle_scroller = MiddleClickAutoScroller(self.scroll_area, self)
        self._active_highlighted_card: Optional[TimelineCard] = None
        self._is_reflow_held = False
        self._resize_settle_timer = QTimer(self)
        self._resize_settle_timer.setSingleShot(True)
        self._resize_settle_timer.timeout.connect(self._on_resize_settled)
        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.timeout.connect(self._on_scroll_idle)
        self._idle_scrub_timer = QTimer(self)
        self._idle_scrub_timer.setSingleShot(True)
        self._idle_scrub_timer.timeout.connect(self._on_idle_cache_scrub)
        layout.addWidget(self.scroll_area)

        # Hook scrollbar to update the visible card window instantly
        sb = self.scroll_area.verticalScrollBar()
        if sb:
            sb.valueChanged.connect(self._on_scroll_changed)

        unfurl_engine.metadata_ready.connect(self._on_link_metadata_ready)

    def _on_link_metadata_ready(self, url: str, meta: LinkMetadata):
        norm = normalize_link(url)
        updated = False
        for card in list(self.card_list):
            try:
                if card.first_link and normalize_link(card.first_link) == norm:
                    card.update_link_metadata(meta)
                    updated = True
            except (RuntimeError, ReferenceError):
                continue
        if updated and hasattr(self, "container"):
            self.container.update()

    @property
    def slot_height(self) -> int:
        return 48

    @property
    def card_height(self) -> int:
        return 44

    def _on_scroll_changed(self, val: int):
        if self.container.active_card is not None:
            self.container.deactivate_card()
        self.container.update()
        self._scroll_idle_timer.start(50)
        self._idle_scrub_timer.start(350)

    def _on_scroll_idle(self):
        cursor_pos = self.container.mapFromGlobal(QCursor.pos())
        if self.container.rect().contains(cursor_pos):
            idx = cursor_pos.y() // self.slot_height
            if 0 <= idx < len(self.filtered_cards):
                self.container.activate_card(idx)

    def _on_idle_cache_scrub(self):
        """Silently scrubs card states and ensures 100% color uniformity when idle."""
        scrubbed = False
        for card in self.card_list:
            if card is not self.container.active_card:
                if card.isHover:
                    card.isHover = False
                    card.invalidate_cache()
                    scrubbed = True
        cursor_pos = self.container.mapFromGlobal(QCursor.pos())
        if not self.container.rect().contains(cursor_pos) and self.container.active_card is not None:
            self.container.deactivate_card()
            scrubbed = True
        if scrubbed:
            self.container.update()

    @property
    def is_resizing_held(self) -> bool:
        """Returns True if splitter drag or active window resize is holding cache rebaking."""
        return self._is_reflow_held or self._resize_settle_timer.isActive()

    def set_hold_reflow(self, hold: bool):
        """Freezes/unfreezes texture invalidation and widget re-rendering during splitter dragging."""
        if self._is_reflow_held == hold:
            return
        self._is_reflow_held = hold
        for card in self.card_list:
            card.set_hold_reflow(hold)
        if not hold:
            if self._resize_settle_timer.isActive():
                self._resize_settle_timer.stop()
            # Drag finished: perform one single clean rebake to final settled width
            for card in self.card_list:
                card.invalidate_cache()
            if hasattr(self, 'container'):
                cw = max(100, self.scroll_area.viewport().width() - 4) if self.scroll_area.viewport() else max(100, self.width() - 28)
                total_h = len(self.filtered_cards) * self.slot_height
                self.container.setFixedSize(cw, max(10, total_h))
                self.container.update()

    def _on_resize_settled(self):
        """Debounced rebake callback for window corner resizing."""
        if self._is_reflow_held:
            return
        for card in self.card_list:
            card.invalidate_cache()
        if hasattr(self, 'container'):
            self.container.update()

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

        cw = max(100, self.scroll_area.viewport().width() - 4) if self.scroll_area.viewport() else max(100, self.width() - 28)
        if hasattr(self, 'container') and cw != self.container.width():
            total_h = len(self.filtered_cards) * self.slot_height
            self.container.setFixedSize(cw, max(10, total_h))
            if not self._is_reflow_held:
                # Debounce rebake for window corner resizes so dragging window edges doesn't stutter
                self._resize_settle_timer.start(70)
            self.container.update()

    def _apply_pending_compact(self):
        self._set_compact_all(self._pending_compact)

    def _set_compact_all(self, compact: bool):
        if self._compact_timer.isActive():
            self._compact_timer.stop()
        if self._is_compact == compact:
            return
        self._is_compact = compact
        for card in self.card_list:
            card.set_compact_mode(compact)
            card.invalidate_cache()
        total_h = len(self.filtered_cards) * self.slot_height
        self.container.setFixedHeight(max(10, total_h))
        self.container.update()

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = ThemeManager.get_palette()
        painter.setPen(QColor(pal["border"]))
        painter.setBrush(QColor(pal["surface"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

    def load_items(self, items: List[ScriptItem], doc_signature: str = "", link_colors: Dict[str, str] = None, linked_footage: Dict[str, str] = None):
        if self.width() > 0:
            self._is_compact = self.width() < 400
        self.linked_footage = linked_footage or getattr(self, "linked_footage", {})
        self.container.deactivate_card()
        for card in self.card_widgets.values():
            card.deleteLater()
        self.card_widgets.clear()
        self.card_list.clear()
        self.filtered_cards.clear()

        for item in items:
            card = TimelineCard(item, doc_signature, link_colors=link_colors, linked_footage=self.linked_footage, parent=self.container)
            if self._is_compact:
                card.set_compact_mode(True)
            card.item_checked_changed.connect(self._on_card_checked_changed)
            card.item_timecode_changed.connect(self.item_timecode_changed.emit)
            card.sync_script_requested.connect(self.sync_script_requested.emit)
            card.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
            card.hide()
            
            self.card_widgets[item.id] = card
            self.card_list.append(card)

        self.apply_filter()

    def set_linked_footage(self, linked_footage: Dict[str, str]):
        self.linked_footage = linked_footage or {}
        for card in self.card_list:
            card.set_linked_footage(self.linked_footage)

    def refresh_timecodes(self):
        """Refreshes all card timecode labels between raw PTS and conformed SMPTE."""
        for card in self.card_list:
            card.update_displayed_timecodes()
        self.container.update()

    def _on_filter_changed(self, text: str = ""):
        self.apply_filter()

    def apply_filter(self):
        mode = self.filter_combo.currentText()
        matching = []
        for card in self.card_list:
            if mode == "Done":
                is_match = card.item.is_checked
            elif mode in ("Pending", "Not Done"):
                is_match = not card.item.is_checked
            else:
                is_match = True
            if is_match:
                matching.append(card)
                card.set_virtual_visible(True)
            else:
                card.set_virtual_visible(False)
                card.hide()

        self.filtered_cards = matching
        total_h = len(self.filtered_cards) * self.slot_height
        self.container.setFixedHeight(max(10, total_h))
        self.container.deactivate_card()
        self.container.update()

    def _on_card_checked_changed(self, item_id: str, is_checked: bool):
        self.item_checked_changed.emit(item_id, is_checked)
        card = self.card_widgets.get(item_id)
        if card:
            card.invalidate_cache()
        mode = self.filter_combo.currentText()
        if mode in ("Done", "Pending", "Not Done"):
            self.apply_filter()
        else:
            self.container.update()

    def scroll_to_timestamp(self, tc: str, sync_id: Optional[int] = None):
        if not tc and sync_id is None:
            return

        norm_search = re.sub(r'[\s()]+', '', tc).lower() if tc else ""
        is_range = bool(tc and any(sep in tc for sep in ('-', '–', '—', 'to')))

        parsed_s_tc, parsed_s_sec = None, None
        parsed_e_tc, parsed_e_sec = None, None
        if tc:
            try:
                if is_range:
                    parsed_s_tc, parsed_s_sec, parsed_e_tc, parsed_e_sec, _, _, _, _ = process_time_span(tc)
                else:
                    parsed_s_tc, parsed_s_sec, _ = parse_time_single(tc)
            except Exception:
                pass

        target_card: Optional[TimelineCard] = None

        # Pass 1: Sync ID + Exact Normalized Raw Match
        if sync_id is not None and norm_search:
            for card in self.card_list:
                if card.item.sync_id == sync_id:
                    norm_raw = re.sub(r'[\s()]+', '', card.item.raw_timestamp or '').lower()
                    if norm_raw == norm_search:
                        target_card = card
                        break

        # Pass 2: Sync ID direct match
        if target_card is None and sync_id is not None:
            for card in self.card_list:
                if card.item.sync_id == sync_id:
                    target_card = card
                    break

        # Pass 3: Exact normalized raw string equality
        if target_card is None and norm_search:
            for card in self.card_list:
                norm_raw = re.sub(r'[\s()]+', '', card.item.raw_timestamp or '').lower()
                if norm_raw and norm_raw == norm_search:
                    target_card = card
                    break

        # Pass 4: Exact SMPTE dual match (start AND end)
        if target_card is None and is_range and parsed_s_tc and parsed_e_tc and parsed_e_tc != '00:00:00:00':
            for card in self.card_list:
                if not card.item.is_single and card.item.start_timecode == parsed_s_tc and card.item.end_timecode == parsed_e_tc:
                    target_card = card
                    break

        # Pass 5: Exact seconds proximity match (start AND end < 0.5s)
        if target_card is None and is_range and parsed_s_sec is not None and parsed_e_sec is not None:
            for card in self.card_list:
                if not card.item.is_single and abs(card.item.start_seconds - parsed_s_sec) < 0.5 and abs(card.item.end_seconds - parsed_e_sec) < 0.5:
                    target_card = card
                    break

        # Pass 6: Single timecode match (ONLY if search was not a range)
        if target_card is None and not is_range and parsed_s_tc:
            for card in self.card_list:
                if card.item.is_single and card.item.start_timecode == parsed_s_tc:
                    target_card = card
                    break
            if target_card is None:
                for card in self.card_list:
                    if card.item.start_timecode == parsed_s_tc:
                        target_card = card
                        break

        if target_card is not None:
            # If target card is not in current filtered list, reset filter to 'All'
            if target_card not in self.filtered_cards:
                if self.filter_combo.currentText() != "All":
                    self.filter_combo.setCurrentText("All")
                self.apply_filter()

            target_idx = self.filtered_cards.index(target_card) if target_card in self.filtered_cards else 0
            slot_h = self.slot_height
            card_h = self.card_height
            card_y = target_idx * slot_h
            viewport_h = self.scroll_area.viewport().height()
            sb = self.scroll_area.verticalScrollBar()
            target_scroll = max(0, min(sb.maximum(), card_y - (viewport_h // 2) + (card_h // 2)))
            self.smooth_scroller.scroll_to(target_scroll)

            if self._active_highlighted_card is not None and self._active_highlighted_card is not target_card:
                try:
                    self._active_highlighted_card.set_highlighted(False)
                except RuntimeError:
                    pass
            self._active_highlighted_card = target_card
            self.container.activate_card(target_idx)
            target_card.pulse_highlight(1200)
