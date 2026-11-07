import os
import sys
import uuid
from typing import Optional, Dict, List, Tuple
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter
)
from ..components.script_viewer import ScriptViewer
from ..components.timeline_list import TimelineList
from ..components.outline_panel import OutlinePanel
from ..components.drawers import NotesDrawer, LinksDrawer
from ..components.link_embed_card import link_embed_controller
from ..components.widgets import GpuGlowSplitter
from ...core.models import DocumentState, EditorNote
from ...core.database import db
from ...core.unfurl_engine import unfurl_engine
from ...core.parser import save_blocks_to_docx, update_timecode_in_docx

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
        self.splitter.drag_started.connect(self._on_splitter_drag_started)
        self.splitter.drag_finished.connect(self._on_splitter_drag_finished)
        self.splitter.handle_clicked.connect(self._on_splitter_handle_clicked)
        self.splitter.handle_double_clicked.connect(self._on_splitter_handle_double_clicked)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # Left Container: Document Outline (Collapsible) + Source Script Viewer
        self.left_container = QWidget(self)
        left_layout = QHBoxLayout(self.left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.outline_panel = OutlinePanel(self.left_container)
        self.outline_panel.closed.connect(self.outline_panel.hide)
        self.outline_panel.heading_clicked.connect(self._on_heading_clicked)
        self.outline_panel.hide()
        left_layout.addWidget(self.outline_panel)

        self.script_viewer = ScriptViewer(self.left_container)
        self.script_viewer.timestamp_sync_clicked.connect(self._on_script_timestamp_sync_clicked)
        self.script_viewer.timestamp_clicked.connect(self._on_script_timestamp_clicked)
        self.script_viewer.add_note_requested.connect(self._on_add_custom_note)
        self.script_viewer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.script_viewer.save_docx_requested.connect(self._on_save_docx_requested)
        self.script_viewer.browser.verticalScrollBar().valueChanged.connect(self._on_script_scrolled)
        left_layout.addWidget(self.script_viewer, 1)

        self.splitter.addWidget(self.left_container)

        # Right Pane: A-Roll Timeline
        self.timeline_list = TimelineList(self)
        self.timeline_list.setMinimumWidth(285)
        self.timeline_list.item_checked_changed.connect(self._on_item_checked_changed)
        self.timeline_list.item_timecode_changed.connect(self._on_item_timecode_changed)
        self.timeline_list.sync_script_requested.connect(self._on_sync_script_requested)
        self.timeline_list.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.splitter.addWidget(self.timeline_list)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, True)

        # Smart NLE split behavior:
        # Left pane (Script) expands/contracts dynamically with window resize (stretch=1)
        # Right pane (Timestamps checklist) maintains steady width unless divider is manually dragged (stretch=0)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        # Initial 50/50 split
        self.splitter.setSizes([500, 500])
        main_layout.addWidget(self.splitter, 1)

        # Fluent Modal Overlay Checklist Drawers (Notes / Links)
        win = self.window() if self.window() else self
        self.notes_drawer = NotesDrawer(win)
        self.notes_drawer.note_added.connect(self._on_add_custom_note)
        self.notes_drawer.note_checked_changed.connect(self._on_note_checked_changed)
        self.notes_drawer.jump_to_block_requested.connect(self._on_jump_to_block)
        self.notes_drawer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.notes_drawer.hide()

        self.links_drawer = LinksDrawer(win)
        self.links_drawer.link_checked_changed.connect(self._on_link_checked_changed)
        self.links_drawer.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        self.links_drawer.hide()

        link_embed_controller.copy_feedback_requested.connect(self.copy_feedback_requested.emit)
        unfurl_engine.cache_cleared.connect(self._on_unfurl_cache_cleared)

    def _on_unfurl_cache_cleared(self, restricted_only: bool):
        if self.current_doc and self.current_doc.links and "pytest" not in sys.modules:
            uncached = [lk for lk in self.current_doc.links if not unfurl_engine.has_cached(lk)]
            if uncached:
                unfurl_engine.request_batch_unfurl(uncached, session_id=getattr(self, "_session_id", ""))

    def load_document(self, doc: DocumentState):
        self.current_doc = doc
        self._session_id = uuid.uuid4().hex
        unfurl_engine.set_active_session(self._session_id)
        
        # Check for managed .clickproj in Documents/Clickables/Projects/
        from ...core.project_manager import project_manager
        proj_path = project_manager.get_auto_project_path(doc.name)
        proj_data = project_manager.load_project(proj_path)
        if proj_data:
            checked_items_set = set(proj_data.checked_items)
            for item in doc.items:
                if item.id in checked_items_set:
                    item.is_checked = True
            for k in proj_data.checked_notes:
                doc.checked_notes[k] = True
            for k in proj_data.checked_links:
                doc.checked_links[k] = True
            self.footage_folder = proj_data.footage_folder
            self.linked_footage = getattr(proj_data, "linked_footage", {})
        else:
            self.footage_folder = getattr(self, "footage_folder", "")
            self.linked_footage = getattr(self, "linked_footage", {})
            self._save_auto_project()

        # 1. Load Left Pane HTML, Blocks, Page Geometry, File Name & Headings
        file_display_name = doc.name or (os.path.basename(doc.file_path) if doc.file_path else "SCRIPT")
        self.script_viewer.set_file_name(file_display_name)
        self.script_viewer.load_document_state(doc)
        self.outline_panel.load_headings(doc.headings)

        # 2. Load Right Pane Timeline Cards with Link Colors & Linked Footage
        self.timeline_list.load_items(doc.items, doc.file_signature, doc.link_colors, getattr(self, "linked_footage", {}))

        # 3. Synchronize probed local media metadata & load drawers
        self._sync_probed_footage_to_unfurl(getattr(self, "linked_footage", {}))
        all_notes = doc.notes + doc.custom_notes
        self.notes_drawer.load_notes(all_notes, doc.checked_notes)
        self.links_drawer.load_links(doc.links, doc.checked_links, getattr(self, "linked_footage", {}))

        # 4. Emit badge counts
        self.notes_count_changed.emit(len(all_notes))
        self.links_count_changed.emit(len(doc.links))

        # 5. Compute Titlebar Stats
        self._recompute_stats()

        # 6. Zero-Network Cold Boot: Only fetch brand-new links missing from SQLite
        if doc.links and "pytest" not in sys.modules:
            uncached = [lk for lk in doc.links if not unfurl_engine.has_cached(lk)]
            if uncached:
                unfurl_engine.request_batch_unfurl(uncached, session_id=self._session_id)

    def close_drawers(self):
        """Closes floating tool drawers cleanly when navigating away or switching to Home."""
        if hasattr(self, "notes_drawer") and self.notes_drawer:
            self.notes_drawer.close()
        if hasattr(self, "links_drawer") and self.links_drawer:
            self.links_drawer.close()

    def _save_auto_project(self, custom_path: Optional[str] = None):
        """Autosaves active workspace state to an isolated .clickproj file."""
        if not self.current_doc:
            return
        from ...core.project_manager import project_manager, ProjectData
        path = custom_path or project_manager.get_auto_project_path(self.current_doc.name)
        checked_item_ids = [i.id for i in self.current_doc.items if i.is_checked]
        checked_note_ids = [k for k, v in self.current_doc.checked_notes.items() if v]
        checked_link_urls = [k for k, v in self.current_doc.checked_links.items() if v]
        data = ProjectData(
            format_version=1,
            project_name=self.current_doc.name,
            script_file_name=self.current_doc.file_path or self.current_doc.name,
            file_signature=self.current_doc.file_signature,
            footage_folder=getattr(self, "footage_folder", ""),
            linked_footage=getattr(self, "linked_footage", {}),
            checked_items=checked_item_ids,
            checked_notes=checked_note_ids,
            checked_links=checked_link_urls,
            notes_data=[n.__dict__ if hasattr(n, "__dict__") else n for n in self.current_doc.custom_notes],
        )
        project_manager.save_project(path, data)

    def _sync_probed_footage_to_unfurl(self, linked_footage: Dict[str, str]):
        """Synchronizes probed technical media properties (duration, resolution, size)
        into UnfurlEngine and SQLite cache so all UI views (Source Links Drawer,
        Link Hover Card, Link Inspector, Timeline Cards) immediately reflect exact local media specs.
        """
        if not linked_footage:
            return
        from ...core.media_probe import probe_media_file
        from ...core.unfurl_engine import unfurl_engine, LinkMetadata, format_bytes_display, format_duration_display
        from ...core.parser import normalize_link

        for raw_url, local_path in linked_footage.items():
            if not local_path or not os.path.isfile(local_path):
                continue
            probed = probe_media_file(local_path, use_cache=True)
            if not probed:
                continue

            norm_url = normalize_link(raw_url)
            cached = unfurl_engine.get_cached(raw_url) or unfurl_engine.get_cached(norm_url)
            if not cached:
                cached = LinkMetadata(
                    url=raw_url,
                    title=os.path.basename(local_path),
                    domain="LOCAL",
                    file_type="VIDEO" if probed.has_video else ("AUDIO" if probed.has_audio else "LINK"),
                    is_restricted=False,
                    is_fallback=False,
                    loading=False,
                )
            if not cached.title or cached.is_fallback:
                cached.title = os.path.basename(local_path)
            if probed.resolution:
                cached.resolution = probed.resolution
            if probed.duration_seconds > 0:
                cached.duration_seconds = probed.duration_seconds
                cached.duration_display = probed.duration_display or format_duration_display(probed.duration_seconds)
            if probed.file_size > 0:
                cached.file_size_bytes = probed.file_size
                cached.file_size_display = format_bytes_display(probed.file_size)
            if probed.has_video:
                cached.file_type = "VIDEO"
            elif probed.has_audio:
                cached.file_type = "AUDIO"
            cached.is_fallback = False
            cached.loading = False

            unfurl_engine.store_metadata(raw_url, cached, persist_sqlite=True)
            if norm_url != raw_url:
                unfurl_engine.store_metadata(norm_url, cached, persist_sqlite=True)
            unfurl_engine.metadata_ready.emit(raw_url, cached)
            if norm_url != raw_url:
                unfurl_engine.metadata_ready.emit(norm_url, cached)

    def set_footage_folder(self, folder_path: str, linked_footage: Optional[Dict[str, str]] = None):
        self.footage_folder = folder_path
        if linked_footage is not None:
            self.linked_footage = linked_footage
        self._sync_probed_footage_to_unfurl(getattr(self, "linked_footage", {}))
        if self.current_doc:
            self.links_drawer.load_links(self.current_doc.links, self.current_doc.checked_links, getattr(self, "linked_footage", {}))
            self.timeline_list.set_linked_footage(getattr(self, "linked_footage", {}))
        self._save_auto_project()

    def mark_all_done(self):
        if not self.current_doc:
            return
        for item in self.current_doc.items:
            item.is_checked = True
        db.save_document(self.current_doc)
        self._save_auto_project()
        self.timeline_list.load_items(self.current_doc.items, self.current_doc.file_signature, self.current_doc.link_colors)
        self._recompute_stats()
        self.copy_feedback_requested.emit("All A-Roll marked done!")

    def reset_all_checklists(self):
        if not self.current_doc:
            return
        for item in self.current_doc.items:
            item.is_checked = False
        self.current_doc.checked_notes.clear()
        self.current_doc.checked_links.clear()
        db.save_document(self.current_doc)
        self._save_auto_project()
        self.timeline_list.load_items(self.current_doc.items, self.current_doc.file_signature, self.current_doc.link_colors)
        all_notes = self.current_doc.notes + self.current_doc.custom_notes
        self.notes_drawer.load_notes(all_notes, self.current_doc.checked_notes)
        self.links_drawer.load_links(self.current_doc.links, self.current_doc.checked_links)
        self._recompute_stats()
        self.copy_feedback_requested.emit("All checklists reset!")

    def toggle_split_view(self):
        """Toggles between Split View and Full Focus Mode (collapsing timestamps to right edge)."""
        sizes = self.splitter.sizes()
        total = sum(sizes) if sum(sizes) > 0 else self.width()
        if len(sizes) == 2 and sizes[1] == 0:
            target_w = self._saved_split_sizes[1] if (self._saved_split_sizes and self._saved_split_sizes[1] >= 285) else 480
            left_w = max(100, total - target_w)
            self.splitter.setSizes([left_w, target_w])
            self._is_focus_mode = False
        else:
            self._saved_split_sizes = sizes
            self.splitter.setSizes([total, 0])
            self._is_focus_mode = True

    def _on_splitter_moved(self, pos: int, index: int):
        sizes = self.splitter.sizes()
        if len(sizes) == 2:
            if sizes[1] == 0:
                self._is_focus_mode = True
            else:
                self._is_focus_mode = False
                self._saved_split_sizes = sizes

    def _on_splitter_handle_clicked(self):
        """When parked at the edge (collapsed), a single click restores the timeline."""
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] == 0:
            total = sum(sizes) if sum(sizes) > 0 else self.width()
            target_w = self._saved_split_sizes[1] if (self._saved_split_sizes and self._saved_split_sizes[1] >= 285) else 480
            left_w = max(100, total - target_w)
            self.splitter.setSizes([left_w, target_w])
            self._is_focus_mode = False

    def _on_splitter_handle_double_clicked(self):
        """Double clicking the handle toggles between collapsed and restored."""
        self.toggle_split_view()

    def toggle_outline(self):
        """Toggles Document Outline panel inside the script pane."""
        if not self.outline_panel.isHidden():
            self.outline_panel.hide()
        else:
            self.outline_panel.show()
            self._on_script_scrolled()

    def toggle_notes_drawer(self):
        if not self.notes_drawer.isHidden():
            self.notes_drawer.hide()
        else:
            win = self.window()
            if win and self.notes_drawer.parent() != win:
                self.notes_drawer.setParent(win)
                win.installEventFilter(self.notes_drawer)
                self.notes_drawer.resize(win.size())
            self.notes_drawer.show()
            self.notes_drawer.raise_()
            self.notes_drawer.activateWindow()

    def toggle_links_drawer(self):
        if not self.links_drawer.isHidden():
            self.links_drawer.hide()
        else:
            win = self.window()
            if win and self.links_drawer.parent() != win:
                self.links_drawer.setParent(win)
                win.installEventFilter(self.links_drawer)
                self.links_drawer.resize(win.size())
            self.links_drawer.show()
            self.links_drawer.raise_()
            self.links_drawer.activateWindow()

    def hide_drawers(self):
        self.notes_drawer.hide()
        self.links_drawer.hide()

    def _on_splitter_drag_started(self):
        """Freezes text reflow and texture rebaking while dragging the splitter for 144Hz fluidity."""
        self.script_viewer.set_hold_reflow(True)
        self.timeline_list.set_hold_reflow(True)

    def _on_splitter_drag_finished(self):
        """Unfreezes and runs a single clean rebake pass to the final settled width."""
        self.script_viewer.set_hold_reflow(False)
        self.timeline_list.set_hold_reflow(False)
        sizes = self.splitter.sizes()
        if len(sizes) == 2:
            if sizes[1] == 0:
                self._is_focus_mode = True
            else:
                self._is_focus_mode = False
                self._saved_split_sizes = sizes

    def _on_heading_clicked(self, heading_id: int):
        self.script_viewer.scroll_to_anchor(f"block-{heading_id}", align_top=True)

    def _on_script_scrolled(self, _val: int = 0):
        if not hasattr(self, "outline_panel") or self.outline_panel.isHidden():
            return
        if not self.current_doc or not self.current_doc.headings:
            return
        blk_id = self.script_viewer.get_visible_block_id()
        active_id = None
        for h in self.current_doc.headings:
            h_id = h.get("id", 0)
            if h_id <= blk_id:
                active_id = h_id
            else:
                break
        if active_id is not None:
            self.outline_panel.set_active_heading(active_id)

    def _conform_timecode_for_script(self, tc_str: str, sync_id: Optional[int] = None) -> Tuple[str, str]:
        from ...core.timecode_engine import conform_timestamp_for_clip
        from ...core.media_probe import probe_media_file
        from ...core.parser import normalize_link
        from ...core.database import db

        auto_conform = db.get_setting("auto_conform_smpte", "true").lower() == "true"
        fallback_fps_str = db.get_setting("timeline_framerate", "23.976")
        try:
            fallback_fps = float(fallback_fps_str)
        except (ValueError, TypeError):
            fallback_fps = 23.976

        if not auto_conform:
            return tc_str, f"Copied: {tc_str}"

        target_item = None
        if self.current_doc:
            if sync_id is not None:
                for it in self.current_doc.items:
                    if it.sync_id == sync_id:
                        target_item = it
                        break
            if not target_item:
                for it in self.current_doc.items:
                    if it.start_timecode == tc_str or it.raw_timestamp == tc_str:
                        target_item = it
                        break

        linked_path = ""
        if target_item and getattr(target_item, "links", None):
            for lk in target_item.links:
                norm = normalize_link(lk)
                p = getattr(self, "linked_footage", {}).get(norm) or getattr(self, "linked_footage", {}).get(lk)
                if p and os.path.isfile(p):
                    linked_path = p
                    break

        if linked_path:
            probed = probe_media_file(linked_path, use_cache=True)
            if probed and probed.frame_rate > 0:
                smpte, meta = conform_timestamp_for_clip(
                    tc_str,
                    probed_fps=probed.frame_rate,
                    container_start_time=probed.start_time,
                    embedded_timecode=probed.start_timecode,
                    is_vfr=probed.is_vfr
                )
                feedback = f"Copied: {smpte} ({meta['nominal_fps']} FPS"
                if abs(meta['start_time_offset']) > 0.05:
                    feedback += f", +{meta['start_time_offset']:.2f}s offset"
                if meta['is_vfr']:
                    feedback += ", VFR alert"
                feedback += ")"
                return smpte, feedback

        # If no footage is linked, do NOT alter: return raw timestamp
        return tc_str, f"Copied: {tc_str}"

    def refresh_timecodes(self):
        """Refreshes all timeline card timecode displays when conforming toggle changes."""
        self.timeline_list.refresh_timecodes()

    def _on_script_timestamp_sync_clicked(self, tc: str, sync_id: int):
        conformed_tc, feedback = self._conform_timecode_for_script(tc, sync_id)
        from ..components.widgets import safe_copy_to_clipboard
        safe_copy_to_clipboard(conformed_tc)
        self.copy_feedback_requested.emit(feedback)
        self.timeline_list.scroll_to_timestamp(tc, sync_id)

    def _on_script_timestamp_clicked(self, tc: str):
        conformed_tc, feedback = self._conform_timecode_for_script(tc)
        from ..components.widgets import safe_copy_to_clipboard
        safe_copy_to_clipboard(conformed_tc)
        self.copy_feedback_requested.emit(feedback)
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

        # Save state to SQLite and auto-project
        db.save_document(self.current_doc)
        self._save_auto_project()
        self._recompute_stats()

    def _on_save_docx_requested(self, edited_blocks: list):
        if not self.current_doc or not self.current_doc.file_path:
            return
        if not os.path.exists(self.current_doc.file_path):
            return

        script_scroll = self.script_viewer.browser.verticalScrollBar().value()
        tl_scroll = self.timeline_list.scroll_area.verticalScrollBar().value()

        updated_doc = save_blocks_to_docx(
            self.current_doc.file_path,
            edited_blocks,
            previous_doc=self.current_doc
        )
        self.load_document(updated_doc)

        self.script_viewer.browser.verticalScrollBar().setValue(script_scroll)
        self.timeline_list.scroll_area.verticalScrollBar().setValue(tl_scroll)
        self.copy_feedback_requested.emit("Saved .docx!")

    def _on_item_timecode_changed(self, item_id: str, new_tc: str):
        if not self.current_doc:
            return

        target_item = None
        for item in self.current_doc.items:
            if item.id == item_id:
                target_item = item
                break

        if target_item and self.current_doc.file_path and os.path.exists(self.current_doc.file_path):
            script_scroll = self.script_viewer.browser.verticalScrollBar().value()
            tl_scroll = self.timeline_list.scroll_area.verticalScrollBar().value()
            updated_doc = update_timecode_in_docx(
                self.current_doc.file_path,
                target_item,
                target_item.start_timecode,
                target_item.end_timecode,
                previous_doc=self.current_doc
            )
            self.load_document(updated_doc)
            self.script_viewer.browser.verticalScrollBar().setValue(script_scroll)
            self.timeline_list.scroll_area.verticalScrollBar().setValue(tl_scroll)
            return

        db.save_document(self.current_doc)
        self._recompute_stats()

    def _on_link_checked_changed(self, link: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_links[link] = is_checked
        db.save_document(self.current_doc)
        self._save_auto_project()

    def _on_note_checked_changed(self, note_id: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_notes[note_id] = is_checked
        db.save_document(self.current_doc)
        self._save_auto_project()

    def _on_jump_to_block(self, block_id: int):
        if hasattr(self, "notes_drawer") and self.notes_drawer and not self.notes_drawer.isHidden():
            self.notes_drawer.hide()
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
        self._save_auto_project()
        
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
        if getattr(link_embed_controller, "_card", None) is not None:
            link_embed_controller._card.apply_theme()
        if self.splitter and self.splitter.count() > 1:
            handle = self.splitter.handle(1)
            if handle:
                handle.update()

    def set_hold_reflow(self, hold: bool):
        if hasattr(self, 'script_viewer') and self.script_viewer:
            self.script_viewer.set_hold_reflow(hold)
