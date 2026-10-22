import uuid
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter
)
from ..components.script_viewer import ScriptViewer
from ..components.timeline_list import TimelineList
from ..components.outline_panel import OutlinePanel
from ..components.drawers import NotesDrawer, LinksDrawer
from ..components.widgets import GpuGlowSplitter
from ...core.models import DocumentState, EditorNote
from ...core.database import db

class WorkspaceView(QWidget):
    """The Dual Split-Pane Workspace (Source Script + A-Roll Timeline + Outline + Dockable Drawers)."""
    stats_updated = Signal(int, int, float)  # (total_aroll, checked_aroll, est_total_seconds)
    notes_count_changed = Signal(int)
    links_count_changed = Signal(int)
    copy_feedback_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceView")
        self.current_doc: Optional[DocumentState] = None
        self._is_focus_mode = False
        self._saved_split_sizes = [500, 500]

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 8, 8)
        main_layout.setSpacing(0)

        # Main Splitter: Left (Outline + Script) + Right (Timeline) with GPU glow handle
        self.splitter = GpuGlowSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)

        # Left Container: Document Outline (Collapsible) + Source Script Viewer
        self.left_container = QWidget(self)
        left_layout = QHBoxLayout(self.left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.outline_panel = OutlinePanel(self.left_container)
        self.outline_panel.closed.connect(self.outline_panel.hide)
        self.outline_panel.heading_clicked.connect(self._on_heading_clicked)
        self.outline_panel.hide()
        left_layout.addWidget(self.outline_panel)

        self.script_viewer = ScriptViewer(self.left_container)
        self.script_viewer.timestamp_clicked.connect(self._on_script_timestamp_clicked)
        self.script_viewer.add_note_requested.connect(self._on_add_custom_note)
        self.script_viewer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        left_layout.addWidget(self.script_viewer, 1)

        self.splitter.addWidget(self.left_container)

        # Right Pane: A-Roll Timeline
        self.timeline_list = TimelineList(self)
        self.timeline_list.setMinimumWidth(300)
        self.timeline_list.item_checked_changed.connect(self._on_item_checked_changed)
        self.timeline_list.item_timecode_changed.connect(self._on_item_timecode_changed)
        self.timeline_list.sync_script_requested.connect(self._on_sync_script_requested)
        self.timeline_list.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.splitter.addWidget(self.timeline_list)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)

        # Smart NLE split behavior:
        # Left pane (Script) expands/contracts dynamically with window resize (stretch=1)
        # Right pane (Timestamps checklist) maintains steady width unless divider is manually dragged (stretch=0)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        # Initial 50/50 split
        self.splitter.setSizes([500, 500])
        main_layout.addWidget(self.splitter, 1)

        # Independent Desktop Tool Windows (Preferences / Notes / Links)
        self.notes_drawer = NotesDrawer(None)
        self.notes_drawer.note_added.connect(self._on_add_custom_note)
        self.notes_drawer.note_checked_changed.connect(self._on_note_checked_changed)
        self.notes_drawer.jump_to_block_requested.connect(self._on_jump_to_block)
        self.notes_drawer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.notes_drawer.hide()

        self.links_drawer = LinksDrawer(None)
        self.links_drawer.link_checked_changed.connect(self._on_link_checked_changed)
        self.links_drawer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.links_drawer.hide()

    def load_document(self, doc: DocumentState):
        self.current_doc = doc
        
        # 1. Load Left Pane HTML, File Name & Headings
        file_display_name = doc.name or (os.path.basename(doc.file_path) if doc.file_path else "SCRIPT")
        self.script_viewer.set_file_name(file_display_name)
        self.script_viewer.set_content(doc.raw_html)
        self.outline_panel.load_headings(doc.headings)

        # 2. Load Right Pane Timeline Cards with Link Colors
        self.timeline_list.load_items(doc.items, doc.file_signature, doc.link_colors)

        # 3. Load Drawers with checked state!
        all_notes = doc.notes + doc.custom_notes
        self.notes_drawer.load_notes(all_notes, doc.checked_notes)
        self.links_drawer.load_links(doc.links, doc.checked_links)

        # 4. Emit badge counts
        self.notes_count_changed.emit(len(all_notes))
        self.links_count_changed.emit(len(doc.links))

        # 5. Compute Titlebar Stats
        self._recompute_stats()

    def toggle_split_view(self):
        """Toggles between 50/50 Split View and Full Focus Mode (hiding A-Roll Timeline)."""
        self._is_focus_mode = not self._is_focus_mode
        if self._is_focus_mode:
            self._saved_split_sizes = self.splitter.sizes()
            self.timeline_list.hide()
        else:
            self.timeline_list.show()
            self.splitter.setSizes(self._saved_split_sizes)

    def toggle_outline(self):
        """Toggles Document Outline panel inside the script pane."""
        if not self.outline_panel.isHidden():
            self.outline_panel.hide()
        else:
            self.outline_panel.show()

    def toggle_notes_drawer(self):
        if not self.notes_drawer.isHidden():
            self.notes_drawer.hide()
        else:
            self.notes_drawer.show()
            self.notes_drawer.raise_()
            self.notes_drawer.activateWindow()

    def toggle_links_drawer(self):
        if not self.links_drawer.isHidden():
            self.links_drawer.hide()
        else:
            self.links_drawer.show()
            self.links_drawer.raise_()
            self.links_drawer.activateWindow()

    def hide_drawers(self):
        self.notes_drawer.hide()
        self.links_drawer.hide()

    def _on_heading_clicked(self, heading_id: int):
        self.script_viewer.scroll_to_anchor(f"heading-{heading_id}")

    def _on_script_timestamp_clicked(self, tc: str):
        self.timeline_list.scroll_to_timestamp(tc)

    def _on_sync_script_requested(self, sync_id: int, meta: str):
        self.script_viewer.scroll_to_sync(sync_id, meta)

    def _on_item_checked_changed(self, item_id: str, is_checked: bool):
        if not self.current_doc:
            return

        for item in self.current_doc.items:
            if item.id == item_id:
                item.is_checked = is_checked
                break

        # Save state to SQLite
        db.save_document(self.current_doc)
        self._recompute_stats()

    def _on_item_timecode_changed(self, item_id: str, new_tc: str):
        if not self.current_doc:
            return

        for item in self.current_doc.items:
            if item.id == item_id:
                item.start_timecode = new_tc
                break

        db.save_document(self.current_doc)
        self._recompute_stats()

    def _on_link_checked_changed(self, link: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_links[link] = is_checked
        db.save_document(self.current_doc)

    def _on_note_checked_changed(self, note_id: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_notes[note_id] = is_checked
        db.save_document(self.current_doc)

    def _on_jump_to_block(self, block_id: int):
        self.script_viewer.scroll_to_block(block_id)

    def _on_add_custom_note(self, text: str):
        if not self.current_doc:
            return
        new_note = EditorNote(
            id=str(uuid.uuid4()),
            block_id=0,
            text=text
        )
        self.current_doc.custom_notes.append(new_note)
        db.save_document(self.current_doc)
        
        all_notes = self.current_doc.notes + self.current_doc.custom_notes
        self.notes_drawer.load_notes(all_notes)
        self.notes_count_changed.emit(len(all_notes))

    def _recompute_stats(self):
        if not self.current_doc:
            return

        total_arolls = len(self.current_doc.items)
        checked_arolls = sum(1 for i in self.current_doc.items if i.is_checked)
        total_duration_sec = sum(i.duration_seconds for i in self.current_doc.items)

        self.stats_updated.emit(total_arolls, checked_arolls, total_duration_sec)

    def apply_theme(self, variant=None, dark: bool = True):
        # Refresh timeline cards, script viewer, outline panel, and drawers to inherit new palette colors
        for card in self.timeline_list.card_list:
            card._update_card_style()
        self.timeline_list.update()
        self.script_viewer._update_display()
        if hasattr(self, "outline_panel") and self.outline_panel:
            self.outline_panel.apply_theme()
        if hasattr(self, "notes_drawer") and self.notes_drawer:
            self.notes_drawer.apply_theme()
            self.notes_drawer.update()
        if hasattr(self, "links_drawer") and self.links_drawer:
            self.links_drawer.apply_theme()
            self.links_drawer.update()
        if self.splitter and self.splitter.count() > 1:
            handle = self.splitter.handle(1)
            if handle:
                handle.update()

    def set_hold_reflow(self, hold: bool):
        if hasattr(self, 'script_viewer') and self.script_viewer:
            self.script_viewer.set_hold_reflow(hold)
