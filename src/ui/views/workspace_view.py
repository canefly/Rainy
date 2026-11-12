import os
import sys
import re
import uuid
from typing import Optional, Dict, List, Tuple
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter
)
from ..components.script_viewer import ScriptViewer
from ..components.timeline_list import TimelineList
from ..components.outline_panel import OutlinePanel
from ..components.drawers import NotesDrawer, LinksDrawer
from ..components.transcode_dialog import TranscodeDialog
from ..components.link_embed_card import link_embed_controller
from ..components.aroll_trimmer_window import ARollTrimmerWindow
from ..components.widgets import GpuGlowSplitter
from ...core.models import DocumentState, EditorNote
from ...core.database import db
from ...core.engine_manager import is_mpv_available
from ...core.unfurl_engine import unfurl_engine
from ...core.parser import normalize_link

class WorkspaceView(QWidget):
    """The Dual Split-Pane Workspace (Source Script + A-Roll Timeline + Outline + Dockable Drawers)."""
    stats_updated = Signal(int, int, float)  # (total_aroll, checked_aroll, est_total_seconds)
    notes_count_changed = Signal(int)
    links_count_changed = Signal(int)
    footage_count_changed = Signal(int)
    set_footage_folder_requested = Signal()
    relink_requested = Signal()
    copy_feedback_requested = Signal(str)
    aroll_mode_changed = Signal(bool)
    open_settings_requested = Signal(str)

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
        self.timeline_list.open_settings_requested.connect(self.open_settings_requested.emit)

        # A-Roll Precision Trimmer & Source Monitor (Modeless)
        self._aroll_edit_mode = False
        self._active_trimmed_item_id = None
        self.trimmer_window = ARollTrimmerWindow(None)
        self.trimmer_window._workspace_parent = self

        self.timeline_list.card_target_clicked.connect(self._on_aroll_card_targeted)
        self.timeline_list.play_requested.connect(self._on_card_play_requested)
        self.trimmer_window.timecode_changed.connect(self._on_trimmer_timecode_changed)
        self.trimmer_window.request_prev_card.connect(self._on_trimmer_prev_card)
        self.trimmer_window.request_next_card.connect(self._on_trimmer_next_card)
        self.trimmer_window.closed.connect(self._on_trimmer_window_closed)
        self.trimmer_window.edit_applied.connect(self._on_trimmer_edit_applied)

        self._aroll_shortcut = QShortcut(QKeySequence("Alt+E"), self)
        self._aroll_shortcut.activated.connect(self.toggle_aroll_edit_mode)
        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self._on_escape_pressed)

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

        # Subscribe to global RenderQueueService completions so every view in the application
        # (A-Roll Trimmer, Timeline Cards, Source Links Drawer, Unfurl cache) refreshes immediately
        # when any background CFR / compression job finishes.
        from ...core.render_queue import render_queue
        render_queue.job_finished.connect(self._on_render_queue_job_finished)

        # Live GUI MCP IPC Server (allows Antigravity to simulate native UI locks and script VO diffs)
        from ...core.gui_ipc_server import GuiIpcServer
        self.mcp_ipc_server = GuiIpcServer(handler_callback=self.handle_mcp_gui_command, parent=self)
        if "pytest" not in sys.modules and db.get_setting("mcp_bridge_enabled", "true") != "false":
            self.mcp_ipc_server.start()

        # Zero-Lag Debounced Autosave Timer (300ms idle debounce)
        self.current_project_path = ""
        self._save_debounce_timer = QTimer(self)
        self._save_debounce_timer.setSingleShot(True)
        self._save_debounce_timer.setInterval(300)
        self._save_debounce_timer.timeout.connect(self._do_debounced_save)

    def schedule_save(self):
        """Debounces project saves by 300ms to eliminate disk I/O stutter during rapid scrubbing and checking."""
        if hasattr(self, "_save_debounce_timer"):
            self._save_debounce_timer.start(300)

    def flush_save(self):
        """Immediately flushes pending autosave to disk."""
        if hasattr(self, "_save_debounce_timer") and self._save_debounce_timer.isActive():
            self._save_debounce_timer.stop()
            self._do_debounced_save()

    def _do_debounced_save(self):
        self._save_auto_project()

    def _on_unfurl_cache_cleared(self, restricted_only: bool):
        if self.current_doc and self.current_doc.links and "pytest" not in sys.modules:
            uncached = [lk for lk in self.current_doc.links if not unfurl_engine.has_cached(lk)]
            if uncached:
                unfurl_engine.request_batch_unfurl(uncached, session_id=getattr(self, "_session_id", ""))

    def load_document(self, doc: DocumentState, project_path: Optional[str] = None):
        self.current_doc = doc
        self.current_project_path = project_path or ""
        self._session_id = uuid.uuid4().hex
        unfurl_engine.set_active_session(self._session_id)
        
        # Check for managed .clickproj in Documents/Clickables/Projects/
        from ...core.project_manager import project_manager
        proj_path = self.current_project_path or project_manager.get_auto_project_path(doc.name)
        self.current_project_path = proj_path
        proj_data = project_manager.load_project(proj_path)
        if proj_data:
            checked_items_set = set(proj_data.checked_items)
            aroll_locks_map = getattr(proj_data, "aroll_locks", {}) or {}
            overrides_map = getattr(proj_data, "overrides", {}) or {}
            for item in doc.items:
                if item.id in checked_items_set:
                    item.is_checked = True
                lk_data = aroll_locks_map.get(item.id) or aroll_locks_map.get(str(item.row_index))
                if lk_data and isinstance(lk_data, dict):
                    item.vo_locked = bool(lk_data.get("vo_locked", False))
                    item.vo_cut_after_sec = lk_data.get("vo_cut_after_sec")
                    item.vo_resume_sec = lk_data.get("vo_resume_sec")
                    item.vo_lead_in_text = lk_data.get("vo_lead_in_text", "")
                    item.vo_has_adlib = bool(lk_data.get("vo_has_adlib", False))
                    item.vo_adlib_note = lk_data.get("vo_adlib_note", "")
                    item.vo_status = lk_data.get("vo_status", "PENDING")
                ov_data = overrides_map.get(item.id)
                if ov_data and isinstance(ov_data, dict):
                    in_tc_ov = ov_data.get("start_tc") or ov_data.get("in_tc")
                    out_tc_ov = ov_data.get("end_tc") or ov_data.get("out_tc")
                    if in_tc_ov: item.start_timecode = in_tc_ov
                    if out_tc_ov: item.end_timecode = out_tc_ov
                    if "start_sec" in ov_data: item.start_seconds = float(ov_data["start_sec"])
                    if "end_sec" in ov_data: item.end_seconds = float(ov_data["end_sec"])
                    if (in_tc_ov or out_tc_ov) and ("start_sec" not in ov_data or "end_sec" not in ov_data):
                        try:
                            from ...core.parser import process_time_span, parse_time_single
                            if item.end_timecode and not item.is_single:
                                _, s_sec, _, e_sec, dur_s, dur_d, _, _ = process_time_span(f"{item.start_timecode} - {item.end_timecode}")
                                if "start_sec" not in ov_data: item.start_seconds = s_sec
                                if "end_sec" not in ov_data: item.end_seconds = e_sec
                                if "dur_sec" not in ov_data: item.duration_seconds = dur_s
                                item.duration_display = dur_d
                            elif item.start_timecode:
                                _, s_sec = parse_time_single(item.start_timecode)
                                if "start_sec" not in ov_data: item.start_seconds = s_sec
                        except Exception:
                            pass
            if overrides_map and doc.file_signature:
                from ...core.database import db
                for itm_id, ov in overrides_map.items():
                    db.save_override(
                        doc.file_signature,
                        itm_id,
                        ov.get("start_tc") or ov.get("in_tc") or "",
                        ov.get("end_tc") or ov.get("out_tc") or "",
                        ov.get("text")
                    )
            for k in proj_data.checked_notes:
                doc.checked_notes[k] = True
            for k in proj_data.checked_links:
                doc.checked_links[k] = True
            self.footage_folder = proj_data.footage_folder
            self.linked_footage = getattr(proj_data, "linked_footage", {})
            if getattr(proj_data, "vo_file_path", ""):
                doc.vo_file_path = proj_data.vo_file_path
            if getattr(proj_data, "vo_files", None):
                doc.vo_files = list(proj_data.vo_files)
            if getattr(proj_data, "vo_transcript_path", ""):
                doc.vo_transcript_path = proj_data.vo_transcript_path
            if getattr(proj_data, "vo_segments", None):
                doc.vo_segments = list(proj_data.vo_segments)
            if getattr(proj_data, "vo_block_diffs", None):
                doc.vo_block_diffs = dict(proj_data.vo_block_diffs)
            if getattr(proj_data, "timeline_assembly", None):
                doc.timeline_assembly = list(proj_data.timeline_assembly)
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
        if getattr(self, "linked_footage", None):
            from ...core.waveform_engine import waveform_precache_manager
            waveform_precache_manager.queue_footage(list(self.linked_footage.values()))
        all_notes = doc.notes + doc.custom_notes
        self.notes_drawer.load_notes(all_notes, doc.checked_notes)
        self.links_drawer.load_links(doc.links, doc.checked_links, getattr(self, "linked_footage", {}))

        # 4. Emit badge counts
        self.notes_count_changed.emit(len(all_notes))
        self.links_count_changed.emit(len(doc.links))
        self.footage_count_changed.emit(len(getattr(self, "linked_footage", {})))

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
        if hasattr(self, "trimmer_window") and self.trimmer_window:
            self.trimmer_window.hide()

    def closeEvent(self, event):
        if hasattr(self, "_save_debounce_timer") and self._save_debounce_timer.isActive():
            self.flush_save()
        if hasattr(self, "trimmer_window") and self.trimmer_window:
            try:
                self.trimmer_window.destroy_window()
            except Exception:
                pass
        self.close_drawers()
        super().closeEvent(event)

    def _save_auto_project(self, custom_path: Optional[str] = None):
        """Autosaves active workspace state to an isolated .clickproj file."""
        if not self.current_doc:
            return
        from dataclasses import asdict
        from ...core.project_manager import project_manager, ProjectData
        path = custom_path or getattr(self, "current_project_path", "") or project_manager.get_auto_project_path(self.current_doc.name)
        checked_item_ids = [i.id for i in self.current_doc.items if i.is_checked]
        checked_note_ids = [k for k, v in self.current_doc.checked_notes.items() if v]
        checked_link_urls = [k for k, v in self.current_doc.checked_links.items() if v]
        aroll_locks = {}
        for i in self.current_doc.items:
            if getattr(i, "vo_status", "PENDING") != "PENDING" or getattr(i, "vo_cut_after_sec", None) is not None:
                aroll_locks[i.id] = {
                    "row_index": i.row_index,
                    "vo_locked": bool(getattr(i, "vo_locked", False)),
                    "vo_cut_after_sec": getattr(i, "vo_cut_after_sec", None),
                    "vo_resume_sec": getattr(i, "vo_resume_sec", None),
                    "vo_lead_in_text": getattr(i, "vo_lead_in_text", ""),
                    "vo_has_adlib": bool(getattr(i, "vo_has_adlib", False)),
                    "vo_adlib_note": getattr(i, "vo_adlib_note", ""),
                    "vo_status": getattr(i, "vo_status", "PENDING"),
                }
        overrides = {}
        from ...core.database import db
        if self.current_doc.file_signature:
            overrides = db.get_overrides(self.current_doc.file_signature)

        items_payload = []
        for i in self.current_doc.items:
            items_payload.append(asdict(i) if hasattr(i, "__dataclass_fields__") else dict(i.__dict__))

        notes_payload = []
        for n in self.current_doc.notes:
            notes_payload.append(asdict(n) if hasattr(n, "__dataclass_fields__") else dict(n.__dict__))

        custom_notes_payload = []
        for cn in self.current_doc.custom_notes:
            custom_notes_payload.append(asdict(cn) if hasattr(cn, "__dataclass_fields__") else dict(cn.__dict__))

        data = ProjectData(
            format_version=2,
            project_name=self.current_doc.name,
            script_file_name=self.current_doc.file_path or self.current_doc.name,
            file_signature=self.current_doc.file_signature,
            footage_folder=getattr(self, "footage_folder", ""),
            vo_file_path=getattr(self.current_doc, "vo_file_path", ""),
            vo_files=list(getattr(self.current_doc, "vo_files", []) or []),
            vo_transcript_path=getattr(self.current_doc, "vo_transcript_path", "") or "",
            vo_segments=list(getattr(self.current_doc, "vo_segments", []) or []),
            vo_block_diffs=dict(getattr(self.current_doc, "vo_block_diffs", {}) or {}),
            aroll_locks=aroll_locks,
            timeline_assembly=list(getattr(self.current_doc, "timeline_assembly", []) or []),
            linked_footage=getattr(self, "linked_footage", {}),
            checked_items=checked_item_ids,
            checked_notes=checked_note_ids,
            checked_links=checked_link_urls,
            notes_data=custom_notes_payload,
            raw_html=getattr(self.current_doc, "raw_html", ""),
            items=items_payload,
            notes=notes_payload,
            custom_notes=custom_notes_payload,
            headings=list(getattr(self.current_doc, "headings", [])),
            links=list(getattr(self.current_doc, "links", [])),
            link_colors=dict(getattr(self.current_doc, "link_colors", {})),
            page_geometry=dict(getattr(self.current_doc, "page_geometry", {})),
            blocks=list(getattr(self.current_doc, "blocks", [])),
            overrides=overrides,
        )
        project_manager.save_project(path, data)
        db.add_recent_file(path, self.current_doc.name)

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
            self.footage_count_changed.emit(len(getattr(self, "linked_footage", {})))
        self._save_auto_project()

        # Silently pre-cache audio waveforms at low priority so video player opens with zero latency
        if getattr(self, "linked_footage", None):
            from ...core.waveform_engine import waveform_precache_manager
            waveform_precache_manager.queue_footage(list(self.linked_footage.values()))

    def mark_all_done(self):
        if not self.current_doc:
            return
        for item in self.current_doc.items:
            item.is_checked = True
        self.schedule_save()
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
        self.schedule_save()
        self.timeline_list.load_items(self.current_doc.items, self.current_doc.file_signature, self.current_doc.link_colors)
        all_notes = self.current_doc.notes + self.current_doc.custom_notes
        self.notes_drawer.load_notes(all_notes, self.current_doc.checked_notes)
        self.links_drawer.load_links(self.current_doc.links, self.current_doc.checked_links, getattr(self, "linked_footage", {}))
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

    def _on_footage_transcode_requested(self, file_path: str):
        win = self.window() or self
        dlg = TranscodeDialog(file_path, parent=win)
        dlg.transcode_completed.connect(self._on_transcode_finished_refresh)
        dlg.exec()

    def _on_transcode_finished_refresh(self, source_path: str):
        # Force fresh disk re-probe so SQLite cache is guaranteed up-to-date
        if source_path and os.path.isfile(source_path):
            try:
                from ...core.media_probe import probe_media_file
                probe_media_file(source_path, use_cache=False)
            except Exception:
                pass
        self._sync_probed_footage_to_unfurl(getattr(self, "linked_footage", {}))
        self.footage_count_changed.emit(len(getattr(self, "linked_footage", {})))
        if self.current_doc:
            self.links_drawer.load_links(
                self.current_doc.links,
                self.current_doc.checked_links,
                getattr(self, "linked_footage", {})
            )
            self.timeline_list.set_linked_footage(getattr(self, "linked_footage", {}))
            self.timeline_list.refresh_timecodes()
        if hasattr(self, "trimmer_window") and self.trimmer_window:
            self.trimmer_window.refresh_probed_metadata(source_path)
        self.copy_feedback_requested.emit(f"Replaced: {os.path.basename(source_path)}")

    def _on_render_queue_job_finished(self, job, success: bool, final_path_or_err: str):
        if not success:
            return
        target_path = final_path_or_err if (final_path_or_err and os.path.isfile(final_path_or_err)) else getattr(job, "source_path", "")
        if target_path:
            self._on_transcode_finished_refresh(target_path)

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

        self.schedule_save()
        self._recompute_stats()

    def _on_save_docx_requested(self, edited_blocks: list):
        if not self.current_doc:
            return

        # Updates in-memory document blocks — project save file (.clickproj) is the source of truth!
        self.current_doc.blocks = edited_blocks
        self.schedule_save()
        self.copy_feedback_requested.emit("Saved to Project!")

    def _on_item_timecode_changed(self, item_id: str, new_tc: str):
        if not self.current_doc:
            return

        target_item = None
        for item in self.current_doc.items:
            if item.id == item_id:
                target_item = item
                break

        if target_item:
            from ...core.parser import process_time_span
            s_tc, s_sec, e_tc, e_sec, dur_sec, dur_disp, _, _ = process_time_span(new_tc)
            target_item.start_timecode = s_tc
            target_item.start_seconds = s_sec
            target_item.end_timecode = e_tc
            target_item.end_seconds = e_sec
            target_item.duration_seconds = dur_sec
            target_item.duration_display = dur_disp
            if self.current_doc.file_signature:
                db.save_override(self.current_doc.file_signature, target_item.id, s_tc, e_tc)

        self.schedule_save()
        self._recompute_stats()

    def _on_link_checked_changed(self, link: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_links[link] = is_checked
        self.schedule_save()

    def _on_note_checked_changed(self, note_id: str, is_checked: bool):
        if not self.current_doc:
            return
        self.current_doc.checked_notes[note_id] = is_checked
        self.schedule_save()

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
        self.schedule_save()
        
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

    def set_vo_data(
        self,
        vo_file_path: str,
        vo_segments: list,
        vo_transcript_path: str = "",
        vo_files: Optional[list] = None,
    ):
        """Stores bound voiceover path and Whisper segments on the active document and persists project."""
        if not self.current_doc:
            return
        self.current_doc.vo_file_path = vo_file_path
        if vo_files is not None:
            self.current_doc.vo_files = list(vo_files)
        elif vo_file_path and not getattr(self.current_doc, "vo_files", None):
            self.current_doc.vo_files = [vo_file_path]
        if vo_transcript_path:
            self.current_doc.vo_transcript_path = vo_transcript_path
        if vo_segments is not None:
            self.current_doc.vo_segments = list(vo_segments)
        db.save_document(self.current_doc)
        self._save_auto_project()

    def handle_mcp_gui_command(self, action: str, params: dict) -> dict:
        """
        Executes incoming Clickables MCP commands directly on the Qt Main Thread.
        Simulates native card checkbox clicks, renders VO diff callouts on the ScriptViewer,
        and exports multi-track FCP7 XML (V1/A2 = A-Roll, A1 = Voiceover).
        """
        if not self.current_doc:
            raise RuntimeError("No script document is currently loaded in Clickables.")

        fps_param = params.get("fps")
        if fps_param is not None:
            try:
                fps = float(fps_param)
            except Exception:
                fps = 30.0
        else:
            fps_str = db.get_setting("timeline_framerate", "23.976")
            try:
                fps = float(fps_str)
            except Exception:
                fps = 23.976

        linked_map = getattr(self, "linked_footage", {}) or {}

        if action == "get_workspace_status":
            from ...core.vo_engine import get_vo_manifest
            vo_manifest = get_vo_manifest(self.current_doc)
            vo_segs = getattr(self.current_doc, "vo_segments", []) or []
            locked_cnt = sum(1 for it in self.current_doc.items if getattr(it, "vo_status", "PENDING") == "LOCKED")
            checked_cnt = sum(1 for it in self.current_doc.items if it.is_checked)
            return {
                "document_name": self.current_doc.name,
                "file_path": self.current_doc.file_path,
                "file_signature": self.current_doc.file_signature,
                "fps": fps,
                "footage_folder": getattr(self, "footage_folder", ""),
                "total_links": len(self.current_doc.links),
                "linked_footage_count": len(linked_map),
                "vo_file_path": getattr(self.current_doc, "vo_file_path", ""),
                "vo_files": vo_manifest.get("vo_files", []),
                "vo_files_count": vo_manifest.get("vo_files_count", 0),
                "vo_transcript_path": getattr(self.current_doc, "vo_transcript_path", "") or vo_manifest.get("vo_transcript_path", ""),
                "vo_files_manifest": vo_manifest.get("files", []),
                "vo_total_duration_sec": vo_manifest.get("total_duration_sec", 0.0),
                "vo_segment_count": len(vo_segs) or vo_manifest.get("total_segments", 0),
                "total_aroll_items": len(self.current_doc.items),
                "checked_aroll_items": checked_cnt,
                "vo_locked_aroll_items": locked_cnt,
                "pre_flight_reminder": (
                    "Before running assembly, confirm with the user whether to deliver via "
                    "(A) Export Multi-Track FCP7 XML via Clickables or (B) Push directly via Premiere Pro MCP."
                ),
            }

        elif action == "get_masked_script_and_aroll":
            from ...core.vo_engine import build_masked_script_payload, get_vo_manifest
            s_blk = int(params.get("start_block", 0))
            e_blk = int(params.get("end_block", 999999))
            payload = build_masked_script_payload(
                self.current_doc,
                linked_footage=linked_map,
                fps=fps,
                start_block=s_blk,
                end_block=e_blk,
            )
            vo_manifest = get_vo_manifest(self.current_doc)
            payload["vo_file_path"] = getattr(self.current_doc, "vo_file_path", "")
            payload["vo_files"] = vo_manifest.get("vo_files", [])
            payload["vo_files_count"] = vo_manifest.get("vo_files_count", 0)
            payload["vo_transcript_path"] = getattr(self.current_doc, "vo_transcript_path", "") or vo_manifest.get("vo_transcript_path", "")
            payload["vo_files_manifest"] = vo_manifest.get("files", [])
            return payload

        elif action == "get_vo_segments":
            from ...core.vo_engine import get_vo_manifest
            vo_manifest = get_vo_manifest(self.current_doc)
            s_id = int(params.get("start_id", 0))
            e_id = int(params.get("end_id", 999999))
            segs = getattr(self.current_doc, "vo_segments", []) or []
            filtered = [s for s in segs if s_id <= int(s.get("id", 0)) <= e_id]
            return {
                "vo_file_path": getattr(self.current_doc, "vo_file_path", ""),
                "vo_files": vo_manifest.get("vo_files", []),
                "vo_files_count": vo_manifest.get("vo_files_count", 0),
                "vo_transcript_path": getattr(self.current_doc, "vo_transcript_path", "") or vo_manifest.get("vo_transcript_path", ""),
                "files": vo_manifest.get("files", []),
                "total_segments": len(segs) or vo_manifest.get("total_segments", 0),
                "returned_segments": len(filtered),
                "segments": filtered,
            }

        elif action == "apply_script_vo_highlight":
            diffs_list = params.get("diffs") or []
            if not hasattr(self.current_doc, "vo_block_diffs") or self.current_doc.vo_block_diffs is None:
                self.current_doc.vo_block_diffs = {}
            last_blk = None
            for d in diffs_list:
                b_id = d.get("block_id")
                if b_id is None:
                    continue
                self.current_doc.vo_block_diffs[str(b_id)] = {
                    "block_id": int(b_id),
                    "vo_segment_ids": d.get("vo_segment_ids", []),
                    "vo_start_sec": float(d.get("vo_start_sec", 0.0) or 0.0),
                    "vo_end_sec": float(d.get("vo_end_sec", 0.0) or 0.0),
                    "vo_actual_text": str(d.get("vo_actual_text", "")),
                    "match_type": str(d.get("match_type", "EXACT")).upper(),
                    "adlib_note": str(d.get("adlib_note", "")),
                }
                last_blk = int(b_id)
            self.script_viewer.apply_vo_block_diffs(self.current_doc.vo_block_diffs, scroll_to_last_block=last_blk)
            self.schedule_save()
            return {
                "updated_blocks": len(diffs_list),
                "total_highlighted_blocks": len(self.current_doc.vo_block_diffs),
            }

        elif action == "simulate_aroll_lock":
            locks_list = params.get("locks") or []
            updated_rows = []
            for lk in locks_list:
                r_idx = lk.get("row_index")
                it_id = lk.get("item_id")
                cut_sec = lk.get("vo_cut_after_sec")
                res_sec = lk.get("vo_resume_sec")
                lead_txt = lk.get("vo_lead_in_text", "")
                has_adlib = bool(lk.get("vo_has_adlib", False))
                adlib_note = lk.get("vo_adlib_note", "")
                status = str(lk.get("vo_status", "LOCKED")).upper()

                matched_item = self.timeline_list.lock_aroll_card(
                    row_index=r_idx,
                    item_id=it_id,
                    vo_cut_after_sec=float(cut_sec) if cut_sec is not None else None,
                    vo_resume_sec=float(res_sec) if res_sec is not None else None,
                    vo_lead_in_text=lead_txt,
                    vo_has_adlib=has_adlib,
                    vo_adlib_note=adlib_note,
                    vo_status=status,
                    scroll_into_view=(len(locks_list) == 1),
                )
                if matched_item:
                    updated_rows.append(matched_item.row_index)

            self.schedule_save()
            self._recompute_stats()
            return {
                "locked_rows": updated_rows,
                "total_locked": sum(1 for it in self.current_doc.items if getattr(it, "vo_status", "PENDING") == "LOCKED"),
                "total_items": len(self.current_doc.items),
            }

        elif action == "trigger_xml_export":
            from ...core.xml_exporter import export_assembled_fcp_xml_file
            out_path = params.get("output_path", "")
            if not out_path:
                base_dir = os.path.dirname(self.current_doc.file_path) if self.current_doc.file_path else os.path.expanduser("~")
                clean_name = os.path.splitext(self.current_doc.name or "Clickables_Timeline")[0]
                out_path = os.path.join(base_dir, f"{clean_name}_PreCut_Assembly.xml")

            explicit_segs = params.get("assembly_segments")
            if explicit_segs:
                self.current_doc.timeline_assembly = list(explicit_segs)

            saved_xml = export_assembled_fcp_xml_file(
                file_path=out_path,
                doc_state=self.current_doc,
                vo_file_path=getattr(self.current_doc, "vo_file_path", ""),
                assembly_segments=explicit_segs,
                linked_footage=linked_map,
                fps=fps,
                include_clip_markers=bool(params.get("include_clip_markers", False)),
            )
            self.copy_feedback_requested.emit("Exported Pre-Cut XML!")
            return {
                "exported_xml_path": saved_xml,
                "track_layout": {
                    "V1": "Bound A-Roll Video Clips",
                    "A1": "Voiceover Narration Packs",
                    "A2": "A-Roll Native Audio Clips",
                },
                "fps": fps,
            }

        raise ValueError(f"Unknown Clickables MCP GUI action: {action}")

    # --- Inline Clip Player (Loop) & A-Roll Editor ---
    # Entry point is the ▶ on each timestamp card. The shared trimmer window is recycled:
    # Loop state plays In→Out on repeat; its Edit button switches to precision trimming.
    def _arolls(self):
        if not self.current_doc or not self.current_doc.items:
            return []
        return [itm for itm in self.current_doc.items if not itm.is_single and bool(itm.end_timecode)]

    def toggle_aroll_edit_mode(self):
        """Alt+E: toggles the shared clip player (opens on the last played / first A-Roll card)."""
        if self.trimmer_window.isVisible():
            self.trimmer_window.close()
            return
        arolls = self._arolls()
        if not arolls:
            return
        target = next((i for i in arolls if i.id == self._active_trimmed_item_id), arolls[0])
        self._on_card_play_requested(target.id, target)

    def _on_card_play_requested(self, item_id: str, item):
        """Card ▶ clicked: load the clip into the recycled player and loop it."""
        if not is_mpv_available():
            self.timeline_list.show_missing_mpv_banner(True)
            return
        win = self.trimmer_window
        # Same card already loaded: ▶ acts as play/pause for the player
        if win.isVisible() and self._active_trimmed_item_id == item_id:
            win.toggle_play()
            win.raise_()
            return
        if not self._aroll_edit_mode:
            self._aroll_edit_mode = True
            self.timeline_list.missing_mpv_banner.hide()
            self.aroll_mode_changed.emit(True)
        if win.is_edit_mode():
            win.set_mode(win.MODE_LOOP, resume=False)
        self._on_aroll_card_targeted(item_id, item)
        win.activateWindow()

    def _on_escape_pressed(self):
        if self.trimmer_window.isVisible():
            self.trimmer_window.close()

    def _on_trimmer_window_closed(self):
        if hasattr(self, "trimmer_window") and self.trimmer_window:
            self.trimmer_window.flush_trim_overrides()
        self._aroll_edit_mode = False
        self.timeline_list.set_player_target(None)
        self.aroll_mode_changed.emit(False)
        if getattr(self, "_has_pending_trim_changes", False):
            self._has_pending_trim_changes = False
            self.flush_save()

    def _on_trimmer_edit_applied(self, item_id: str):
        """Edit session finished with a changed In/Out: pulse the card so the user sees it landed."""
        self.timeline_list.pulse_card(item_id)
        if getattr(self, "_has_pending_trim_changes", False):
            self._has_pending_trim_changes = False
            self.flush_save()

    def get_footage_path_for_item(self, item) -> Optional[str]:
        """Resolves local media file bound to this script item via URL, drive ID, card, or filename."""
        if not item:
            return None

        # 1. Check card widget directly if instantiated
        card = self.timeline_list.card_widgets.get(item.id)
        if card and hasattr(card, "get_linked_footage_path"):
            p = card.get_linked_footage_path()
            if p and os.path.isfile(p):
                return p

        linked = getattr(self, "linked_footage", {}) or {}
        if not linked:
            return None

        # 2. Check item.links (URL matching + Drive ID matching)
        for lk in getattr(item, "links", []):
            norm = normalize_link(lk)
            p = linked.get(norm) or linked.get(lk)
            if p and os.path.isfile(p):
                return p

            # Fallback: Google Drive File ID match
            m = re.search(r'/d/([a-zA-Z0-9_-]+)', lk)
            if m:
                fid = m.group(1)
                for map_url, path in linked.items():
                    if fid in map_url and os.path.isfile(path):
                        return path

        # 3. Direct lookup by item.id or raw_timestamp
        p = linked.get(item.id) or linked.get(item.raw_timestamp)
        if p and os.path.isfile(p):
            return p

        # 4. Fallback: match speaker / stem against linked files
        speaker = getattr(item, "speaker", "")
        if speaker:
            spk_clean = re.sub(r'[\W_]+', '', speaker).lower()
            if spk_clean:
                for path in linked.values():
                    if os.path.isfile(path):
                        base_stem = re.sub(r'[\W_]+', '', os.path.splitext(os.path.basename(path))[0]).lower()
                        if spk_clean in base_stem or base_stem in spk_clean:
                            return path

        return None

    def _on_aroll_card_targeted(self, item_id: str, item):
        self._active_trimmed_item_id = item_id
        footage_path = self.get_footage_path_for_item(item)
        doc_sig = self.current_doc.file_signature if self.current_doc else ""

        self.trimmer_window.load_item(item, footage_path, doc_signature=doc_sig)
        self.trimmer_window.show()
        self.trimmer_window.raise_()

        # Outline only the card currently loaded in the player
        self.timeline_list.set_player_target(item_id)
        self.timeline_list.scroll_to_card_by_id(item_id)

    def _on_trimmer_timecode_changed(self, item_id: str, in_tc: str, out_tc: str):
        # Update badge and values in TimelineList in memory
        card = self.timeline_list.card_widgets.get(item_id)
        if card:
            if hasattr(card, "apply_timecode_override"):
                card.apply_timecode_override(in_tc, out_tc)
            else:
                card._update_card_style()
        self._has_pending_trim_changes = True

    def _on_trimmer_prev_card(self):
        if not self.current_doc or not self.current_doc.items:
            return
        arolls = [itm for itm in self.current_doc.items if not itm.is_single and bool(itm.end_timecode)]
        if not arolls:
            return
        cur_idx = 0
        for idx, itm in enumerate(arolls):
            if itm.id == self._active_trimmed_item_id:
                cur_idx = idx
                break
        prev_idx = (cur_idx - 1) % len(arolls)
        target = arolls[prev_idx]
        self._on_aroll_card_targeted(target.id, target)

    def _on_trimmer_next_card(self):
        if not self.current_doc or not self.current_doc.items:
            return
        arolls = [itm for itm in self.current_doc.items if not itm.is_single and bool(itm.end_timecode)]
        if not arolls:
            return
        cur_idx = 0
        for idx, itm in enumerate(arolls):
            if itm.id == self._active_trimmed_item_id:
                cur_idx = idx
                break
        next_idx = (cur_idx + 1) % len(arolls)
        target = arolls[next_idx]
        self._on_aroll_card_targeted(target.id, target)

    def replace_linked_footage_path(self, old_path: str, new_path: str):
        """
        Updates internal linked footage mapping when a video file is transcoded/renamed (e.g. .avi -> .mp4).
        Updates linked_footage, refreshes thumbnails/cards, and refreshes the trimmer player if the active file changed.
        """
        if not getattr(self, "linked_footage", None):
            return
        norm_old = os.path.normcase(os.path.abspath(old_path))
        changed = False
        for k, v in list(self.linked_footage.items()):
            if v and os.path.normcase(os.path.abspath(v)) == norm_old:
                self.linked_footage[k] = new_path
                changed = True
        if changed:
            self.set_footage_folder(getattr(self, "footage_folder", ""), self.linked_footage)
            if hasattr(self, "trimmer_window") and self.trimmer_window and self.trimmer_window.isVisible():
                if self.trimmer_window.current_file and os.path.normcase(os.path.abspath(self.trimmer_window.current_file)) == norm_old:
                    self.trimmer_window.refresh_probed_metadata(new_path)

