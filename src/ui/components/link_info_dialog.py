import os
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QColor, QCursor, QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QApplication, QSizePolicy, QDialog
)
from qfluentwidgets import (
    MessageBoxBase, PushButton, PrimaryPushButton, ToolButton, FluentIcon, LineEdit
)

from ...core.unfurl_engine import unfurl_engine, LinkMetadata
from ...core.parser import normalize_link
from .widgets import safe_copy_to_clipboard
from ..theme import ThemeManager


class LinkMetadataDialog(MessageBoxBase):
    """Fluent modal inspector displaying full extracted metadata, resolution,
    exact byte count, duration, thumbnail, and persistent SQLite cache status."""

    def __init__(self, url: str, parent: Optional[QWidget] = None, local_path: Optional[str] = None):
        self._fallback_parent = None
        target_parent = parent or QApplication.activeWindow()
        if target_parent is None:
            # Fallback widget to satisfy MaskDialogBase parent requirements in headless tests
            self._fallback_parent = QWidget()
            self._fallback_parent.resize(480, 640)
            target_parent = self._fallback_parent
        super().__init__(parent=target_parent)

        self.url = url
        self.norm_url = normalize_link(url)
        self.local_path = local_path

        # Dialog sizing
        parent_w = target_parent.width() if target_parent else 440
        self.widget.setFixedWidth(min(420, max(330, parent_w - 24)))

        self.viewLayout.setSpacing(10)
        self.viewLayout.setContentsMargins(20, 20, 20, 16)

        # 1. Header Row: [Title] + [Status Pill]
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.header_title = QLabel("Link Metadata Inspector", self.widget)
        self.header_title.setObjectName("DialogHeaderTitle")
        header_layout.addWidget(self.header_title, 1, Qt.AlignVCenter)

        self.status_badge = QLabel("FETCHING", self.widget)
        self.status_badge.setFixedHeight(20)
        self.status_badge.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.status_badge, 0, Qt.AlignVCenter)

        self.viewLayout.addLayout(header_layout)

        # 2. Main Metadata Details Container Card
        self.details_card = QFrame(self.widget)
        self.details_card.setObjectName("MetadataCard")
        card_layout = QVBoxLayout(self.details_card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        # Thumbnail + Title Row
        top_meta = QHBoxLayout()
        top_meta.setContentsMargins(0, 0, 0, 0)
        top_meta.setSpacing(10)

        self.thumb_lbl = QLabel(self.details_card)
        self.thumb_lbl.setFixedSize(96, 60)
        self.thumb_lbl.setAlignment(Qt.AlignCenter)
        top_meta.addWidget(self.thumb_lbl, 0, Qt.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.title_lbl = QLabel(self.details_card)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setObjectName("MetadataTitle")
        text_layout.addWidget(self.title_lbl)

        self.provider_lbl = QLabel(self.details_card)
        self.provider_lbl.setObjectName("MetadataMuted")
        text_layout.addWidget(self.provider_lbl)

        top_meta.addLayout(text_layout, 1)
        card_layout.addLayout(top_meta)

        # Divider
        self.div = QFrame(self.details_card)
        self.div.setFrameShape(QFrame.HLine)
        self.div.setFixedHeight(1)
        card_layout.addWidget(self.div)

        # Key-Value Grid
        self.grid_container = QWidget(self.details_card)
        grid = QVBoxLayout(self.grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.res_row = self._create_row("Resolution:", grid)
        self.size_row = self._create_row("Exact Size:", grid)
        self.dur_row = self._create_row("Duration:", grid)
        self.fps_row = self._create_row("Framerate:", grid)
        self.offset_row = self._create_row("PTS / Offset:", grid)
        self.local_row = self._create_row("Local Footage:", grid)
        self.cache_row = self._create_row("Database Cache:", grid)

        card_layout.addWidget(self.grid_container)
        self.viewLayout.addWidget(self.details_card)

        # 3. Source URL Readout with Copy Button
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(0, 0, 0, 0)
        url_layout.setSpacing(6)

        self.url_edit = LineEdit(self.widget)
        self.url_edit.setText(self.url)
        self.url_edit.setReadOnly(True)
        self.url_edit.setToolTip(self.url)
        url_layout.addWidget(self.url_edit, 1, Qt.AlignVCenter)

        self.copy_url_btn = ToolButton(FluentIcon.COPY, self.widget)
        self.copy_url_btn.setFixedSize(32, 32)
        self.copy_url_btn.setToolTip("Copy full URL")
        self.copy_url_btn.clicked.connect(self._on_copy_url)
        url_layout.addWidget(self.copy_url_btn, 0, Qt.AlignVCenter)

        self.viewLayout.addLayout(url_layout)

        # 4. Action Buttons in Button Group
        self.hideCancelButton()
        self.yesButton.setText("Close")

        self.refresh_btn = PushButton("↻ Re-fetch", self.buttonGroup)
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setToolTip("Force re-fetch from web")
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.buttonLayout.insertWidget(0, self.refresh_btn, 1, Qt.AlignVCenter)

        self.open_btn = PushButton("Open ↗", self.buttonGroup)
        self.open_btn.setFixedHeight(32)
        self.open_btn.setToolTip("Open URL in browser")
        self.open_btn.clicked.connect(lambda: webbrowser.open(self.url))
        self.buttonLayout.insertWidget(1, self.open_btn, 1, Qt.AlignVCenter)

        self.setClosableOnMaskClicked(True)

        # Connect unfurl engine signals
        unfurl_engine.metadata_ready.connect(self._on_metadata_ready)

        # Load initial metadata
        cached = unfurl_engine.get_cached(self.url)
        self.update_content(cached)
        self.apply_theme()

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

    def _create_row(self, label_text: str, parent_layout: QVBoxLayout) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        lbl = QLabel(label_text, self.details_card)
        lbl.setFixedWidth(100)
        lbl.setObjectName("MetadataLabel")
        row.addWidget(lbl, 0, Qt.AlignVCenter)

        val = QLabel("—", self.details_card)
        val.setObjectName("MetadataValue")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(val, 1, Qt.AlignVCenter)

        parent_layout.addLayout(row)
        return val

    def _on_metadata_ready(self, url: str, meta: LinkMetadata):
        if normalize_link(url) == self.norm_url:
            self.update_content(meta)

    def update_content(self, meta: Optional[LinkMetadata]):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)

        if not meta or meta.loading:
            self.status_badge.setText("FETCHING")
            self.status_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(56, 189, 248, 0.16);
                    color: #38bdf8;
                    border: 1px solid rgba(56, 189, 248, 0.32);
                    border-radius: 4px;
                    padding: 1px 8px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)
            self.title_lbl.setText("Fetching metadata from source...")
            self.provider_lbl.setText(self.url.split('/')[2] if '/' in self.url else "Link")
            self.res_row.setText("Detecting...")
            self.size_row.setText("Detecting...")
            self.dur_row.setText("Detecting...")
            if hasattr(self, "fps_row"):
                self.fps_row.setText("Detecting...")
            if hasattr(self, "offset_row"):
                self.offset_row.setText("Detecting...")
            self.cache_row.setText("Querying cache...")
            self._set_placeholder_thumb(FluentIcon.SYNC)
            return

        # Status Badge
        if meta.is_restricted:
            self.status_badge.setText("RESTRICTED")
            self.status_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(245, 158, 11, 0.18);
                    color: #fbbf24;
                    border: 1px solid rgba(245, 158, 11, 0.38);
                    border-radius: 4px;
                    padding: 1px 8px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)
        elif meta.title and not meta.is_fallback:
            self.status_badge.setText("FETCHED")
            self.status_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(16, 185, 129, 0.16);
                    color: #34d399;
                    border: 1px solid rgba(16, 185, 129, 0.36);
                    border-radius: 4px;
                    padding: 1px 8px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)
        else:
            self.status_badge.setText("RAW LINK")
            self.status_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(148, 163, 184, 0.14);
                    color: #94a3b8;
                    border: 1px solid rgba(148, 163, 184, 0.28);
                    border-radius: 4px;
                    padding: 1px 8px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)

        # Title & Provider
        title_text = meta.title or meta.domain or self.url
        self.title_lbl.setText(title_text)
        prov_text = f"{meta.provider or meta.domain or 'Web Link'} • {meta.file_type or 'LINK'}"
        if meta.author:
            prov_text += f" (by {meta.author})"
        self.provider_lbl.setText(prov_text)

        # Resolution
        if meta.resolution:
            self.res_row.setText(f"{meta.resolution}")
        elif meta.file_type == "AUDIO":
            self.res_row.setText("N/A (Audio Track)")
        elif meta.file_type == "FOLDER":
            self.res_row.setText("N/A (Folder)")
        else:
            self.res_row.setText("N/A")

        # File Size (exact bytes & display string)
        if meta.file_size_display and meta.file_size_bytes:
            self.size_row.setText(f"{meta.file_size_display}  ({meta.file_size_bytes:,} bytes)")
        elif meta.file_size_display:
            self.size_row.setText(f"{meta.file_size_display}")
        elif meta.file_size_bytes:
            self.size_row.setText(f"{meta.file_size_bytes:,} bytes")
        else:
            self.size_row.setText("N/A")

        # Duration
        if meta.duration_display:
            dur_info = meta.duration_display
            if meta.duration_seconds:
                dur_info += f"  ({meta.duration_seconds:.1f}s)"
            self.dur_row.setText(dur_info)
        elif meta.duration_seconds:
            self.dur_row.setText(f"{meta.duration_seconds:.1f}s")
        elif meta.file_type == "IMAGE":
            self.dur_row.setText("N/A (Image Asset)")
        elif meta.file_type == "DOC":
            self.dur_row.setText("N/A (Document)")
        elif meta.file_type == "FOLDER":
            self.dur_row.setText("N/A (Folder)")
        else:
            self.dur_row.setText("N/A")

        # Database Cache
        self.cache_row.setText("Persistent SQLite (Instant 0ms)")

        # Local Footage Details
        if hasattr(self, "local_row") and self.local_row:
            if self.local_path and os.path.isfile(self.local_path):
                self.local_row.setText(os.path.basename(self.local_path))
                self.local_row.setToolTip(self.local_path)
                from ...core.media_probe import probe_media_file
                from ...core.unfurl_engine import format_bytes_display
                probed = probe_media_file(self.local_path, use_cache=True)
                if probed:
                    if probed.resolution:
                        self.res_row.setText(f"{probed.resolution}  ({probed.width}×{probed.height})")
                    if probed.file_size > 0:
                        self.size_row.setText(f"{format_bytes_display(probed.file_size)}  ({probed.file_size:,} bytes)")
                    if probed.duration_seconds > 0:
                        self.dur_row.setText(f"{probed.duration_display}  ({probed.duration_seconds:.1f}s)")
                    if probed.frame_rate > 0:
                        if probed.is_vfr:
                            self.fps_row.setText(f"{probed.frame_rate:.2f} FPS (VFR — Drift Risk)")
                            self.fps_row.setStyleSheet("color: #fbbf24; font-weight: 600;")
                        else:
                            self.fps_row.setText(f"{probed.frame_rate:.3f} FPS (CFR — Constant Frame Rate)")
                            self.fps_row.setStyleSheet("color: #38bdf8; font-weight: 600;")
                    else:
                        self.fps_row.setText("—")
                        self.fps_row.setStyleSheet("")
                    if abs(probed.start_time) > 0.001:
                        self.offset_row.setText(f"+{probed.start_time:.3f}s (Compensated in Premiere)")
                    else:
                        self.offset_row.setText("0.000s (Aligned)")
                else:
                    self.fps_row.setText("—")
                    self.fps_row.setStyleSheet("")
                    self.offset_row.setText("—")
                self.status_badge.setText("LINKED")
                self.status_badge.setStyleSheet("""
                    QLabel {
                        background-color: rgba(16, 185, 129, 0.16);
                        color: #34d399;
                        border: 1px solid rgba(16, 185, 129, 0.36);
                        border-radius: 4px;
                        padding: 1px 8px;
                        font-size: 10px;
                        font-weight: 700;
                    }
                """)
            else:
                self.local_row.setText("Not Linked (Remote)")
                self.fps_row.setText("—")
                self.fps_row.setStyleSheet("")
                self.offset_row.setText("—")

        # Thumbnail
        if meta.thumbnail_bytes:
            img = QImage.fromData(meta.thumbnail_bytes)
            if not img.isNull():
                scaled = img.scaled(96, 60, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.thumb_lbl.setText("")
                self.thumb_lbl.setPixmap(QPixmap.fromImage(scaled))
                self.thumb_lbl.setStyleSheet("""
                    QLabel {
                        background: transparent;
                        border: 1px solid rgba(255, 255, 255, 0.16);
                        border-radius: 6px;
                    }
                """)
            else:
                self._choose_meta_icon(meta)
        else:
            self._choose_meta_icon(meta)

    def _choose_meta_icon(self, meta: LinkMetadata):
        ft = (meta.file_type or "").upper()
        if ft == "VIDEO":
            self._set_placeholder_thumb(FluentIcon.VIDEO)
        elif ft == "IMAGE":
            self._set_placeholder_thumb(FluentIcon.PHOTO)
        elif ft == "AUDIO":
            self._set_placeholder_thumb(FluentIcon.MUSIC)
        elif ft == "FOLDER":
            self._set_placeholder_thumb(FluentIcon.FOLDER)
        else:
            self._set_placeholder_thumb(FluentIcon.LINK)

    def _set_placeholder_thumb(self, icon: Optional[FluentIcon] = None):
        target_icon = icon or FluentIcon.LINK
        pm = target_icon.colored(QColor("#94a3b8"), QColor("#94a3b8")).icon().pixmap(QSize(28, 28))
        self.thumb_lbl.setText("")
        self.thumb_lbl.setPixmap(pm)
        self.thumb_lbl.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
            }
        """)

    def _on_refresh(self):
        self.status_badge.setText("REFRESHING")
        self.res_row.setText("Detecting...")
        self.size_row.setText("Detecting...")
        self.dur_row.setText("Detecting...")
        self._set_placeholder_thumb(FluentIcon.SYNC)
        unfurl_engine.request_unfurl(self.url, force_refresh=True, priority=True)

    def _on_copy_url(self):
        safe_copy_to_clipboard(self.url)
        self.copy_url_btn.setToolTip("Copied!")

    def apply_theme(self):
        pal = ThemeManager.get_palette()
        is_dark = pal.get("is_dark", True)
        text_col = pal.get("text", "#ffffff")
        muted = pal.get("muted", "#94a3b8")
        surface = pal.get("surface", "#18181b")
        border = pal.get("border", "#27272a")

        self.header_title.setStyleSheet(f"""
            QLabel#DialogHeaderTitle {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 14px;
                font-weight: 700;
            }}
        """)

        self.details_card.setStyleSheet(f"""
            QFrame#MetadataCard {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLabel#MetadataTitle {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#MetadataMuted {{
                color: {muted};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 600;
            }}
            QLabel#MetadataLabel {{
                color: {muted};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#MetadataValue {{
                color: {text_col};
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
            }}
        """)
        self.div.setStyleSheet(f"background-color: {border};")
