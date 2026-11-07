import os
import json
import time
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

from .logger import get_logger

logger = get_logger("ProjectManager")


DOCUMENTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Clickables")
PROJECTS_DIR = os.path.join(DOCUMENTS_DIR, "Projects")
AUTOSAVE_DIR = os.path.join(PROJECTS_DIR, ".autosave")


def sanitize_filename(name: str) -> str:
    """Removes invalid filesystem characters for Windows/POSIX paths."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or "Untitled_Project"


@dataclass
class ProjectData:
    format_version: int = 1
    project_name: str = ""
    script_file_name: str = ""
    file_signature: str = ""
    footage_folder: str = ""
    checked_items: List[str] = field(default_factory=list)
    checked_notes: List[str] = field(default_factory=list)
    checked_links: List[str] = field(default_factory=list)
    linked_footage: Dict[str, str] = field(default_factory=dict)
    notes_data: List[Dict[str, Any]] = field(default_factory=list)
    last_modified: float = field(default_factory=time.time)


class ProjectManager:
    """
    Manages isolated, zero-corruption .clickproj workspace files under Documents/Clickables/Projects/.
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
    def get_auto_project_path(doc_name: str) -> str:
        """Returns the managed project file path under Documents/Clickables/Projects/."""
        base_name = os.path.splitext(doc_name)[0]
        sanitized = sanitize_filename(base_name)
        return os.path.join(PROJECTS_DIR, f"{sanitized}.clickproj")

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
        """Loads and parses a .clickproj file."""
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            proj = ProjectData(
                format_version=int(raw.get("format_version", 1)),
                project_name=raw.get("project_name", ""),
                script_file_name=raw.get("script_file_name", ""),
                file_signature=raw.get("file_signature", ""),
                footage_folder=raw.get("footage_folder", ""),
                checked_items=list(raw.get("checked_items", [])),
                checked_notes=list(raw.get("checked_notes", [])),
                checked_links=list(raw.get("checked_links", [])),
                linked_footage=dict(raw.get("linked_footage", {})),
                notes_data=list(raw.get("notes_data", [])),
                last_modified=float(raw.get("last_modified", 0.0)),
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
            snap_path = os.path.join(AUTOSAVE_DIR, f"{base_name}_{timestamp}.clickproj")
            
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(asdict(data), f, indent=2, ensure_ascii=False)

            # Prune older autosaves for this project
            existing = sorted([
                os.path.join(AUTOSAVE_DIR, f) for f in os.listdir(AUTOSAVE_DIR)
                if f.startswith(f"{base_name}_") and f.endswith(".clickproj")
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
