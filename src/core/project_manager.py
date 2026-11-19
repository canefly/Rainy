import os
import json
import time
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

from .logger import get_logger

logger = get_logger("ProjectManager")


DOCUMENTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Rainy")
PROJECTS_DIR = os.path.join(DOCUMENTS_DIR, "Projects")
AUTOSAVE_DIR = os.path.join(PROJECTS_DIR, ".autosave")


def sanitize_filename(name: str) -> str:
    """Removes invalid filesystem characters for Windows/POSIX paths."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or "Untitled_Project"


@dataclass
class ProjectData:
    format_version: int = 2
    project_name: str = ""
    script_file_name: str = ""
    file_signature: str = ""
    footage_folder: str = ""
    vo_file_path: str = ""
    vo_files: List[str] = field(default_factory=list)
    vo_transcript_path: str = ""
    vo_segments: List[Dict[str, Any]] = field(default_factory=list)
    vo_block_diffs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    aroll_locks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    timeline_assembly: List[Dict[str, Any]] = field(default_factory=list)
    checked_items: List[str] = field(default_factory=list)
    checked_notes: List[str] = field(default_factory=list)
    checked_links: List[str] = field(default_factory=list)
    linked_footage: Dict[str, str] = field(default_factory=dict)
    notes_data: List[Dict[str, Any]] = field(default_factory=list)
    last_modified: float = field(default_factory=time.time)

    # Standalone self-contained script & document data
    raw_html: str = ""
    items: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Dict[str, Any]] = field(default_factory=list)
    custom_notes: List[Dict[str, Any]] = field(default_factory=list)
    headings: List[Dict[str, Any]] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    link_colors: Dict[str, str] = field(default_factory=dict)
    page_geometry: Dict[str, float] = field(default_factory=dict)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def doc_name(self) -> str:
        return self.project_name

    @property
    def docx_path(self) -> str:
        return self.script_file_name

    def has_embedded_document(self) -> bool:
        """Returns True if this project file contains full self-contained document content."""
        return bool(self.items or self.blocks or self.raw_html)

    def to_document_state(self) -> Any:
        """Reconstructs a full, self-contained DocumentState ready for immediate workspace rendering."""
        from .models import DocumentState, ScriptItem, EditorNote
        import uuid

        # 1. Hydrate ScriptItems
        built_items: List[ScriptItem] = []
        checked_set = set(self.checked_items)
        for it_raw in self.items:
            if isinstance(it_raw, ScriptItem):
                it = it_raw
            elif isinstance(it_raw, dict):
                valid_fields = set(ScriptItem.__dataclass_fields__.keys())
                filtered_args = {k: v for k, v in it_raw.items() if k in valid_fields}
                if "id" not in filtered_args:
                    filtered_args["id"] = str(uuid.uuid4())
                if "row_index" not in filtered_args:
                    filtered_args["row_index"] = 0
                if "raw_timestamp" not in filtered_args:
                    filtered_args["raw_timestamp"] = ""
                if "start_timecode" not in filtered_args:
                    filtered_args["start_timecode"] = "00:00:00:00"
                if "start_seconds" not in filtered_args:
                    filtered_args["start_seconds"] = 0.0
                if "end_timecode" not in filtered_args:
                    filtered_args["end_timecode"] = "00:00:00:00"
                if "end_seconds" not in filtered_args:
                    filtered_args["end_seconds"] = 0.0
                if "duration_seconds" not in filtered_args:
                    filtered_args["duration_seconds"] = 0.0
                if "duration_display" not in filtered_args:
                    filtered_args["duration_display"] = "0s"
                if "text" not in filtered_args:
                    filtered_args["text"] = ""
                it = ScriptItem(**filtered_args)
            else:
                continue

            if it.id in checked_set:
                it.is_checked = True

            # Apply aroll locks if any
            lk = self.aroll_locks.get(it.id) or self.aroll_locks.get(str(it.row_index))
            if lk and isinstance(lk, dict):
                it.vo_locked = bool(lk.get("vo_locked", False))
                it.vo_cut_after_sec = lk.get("vo_cut_after_sec")
                it.vo_resume_sec = lk.get("vo_resume_sec")
                it.vo_lead_in_text = lk.get("vo_lead_in_text", "")
                it.vo_has_adlib = bool(lk.get("vo_has_adlib", False))
                it.vo_adlib_note = lk.get("vo_adlib_note", "")
                it.vo_status = lk.get("vo_status", "PENDING")

            # Apply overrides if any
            ov = self.overrides.get(it.id)
            if ov and isinstance(ov, dict):
                in_tc_ov = ov.get("start_tc") or ov.get("in_tc")
                out_tc_ov = ov.get("end_tc") or ov.get("out_tc")
                if in_tc_ov:
                    it.start_timecode = in_tc_ov
                if out_tc_ov:
                    it.end_timecode = out_tc_ov

                if "start_sec" in ov:
                    it.start_seconds = float(ov["start_sec"])
                if "end_sec" in ov:
                    it.end_seconds = float(ov["end_sec"])
                if "dur_sec" in ov:
                    it.duration_seconds = float(ov["dur_sec"])

                # If timecodes were overridden but seconds were not provided, parse them
                if (in_tc_ov or out_tc_ov) and ("start_sec" not in ov or "end_sec" not in ov):
                    try:
                        from .parser import process_time_span, parse_time_single
                        if it.end_timecode and not it.is_single:
                            _, s_sec, _, e_sec, dur_s, dur_d, _, _ = process_time_span(f"{it.start_timecode} - {it.end_timecode}")
                            if "start_sec" not in ov:
                                it.start_seconds = s_sec
                            if "end_sec" not in ov:
                                it.end_seconds = e_sec
                            if "dur_sec" not in ov:
                                it.duration_seconds = dur_s
                            it.duration_display = dur_d
                        elif it.start_timecode:
                            _, s_sec = parse_time_single(it.start_timecode)
                            if "start_sec" not in ov:
                                it.start_seconds = s_sec
                    except Exception:
                        pass

                if "text" in ov and ov["text"]:
                    it.text = ov["text"]

            built_items.append(it)

        # 2. Hydrate Notes
        built_notes: List[EditorNote] = []
        for n_raw in self.notes:
            if isinstance(n_raw, EditorNote):
                built_notes.append(n_raw)
            elif isinstance(n_raw, dict):
                valid_fields = set(EditorNote.__dataclass_fields__.keys())
                filtered_args = {k: v for k, v in n_raw.items() if k in valid_fields}
                if "id" not in filtered_args:
                    filtered_args["id"] = str(uuid.uuid4())
                if "block_id" not in filtered_args:
                    filtered_args["block_id"] = 0
                if "text" not in filtered_args:
                    filtered_args["text"] = ""
                built_notes.append(EditorNote(**filtered_args))

        # 3. Hydrate Custom Notes
        built_custom_notes: List[EditorNote] = []
        c_notes_source = self.custom_notes or self.notes_data
        for cn_raw in c_notes_source:
            if isinstance(cn_raw, EditorNote):
                built_custom_notes.append(cn_raw)
            elif isinstance(cn_raw, dict):
                valid_fields = set(EditorNote.__dataclass_fields__.keys())
                filtered_args = {k: v for k, v in cn_raw.items() if k in valid_fields}
                if "id" not in filtered_args:
                    filtered_args["id"] = str(uuid.uuid4())
                if "block_id" not in filtered_args:
                    filtered_args["block_id"] = 0
                if "text" not in filtered_args:
                    filtered_args["text"] = ""
                built_custom_notes.append(EditorNote(**filtered_args))

        # 4. Check dicts
        checked_notes_dict = {k: True for k in self.checked_notes}
        checked_links_dict = {k: True for k in self.checked_links}

        # 5. Raw HTML fallback
        html = self.raw_html
        if not html and self.blocks:
            from .parser import build_raw_html
            html = build_raw_html(self.blocks, self.link_colors)

        default_geom = {
            "width_pt": 612.0, "height_pt": 792.0,
            "margin_top_pt": 72.0, "margin_bottom_pt": 72.0,
            "margin_left_pt": 72.0, "margin_right_pt": 72.0,
        }

        return DocumentState(
            id=self.file_signature or str(uuid.uuid4()),
            file_signature=self.file_signature,
            name=self.project_name or "Project",
            file_path=self.script_file_name,
            raw_html=html,
            items=built_items,
            notes=built_notes,
            custom_notes=built_custom_notes,
            headings=list(self.headings),
            links=list(self.links),
            checked_links=checked_links_dict,
            checked_notes=checked_notes_dict,
            link_colors=dict(self.link_colors),
            page_geometry=dict(self.page_geometry) if self.page_geometry else default_geom,
            blocks=list(self.blocks),
            vo_file_path=self.vo_file_path,
            vo_files=list(self.vo_files),
            vo_transcript_path=self.vo_transcript_path,
            vo_segments=list(self.vo_segments),
            vo_block_diffs=dict(self.vo_block_diffs),
            timeline_assembly=list(self.timeline_assembly),
        )


class ProjectManager:
    """
    Manages isolated, zero-corruption .rainyproj workspace files under Documents/Rainy/Projects/.
    Guarantees atomic writes (via .tmp and os.replace) so projects can never corrupt during power loss.
    """
    _instance: Optional["ProjectManager"] = None

    @classmethod
    def instance(cls) -> "ProjectManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.ensure_directories()

    @staticmethod
    def ensure_directories():
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        os.makedirs(AUTOSAVE_DIR, exist_ok=True)

    @staticmethod
    def get_projects_dir() -> str:
        return PROJECTS_DIR

    @staticmethod
    def get_auto_project_path(doc_name: str) -> str:
        """Returns the managed project file path under Documents/Rainy/Projects/."""
        base_name = os.path.splitext(doc_name)[0]
        sanitized = sanitize_filename(base_name)
        return os.path.join(PROJECTS_DIR, f"{sanitized}.rainyproj")

    def save_project(self, file_path: str, data: ProjectData) -> bool:
        """
        Saves project data using atomic write-and-replace (via .tmp and os.replace)
        to prevent file corruption during hard crashes or power outages.
        """
        if not file_path:
            return False

        t0 = time.perf_counter()
        parent_dir = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(parent_dir, exist_ok=True)

        tmp_path = file_path + ".tmp"
        data.last_modified = time.time()
        payload = asdict(data)

        # Debloat: if independent VO transcript JSON exists, avoid duplicating raw segments in .rainyproj
        if data.vo_transcript_path and os.path.isfile(data.vo_transcript_path):
            payload["vo_segments"] = []

        for attempt in range(2):
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic swap on Windows NTFS / POSIX
                os.replace(tmp_path, file_path)
                self._create_autosave_snapshot(file_path, data)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.info(
                    "Project saved: '%s' (%d checks, %d links, %d notes, %.1f ms)",
                    os.path.basename(file_path),
                    len(data.checked_items),
                    len(data.checked_links),
                    len(data.checked_notes),
                    elapsed_ms
                )
                return True
            except PermissionError as pe:
                if attempt == 0:
                    time.sleep(0.05)
                    continue
                logger.error("PermissionError during atomic save of '%s': %s", file_path, pe)
            except Exception as e:
                logger.error("Unexpected error saving project '%s': %s", file_path, e, exc_info=True)
                break

        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False

    def load_project(self, file_path: str) -> Optional[ProjectData]:
        """Loads and parses a .rainyproj file."""
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            vo_trans_path = raw.get("vo_transcript_path", "")
            raw_segs = list(raw.get("vo_segments", []))
            raw_vo_files = list(raw.get("vo_files", []))
            if vo_trans_path and os.path.isfile(vo_trans_path):
                try:
                    with open(vo_trans_path, "r", encoding="utf-8") as tf:
                        tdata = json.load(tf)
                        if isinstance(tdata, dict):
                            if "segments" in tdata:
                                from .vo_islands import slim_segments
                                raw_segs = slim_segments(tdata["segments"])
                            if not raw_vo_files and "files" in tdata:
                                raw_vo_files = [f.get("path") for f in tdata["files"] if f.get("path")]
                except Exception as e:
                    logger.warning("Failed to load VO transcript JSON from %s: %s", vo_trans_path, e)

            proj = ProjectData(
                format_version=int(raw.get("format_version", 1)),
                project_name=raw.get("project_name", ""),
                script_file_name=raw.get("script_file_name", ""),
                file_signature=raw.get("file_signature", ""),
                footage_folder=raw.get("footage_folder", ""),
                vo_file_path=raw.get("vo_file_path", ""),
                vo_files=raw_vo_files,
                vo_transcript_path=vo_trans_path,
                vo_segments=raw_segs,
                vo_block_diffs=dict(raw.get("vo_block_diffs", {})),
                aroll_locks=dict(raw.get("aroll_locks", {})),
                timeline_assembly=list(raw.get("timeline_assembly", [])),
                checked_items=list(raw.get("checked_items", [])),
                checked_notes=list(raw.get("checked_notes", [])),
                checked_links=list(raw.get("checked_links", [])),
                linked_footage=dict(raw.get("linked_footage", {})),
                notes_data=list(raw.get("notes_data", [])),
                last_modified=float(raw.get("last_modified", 0.0)),
                raw_html=raw.get("raw_html", ""),
                items=list(raw.get("items", [])),
                notes=list(raw.get("notes", [])),
                custom_notes=list(raw.get("custom_notes", [])),
                headings=list(raw.get("headings", [])),
                links=list(raw.get("links", [])),
                link_colors=dict(raw.get("link_colors", {})),
                page_geometry=dict(raw.get("page_geometry", {})),
                blocks=list(raw.get("blocks", [])),
                overrides=dict(raw.get("overrides", {})),
            )
            logger.info(
                "Project loaded: '%s' (ver %d, %d checks, %d links)",
                os.path.basename(file_path),
                proj.format_version,
                len(proj.checked_items),
                len(proj.checked_links)
            )
            return proj
        except Exception as e:
            logger.warning("Failed to load project '%s': %s", file_path, e)
            return None

    def _create_autosave_snapshot(self, original_path: str, data: ProjectData, max_autosaves: int = 5):
        """Creates timestamped rolling snapshots in the autosave directory."""
        try:
            base_name = os.path.splitext(os.path.basename(original_path))[0]
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            snap_path = os.path.join(AUTOSAVE_DIR, f"{base_name}_{timestamp}.rainyproj")
            
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(asdict(data), f, indent=2, ensure_ascii=False)

            # Prune older autosaves for this project
            existing = sorted([
                os.path.join(AUTOSAVE_DIR, f) for f in os.listdir(AUTOSAVE_DIR)
                if f.startswith(f"{base_name}_") and f.endswith(".rainyproj")
            ])
            while len(existing) > max_autosaves:
                old = existing.pop(0)
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass


project_manager = ProjectManager.instance()
