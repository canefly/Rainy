import webbrowser
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QObject, Signal, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPixmap, QCursor, QGuiApplication
)
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget
)
from qfluentwidgets import FluentIcon

from ...core.unfurl_engine import unfurl_engine, LinkMetadata, extract_domain
from ...core.parser import normalize_link
from ..theme import ThemeManager
from .widgets import safe_copy_to_clipboard


def _create_rounded_pixmap(raw_bytes: bytes, w: int, h: int, radius: int = 6) -> Optional[QPixmap]:
    if not raw_bytes:
        return None
    src = QPixmap()
    if not src.loadFromData(raw_bytes):
        return None
    if src.isNull():
        return None

    scaled = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    # Center crop to w x h
    x_off = max(0, (scaled.width() - w) // 2)
    y_off = max(0, (scaled.height() - h) // 2)
    cropped = scaled.copy(x_off, y_off, w, h)

    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out


class LinkEmbedHoverCard(QFrame):
    """
    Floating Fluent Rich Embed Hover Card (Discord / iMessage style link unfurl popover).
    Displays provider badge, file type / restricted status, thumbnail, real title,
    description, and interactive quick actions (Copy Link, Copy Title, Refresh, Open).
    """
    copy_feedback_requested = Signal(str)

    def __init__(self, controller: "LinkEmbedController", parent: Optional[QWidget] = None):
        super().__init__(
            parent,
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )
        self.controller = controller
        self.setObjectName("LinkEmbedHoverCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedWidth(348)

        self.current_url: str = ""
        self.current_meta: Optional[LinkMetadata] = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # 1. Top Badge Row: [PROVIDER] [FILE TYPE / RESTRICTED] [by Author]
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.provider_badge = QLabel("LINK", self)
        self.provider_badge.setFixedHeight(18)
        self.provider_badge.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.provider_badge, 0, Qt.AlignVCenter)

        self.type_badge = QLabel("LINK", self)
        self.type_badge.setFixedHeight(18)
        self.type_badge.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.type_badge, 0, Qt.AlignVCenter)

        self.res_badge = QLabel("", self)
        self.res_badge.setFixedHeight(18)
        self.res_badge.setAlignment(Qt.AlignCenter)
        self.res_badge.hide()
        top_row.addWidget(self.res_badge, 0, Qt.AlignVCenter)

        self.dur_badge = QLabel("", self)
        self.dur_badge.setFixedHeight(18)
        self.dur_badge.setAlignment(Qt.AlignCenter)
        self.dur_badge.hide()
        top_row.addWidget(self.dur_badge, 0, Qt.AlignVCenter)

        self.size_badge = QLabel("", self)
        self.size_badge.setFixedHeight(18)
        self.size_badge.setAlignment(Qt.AlignCenter)
        self.size_badge.hide()
        top_row.addWidget(self.size_badge, 0, Qt.AlignVCenter)

        self.author_lbl = QLabel("", self)
        self.author_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        top_row.addWidget(self.author_lbl, 1, Qt.AlignVCenter)

        root_layout.addLayout(top_row)

        # 2. Content Row: [Thumbnail 88x56] + [Title & Description]
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(10)

        self.thumb_lbl = QLabel(self)
        self.thumb_lbl.setFixedSize(88, 56)
        self.thumb_lbl.setAlignment(Qt.AlignCenter)
        body_row.addWidget(self.thumb_lbl, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)

        self.title_lbl = QLabel("Loading preview...", self)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        text_col.addWidget(self.title_lbl)

        self.desc_lbl = QLabel("", self)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        text_col.addWidget(self.desc_lbl)

        text_col.addStretch(1)
        body_row.addLayout(text_col, 1)
        root_layout.addLayout(body_row)

        # 3. Divider
        self.divider = QFrame(self)
        self.divider.setFixedHeight(1)
        root_layout.addWidget(self.divider)

        # 4. Interactive Action Row: [Copy Link] [Copy Title] [↻ Refresh] [Open ↗]
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)

        self.copy_link_btn = QPushButton("Copy Link", self)
        self.copy_link_btn.setFixedHeight(24)
        self.copy_link_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.copy_link_btn.clicked.connect(self._on_copy_link)
        action_row.addWidget(self.copy_link_btn)

        self.copy_title_btn = QPushButton("Copy Title", self)
        self.copy_title_btn.setFixedHeight(24)
        self.copy_title_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.copy_title_btn.clicked.connect(self._on_copy_title)
        action_row.addWidget(self.copy_title_btn)

        self.refresh_btn = QPushButton("↻ Refresh", self)
        self.refresh_btn.setFixedHeight(24)
        self.refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.refresh_btn.setToolTip("Re-fetch link metadata and thumbnail")
        self.refresh_btn.clicked.connect(self._on_refresh)
        action_row.addWidget(self.refresh_btn)

        action_row.addStretch(1)

        self.open_btn = QPushButton("Open ↗", self)
        self.open_btn.setFixedHeight(24)
        self.open_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.open_btn.clicked.connect(self._on_open_browser)
        action_row.addWidget(self.open_btn)

        root_layout.addLayout(action_row)
        self.apply_theme()

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        accent = pal.get("accent", "#fb7185")
        rgb = pal.get("accent_rgb", "251, 113, 133")
        surface_hover = pal.get("surface_hover", "#27272a")
        border = pal.get("border", "#3f3f46")

        self.provider_badge.setStyleSheet(f"""
            QLabel {{
                background-color: rgba({rgb}, 0.16);
                color: {accent};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 9px;
                font-weight: 700;
                padding: 1px 6px;
                border-radius: 4px;
                border: 1px solid rgba({rgb}, 0.32);
                letter-spacing: 0.4px;
            }}
        """)

        self.author_lbl.setStyleSheet(f"""
            QLabel {{
                color: {muted};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 600;
                background: transparent;
            }}
        """)

        self.title_lbl.setStyleSheet(f"""
            QLabel {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }}
        """)

        self.desc_lbl.setStyleSheet(f"""
            QLabel {{
                color: {muted};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 500;
                background: transparent;
            }}
        """)

        div_col = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"
        self.divider.setStyleSheet(f"background-color: {div_col}; border: none;")

        btn_qss = f"""
            QPushButton {{
                background-color: {'rgba(255, 255, 255, 0.05)' if is_dark else 'rgba(0, 0, 0, 0.04)'};
                color: {text_col};
                border: 1px solid {border};
                border-radius: 5px;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 600;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background-color: {surface_hover};
                border-color: {accent};
                color: {accent};
            }}
        """
        for btn in (self.copy_link_btn, self.copy_title_btn, self.refresh_btn):
            btn.setStyleSheet(btn_qss)

        self.open_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba({rgb}, 0.16);
                color: {accent};
                border: 1px solid rgba({rgb}, 0.35);
                border-radius: 5px;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 700;
                padding: 2px 9px;
            }}
            QPushButton:hover {{
                background-color: {accent};
                color: #0b0f1a;
            }}
        """)
        self._style_type_badge()
        self.update()

    def _style_type_badge(self):
        meta = self.current_meta
        if meta and meta.is_restricted:
            self.type_badge.setText("RESTRICTED")
            self.type_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(245, 158, 11, 0.18);
                    color: #fbbf24;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 1px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba(245, 158, 11, 0.38);
                }
            """)
            return

        ftype = (meta.file_type if meta else "LINK") or "LINK"
        self.type_badge.setText(ftype)
        if ftype == "VIDEO":
            col, bg, brd = "#38bdf8", "rgba(56, 189, 248, 0.16)", "rgba(56, 189, 248, 0.32)"
        elif ftype == "AUDIO":
            col, bg, brd = "#c084fc", "rgba(192, 132, 252, 0.16)", "rgba(192, 132, 252, 0.32)"
        elif ftype == "FOLDER":
            col, bg, brd = "#34d399", "rgba(52, 211, 153, 0.16)", "rgba(52, 211, 153, 0.32)"
        else:
            col, bg, brd = "#94a3b8", "rgba(148, 163, 184, 0.14)", "rgba(148, 163, 184, 0.28)"

        self.type_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 9px;
                font-weight: 700;
                padding: 1px 6px;
                border-radius: 4px;
                border: 1px solid {brd};
            }}
        """)

        # Style Resolution & File Size Chips
        if meta and meta.resolution and not meta.is_restricted:
            self.res_badge.setText(meta.resolution)
            self.res_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(56, 189, 248, 0.12);
                    color: #38bdf8;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 1px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba(56, 189, 248, 0.28);
                }
            """)
            self.res_badge.show()
        else:
            self.res_badge.hide()

        if meta and meta.duration_display and not meta.is_restricted:
            self.dur_badge.setText(meta.duration_display)
            self.dur_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(56, 189, 248, 0.12);
                    color: #38bdf8;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 1px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba(56, 189, 248, 0.28);
                }
            """)
            self.dur_badge.show()
        else:
            self.dur_badge.hide()

        if meta and meta.file_size_display and not meta.is_restricted:
            self.size_badge.setText(meta.file_size_display)
            self.size_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(148, 163, 184, 0.12);
                    color: #cbd5e1;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 1px 6px;
                    border-radius: 4px;
                    border: 1px solid rgba(148, 163, 184, 0.24);
                }
            """)
            self.size_badge.show()
        else:
            self.size_badge.hide()

    def set_loading(self, url: str):
        self.current_url = url
        self.current_meta = None
        domain = extract_domain(url).upper()
        self.provider_badge.setText(domain[:18])
        self.type_badge.setText("FETCHING")
        self.res_badge.hide()
        self.dur_badge.hide()
        self.size_badge.hide()
        self.author_lbl.setText("")
        self.title_lbl.setText("Fetching link preview...")
        self.desc_lbl.setText(url[:68] + ("..." if len(url) > 68 else ""))
        self._set_placeholder_thumbnail(FluentIcon.SYNC)
        self.apply_theme()
        self.adjustSize()

    def set_metadata(self, url: str, meta: LinkMetadata):
        self.current_url = url
        self.current_meta = meta
        prov = (meta.provider or meta.domain or extract_domain(url)).upper()
        self.provider_badge.setText(prov[:20])
        self.author_lbl.setText(f"by {meta.author}" if meta.author else "")

        title = meta.title or meta.domain or url
        if len(title) > 85:
            title = title[:82] + "..."
        self.title_lbl.setText(title)

        desc = meta.description or "No description available."
        if len(desc) > 115:
            desc = desc[:112] + "..."
        self.desc_lbl.setText(desc)

        pix = _create_rounded_pixmap(meta.thumbnail_bytes, 88, 56, radius=6) if meta.thumbnail_bytes else None
        if pix is not None:
            self.thumb_lbl.setText("")
            self.thumb_lbl.setPixmap(pix)
            self.thumb_lbl.setStyleSheet("""
                QLabel {
                    background: transparent;
                    border: 1px solid rgba(255, 255, 255, 0.14);
                    border-radius: 6px;
                }
            """)
        else:
            ft = (meta.file_type or "").upper()
            if meta.is_restricted:
                icon = FluentIcon.INFO
            elif ft == "VIDEO":
                icon = FluentIcon.VIDEO
            elif ft == "AUDIO":
                icon = FluentIcon.MUSIC
            elif ft == "IMAGE":
                icon = FluentIcon.PHOTO
            elif ft == "FOLDER":
                icon = FluentIcon.FOLDER
            else:
                icon = FluentIcon.LINK
            self._set_placeholder_thumbnail(icon)

        self.apply_theme()
        self.adjustSize()

    def _set_placeholder_thumbnail(self, icon: Optional[FluentIcon] = None):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.05)"
        brd = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(0, 0, 0, 0.10)"
        target_icon = icon or FluentIcon.LINK
        col = QColor("#94a3b8") if is_dark else QColor("#64748b")
        pm = target_icon.colored(col, col).icon().pixmap(QSize(24, 24))
        self.thumb_lbl.setText("")
        self.thumb_lbl.setPixmap(pm)
        self.thumb_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                border: 1px solid {brd};
                border-radius: 6px;
            }}
        """)

    def _on_copy_link(self):
        if self.current_url:
            safe_copy_to_clipboard(self.current_url)
            self.copy_feedback_requested.emit("Copied Link!")

    def _on_copy_title(self):
        title_to_copy = (self.current_meta.title if self.current_meta and self.current_meta.title else self.current_url)
        if title_to_copy:
            safe_copy_to_clipboard(title_to_copy)
            self.copy_feedback_requested.emit("Copied Title!")

    def _on_refresh(self):
        if self.current_url:
            url = self.current_url
            self.set_loading(url)
            unfurl_engine.request_unfurl(url, force_refresh=True, priority=True)

    def _on_open_browser(self):
        if self.current_url and self.current_url != "#":
            webbrowser.open(self.current_url)
            self.hide()

    def enterEvent(self, event):
        self.controller.cancel_hide()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.controller.schedule_hide()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        bg_col = QColor(pal.get("surface", "#1e1b1e"))
        bg_col.setAlpha(248 if is_dark else 252)
        border_col = QColor(pal.get("border", "#3f3f46"))
        painter.setBrush(bg_col)
        painter.setPen(border_col)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        painter.end()


class LinkEmbedController(QObject):
    """
    Coordinates the singleton LinkEmbedHoverCard with:
      - Smart Hover Dwell (~250ms) to prevent accidental popups on fast mouse sweeps
      - Interactive Grace Window (~200ms) so users can move their cursor onto the card and click buttons
      - Live reactive updates when UnfurlEngine finishes background fetching
    """
    copy_feedback_requested = Signal(str)
    _instance: Optional["LinkEmbedController"] = None

    @classmethod
    def instance(cls) -> "LinkEmbedController":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.dwell_ms: int = 250
        self.grace_ms: int = 200

        self._pending_url: str = ""
        self._pending_rect: QRect = QRect()
        self._active_norm_url: str = ""

        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._on_dwell_timeout)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_immediately)

        self._card: Optional[LinkEmbedHoverCard] = None
        unfurl_engine.metadata_ready.connect(self._on_metadata_ready)

    @property
    def card(self) -> LinkEmbedHoverCard:
        if self._card is None:
            self._card = LinkEmbedHoverCard(self)
            self._card.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        return self._card

    def schedule_show(self, url: str, anchor_global_rect: QRect, immediate: bool = False):
        if not url or url == "#":
            self.schedule_hide()
            return

        self.cancel_hide()
        norm = normalize_link(url)

        # If already showing this exact link, keep it open
        if not self.card.isHidden() and self._active_norm_url == norm:
            self._pending_url = url
            self._pending_rect = anchor_global_rect
            return

        self._pending_url = url
        self._pending_rect = anchor_global_rect
        if immediate or self.dwell_ms <= 0:
            self._dwell_timer.stop()
            self._on_dwell_timeout()
        else:
            self._dwell_timer.start(self.dwell_ms)

    def schedule_hide(self):
        self._dwell_timer.stop()
        if self._card is not None and not self._card.isHidden():
            self._hide_timer.start(max(10, self.grace_ms))

    def cancel_hide(self):
        self._hide_timer.stop()

    def hide_immediately(self):
        self._dwell_timer.stop()
        self._hide_timer.stop()
        self._active_norm_url = ""
        if self._card is not None:
            self._card.hide()

    def _on_dwell_timeout(self):
        url = self._pending_url
        if not url or url == "#":
            return
        norm = normalize_link(url)
        self._active_norm_url = norm

        cached = unfurl_engine.get_cached(url)
        if cached is not None and not cached.loading:
            self.card.set_metadata(url, cached)
        else:
            self.card.set_loading(url)
            unfurl_engine.request_unfurl(url, priority=True)

        self._position_card(self._pending_rect)
        self.card.show()
        self.card.raise_()

    def _on_metadata_ready(self, url: str, meta: LinkMetadata):
        norm = normalize_link(url)
        card_norm = normalize_link(self.card.current_url) if self.card.current_url else ""
        if (self._active_norm_url and norm == self._active_norm_url) or (card_norm and norm == card_norm):
            self.card.set_metadata(self.card.current_url or url, meta)
            if not self.card.isHidden():
                self._position_card(self._pending_rect)

    def _position_card(self, anchor_rect: QRect):
        card_w = self.card.width()
        card_h = max(128, self.card.sizeHint().height())

        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        # Prefer placing below or above anchor, clamped within screen bounds
        x = anchor_rect.left()
        if x + card_w > avail.right() - 12:
            x = max(avail.left() + 12, avail.right() - card_w - 12)
        if x < avail.left() + 12:
            x = avail.left() + 12

        y_below = anchor_rect.bottom() + 8
        y_above = anchor_rect.top() - card_h - 8
        if y_below + card_h <= avail.bottom() - 12:
            y = y_below
        elif y_above >= avail.top() + 12:
            y = y_above
        else:
            y = max(avail.top() + 12, min(y_below, avail.bottom() - card_h - 12))

        self.card.move(QPoint(x, y))


link_embed_controller = LinkEmbedController.instance()
